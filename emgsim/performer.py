"""PERFORMER: a simulated person who contracts at random, like a real player.

Rests of varying length alternate with gestures of different kinds:
  normal   an ordinary contraction (0.6-2.5 s) with a slow wobble
  accent   a short sharp squeeze (0.1-0.35 s)
  strong   a near-maximal burst (0.5-2 s) with a fine 8-12 Hz tremble
  hold     a long sustained contraction (3-8 s): fatigue shows up here
  ramp     a slow build-up and release (1-3 s)
  flurry   3-6 quick squeezes in a row
  tremble  a shaky, unsteady contraction (4-7 Hz)
activity  0..1  how busy the player is (short rests vs long rests)
intensity 0..1  how hard: more strong bursts, higher peaks
level     0..1  overall ceiling of the effort
Pure Python (no NumPy) so the same text also runs inside TouchDesigner.
"""
import math
import random

KINDS = (('normal', 0.32), ('accent', 0.18), ('strong', 0.12), ('hold', 0.09),
         ('ramp', 0.12), ('flurry', 0.10), ('tremble', 0.07))


class Performer:
    def __init__(self, seed=0):
        self.rng = random.Random(seed)
        self.kind = 'gesture'          # so the first call starts with a rest
        self.t0 = 0.0
        self.t_end = 0.0
        self.p = {}
        self.started = False

    def _choose(self, intensity):
        w = []
        for k, base in KINDS:
            if k == 'strong':
                base *= 0.25 + 2.5 * intensity
            elif k == 'normal':
                base *= 1.3 - 0.6 * intensity
            w.append(base)
        x = self.rng.random() * sum(w)
        for (k, _), wi in zip(KINDS, w):
            x -= wi
            if x <= 0:
                return k
        return 'normal'

    def _next(self, t, activity, intensity):
        r = self.rng
        if self.kind != 'rest':
            mean = 0.35 + 3.2 * (1.0 - activity)
            dur = min(r.lognormvariate(math.log(mean), 0.7), 12.0)
            self.kind, self.t0, self.t_end = 'rest', t, t + dur
            self.p = {'tone': r.uniform(0.0, 0.035)}         # muscles are rarely fully silent
            return
        k = self._choose(intensity)
        up = 0.75 + 0.5 * intensity
        p = {'wob_f': r.uniform(0.4, 1.8), 'wob_ph': r.uniform(0, 6.28), 'wob': r.uniform(0.04, 0.12)}
        if k == 'normal':
            p.update(dur=r.uniform(0.6, 2.5), peak=r.uniform(0.22, 0.55) * up, att=r.uniform(0.08, 0.25),
                     rel=r.uniform(0.1, 0.35))
        elif k == 'accent':
            p.update(dur=r.uniform(0.1, 0.35), peak=r.uniform(0.4, 0.85) * up)
        elif k == 'strong':
            p.update(dur=r.uniform(0.5, 2.0), peak=r.uniform(0.82, 1.0), att=r.uniform(0.04, 0.1),
                     rel=r.uniform(0.08, 0.2), trem_f=r.uniform(8.0, 12.0), trem=0.06)
        elif k == 'hold':
            p.update(dur=r.uniform(3.0, 8.0), peak=r.uniform(0.3, 0.6) * up, att=r.uniform(0.2, 0.5),
                     rel=r.uniform(0.2, 0.5), wob=0.04)
        elif k == 'ramp':
            p.update(dur=r.uniform(1.0, 3.0), peak=r.uniform(0.5, 0.95) * up, top=r.uniform(0.6, 0.85))
        elif k == 'flurry':
            n = r.randint(3, 6)
            per = r.uniform(0.16, 0.35)
            p.update(dur=n * per, per=per, peak=r.uniform(0.4, 0.8) * up, duty=r.uniform(0.35, 0.6))
        else:  # tremble
            p.update(dur=r.uniform(1.0, 2.5), peak=r.uniform(0.3, 0.5) * up, att=0.15, rel=0.2,
                     trem_f=r.uniform(4.0, 7.0), trem=0.3)
        p['peak'] = min(1.0, p['peak'])
        self.kind, self.t0, self.t_end, self.p = k, t, t + p['dur'], p

    def value(self, t, activity=0.5, intensity=0.5, level=1.0):
        """Target effort at time t (seconds). Call with increasing t."""
        if not self.started:
            self.started = True
            self.kind = 'gesture'
            self._next(t, activity, intensity)
        while t >= self.t_end:
            self._next(self.t_end, activity, intensity)
        p, k, u = self.p, self.kind, t - self.t0
        if k == 'rest':
            return p['tone'] * level
        d = p['dur']
        wob = 1.0 + p['wob'] * math.sin(2 * math.pi * p['wob_f'] * u + p['wob_ph'])
        if k == 'accent':
            e = math.sin(math.pi * min(u / d, 1.0)) ** 0.6
        elif k == 'ramp':
            x = u / d
            e = x / p['top'] if x < p['top'] else max(0.0, 1.0 - (x - p['top']) / (1.0 - p['top']))
        elif k == 'flurry':
            x = (u / p['per']) % 1.0
            e = math.sin(math.pi * min(x / p['duty'], 1.0)) if x < p['duty'] else 0.0
        else:
            att, rel = p['att'], p['rel']
            e = min(1.0, u / att, max(0.0, (d - u) / rel))
            if 'trem' in p:
                e *= 1.0 + p['trem'] * math.sin(2 * math.pi * p['trem_f'] * u)
        return max(0.0, min(1.0, e * p['peak'] * wob * level))
