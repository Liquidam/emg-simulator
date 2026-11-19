"""The simulator engine: physiology + noise + device, real-time pacing, OSC in/out.

Signal chain per channel (each step = block_ms of signal at fs):
  scenario drive 0..1 -> muscle (motor units, MUAPs, fatigue) -> crosstalk
  -> + noise (baseline, hum, wander, artifacts, pops, ECG)
  -> amplifier filters (high-pass, low-pass, notch) = "signal" in mV
  -> ADC (offset, gain, bits, converter noise, clipping) = "counts"
Outputs (OSC streams, each can be switched on/off and has its own rate):
  raw, rawblock, env, features, truth, bio (EMG6 / ESP32 format), adc (WearPi format).
"""
import math
import os
import threading
import time
import wave
import numpy as np

from . import config as C
from . import features as F
from .dsp import butter_hp, butter_lp, notch, OnePole
from .noise import NoiseBank
from .osc import Sender, Receiver, message, bundle
from .physiology import Muscle
from .scenarios import Scenario


class Simulator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.build()

    def build(self):
        cfg = self.cfg
        self.fs = fs = int(cfg['fs'])
        self.ch = ch = int(max(1, min(8, cfg['channels'])))
        seed = int(cfg.get('seed', 0))
        if seed <= 0:                       # a new random run every time
            seed = int.from_bytes(os.urandom(3), 'big') + 1
        self.seed_used = seed
        ph = cfg['physiology']
        self.muscles = [Muscle(fs, mvc_rms_mV=ph['mvc_rms_mV'], seed=seed * 1000 + c,
                               **self._pool_kw()) for c in range(ch)]
        self.noise = NoiseBank(fs, ch, seed)
        self.scen = [Scenario(fs, seed * 10 + c) for c in range(ch)]
        self.cd_lp = OnePole(1.5, fs, ch)
        self.cd_rng = np.random.default_rng(seed + 77)
        self.xt_lp = OnePole(120.0, fs, ch)
        self.adc_rng = np.random.default_rng(seed + 55)
        self.build_filters()
        self.t = 0.0
        self.manual = [0.0] * ch
        self.osc_drive = [0.0] * ch
        self.prev_drive = np.zeros(ch)

    def _pool_kw(self):
        ph = self.cfg['physiology']
        return dict(n_mu=int(ph['n_mu']), rr=ph['rr'], mfr=ph['mfr'], pfr_first=ph['pfr_first'],
                    pfr_last=ph['pfr_last'], isi_cv=ph['isi_cv'], muap_width_ms=ph['muap_width_ms'],
                    depth_max=ph['depth_max'])

    def build_filters(self):
        d = self.cfg['device']
        fs, ch = self.fs, self.ch
        self.hp = butter_hp(d['hp_hz'], fs, ch) if d.get('hp_on') else None
        self.hp2 = butter_hp(d['hp_hz'], fs, ch) if d.get('hp_on') else None       # 4th order
        self.lp = butter_lp(d['lp_hz'], fs, ch) if d.get('lp_on') else None
        f0 = 60.0 if int(self.cfg['noise'].get('mains_hz', 50)) == 60 else 50.0
        self.nt = notch(f0, fs, 8.0, ch) if d.get('notch_on') else None

    def rebuild_physiology(self):
        ph = self.cfg['physiology']
        for m in self.muscles:
            m.mvc_rms_mV = ph['mvc_rms_mV']
            m.reconfigure(**self._pool_kw())

    def step(self, n_s):
        cfg, fs, ch = self.cfg, self.fs, self.ch
        ph = cfg['physiology']
        drive = np.zeros((ch, n_s))
        linked = cfg.get('link_channels')
        for c in range(ch):
            p = C.scenario_for(cfg, c)
            man = self.manual[0] if linked else self.manual[c]
            oscv = self.osc_drive[0] if linked else self.osc_drive[c]
            if linked and c > 0 and p.get('mode') == 'performer':
                # one player: neighbouring muscles co-contract, a little weaker each
                drive[c] = drive[0] * (0.85 ** c) * (0.9 + 0.1 * math.sin(self.t * (0.7 + 0.3 * c)))
                continue
            drive[c] = self.scen[c].render(self.t, n_s, p, man, oscv)
        onsets = [c for c in range(ch) if self.prev_drive[c] < 0.1 <= drive[c, -1]]
        self.prev_drive = drive[:, -1].copy()

        cd = self.cd_lp.process(self.cd_rng.standard_normal((ch, n_s))) / math.sqrt(self.cd_lp.k / 2.0)
        clean = np.zeros((ch, n_s))
        infos = []
        for c, m in enumerate(self.muscles):
            x, info = m.render(self.t, drive[c], fatigue_on=ph['fatigue_on'],
                               fatigue_rate=ph['fatigue_rate'], recovery_rate=ph['recovery_rate'],
                               common_drive=ph['common_drive'], cd_noise=cd[c])
            clean[c] = x
            infos.append(info)

        xt = float(ph.get('crosstalk', 0.0))
        mixed = clean
        if xt > 0 and ch > 1:
            far = self.xt_lp.process(clean)            # tissue low-passes what travels far
            mixed = clean.copy()
            for i in range(ch):
                for j in range(ch):
                    if i != j:
                        mixed[i] += (xt ** abs(i - j)) * far[j]

        sig = mixed + self.noise.render(n_s, cfg['noise'], onsets)
        for f in (self.hp, self.hp2, self.lp, self.nt):
            if f is not None:
                sig = f.process(sig)

        d = cfg['device']
        top = (1 << int(d['adc_bits'])) - 1
        counts = d['adc_offset'] + sig * d['adc_counts_per_mV'] + \
            self.adc_rng.standard_normal((ch, n_s)) * d['adc_noise_lsb']
        counts = np.clip(np.round(counts), 0, top)
        t0 = self.t
        self.t += n_s / fs
        return dict(t0=t0, drive=drive, clean=clean, signal=sig, counts=counts, info=infos)


