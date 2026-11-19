"""The muscle: a motor-unit pool that fires, and the surface potentials it makes.

Model (one muscle under one bipolar surface electrode)
------------------------------------------------------
1. Recruitment and rate coding - Fuglevand, Winter & Patla (1993),
   J Neurophysiol 70(6):2470-2488:
   * n motor units (default 120). Recruitment thresholds grow exponentially:
     RTE_i = exp(ln(RR) * i / n), RR = 30 (range of thresholds).
     Small units are recruited first (Henneman's size principle).
   * Firing rate rises linearly with excitation above threshold:
     rate_i = MFR + g * (E - RTE_i), capped at the unit's peak rate PFR_i.
     MFR = 8 Hz, g = 1 Hz per excitation unit, PFR from 35 Hz (first unit)
     down to 25 Hz (last unit) - the "low-threshold units fire faster" scheme.
   * Discharge times are a renewal process: ISI = mean * (1 + CV * N(0,1)),
     CV = 0.2.
   * Maximum excitation is set so the last unit reaches its peak rate.
   * Common drive (De Luca): all units share one slow excitation
     fluctuation, which gives the natural unsteadiness of force.
2. Motor-unit action potentials (MUAPs) at the skin:
   * Shape: a mix of the first and second Hermite-Rodriguez functions
     (bi- and triphasic), a standard analytic MUAP model.
   * Width grows with depth (tissue acts as a low-pass filter) and with
     1 / conduction velocity; larger units conduct faster (3 to 5.5 m/s).
   * Amplitude grows with unit size and falls off exponentially with depth.
3. Fatigue: during effort a fatigue state rises and recovers at rest.
   Conduction velocity drops (up to 45 %), so every MUAP widens and the
   spectrum compresses towards lower frequencies (the classic median
   frequency decline); to hold the same effort the excitation rises.
4. Force (ground truth, approximate): each unit's twitch size P_i spans
   RP = 100 (exponential, like the thresholds); its force grows with firing
   rate as a saturating function (Fuglevand's normalised gain).

Units: the muscle returns the surface signal in millivolts. Its scale is
calibrated once so that the RMS at maximal effort equals `mvc_rms_mV`.
"""
import math
import numpy as np


class MotorUnitPool:
    def __init__(self, fs, n_mu=120, rr=30.0, rp=100.0, mfr=8.0, pfr_first=35.0,
                 pfr_last=25.0, gain=1.0, isi_cv=0.2, cv_min=3.0, cv_max=5.5,
                 depth_min=3.0, depth_max=15.0, muap_width_ms=2.0, seed=0):
        self.fs = float(fs)
        self.n = n = int(n_mu)
        self.rng = np.random.default_rng(seed)
        i = np.arange(1, n + 1)
        self.rte = np.exp(math.log(rr) * i / n)
        span = max(self.rte[-1] - self.rte[0], 1e-9)
        self.pfr = pfr_first - (pfr_first - pfr_last) * (self.rte - self.rte[0]) / span
        self.mfr, self.gain, self.isi_cv = mfr, gain, isi_cv
        self.emax = self.rte[-1] + (self.pfr[-1] - mfr) / gain
        self.size = np.exp(math.log(rp) * i / n)                 # twitch size 1..RP
        size01 = (self.size - 1.0) / max(rp - 1.0, 1e-9)
        self.cv0 = cv_min + (cv_max - cv_min) * size01 + self.rng.normal(0, 0.15, n)
        self.cv0 = np.clip(self.cv0, cv_min * 0.9, cv_max * 1.1)
        self.depth = self.rng.uniform(depth_min, depth_max, n)   # mm under the skin
        self.mix = self.rng.uniform(0.0, 1.0, n)                 # bi- vs triphasic
        self.sign = np.where(self.rng.random(n) < 0.5, -1.0, 1.0)
        self.amp = np.sqrt(self.size) * np.exp(-(self.depth - depth_min) / 6.0)
        self.width_ms = muap_width_ms
        # twitch contraction time (ms): small units slow, large units fast
        self.tc = 90.0 * (1.0 / self.size) ** (1.0 / math.log(rp, 3.0))
        self.next_t = np.full(n, np.inf)
        self.rate = np.zeros(n)
        self._templates = None
        self._tmpl_scale = None
        self.half = int(round(0.015 * self.fs))                  # +-15 ms window

    # -- MUAP templates ------------------------------------------------------------------
    def templates(self, cv_scale):
        """(n, L) MUAP shapes for the current conduction-velocity scale."""
        if self._templates is not None and abs(cv_scale - self._tmpl_scale) < 0.005:
            return self._templates
        t = np.arange(-self.half, self.half + 1) / self.fs * 1000.0   # ms
        cv = self.cv0 * cv_scale
        lam = self.width_ms * (4.0 / cv) * (1.0 + (self.depth - 3.0) / 12.0)   # ms
        u = t[None, :] / lam[:, None]
        g = np.exp(-u * u)
        hr1 = u * g * 2.3316                                     # peak of u*exp(-u^2) = 0.4289
        hr2 = (1.0 - 2.0 * u * u) * g
        shape = (1.0 - self.mix[:, None]) * hr1 + self.mix[:, None] * hr2
        # fatigue: a slower wave of the same charge is wider and only a little lower,
        # so the energy (RMS) rises slightly - as measured in sustained contractions
        amp = self.amp * (1.0 / cv_scale) ** 0.5
        self._templates = shape * (self.sign * amp)[:, None]
        self._tmpl_scale = cv_scale
        return self._templates

    # -- firing ------------------------------------------------------------------------------
    def rates(self, e):
        r = self.mfr + self.gain * (e - self.rte)
        r = np.minimum(r, self.pfr)
        r[e < self.rte] = 0.0
        return r

    def fire(self, t0, e_block, rate_scale=1.0):
        """Spike times inside [t0, t0 + len/fs). e_block: excitation per sample.
        Returns a list of (unit index, sample offset)."""
        n_s = len(e_block)
        fs = self.fs
        t_end = t0 + n_s / fs
        spikes = []
        e_now = e_block[0]
        active = e_now >= self.rte
        # newly recruited units start at a random phase of their first interval
        new = active & ~np.isfinite(self.next_t)
        if new.any():
            r = self.rates(e_now)[new] * rate_scale
            self.next_t[new] = t0 + self.rng.uniform(0.0, 1.0, new.sum()) / np.maximum(r, 1e-3)
        self.next_t[~active] = np.inf
        idx = np.nonzero(np.isfinite(self.next_t) & (self.next_t < t_end))[0]
        for i in idx:
            t = self.next_t[i]
            while t < t_end:
                k = int((t - t0) * fs)
                k = min(max(k, 0), n_s - 1)
                e = e_block[k]
                if e < self.rte[i]:
                    t = np.inf
                    break
                spikes.append((i, k))
                r = min(self.mfr + self.gain * (e - self.rte[i]), self.pfr[i]) * rate_scale
                mean = 1.0 / max(r, 1e-3)
                isi = mean * (1.0 + self.isi_cv * self.rng.standard_normal())
                t = t + max(isi, 0.35 * mean)
            self.next_t[i] = t
        self.rate = self.rates(e_block[-1]) * rate_scale
        return spikes

    def force(self):
        """Normalised force estimate 0..~1 from the current firing rates."""
        r = self.rate
        x = r * self.tc / 1000.0                       # rate x contraction time
        f = self.size * (1.0 - np.exp(-2.0 * x ** 3)) / 0.865
        return float(f.sum() / self.size.sum())


