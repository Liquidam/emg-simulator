"""Desktop window for the EMG simulator (tkinter: ships with Python on Mac and Windows)."""
import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np

from . import config as C
from . import features as F
from .engine import Runner
from .scenarios import MODES

BG, PANEL, FG, DIM = '#14161a', '#1d2026', '#e6e8eb', '#8a9099'
ACC, GOLD, PINK = '#73c7f2', '#ffd673', '#ff6fa8'
LANE_COLS = ['#ff6fa8', '#73c7f2', '#9be37a', '#ffd673', '#c49bff', '#ff9a5c', '#5ce0d0', '#e0e0e0']


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.runner = Runner(cfg)
        self._remote_flag = False
        self.runner.on_remote_change = self._flag_remote     # engine thread: only set a flag
        self.root = tk.Tk()
        self.root.title('EMG Simulator')
        self.root.configure(bg=BG)
        self.root.geometry('1280x820')
        self.root.minsize(1000, 640)
        self._style()
        self.vars = {}          # cfg path -> (tk var, kind)
        self._pending = {}
        self.space_down = False
        self._build()
        self.root.bind('<KeyPress-space>', self._space_on)
        self.root.bind('<KeyRelease-space>', self._space_off)
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        self.runner.start()
        self._after = self.root.after(40, self._tick)

    # ------------------------------------------------------------------ look
    def _style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use('clam')
        except tk.TclError:
            pass
        s.configure('.', background=BG, foreground=FG, fieldbackground=PANEL)
        s.configure('TNotebook', background=BG, borderwidth=0)
        s.configure('TNotebook.Tab', background=PANEL, foreground=DIM, padding=(12, 6))
        s.map('TNotebook.Tab', background=[('selected', '#2a2f37')], foreground=[('selected', FG)])
        s.configure('TFrame', background=BG)
        s.configure('Card.TFrame', background=PANEL)
        s.configure('TLabel', background=PANEL, foreground=FG)
        s.configure('Dim.TLabel', background=PANEL, foreground=DIM)
        s.configure('Head.TLabel', background=PANEL, foreground=ACC, font=('Helvetica', 11, 'bold'))
        s.configure('Top.TLabel', background=BG, foreground=DIM)
        s.configure('TCheckbutton', background=PANEL, foreground=FG)
        s.configure('TButton', background='#2a2f37', foreground=FG, padding=(10, 4))
        s.map('TButton', background=[('active', '#38404b')])
        s.configure('Big.TButton', font=('Helvetica', 12, 'bold'), padding=(14, 8))
        s.configure('Horizontal.TScale', background=PANEL)

    # ------------------------------------------------------------------ helpers
    def _get(self, path):
        d = self.cfg
        for k in path.split('.'):
            d = d[int(k)] if isinstance(d, list) else d[k]
        return d

    def _apply(self, path, value, kind):
        C.set_path(self.cfg, path, value)
        if kind == 'live':
            return
        # rebuilds are debounced: dragging a slider rebuilds once, after it stops
        if path in self._pending:
            self.root.after_cancel(self._pending[path])
        self._pending[path] = self.root.after(350, lambda: self._rebuild(path, kind))

    def _rebuild(self, path, kind):
        self._pending.pop(path, None)
        self.runner.rebuild(kind)
        if kind == 'all':
            self._build_lanes()

    def slider(self, parent, label, path, lo, hi, kind='live', res=None, fmt='%.2f', row=None):
        f = ttk.Frame(parent, style='Card.TFrame')
        f.pack(fill='x', padx=10, pady=2)
        ttk.Label(f, text=label, width=24).pack(side='left')
        v = tk.DoubleVar(value=float(self._get(path)))
        val = ttk.Label(f, text=fmt % v.get(), width=8, style='Dim.TLabel')
        is_int = isinstance(self._get(path), int) and not isinstance(self._get(path), bool)

        def changed(_=None):
            x = v.get()
            if res:
                x = round(x / res) * res
            if is_int:
                x = int(round(x))
            val.configure(text=fmt % x)
            self._apply(path, x, kind)
        sc = ttk.Scale(f, from_=lo, to=hi, variable=v, command=changed)
        sc.pack(side='left', fill='x', expand=True, padx=6)
        val.pack(side='left')
        self.vars[path] = (v, 'num', val, fmt)
        return v

    def check(self, parent, label, path, kind='live'):
        v = tk.BooleanVar(value=bool(self._get(path)))
        cb = ttk.Checkbutton(parent, text=label, variable=v,
                             command=lambda: self._apply(path, v.get(), kind))
        cb.pack(anchor='w', padx=10, pady=2)
        self.vars[path] = (v, 'bool', None, None)
        return v

    def choice(self, parent, label, path, values, kind='live'):
        f = ttk.Frame(parent, style='Card.TFrame')
        f.pack(fill='x', padx=10, pady=2)
        ttk.Label(f, text=label, width=24).pack(side='left')
        v = tk.StringVar(value=str(self._get(path)))
        cb = ttk.Combobox(f, textvariable=v, values=[str(x) for x in values], state='readonly', width=14)
        cb.pack(side='left')

        def changed(_=None):
            x = v.get()
            old = self._get(path)
            if isinstance(old, int) and not isinstance(old, bool):
                x = int(x)
            self._apply(path, x, kind)
        cb.bind('<<ComboboxSelected>>', changed)
        self.vars[path] = (v, 'str', None, None)
        return v

    def entry(self, parent, label, path, kind='live', width=22):
        f = ttk.Frame(parent, style='Card.TFrame')
        f.pack(fill='x', padx=10, pady=2)
        ttk.Label(f, text=label, width=24).pack(side='left')
        v = tk.StringVar(value='' if self._get(path) is None else str(self._get(path)))
        e = tk.Entry(f, textvariable=v, width=width, bg='#262a31', fg=FG, insertbackground=FG,
                     relief='flat')
        e.pack(side='left')

        def commit(_=None):
            x = v.get().strip()
            old = self._get(path)
            try:
                if isinstance(old, int) and not isinstance(old, bool):
                    x = int(float(x))
                elif isinstance(old, float):
                    x = float(x)
                elif old is None and x == '':
                    x = None
                elif old is None:
                    x = int(x)
            except ValueError:
                return
            self._apply(path, x, kind)
        e.bind('<Return>', commit)
        e.bind('<FocusOut>', commit)
        self.vars[path] = (v, 'str', None, None)
        return v

    def head(self, parent, text, sub=None):
        ttk.Label(parent, text=text, style='Head.TLabel').pack(anchor='w', padx=10, pady=(10, 0))
        if sub:
            ttk.Label(parent, text=sub, style='Dim.TLabel', wraplength=430,
                      justify='left').pack(anchor='w', padx=10, pady=(0, 4))

    def _tab(self, nb, name):
        outer = ttk.Frame(nb, style='Card.TFrame')
        nb.add(outer, text=name)
        canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0, width=470)
        sb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        inner = ttk.Frame(canvas, style='Card.TFrame')
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw', width=460)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        return inner

    # ------------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(fill='x', padx=10, pady=8)
        tk.Label(top, text='EMG SIMULATOR', bg=BG, fg=FG, font=('Helvetica', 16, 'bold')).pack(side='left')
        tk.Label(top, text='  biophysical surface EMG  ->  OSC', bg=BG, fg=DIM).pack(side='left')
        self.btn_pause = ttk.Button(top, text='Pause', command=self.toggle_pause)
        self.btn_pause.pack(side='right', padx=4)
        ttk.Button(top, text='Save settings', command=self.save_cfg).pack(side='right', padx=4)
        ttk.Button(top, text='Load settings', command=self.load_cfg).pack(side='right', padx=4)
        self.preset_var = tk.StringVar(value='choose preset')
        pc = ttk.Combobox(top, textvariable=self.preset_var, values=C.presets(), state='readonly', width=22)
        pc.pack(side='right', padx=8)
        pc.bind('<<ComboboxSelected>>', lambda e: self.load_preset(self.preset_var.get()))
        tk.Label(top, text='Preset', bg=BG, fg=DIM).pack(side='right')

        body = ttk.Frame(self.root)
        body.pack(fill='both', expand=True, padx=10)
        left = ttk.Frame(body)
        left.pack(side='left', fill='y')
        self.nb = ttk.Notebook(left, width=480)
        self.nb.pack(fill='y', expand=True)
        self._tab_perform(self._tab(self.nb, 'PERFORM'))
        self._tab_muscle(self._tab(self.nb, 'MUSCLE'))
        self._tab_noise(self._tab(self.nb, 'NOISE'))
        self._tab_device(self._tab(self.nb, 'DEVICE'))
        self._tab_osc(self._tab(self.nb, 'OSC OUT'))

        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))
        self.scope = tk.Canvas(right, bg='#0e1013', highlightthickness=0)
        self.scope.pack(fill='both', expand=True)
        self.meters = tk.Canvas(right, bg=BG, height=96, highlightthickness=0)
        self.meters.pack(fill='x', pady=(6, 0))

        bottom = ttk.Frame(self.root)
        bottom.pack(fill='x', padx=10, pady=8)
        self.btn_rec = ttk.Button(bottom, text='Record', command=self.toggle_record)
        self.btn_rec.pack(side='left')
        self.status = tk.Label(bottom, text='', bg=BG, fg=DIM, anchor='w')
        self.status.pack(side='left', padx=12, fill='x', expand=True)
        self._build_lanes()

    def _tab_perform(self, t):
        self.head(t, 'EFFORT', 'Hold the SPACE bar to contract (anywhere in this window), or drag. '
                  'Scenarios play by themselves.')
        f = ttk.Frame(t, style='Card.TFrame')
        f.pack(fill='x', padx=10, pady=4)
        self.effort = tk.DoubleVar(value=0.0)
        self.effort_scale = tk.Scale(f, from_=1.0, to=0.0, resolution=0.01, orient='vertical',
                                     variable=self.effort, length=170, width=34, bg=PANEL, fg=FG,
                                     troughcolor='#262a31', highlightthickness=0, sliderlength=26,
                                     command=lambda x: self.runner.set_manual(float(x)))
        self.effort_scale.pack(side='left', padx=(0, 16))
        g = ttk.Frame(f, style='Card.TFrame')
        g.pack(side='left', fill='both', expand=True)
        ttk.Label(g, text='Scenario (channel 1, or all when linked)', style='Dim.TLabel').pack(anchor='w')
        self.choice(g, 'mode', 'scenario.0.mode', MODES)
        self.slider(g, 'level', 'scenario.0.level', 0, 1)
        self.slider(g, 'period (s)', 'scenario.0.period', 0.2, 10, fmt='%.1f')
        self.slider(g, 'duty (pulses)', 'scenario.0.duty', 0.05, 0.95)
        self.head(t, 'MORE SCENARIO SETTINGS')
        self.slider(t, 'tremor frequency (Hz)', 'scenario.0.tremor_hz', 2, 14, fmt='%.1f')
        self.slider(t, 'tremor depth', 'scenario.0.tremor_depth', 0, 0.6)
        self.slider(t, 'improv speed', 'scenario.0.speed', 0.05, 2.0)
        self.slider(t, 'staccato accents per s', 'scenario.0.rate', 0.1, 6, fmt='%.1f')
        self.head(t, 'CHANNELS', 'Linked: every channel follows channel 1, slightly out of step, '
                  'like neighbouring muscles. Unlinked: each channel has its own slider below.')
        self.check(t, 'link all channels', 'link_channels')
        self.chan_frame = ttk.Frame(t, style='Card.TFrame')
        self.chan_frame.pack(fill='x')
        self._build_chan_sliders()
        ttk.Button(t, text='Reset fatigue', command=self.runner.reset_fatigue).pack(anchor='w', padx=10, pady=8)

    def _build_chan_sliders(self):
        for w in self.chan_frame.winfo_children():
            w.destroy()
        self.chan_vars = []
        for c in range(self.runner.sim.ch):
            f = ttk.Frame(self.chan_frame, style='Card.TFrame')
            f.pack(fill='x', padx=10, pady=1)
            tk.Label(f, text='ch %d' % (c + 1), bg=PANEL, fg=LANE_COLS[c % 8], width=6).pack(side='left')
            v = tk.DoubleVar(value=0.0)
            ttk.Scale(f, from_=0, to=1, variable=v,
                      command=lambda x, c=c: self.runner.set_manual(float(x), c)).pack(side='left', fill='x', expand=True)
            self.chan_vars.append(v)

    def _tab_muscle(self, t):
        self.head(t, 'MUSCLES', 'One muscle under each electrode. Fuglevand et al. 1993 motor-unit pool.')
        self.slider(t, 'channels', 'channels', 1, 8, kind='all', fmt='%d')
        self.slider(t, 'motor units per muscle', 'physiology.n_mu', 20, 300, kind='physiology', fmt='%d')
        self.slider(t, 'amplitude at max (mV RMS)', 'physiology.mvc_rms_mV', 0.05, 3.0, kind='physiology')
        self.slider(t, 'MUAP width (ms)', 'physiology.muap_width_ms', 0.8, 4.0, kind='physiology',
                    fmt='%.2f')
        self.slider(t, 'deepest unit (mm)', 'physiology.depth_max', 4, 30, kind='physiology', fmt='%.0f')
        self.head(t, 'FIRING')
        self.slider(t, 'recruitment range', 'physiology.rr', 5, 100, kind='physiology', fmt='%.0f')
        self.slider(t, 'min firing rate (Hz)', 'physiology.mfr', 4, 15, kind='physiology', fmt='%.1f')
        self.slider(t, 'peak rate first unit (Hz)', 'physiology.pfr_first', 15, 60, kind='physiology', fmt='%.0f')
        self.slider(t, 'peak rate last unit (Hz)', 'physiology.pfr_last', 10, 50, kind='physiology', fmt='%.0f')
        self.slider(t, 'firing irregularity (CV)', 'physiology.isi_cv', 0.0, 0.4, kind='physiology')
        self.slider(t, 'common drive', 'physiology.common_drive', 0.0, 0.2)
        self.head(t, 'FATIGUE', 'Sustained effort slows conduction: the spectrum moves down, '
                  'amplitude rises. Rest recovers.')
        self.check(t, 'fatigue on', 'physiology.fatigue_on')
        self.slider(t, 'fatigue rate', 'physiology.fatigue_rate', 0.0, 0.2, fmt='%.3f')
        self.slider(t, 'recovery rate', 'physiology.recovery_rate', 0.0, 0.1, fmt='%.3f')
        self.head(t, 'CROSSTALK')
        self.slider(t, 'leak to neighbours', 'physiology.crosstalk', 0.0, 0.5)

    def _tab_noise(self, t):
        self.head(t, 'ELECTRODE CONTACT', '1 = perfect (gel, clean skin). Lower = more hum, '
                  'more movement artifacts, electrode pops.')
        self.slider(t, 'contact quality', 'noise.contact', 0.0, 1.0)
        self.head(t, 'NOISE')
        self.slider(t, 'baseline noise (uV RMS)', 'noise.baseline_uV', 0, 30, fmt='%.1f')
        self.slider(t, 'baseline wander (uV)', 'noise.wander_uV', 0, 300, fmt='%.0f')
        self.head(t, 'POWER LINE')
        self.check(t, 'hum on', 'noise.hum_on')
        self.choice(t, 'mains (Hz)', 'noise.mains_hz', [50, 60], kind='device')
        self.slider(t, 'hum level (uV)', 'noise.hum_uV', 0, 100, fmt='%.0f')
        self.head(t, 'MOVEMENT')
        self.slider(t, 'artifact size (mV)', 'noise.artifact_mV', 0, 3, fmt='%.2f')
        self.slider(t, 'random artifacts per min', 'noise.artifacts_per_min', 0, 30, fmt='%.1f')
        self.slider(t, 'artifact at movement start', 'noise.onset_artifact_prob', 0, 1)
        self.slider(t, 'electrode pop size (mV)', 'noise.pop_mV', 0, 5, fmt='%.1f')
        self.slider(t, 'pops per min (x looseness)', 'noise.pops_per_min', 0, 5, fmt='%.1f')
        self.head(t, 'HEART', 'ECG crosstalk, for electrodes near the chest or shoulder.')
        self.slider(t, 'ECG level (uV)', 'noise.ecg_uV', 0, 500, fmt='%.0f')
        self.slider(t, 'heart rate (bpm)', 'noise.heart_bpm', 40, 160, fmt='%.0f')

    def _tab_device(self, t):
        self.head(t, 'SAMPLING', 'Internal rate of the simulation. Outputs can be slower (OSC OUT).')
        self.choice(t, 'sample rate (Hz)', 'fs', [1000, 2000, 4000], kind='all')
        self.slider(t, 'engine block (ms)', 'block_ms', 5, 40, fmt='%d')
        self.head(t, 'AMPLIFIER FILTERS', 'Standard surface EMG band: 20-450 Hz (SENIAM, De Luca 2010).')
        self.check(t, 'high-pass on (4th order)', 'device.hp_on', kind='device')
        self.slider(t, 'high-pass (Hz)', 'device.hp_hz', 1, 60, kind='device', fmt='%.0f')
        self.check(t, 'low-pass on', 'device.lp_on', kind='device')
        self.slider(t, 'low-pass (Hz)', 'device.lp_hz', 100, 900, kind='device', fmt='%.0f')
        self.check(t, 'notch at mains', 'device.notch_on', kind='device')
        self.head(t, 'ADC (for "counts" units and the /bio stream)',
                  'Preset emg6_esp32 imitates our ESP32 + EMG module: 12 bit, rest 2160, 1400 counts per mV.')
        self.slider(t, 'bits', 'device.adc_bits', 8, 24, fmt='%d')
        self.slider(t, 'offset at 0 mV (counts)', 'device.adc_offset', 0, 65535, fmt='%d')
        self.slider(t, 'counts per mV', 'device.adc_counts_per_mV', 10, 4000, fmt='%.0f')
        self.slider(t, 'converter noise (LSB)', 'device.adc_noise_lsb', 0, 20, fmt='%.1f')

    def _tab_osc(self, t):
        self.head(t, 'DESTINATION', 'Where the messages go. Each stream may override the port.')
        self.entry(t, 'host', 'osc.host')
        self.entry(t, 'port', 'osc.port')
        self.entry(t, 'control input port', 'osc.in_port', kind='osc_in')
        self.check(t, 'send OSC bundles', 'osc.use_bundles')
        self.slider(t, 'simulate packet loss', 'osc.packet_loss', 0, 0.5)
        S = 'osc.streams.'
        self.head(t, 'raw   one message per sample, all channels',
                  '/emgsim/raw ch1 ch2 ...   units: mV, uV, counts or norm (-1..1). '
                  '"average" = anti-aliased; "point" = like a microcontroller (aliasing).')
        self.check(t, 'on', S + 'raw.on')
        self.entry(t, 'address', S + 'raw.address')
        self.entry(t, 'rate (Hz)', S + 'raw.rate')
        self.choice(t, 'units', S + 'raw.units', ['mV', 'uV', 'counts', 'norm'])
        self.choice(t, 'decimate', S + 'raw.decimate', ['average', 'point'])
        self.entry(t, 'port (empty = default)', S + 'raw.port')
        self.head(t, 'rawblock   every engine block, one message per channel',
                  '/emgsim/rawblock/N  s1 s2 ... at the full sample rate (for audio-rate use).')
        self.check(t, 'on', S + 'rawblock.on')
        self.entry(t, 'address', S + 'rawblock.address')
        self.choice(t, 'units', S + 'rawblock.units', ['mV', 'uV', 'counts', 'norm'])
        self.head(t, 'env   RMS envelope, all channels', '/emgsim/env e1 e2 ...  normalised: 1 = maximal effort.')
        self.check(t, 'on', S + 'env.on')
        self.entry(t, 'address', S + 'env.address')
        self.entry(t, 'rate (Hz)', S + 'env.rate')
        self.entry(t, 'window (ms)', S + 'env.window_ms')
        self.check(t, 'normalise', S + 'env.normalise')
        self.head(t, 'features   per channel', '/emgsim/features/N  rms mav wl zcr tkeo mnf mdf')
        self.check(t, 'on', S + 'features.on')
        self.entry(t, 'address', S + 'features.address')
        self.entry(t, 'rate (Hz)', S + 'features.rate')
        self.entry(t, 'window (ms)', S + 'features.window_ms')
        self.head(t, 'truth   what the simulated body really did',
                  '/emgsim/truth/drive, /force, /fatigue, /active (units), /rate (Hz): for ML training and checks.')
        self.check(t, 'on', S + 'truth.on')
        self.entry(t, 'rate (Hz)', S + 'truth.rate')
        self.head(t, 'bio   EMG6 / ESP32 sensor format',
                  '/bio seq t_us emg_raw ax ay az gx gy gz  (200 Hz, EMG in ADC counts, IMU in g and deg/s). '
                  'Drop-in replacement for our sensor: send to EMG6 port 9100.')
        self.check(t, 'on', S + 'bio.on')
        self.entry(t, 'channel', S + 'bio.channel')
        self.entry(t, 'rate (Hz)', S + 'bio.rate')
        self.entry(t, 'port (empty = default)', S + 'bio.port')
        self.check(t, 'simulate IMU', S + 'bio.imu')
        self.head(t, 'adc   WearPi format', '/adc a1 a2 a3 a4  envelopes 0..1 with adaptive min/max '
                  'and moving average, like Federico Visi\'s wearpi-py.')
        self.check(t, 'on', S + 'adc.on')
        self.entry(t, 'rate (Hz)', S + 'adc.rate')
        self.entry(t, 'smoothing (samples)', S + 'adc.smooth')
        self.entry(t, 'port (empty = default)', S + 'adc.port')
        self.head(t, 'CONTROL INPUT (send to the control port)',
                  '/emgsim/drive f  |  /emgsim/drive ch f  |  /emgsim/mode name  |  /emgsim/mode ch name  |  '
                  '/emgsim/set path value (e.g. noise.contact 0.3)  |  /emgsim/preset name  |  '
                  '/emgsim/fatigue/reset  |  /emgsim/pause 0/1')

    # ------------------------------------------------------------------ scope
    def _build_lanes(self):
        self.lanes = self.runner.sim.ch
        if hasattr(self, 'chan_frame'):
            self._build_chan_sliders()

    def _draw(self):
        cv = self.scope
        W, H = max(cv.winfo_width(), 200), max(cv.winfo_height(), 200)
        cv.delete('all')
        with self.runner.lock:
            sig = self.runner.view['signal'].copy()
            drv = self.runner.view['drive'].copy()
            info = list(self.runner.last_info)
            feats = self.runner.out.last.get('features')
        ch = sig.shape[0]
        lane = H / ch
        mvc = max(self.cfg['physiology']['mvc_rms_mV'], 1e-6)
        n = sig.shape[1]
        step = max(1, n // W)
        xs = np.linspace(0, W, n // step)
        for c in range(ch):
            y0 = lane * (c + 0.5)
            cv.create_line(0, y0, W, y0, fill='#22262d')
            s = sig[c, ::step][:len(xs)]
            # min/max per pixel would be nicer; plain decimation is enough to see the texture
            ys = y0 - np.clip(s / (4.0 * mvc), -1, 1) * lane * 0.45
            pts = np.empty(2 * len(xs))
            pts[0::2], pts[1::2] = xs[:len(ys)], ys
            if len(pts) >= 4:
                cv.create_line(*pts.tolist(), fill=LANE_COLS[c % 8], width=1)
            d = drv[c, ::step][:len(xs)]
            yd = y0 + lane * 0.45 - d * lane * 0.9
            pd = np.empty(2 * len(xs))
            pd[0::2], pd[1::2] = xs[:len(yd)], yd
            if len(pd) >= 4:
                cv.create_line(*pd.tolist(), fill='#ffffff', width=1, dash=(2, 3))
            txt = 'ch %d' % (c + 1)
            if c < len(info):
                i = info[c]
                txt += '   units %3d   rate %4.1f Hz   fatigue %.2f' % (i['active'], i['mean_rate'], i['fatigue'])
            if feats is not None and c < len(feats):
                txt += '   RMS %.3f mV   MDF %3.0f Hz' % (feats[c][0], feats[c][6])
            cv.create_text(8, lane * c + 10, text=txt, anchor='nw', fill=DIM, font=('Menlo', 10))
        cv.create_text(W - 8, 8, text='last 4 s   (dashed = effort)', anchor='ne', fill=DIM, font=('Helvetica', 10))

        m = self.meters
        m.delete('all')
        Wm = max(m.winfo_width(), 200)
        env = self.runner.out.last.get('env')
        if env is None and feats is not None:
            env = np.array([f[0] for f in feats]) / mvc
        bw = Wm / max(ch, 1)
        for c in range(ch):
            v = float(env[c]) if env is not None and c < len(env) else 0.0
            x0 = c * bw + 6
            m.create_rectangle(x0, 20, x0 + bw - 12, 80, outline='#2a2f37')
            m.create_rectangle(x0, 80 - 60 * min(v, 1.0), x0 + bw - 12, 80, fill=LANE_COLS[c % 8], outline='')
            m.create_text(x0, 4, text='ch %d  %.2f' % (c + 1, v), anchor='nw', fill=DIM, font=('Helvetica', 10))

    def _flag_remote(self):
        self._remote_flag = True

    def _tick(self):
        try:
            if self._remote_flag:
                self._remote_flag = False
                self.refresh_widgets()
            self._draw()
            r = self.runner
            o = self.cfg['osc']
            on = [k for k, s in o['streams'].items() if s.get('on')]
            st = 'OSC -> %s:%s   %s   %.0f msg/s   CPU %.0f ms per s' % (
                o['host'], o['port'], ', '.join(on) or 'no streams', r.out.msgs_per_s, r.load_ms)
            st += '   |   control in :%s' % o['in_port'] if r.rx else ''
            if r.rx_error:
                st += '   |   ' + r.rx_error
            if r.recording is not None:
                st = 'RECORDING   ' + st
            self.status.configure(text=st)
        finally:
            self._after = self.root.after(40, self._tick)

    # ------------------------------------------------------------------ actions
    def _space_on(self, e):
        if isinstance(self.root.focus_get(), tk.Entry):
            return
        self.space_down = True
        lv = float(self.cfg['scenario'][0].get('level', 0.6))
        self.effort.set(lv)
        self.runner.set_manual(lv)

    def _space_off(self, e):
        self.space_down = False
        # key repeat sends release+press pairs: release only if no press follows quickly
        self.root.after(60, self._space_check)

    def _space_check(self):
        if not self.space_down:
            self.effort.set(0.0)
            self.runner.set_manual(0.0)

    def toggle_pause(self):
        self.runner.paused = not self.runner.paused
        self.btn_pause.configure(text='Resume' if self.runner.paused else 'Pause')

    def toggle_record(self):
        if self.runner.recording is None:
            self.runner.record_start()
            self.btn_rec.configure(text='Stop + save')
        else:
            path = filedialog.asksaveasfilename(defaultextension='.csv',
                                                filetypes=[('CSV', '*.csv'), ('WAV', '*.wav')])
            if path:
                secs = self.runner.record_stop(path)
                messagebox.showinfo('Saved', '%.1f s saved to\n%s' % (secs, path))
            else:
                self.runner.recording = None
            self.btn_rec.configure(text='Record')

    def refresh_widgets(self):
        for path, (v, kind, lab, fmt) in self.vars.items():
            try:
                x = self._get(path)
            except (KeyError, IndexError, TypeError):
                continue
            if kind == 'num':
                v.set(float(x))
                lab.configure(text=fmt % x)
            elif kind == 'bool':
                v.set(bool(x))
            else:
                v.set('' if x is None else str(x))
        self._build_lanes()

    def load_preset(self, name):
        new = C.merge(C.DEFAULTS, C.preset(name))
        self._replace_cfg(new)

    def _replace_cfg(self, new):
        with self.runner.lock:
            self.cfg.clear()
            self.cfg.update(new)
            self.runner.sim.cfg = self.cfg
        self.runner.rebuild('all')
        self.runner.rebuild('osc_in')
        self.refresh_widgets()

    def save_cfg(self):
        path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
        if path:
            C.save(self.cfg, path)

    def load_cfg(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if path:
            try:
                self._replace_cfg(C.load(path))
            except (OSError, ValueError, json.JSONDecodeError) as e:
                messagebox.showerror('Could not load', str(e))

    def quit(self):
        try:
            self.root.after_cancel(self._after)
        except (tk.TclError, AttributeError):
            pass
        self.runner.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(cfg):
    App(cfg).run()
