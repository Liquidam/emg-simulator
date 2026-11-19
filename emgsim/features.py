"""Standard surface-EMG features over a sliding window (per channel).

RMS   root mean square (amplitude)            MAV   mean absolute value
WL    waveform length (sum of |dx|)           ZCR   zero crossings per second (with a dead band)
TKEO  mean Teager-Kaiser energy (onsets)      MNF   mean frequency (Hz)    MDF  median frequency (Hz)
See Visi & Tanaka (2021) 'Interactive Machine Learning of Musical Gesture' for their musical use.
"""
import numpy as np

NAMES = ['rms', 'mav', 'wl', 'zcr', 'tkeo', 'mnf', 'mdf']


def compute(x, fs, zc_threshold=0.01):
    """x: (channels, n) in mV, already band-limited. Returns (channels, 7)."""
    ch, n = x.shape
    out = np.zeros((ch, len(NAMES)))
    if n < 8:
        return out
    xc = x - x.mean(axis=1, keepdims=True)
    out[:, 0] = np.sqrt(np.mean(xc * xc, axis=1))
    out[:, 1] = np.mean(np.abs(xc), axis=1)
    out[:, 2] = np.sum(np.abs(np.diff(xc, axis=1)), axis=1) / (n / fs)
    s = np.sign(xc)
    big = np.abs(xc[:, 1:] - xc[:, :-1]) > zc_threshold
    out[:, 3] = np.sum((s[:, 1:] * s[:, :-1] < 0) & big, axis=1) / (n / fs)
    tk = xc[:, 1:-1] ** 2 - xc[:, :-2] * xc[:, 2:]
    out[:, 4] = np.mean(tk, axis=1)
    X = np.abs(np.fft.rfft(xc * np.hanning(n)[None, :], axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1.0 / fs)
    tot = X.sum(axis=1) + 1e-20
    out[:, 5] = (X * f[None, :]).sum(axis=1) / tot
    c = np.cumsum(X, axis=1)
    out[:, 6] = f[np.argmax(c >= (c[:, -1:] / 2.0), axis=1)]
    return out