class Muscle:
    """One muscle: pool + fatigue + overlap-add synthesis, output in mV."""

    def __init__(self, fs, mvc_rms_mV=0.5, seed=0, **pool_kw):
        self.fs = fs
        self.pool_kw = dict(pool_kw)
        self.seed = seed
        self.mvc_rms_mV = mvc_rms_mV
        self.fatigue = 0.0
        self._build()

    def _build(self):
        self.pool = MotorUnitPool(self.fs, seed=self.seed, **self.pool_kw)
        self.tail = np.zeros(2 * self.pool.half + 1)
        self.k = 1.0
        self.k = self.mvc_rms_mV / max(self._calibration_rms(), 1e-12)

    def _calibration_rms(self):
        """RMS (in model units) of 1 s at maximal excitation, on a throw-away copy."""
        p = MotorUnitPool(self.fs, seed=self.seed + 7919, **self.pool_kw)
        n = int(self.fs)
        tmpl = p.templates(1.0)
        L = tmpl.shape[1]
        out = np.zeros(n + L)
        e = np.full(n, p.emax)
        for i, k in p.fire(0.0, e):
            out[k:k + L] += tmpl[i]
        x = out[L:n]
        return float(np.sqrt(np.mean(x * x)))

    def reconfigure(self, **pool_kw):
        self.pool_kw.update(pool_kw)
        self._build()

    def render(self, t0, drive, fatigue_on=True, fatigue_rate=0.02, recovery_rate=0.01,
               common_drive=0.02, cd_noise=None):
        """drive: array 0..1 per sample (the effort). Returns (signal mV, info)."""
        p = self.pool
        n_s = len(drive)
        dt = n_s / self.fs
        d_mean = float(np.mean(drive))
        if fatigue_on:
            # effort above ~15 % tires the muscle, rest recovers it
            load = max(0.0, d_mean - 0.15) / 0.85
            self.fatigue += dt * (fatigue_rate * load ** 1.5 - recovery_rate * self.fatigue * (1.0 - d_mean))
            self.fatigue = min(max(self.fatigue, 0.0), 1.0)
        cv_scale = 1.0 - 0.45 * self.fatigue
        rate_scale = 1.0 - 0.15 * self.fatigue
        compensation = 1.0 + 0.45 * self.fatigue           # more drive to hold the effort
        e = np.clip(drive, 0.0, 1.0) * p.emax * compensation
        if cd_noise is not None:
            e = e * (1.0 + common_drive * cd_noise)
        e = np.clip(e, 0.0, p.emax * 1.5)
        spikes = p.fire(t0, e, rate_scale)
        tmpl = p.templates(cv_scale)
        L = tmpl.shape[1]
        buf = np.zeros(n_s + L)
        buf[:L] += self.tail
        # a spike at sample k starts its waveform there; the peak follows `half`
        # samples later (a fixed 15 ms delay), so no part of a MUAP is ever lost
        for i, k in spikes:
            buf[k:k + L] += tmpl[i]
        out = buf[:n_s].copy()
        self.tail = buf[n_s:n_s + L].copy()
        out_mV = out * self.k
        info = dict(spikes=len(spikes), active=int((p.rate > 0).sum()),
                    mean_rate=float(p.rate[p.rate > 0].mean()) if (p.rate > 0).any() else 0.0,
                    force=p.force(), fatigue=self.fatigue, cv_scale=cv_scale)
        return out_mV, info
