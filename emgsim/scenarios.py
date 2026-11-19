"""Effort scenarios: what the 'performer' does, as a drive 0..1 per sample.

Modes
  performer a simulated person contracting at random: rests, normal squeezes, accents,
            strong bursts, holds, ramps, flurries (see performer.py). activity, intensity
  manual    the slider / space bar / OSC value
  osc       only what arrives on OSC (/emgsim/drive)
  pulses    contract - relax - contract ... (level, period, duty, ramp)
  ramp      slow rise to `level` and back (period)
  hold      a sustained contraction at `level` (shows fatigue)
  tremor    `level` with a tremor of `tremor_hz` and `tremor_depth`
  improv    a wandering, musical improvisation (speed)
  staccato  short sharp accents at random times (rate per second)
  mvc_test  rest 3 s, maximum 3 s, rest 3 s, 50 % 3 s ... (calibration routine)
The slider / space bar always adds on top (the larger of the two wins).
Every mode passes through a human-like slew limit (no instant jumps).
"""
import math
import numpy as np

from .performer import Performer

MODES = ['performer', 'manual', 'osc', 'pulses', 'ramp', 'hold', 'tremor', 'improv', 'staccato', 'mvc_test']


class Scenario:
    def __init__(self, fs, seed=0):
        self.fs = fs
        self.rng = np.random.default_rng(seed + 303)
        self.y = 0.0
        self.walk = 0.0
        self.walk_v = 0.0
        self.stacc = []
        self.perf = Performer(seed * 7 + 11)

    def render(self, t0, n_s, p, manual, osc_value):
        fs = self.fs
        t = t0 + np.arange(n_s) / fs
        mode = p.get('mode', 'manual')
        lv = float(p.get('level', 0.5))
        per = max(float(p.get('period', 2.0)), 0.05)
        ph = float(p.get('phase', 0.0))
        if mode == 'performer':
            act = float(p.get('activity', 0.5))
            inten = float(p.get('intensity', 0.5))
            pf = self.perf
            target = np.array([pf.value(x, act, inten, lv) for x in t.tolist()])
        elif mode == 'manual':
            target = np.full(n_s, manual)
        elif mode == 'osc':
            target = np.full(n_s, osc_value)
        elif mode == 'pulses':
            duty = float(p.get('duty', 0.5))
            x = ((t / per) + ph) % 1.0
            target = np.where(x < duty, lv, 0.0)
        elif mode == 'ramp':
            x = ((t / per) + ph) % 1.0
            target = lv * (1.0 - np.abs(2.0 * x - 1.0))
        elif mode == 'hold':
            target = np.full(n_s, lv)
        elif mode == 'tremor':
            hz = float(p.get('tremor_hz', 9.0))
            dp = float(p.get('tremor_depth', 0.15))
            target = lv * (1.0 + dp * np.sin(2 * math.pi * hz * t))
        elif mode == 'improv':
            sp = float(p.get('speed', 0.5))
            target = np.empty(n_s)
            for i in range(n_s):
                self.walk_v += (self.rng.standard_normal() * 3.0 * sp - 2.0 * self.walk_v) / fs * 4
                self.walk += self.walk_v / fs * 2.0
                self.walk -= 0.3 * (self.walk - 0.0) / fs
                target[i] = lv * 0.5 * (1.0 + math.tanh(self.walk))
        elif mode == 'staccato':
            rate = float(p.get('rate', 1.5))
            if self.rng.random() < rate * n_s / fs:
                self.stacc.append([t[0] + self.rng.uniform(0, n_s / fs), lv * self.rng.uniform(0.6, 1.0)])
            target = np.zeros(n_s)
            keep = []
            for s0, a in self.stacc:
                d = t - s0
                target += np.where(d >= 0, a * np.exp(-np.maximum(d, 0) / 0.12) * (1 - np.exp(-np.maximum(d, 0) / 0.015)), 0.0)
                if d[-1] < 1.0:
                    keep.append([s0, a])
            self.stacc = keep
        elif mode == 'mvc_test':
            steps = [0, 1.0, 0, 0.5, 0, 0.25, 0, 0.75]
            k = ((t / 3.0).astype(int)) % len(steps)
            target = np.array(steps)[k]
        else:
            target = np.full(n_s, manual)
        if mode != 'manual':
            target = np.maximum(target, manual)          # the space bar always works
        target = np.clip(target, 0.0, 1.0)
        # slew: rise ~60 ms, fall ~90 ms (muscles relax more slowly than they contract)
        up = 1.0 - math.exp(-1.0 / (0.060 * fs))
        dn = 1.0 - math.exp(-1.0 / (0.090 * fs))
        if mode in ('tremor', 'staccato'):
            up = 1.0 - math.exp(-1.0 / (0.008 * fs))
            dn = up
        out = []
        y = self.y
        for x in target.tolist():
            y += (up if x > y else dn) * (x - y)
            out.append(y)
        self.y = y
        return np.array(out)