class Imu:
    """A simple arm IMU for the /bio stream: lifts with effort, wanders, has noise."""

    def __init__(self, seed=0):
        self.rng = np.random.default_rng(seed + 909)
        self.ang = np.zeros(3)          # pitch, roll, yaw (deg)
        self.w = np.zeros(3)

    def sample(self, dt, effort):
        r = self.rng
        target = np.array([35.0 * effort, 8.0 * effort, 0.0])
        self.w += (r.standard_normal(3) * np.array([20, 20, 30]) - 3.0 * self.w) * dt
        self.w += (target - self.ang) * 4.0 * dt
        self.ang += self.w * dt
        p, q = np.radians(self.ang[0]), np.radians(self.ang[1])
        acc = np.array([-math.sin(p), math.sin(q) * math.cos(p), math.cos(q) * math.cos(p)])
        acc += r.standard_normal(3) * 0.01
        gyro = self.w + r.standard_normal(3) * 0.4
        return acc, gyro


class Outputs:
    """Turns simulator blocks into OSC messages for every enabled stream."""

    def __init__(self, sim):
        self.sim = sim
        self.sender = Sender()
        self.reset()

    def reset(self):
        fs, ch = self.sim.fs, self.sim.ch
        self.hist = np.zeros((ch, fs))         # last 1 s of signal for windows
        self.acc = {}                          # per-stream sample phase
        self.seq = 0
        self.imu = Imu(self.sim.seed_used)
        self.adc_min = np.zeros(4)
        self.adc_max = np.ones(4) * 1e-3
        self.adc_hist = []
        self.msgs_per_s = 0.0
        self._count = 0
        self._t_count = time.time()
        self.last = {}

    def _dest(self, s):
        o = self.sim.cfg['osc']
        return o['host'], (s.get('port') or o['port'])

    def _ticks(self, name, rate, n_s):
        """Sample indices inside this block where a stream at `rate` Hz emits."""
        fs = self.sim.fs
        step = fs / float(max(rate, 1e-3))
        ph = self.acc.get(name, 0.0)
        idx = []
        while ph < n_s:
            idx.append(int(ph))
            ph += step
        self.acc[name] = ph - n_s
        return idx

    def process(self, b):
        cfg = self.sim.cfg
        o = cfg['osc']
        st = o['streams']
        fs, ch = self.sim.fs, self.sim.ch
        sig, counts, n_s = b['signal'], b['counts'], b['signal'].shape[1]
        self.hist = np.concatenate([self.hist[:, n_s:], sig], axis=1)
        out = {}                                       # (host, port) -> [messages]

        def emit(s, m):
            if o.get('packet_loss', 0) > 0 and np.random.random() < o['packet_loss']:
                return
            out.setdefault(self._dest(s), []).append(m)

        mvc = max(cfg['physiology']['mvc_rms_mV'], 1e-6)

        def units(x, u, cnt):
            if u == 'counts':
                return [int(v) for v in cnt]
            if u == 'uV':
                return [float(v * 1000.0) for v in x]
            if u == 'norm':
                return [float(max(-1.0, min(1.0, v / (4.0 * mvc)))) for v in x]
            return [float(v) for v in x]

        s = st.get('raw', {})
        if s.get('on'):
            idx = self._ticks('raw', s.get('rate', 1000), n_s)
            D = max(1, int(round(fs / float(s.get('rate', 1000)))))
            for k in idx:
                if s.get('decimate') == 'average':
                    a = max(0, k - D + 1)
                    x = sig[:, a:k + 1].mean(axis=1)
                    cn = np.round(counts[:, a:k + 1].mean(axis=1))
                else:
                    x, cn = sig[:, k], counts[:, k]
                emit(s, message(s['address'], *units(x, s.get('units', 'mV'), cn)))

        s = st.get('rawblock', {})
        if s.get('on'):
            for c in range(ch):
                emit(s, message('%s/%d' % (s['address'], c + 1),
                                *units(sig[c], s.get('units', 'mV'), counts[c])))

        s = st.get('env', {})
        if s.get('on'):
            w = max(4, int(fs * s.get('window_ms', 100) / 1000.0))
            for k in self._ticks('env', s.get('rate', 100), n_s):
                end = self.hist.shape[1] - (n_s - 1 - k)
                seg = self.hist[:, max(0, end - w):end]
                seg = seg - seg.mean(axis=1, keepdims=True)
                e = np.sqrt(np.mean(seg * seg, axis=1))
                if s.get('normalise', True):
                    e = np.clip(e / mvc, 0.0, 1.5)
                self.last['env'] = e
                emit(s, message(s['address'], *[float(v) for v in e]))

        s = st.get('features', {})
        if s.get('on') or True:
            w = max(16, int(fs * s.get('window_ms', 250) / 1000.0))
            for k in self._ticks('features', s.get('rate', 20), n_s):
                feats = F.compute(self.hist[:, -w:], fs)
                self.last['features'] = feats
                if s.get('on'):
                    for c in range(ch):
                        emit(s, message('%s/%d' % (s['address'], c + 1), *[float(v) for v in feats[c]]))

        s = st.get('truth', {})
        if s.get('on'):
            for k in self._ticks('truth', s.get('rate', 50), n_s):
                inf = b['info']
                base = s['address']
                emit(s, message(base + '/drive', *[float(b['drive'][c, k]) for c in range(ch)]))
                emit(s, message(base + '/force', *[float(i['force']) for i in inf]))
                emit(s, message(base + '/fatigue', *[float(i['fatigue']) for i in inf]))
                emit(s, message(base + '/active', *[int(i['active']) for i in inf]))
                emit(s, message(base + '/rate', *[float(i['mean_rate']) for i in inf]))

        s = st.get('bio', {})
        if s.get('on'):
            c = int(min(max(s.get('channel', 1), 1), ch)) - 1
            rate = s.get('rate', 200)
            for k in self._ticks('bio', rate, n_s):
                acc, gyro = self.imu.sample(1.0 / rate, float(b['drive'][c, k]))
                t_us = int(((b['t0'] + k / fs) * 1e6) % (2 ** 31))
                self.seq += 1
                if not s.get('imu', True):
                    acc, gyro = np.array([0.0, 0.0, 1.0]), np.zeros(3)
                emit(s, message(s['address'], int(self.seq % (2 ** 31)), t_us, int(counts[c, k]),
                                *[float(v) for v in acc], *[float(v) for v in gyro]))

        s = st.get('adc', {})
        if s.get('on'):
            w = max(4, int(fs * 0.05))
            for k in self._ticks('adc', s.get('rate', 100), n_s):
                end = self.hist.shape[1] - (n_s - 1 - k)
                seg = self.hist[:, max(0, end - w):end]
                e = np.zeros(4)
                n4 = min(4, ch)
                e[:n4] = np.sqrt(np.mean((seg[:n4] - seg[:n4].mean(axis=1, keepdims=True)) ** 2, axis=1))
                # WearPi style: adaptive min/max normalisation, then moving average
                self.adc_min = np.minimum(self.adc_min + 0.0005, e)
                self.adc_max = np.maximum(self.adc_max * 0.9995, e)
                v = np.clip((e - self.adc_min) / np.maximum(self.adc_max - self.adc_min, 1e-6), 0, 1)
                self.adc_hist.append(v)
                self.adc_hist = self.adc_hist[-int(max(1, s.get('smooth', 8))):]
                v = np.mean(self.adc_hist, axis=0)
                emit(s, message(s['address'], *[float(x) for x in v]))

        n = 0
        for (host, port), msgs in out.items():
            if o.get('use_bundles') and len(msgs) > 1:
                for i in range(0, len(msgs), 40):
                    self.sender.send(bundle(msgs[i:i + 40]), host, port)
            else:
                for m in msgs:
                    self.sender.send(m, host, port)
            n += len(msgs)
        self._count += n
        now = time.time()
        if now - self._t_count >= 1.0:
            self.msgs_per_s = self._count / (now - self._t_count)
            self._count, self._t_count = 0, now


