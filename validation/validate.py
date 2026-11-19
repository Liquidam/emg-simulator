"""Checks the simulator against known properties of real surface EMG.

Run:  python validation/validate.py      (prints a report, writes validation/REPORT.txt)
Every check prints the measured number, the expected range, and PASS / CHECK.
Expected ranges come from the surface-EMG literature (sources in README.md).
"""
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from emgsim import config as C                    # noqa: E402
from emgsim.engine import Simulator              # noqa: E402
from emgsim import features as F                  # noqa: E402

LINES = []


def say(s=''):
    print(s)
    LINES.append(s)


def check(name, value, lo, hi, fmt='%.2f'):
    ok = lo <= value <= hi
    say(('  %-46s ' + fmt + '   expected %s..%s   %s') % (name, value, lo, hi, 'PASS' if ok else 'CHECK'))
    return ok


def make(over=None, ch=1):
    cfg = C.merge(C.DEFAULTS, over or {})
    cfg['channels'] = ch
    cfg['seed'] = 1                      # validation is repeatable
    cfg['scenario'][0]['mode'] = 'osc'
    return cfg


def run(cfg, drive, seconds, block=20):
    sim = Simulator(cfg)
    sim.osc_drive = [drive] * sim.ch
    sig, cln, info = [], [], None
    for _ in range(int(seconds * sim.fs / block)):
        b = sim.step(block)
        sig.append(b['signal'])
        cln.append(b['clean'])
        info = b['info']
    return np.concatenate(sig, axis=1), np.concatenate(cln, axis=1), info, sim


def spectrum_stats(x, fs):
    f = F.compute(x[None, :], fs)[0]
    return f[5], f[6]


def main():
    passed = []
    quiet = {'noise': {'hum_on': False, 'artifacts_per_min': 0, 'onset_artifact_prob': 0,
                       'pops_per_min': 0, 'wander_uV': 0}}
    fs = 2000

    say('1. Amplitude grows with effort, compressing near the top (sEMG-force relation)')
    rms = []
    for d in (0.1, 0.3, 0.5, 0.7, 0.9):
        x, _, info, _ = run(make(quiet), d, 3)
        r = float(np.sqrt(np.mean(x[0, fs:] ** 2)))
        rms.append(r)
        say('     effort %.1f   RMS %.3f mV   active units %3d   mean rate %4.1f Hz'
            % (d, r, info[0]['active'], info[0]['mean_rate']))
    passed.append(check('monotonic rise (min step, mV)', min(np.diff(rms)), 0.0, 10.0, '%.3f'))
    passed.append(check('slope top / slope bottom', (rms[4] - rms[3]) / (rms[1] - rms[0]), 0.05, 1.0))

    say('2. Frequency content at 50 % effort (surface EMG: median 50-150 Hz)')
    x, _, _, _ = run(make(quiet), 0.5, 4)
    mnf, mdf = spectrum_stats(x[0, fs:], fs)
    passed.append(check('mean frequency MNF (Hz)', mnf, 60, 160, '%.1f'))
    passed.append(check('median frequency MDF (Hz)', mdf, 50, 150, '%.1f'))

    say('3. Recruitment order (Henneman): units at 10 % / 50 % / 90 % effort')
    acts = []
    for d in (0.1, 0.5, 0.9):
        _, _, info, _ = run(make(quiet), d, 1)
        acts.append(info[0]['active'])
    say('     active units: %s of 120' % acts)
    passed.append(check('all units recruited by 90 % (count)', acts[2], 110, 120, '%d'))
    passed.append(check('fraction recruited at 10 %', acts[0] / 120.0, 0.2, 0.6))

    say('4. Fatigue: 60 s hold at 60 % (MDF falls, RMS rises; Merletti, Lowery 2000)')
    cfg = make(C.merge(quiet, {'physiology': {'fatigue_rate': 0.02}}))
    sim = Simulator(cfg)
    sim.osc_drive = [0.6]
    segs = []
    for sec in range(60):
        buf = [sim.step(20)['signal'] for _ in range(fs // 20)]
        segs.append(np.concatenate(buf, axis=1)[0])
    a = np.concatenate(segs[1:6])
    b = np.concatenate(segs[-5:])
    mdf_a = spectrum_stats(a, fs)[1]
    mdf_b = spectrum_stats(b, fs)[1]
    rms_a = float(np.sqrt(np.mean(a ** 2)))
    rms_b = float(np.sqrt(np.mean(b ** 2)))
    say('     start: MDF %.1f Hz RMS %.3f mV   end: MDF %.1f Hz RMS %.3f mV   fatigue %.2f'
        % (mdf_a, rms_a, mdf_b, rms_b, sim.muscles[0].fatigue))
    passed.append(check('MDF change over 60 s (%)', 100 * (mdf_b - mdf_a) / mdf_a, -40, -8, '%.1f'))
    passed.append(check('RMS change over 60 s (%)', 100 * (rms_b - rms_a) / rms_a, 2, 80, '%.1f'))

    say('5. Rest: noise floor with good contact (filtered, uV RMS)')
    x, _, _, _ = run(make({'noise': {'contact': 0.95, 'artifacts_per_min': 0, 'onset_artifact_prob': 0,
                                     'pops_per_min': 0}}), 0.0, 3)
    passed.append(check('rest RMS (uV)', 1000 * float(np.sqrt(np.mean(x[0, fs:] ** 2))), 1, 15, '%.1f'))

    say('6. Loose electrode: the 50 Hz line dominates the rest spectrum')
    x, _, _, _ = run(make({'noise': {'contact': 0.2, 'artifacts_per_min': 0, 'onset_artifact_prob': 0,
                                     'pops_per_min': 0}}), 0.0, 3)
    X = np.abs(np.fft.rfft(x[0, fs:] * np.hanning(len(x[0, fs:])))) ** 2
    f = np.fft.rfftfreq(len(x[0, fs:]), 1 / fs)
    band = (f > 45) & (f < 55)
    passed.append(check('share of power at 45-55 Hz', float(X[band].sum() / X.sum()), 0.5, 1.0))

    say('7. Crosstalk: a relaxed neighbour picks up part of an active muscle')
    cfg = make(quiet, ch=2)
    cfg['link_channels'] = False
    sim = Simulator(cfg)
    sim.osc_drive = [0.6, 0.0]
    sig = np.concatenate([sim.step(20)['signal'] for _ in range(150)], axis=1)
    r = np.sqrt(np.mean(sig[:, fs // 2:] ** 2, axis=1))
    passed.append(check('neighbour / active RMS ratio', float(r[1] / r[0]), 0.05, 0.35))

    say('8. Blocks do not change the result (same seed, 7- and 40-sample steps)')
    c1, c2 = make(quiet), make(quiet)
    s1, s2 = Simulator(c1), Simulator(c2)
    s1.osc_drive = s2.osc_drive = [0.4]
    y1 = np.concatenate([s1.step(40)['clean'] for _ in range(50)], axis=1)
    y2 = np.concatenate([s2.step(8)['clean'] for _ in range(250)], axis=1)
    say('     note: spike times depend on the block grid (random draws), statistics must match')
    r1, r2 = np.sqrt(np.mean(y1 ** 2)), np.sqrt(np.mean(y2 ** 2))
    passed.append(check('RMS ratio between block sizes', float(r1 / r2), 0.85, 1.15))

    n_ok = sum(1 for p in passed if p)
    say()
    say('RESULT: %d / %d checks in range' % (n_ok, len(passed)))
    with open(os.path.join(HERE, 'REPORT.txt'), 'w') as f:
        f.write('\n'.join(LINES) + '\n')


if __name__ == '__main__':
    main()
