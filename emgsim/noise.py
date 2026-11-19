"""Everything that is not muscle: the real-world contamination of surface EMG.

Sources (all optional, all adjustable, all in millivolts at the electrode):
* baseline noise      white, amplifier thermal + electrode-skin electrochemical noise.
                       Good electrodes about 1-5 uV RMS (De Luca et al. 2010, J Biomech).
* powerline hum        50 or 60 Hz plus odd harmonics. Grows quickly when the
                       electrode contact gets worse (impedance imbalance). Its level
                       and phase drift slowly, as in a real room.
* baseline wander      slow drift (< 1 Hz): sweat, temperature, half-cell potential.
* motion artifacts     low-frequency bumps (mostly 0-20 Hz, De Luca 2010) from cable and
                       electrode movement; random, and also at the start of movements.
* electrode pops       a sudden step that decays (a loose electrode losing contact).
* ECG                  heartbeat crosstalk, for electrodes near the trunk or shoulder.
`contact` (0 = loose, 1 = perfect) scales hum, artifacts and pops together.
"""
import math
import numpy as np
from .dsp import OnePole


class NoiseBank:
    def __init__(self, fs, channels, seed=0):
        self.fs = fs
        self.ch = channels
        self.rng = np.random.default_rng(seed + 101)
        self.t = 0.0
        self.hum_phase = self.rng.uniform(0, 2 * math.pi, channels)
        self.hum_level = np.ones(channels)
        self.wander_lp = OnePole(0.3, fs, channels)
        self.wander_state = np.zeros(channels)
        self.events = []          # [channel, start sample (global), kind, params]
        self.n = 0                # global sample counter
        self.hr_phase = 0.0

    # ------------------------------------------------------------------------------------
    def _ecg_wave(self, ph):
        """One heartbeat on phase 0..1: P, QRS, T as Gaussians (mV, R peak = 1)."""
        def g(c, w, a):
            return a * np.exp(-((ph - c) / w) ** 2)
        return g(0.20, 0.025, 0.12) + g(0.37, 0.008, -0.15) + g(0.40, 0.010, 1.0) + \
            g(0.43, 0.008, -0.25) + g(0.68, 0.045, 0.30)

    def render(self, n_s, cfg, onsets):
        """cfg: the 'noise' section of the config. onsets: channels that just began moving.
        Returns (channels, n_s) in mV."""
        fs, ch, rng = self.fs, self.ch, self.rng
        out = np.zeros((ch, n_s))
        contact = float(np.clip(cfg.get('contact', 0.9), 0.0, 1.0))
        bad = 1.0 - contact
        tt = (self.n + np.arange(n_s)) / fs

        # baseline noise
        b = cfg.get('baseline_uV', 3.0) * 1e-3
        if b > 0:
            out += rng.standard_normal((ch, n_s)) * b

        # powerline hum
        hum = cfg.get('hum_uV', 8.0) * 1e-3 * (0.3 + 6.0 * bad * bad)
        if hum > 0 and cfg.get('hum_on', True):
            f0 = 60.0 if int(cfg.get('mains_hz', 50)) == 60 else 50.0
            self.hum_level += (rng.standard_normal(ch) * 0.02 - 0.01 * (self.hum_level - 1.0))
            self.hum_level = np.clip(self.hum_level, 0.5, 1.6)
            self.hum_phase += rng.standard_normal(ch) * 0.002
            w = 2 * math.pi * f0 * tt
            for h, a in ((1, 1.0), (3, 0.25), (5, 0.08)):
                out += (hum * a * self.hum_level)[:, None] * np.sin(h * (w[None, :] + self.hum_phase[:, None]))

        # baseline wander
        wa = cfg.get('wander_uV', 20.0) * 1e-3
        if wa > 0:
            x = rng.standard_normal((ch, n_s)) * wa * 6.0
            out += self.wander_lp.process(x)

        # ECG
        ecg = cfg.get('ecg_uV', 0.0) * 1e-3
        if ecg > 0:
            bpm = cfg.get('heart_bpm', 70.0)
            ph = (self.hr_phase + np.arange(n_s) * bpm / 60.0 / fs) % 1.0
            self.hr_phase = (self.hr_phase + n_s * bpm / 60.0 / fs) % 1.0
            wave = self._ecg_wave(ph)
            gains = np.linspace(1.0, 0.4, ch)          # channel 1 nearest to the heart
            out += ecg * gains[:, None] * wave[None, :]

        # new events: motion artifacts (random + at movement onsets) and pops
        art = cfg.get('artifact_mV', 0.3) * (0.3 + 1.5 * bad)
        per_s = n_s / fs
        if art > 0:
            rate = cfg.get('artifacts_per_min', 2.0) / 60.0
            for c in range(ch):
                if rng.random() < rate * per_s:
                    self._add_artifact(c, n_s, art)
                if c in onsets and rng.random() < cfg.get('onset_artifact_prob', 0.5):
                    self._add_artifact(c, n_s, art * 0.7)
        pop = cfg.get('pop_mV', 1.5)
        pops_per_min = cfg.get('pops_per_min', 0.2) * (bad * 5.0)
        if pop > 0 and pops_per_min > 0:
            for c in range(ch):
                if rng.random() < pops_per_min / 60.0 * per_s:
                    self.events.append([c, self.n + rng.integers(0, n_s), 'pop',
                                        dict(a=pop * rng.uniform(-1, 1), tau=rng.uniform(0.1, 0.4))])

        # play all live events
        keep = []
        for ev in self.events:
            c, s0, kind, p = ev
            k = (self.n + np.arange(n_s)) - s0
            m = k >= 0
            if not m.any():
                keep.append(ev)
                continue
            t = np.where(m, k / fs, 0.0)
            if kind == 'art':
                env = np.where(m, np.exp(-t / p['tau']) * (1 - np.exp(-t / 0.02)), 0.0)
                out[c] += p['a'] * env * np.sin(2 * math.pi * p['f'] * t + p['ph'])
                done = t[-1] > 6 * p['tau']
            else:
                out[c] += np.where(m, p['a'] * np.exp(-t / p['tau']), 0.0)
                done = t[-1] > 8 * p['tau']
            if not done:
                keep.append(ev)
        self.events = keep
        self.n += n_s
        return out

    def _add_artifact(self, c, n_s, amp):
        r = self.rng
        self.events.append([c, self.n + r.integers(0, n_s), 'art',
                            dict(a=amp * r.uniform(0.3, 1.0) * (1 if r.random() < 0.5 else -1),
                                 f=r.uniform(1.0, 12.0), tau=r.uniform(0.08, 0.35),
                                 ph=r.uniform(0, 2 * math.pi))])