class Runner:
    """Runs the simulator in real time on a thread. The GUI and the CLI both use it."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.sim = Simulator(cfg)
        self.out = Outputs(self.sim)
        self.rx = None
        self.rx_error = ''
        self.open_rx()
        self.running = False
        self.paused = False
        self.thread = None
        self.view_rate = 500
        self._init_view()
        self.recording = None
        self.last_info = []
        self.load_ms = 0.0
        self.on_remote_change = None

    def _init_view(self):
        ch = self.sim.ch
        n = self.view_rate * 6
        self.view = dict(signal=np.zeros((ch, n)), drive=np.zeros((ch, n)))
        self.view_n = getattr(self, 'view_n', 0)          # total samples ever added (for the web page)

    def open_rx(self):
        if self.rx:
            self.rx.close()
            self.rx = None
        try:
            self.rx = Receiver(self.cfg['osc']['in_port'])
            self.rx_error = ''
        except OSError as e:
            self.rx_error = 'OSC in port %s busy (%s)' % (self.cfg['osc']['in_port'], e)

    # -- control ------------------------------------------------------------------------------------
    def rebuild(self, what='all'):
        with self.lock:
            if what == 'all':
                manual = list(self.sim.manual)
                self.sim = Simulator(self.cfg)
                for c in range(min(len(manual), self.sim.ch)):
                    self.sim.manual[c] = manual[c]
                self.out = Outputs(self.sim)
                self._init_view()
            elif what == 'physiology':
                self.sim.rebuild_physiology()
            elif what == 'device':
                self.sim.build_filters()
            elif what == 'osc_in':
                self.open_rx()

    def set_manual(self, value, channel=None):
        with self.lock:
            if channel is None:
                self.sim.manual = [float(value)] * self.sim.ch
            elif 0 <= channel < self.sim.ch:
                self.sim.manual[channel] = float(value)

    def reset_fatigue(self):
        with self.lock:
            for m in self.sim.muscles:
                m.fatigue = 0.0

    def _remote(self):
        if not self.rx:
            return
        changed = False
        for addr, args in self.rx.poll():
            try:
                if addr == '/emgsim/drive':
                    if len(args) == 1:
                        self.sim.osc_drive = [float(args[0])] * self.sim.ch
                        self.sim.manual = [float(args[0])] * self.sim.ch
                    elif len(args) >= 2:
                        c = int(args[0]) - 1
                        if 0 <= c < self.sim.ch:
                            self.sim.osc_drive[c] = float(args[1])
                            self.sim.manual[c] = float(args[1])
                elif addr == '/emgsim/mode':
                    if len(args) == 1:
                        self.cfg['scenario'][0]['mode'] = str(args[0])
                    else:
                        c = int(args[0]) - 1
                        while len(self.cfg['scenario']) <= c:
                            self.cfg['scenario'].append(dict(self.cfg['scenario'][0]))
                        self.cfg['scenario'][c]['mode'] = str(args[1])
                    changed = True
                elif addr == '/emgsim/set' and len(args) >= 2:
                    C.set_path(self.cfg, str(args[0]), args[1])
                    top = str(args[0]).split('.')[0]
                    if top in ('fs', 'channels', 'seed'):
                        self.rebuild('all')
                    elif top == 'physiology' and str(args[0]).split('.')[1] in (
                            'n_mu', 'rr', 'mfr', 'pfr_first', 'pfr_last', 'isi_cv',
                            'muap_width_ms', 'depth_max', 'mvc_rms_mV'):
                        self.rebuild('physiology')
                    elif top == 'device':
                        self.rebuild('device')
                    changed = True
                elif addr == '/emgsim/fatigue/reset':
                    self.reset_fatigue()
                elif addr == '/emgsim/pause':
                    self.paused = bool(args[0]) if args else not self.paused
                elif addr == '/emgsim/preset' and args:
                    new = C.merge(C.DEFAULTS, C.preset(str(args[0])))
                    self.cfg.clear()
                    self.cfg.update(new)
                    self.sim.cfg = self.cfg
                    self.rebuild('all')
                    changed = True
            except Exception as e:                 # never let a bad message stop the engine
                self.rx_error = 'bad OSC %s %s: %s' % (addr, args, e)
        if changed and self.on_remote_change:
            self.on_remote_change()

    # -- loop ------------------------------------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.rx:
            self.rx.close()

    def _loop(self):
        nxt = time.perf_counter()
        while self.running:
            with self.lock:
                fs = self.sim.fs
                n_s = max(1, int(round(fs * self.cfg['block_ms'] / 1000.0)))
                self._remote()
                if not self.paused:
                    t_a = time.perf_counter()
                    b = self.sim.step(n_s)
                    self.out.process(b)
                    self._to_view(b)
                    if self.recording is not None:
                        self.recording.append((b['drive'].copy(), b['signal'].copy(), b['counts'].copy()))
                    self.last_info = b['info']
                    dt = time.perf_counter() - t_a
                    self.load_ms = 0.9 * self.load_ms + 0.1 * dt * 1000.0 / (n_s / fs)
            nxt += n_s / fs
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:
                nxt = time.perf_counter()          # fell behind: resync, don't burst

    def _to_view(self, b):
        fs = self.sim.fs
        step = max(1, int(fs / self.view_rate))
        ph = getattr(self, '_view_ph', 0)
        n_new = 0
        for key, arr in (('signal', b['signal']), ('drive', b['drive'])):
            d = arr[:, ph::step]
            n_new = d.shape[1]
            v = self.view[key]
            if v.shape[0] != d.shape[0]:
                self._init_view()
                v = self.view[key]
            self.view[key] = np.concatenate([v[:, d.shape[1]:], d], axis=1)
        self._view_ph = (ph - b['signal'].shape[1]) % step
        self.view_n += n_new

    # -- recording -------------------------------------------------------------------------------
    def record_start(self):
        with self.lock:
            self.recording = []

    def record_stop(self, path):
        """Saves .csv (time, drive_n, signal_mV_n, counts_n) or .wav (signal, one channel each)."""
        with self.lock:
            rec, self.recording = self.recording, None
        if not rec:
            return 0
        drive = np.concatenate([r[0] for r in rec], axis=1)
        sig = np.concatenate([r[1] for r in rec], axis=1)
        cnt = np.concatenate([r[2] for r in rec], axis=1)
        fs = self.sim.fs
        ch, n = sig.shape
        if path.lower().endswith('.wav'):
            peak = max(float(np.max(np.abs(sig))), 1e-9)
            data = (np.clip(sig / peak, -1, 1) * 32767).astype('<i2').T.copy()
            with wave.open(path, 'wb') as w:
                w.setnchannels(ch)
                w.setsampwidth(2)
                w.setframerate(fs)
                w.writeframes(data.tobytes())
        else:
            t = np.arange(n) / fs
            cols = [t] + list(drive) + list(sig) + list(cnt)
            head = ['t_s'] + ['drive_%d' % (c + 1) for c in range(ch)] + \
                ['signal_mV_%d' % (c + 1) for c in range(ch)] + ['counts_%d' % (c + 1) for c in range(ch)]
            np.savetxt(path, np.column_stack(cols), delimiter=',', header=','.join(head),
                       comments='', fmt='%.6g')
        return n / fs
