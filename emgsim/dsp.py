"""Small stateful filters (no SciPy needed).

All filters process blocks and keep their state between blocks, so a stream
cut into blocks gives exactly the same result as one long array.
"""
import math
import numpy as np


class Biquad:
    """Second-order IIR section, transposed direct form II, one or more channels."""

    def __init__(self, b, a, channels=1):
        a0 = a[0]
        self.b = [b[0] / a0, b[1] / a0, b[2] / a0]
        self.a = [1.0, a[1] / a0, a[2] / a0]
        self.z1 = np.zeros(channels)
        self.z2 = np.zeros(channels)

    def process(self, x):
        """x: array (channels, n). Returns filtered copy."""
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        y = np.empty_like(x)
        for c in range(x.shape[0]):          # plain floats are far faster than tiny arrays
            z1, z2 = float(self.z1[c]), float(self.z2[c])
            row = []
            ap = row.append
            for xi in x[c].tolist():
                yi = b0 * xi + z1
                z1 = b1 * xi - a1 * yi + z2
                z2 = b2 * xi - a2 * yi
                ap(yi)
            y[c] = row
            self.z1[c], self.z2[c] = z1, z2
        return y


def _rbj(kind, fc, fs, q=1.0 / math.sqrt(2.0)):
    """Coefficients from the RBJ audio EQ cookbook. q=0.707 = Butterworth."""
    fc = min(max(fc, 1e-3), 0.49 * fs)
    w0 = 2.0 * math.pi * fc / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * q)
    if kind == 'lp':
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
    elif kind == 'hp':
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
    elif kind == 'notch':
        b = [1.0, -2 * cw, 1.0]
    else:
        raise ValueError(kind)
    a = [1 + alpha, -2 * cw, 1 - alpha]
    return b, a


def butter_lp(fc, fs, channels=1):
    return Biquad(*_rbj('lp', fc, fs), channels=channels)


def butter_hp(fc, fs, channels=1):
    return Biquad(*_rbj('hp', fc, fs), channels=channels)


def notch(fc, fs, q=10.0, channels=1):
    return Biquad(*_rbj('notch', fc, fs, q), channels=channels)


class OnePole:
    """y += k (x - y): a cheap low-pass, k from a cut-off frequency."""

    def __init__(self, fc, fs, channels=1):
        self.k = 1.0 - math.exp(-2.0 * math.pi * fc / fs)
        self.y = np.zeros(channels)

    def process(self, x):
        k = self.k
        y = np.empty_like(x)
        for c in range(x.shape[0]):
            s = float(self.y[c])
            row = []
            ap = row.append
            for xi in x[c].tolist():
                s += k * (xi - s)
                ap(s)
            y[c] = row
            self.y[c] = s
        return y
