"""All settings in one dictionary. Load/save as JSON. Presets override parts of it."""
import copy
import json
import os

DEFAULTS = {
    "fs": 2000,                    # internal sample rate (Hz)
    "block_ms": 10,                # engine step
    "channels": 4,                 # 1..8 muscles / electrodes
    "seed": 0,                     # 0 = a new random run every start; any other number = repeatable
    "physiology": {
        "n_mu": 120,               # motor units per muscle (Fuglevand 1993: 120)
        "rr": 30.0,                # range of recruitment thresholds
        "mfr": 8.0,                # minimum firing rate (Hz)
        "pfr_first": 35.0,         # peak firing rate of the first unit (Hz)
        "pfr_last": 25.0,          # peak firing rate of the last unit (Hz)
        "isi_cv": 0.2,             # firing irregularity
        "muap_width_ms": 2.0,      # MUAP width scale (wider = lower frequencies)
        "depth_max": 15.0,         # deepest unit (mm); deeper = smaller, wider
        "mvc_rms_mV": 0.5,         # RMS at maximal effort
        "common_drive": 0.03,      # shared slow fluctuation of excitation
        "fatigue_on": True,
        "fatigue_rate": 0.02,      # per second at full effort
        "recovery_rate": 0.01,     # per second at rest
        "crosstalk": 0.15          # leakage to neighbouring channels (0..0.5)
    },
    "noise": {
        "contact": 0.9,            # 1 = perfect electrode contact, 0 = loose
        "baseline_uV": 3.0,
        "hum_on": True,
        "mains_hz": 50,
        "hum_uV": 8.0,
        "wander_uV": 20.0,
        "artifact_mV": 0.3,
        "artifacts_per_min": 2.0,
        "onset_artifact_prob": 0.4,
        "pop_mV": 1.5,
        "pops_per_min": 0.2,
        "ecg_uV": 0.0,
        "heart_bpm": 70.0
    },
    "device": {
        "hp_on": True, "hp_hz": 20.0,          # SENIAM / De Luca 2010
        "lp_on": True, "lp_hz": 450.0,
        "notch_on": False,
        "adc_bits": 12,
        "adc_offset": 2048,                    # counts at 0 mV
        "adc_counts_per_mV": 370.0,            # amplifier gain expressed in ADC counts
        "adc_noise_lsb": 2.0
    },
    "scenario": [
        {"mode": "performer", "level": 0.9, "period": 2.0, "duty": 0.5, "phase": 0.0,
         "tremor_hz": 9.0, "tremor_depth": 0.15, "speed": 0.5, "rate": 1.5,
         "activity": 0.5, "intensity": 0.5}
    ],
    "link_channels": True,         # all channels follow channel 1's scenario (with offsets)
    "osc": {
        "host": "127.0.0.1",
        "port": 9100,
        "in_port": 9201,           # control the simulator from Max / Pd / TD
        "use_bundles": False,
        "packet_loss": 0.0,        # 0..1, to test receivers
        "streams": {
            "raw":      {"on": True,  "address": "/emgsim/raw", "rate": 1000, "units": "mV",
                         "decimate": "average", "port": None},
            "rawblock": {"on": False, "address": "/emgsim/rawblock", "units": "mV", "port": None},
            "env":      {"on": True,  "address": "/emgsim/env", "rate": 100, "window_ms": 100,
                         "normalise": True, "port": None},
            "features": {"on": False, "address": "/emgsim/features", "rate": 20, "window_ms": 250,
                         "port": None},
            "truth":    {"on": False, "address": "/emgsim/truth", "rate": 50, "port": None},
            "bio":      {"on": False, "address": "/bio", "rate": 200, "channel": 1, "port": None,
                         "imu": True},
            "adc":      {"on": False, "address": "/adc", "rate": 100, "smooth": 8, "port": None}
        }
    }
}

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_DIR = os.path.join(HERE, 'presets')


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    if isinstance(out.get('scenario'), list):       # every scenario entry gets all keys
        out['scenario'] = [dict(DEFAULTS['scenario'][0], **s) for s in out['scenario']] or \
            [dict(DEFAULTS['scenario'][0])]
    return out


def load(path):
    with open(path, encoding='utf-8') as f:
        return merge(DEFAULTS, json.load(f))


def save(cfg, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


def presets():
    if not os.path.isdir(PRESET_DIR):
        return []
    return sorted(p[:-5] for p in os.listdir(PRESET_DIR) if p.endswith('.json'))


def preset(name):
    with open(os.path.join(PRESET_DIR, name + '.json'), encoding='utf-8') as f:
        return json.load(f)


def scenario_for(cfg, c):
    sc = cfg['scenario']
    p = dict(sc[c] if c < len(sc) else sc[0])
    if cfg.get('link_channels') and c > 0:
        p = dict(sc[0])
        p['phase'] = p.get('phase', 0.0) + 0.17 * c       # neighbours a little out of step
    return p


def set_path(cfg, path, value):
    """'noise.hum_uV' -> cfg['noise']['hum_uV'] = value (used by OSC /emgsim/set)."""
    keys = path.split('.')
    d = cfg
    for k in keys[:-1]:
        if k.isdigit() and isinstance(d, list):
            d = d[int(k)]
        else:
            d = d.setdefault(k, {})
    last = keys[-1]
    old = d[int(last)] if isinstance(d, list) else d.get(last)
    if isinstance(old, bool):
        value = bool(value) if not isinstance(value, str) else value.lower() in ('1', 'true', 'on')
    elif isinstance(old, int) and not isinstance(value, str):
        value = int(value)
    elif isinstance(old, float) and not isinstance(value, str):
        value = float(value)
    if isinstance(d, list):
        d[int(last)] = value
    else:
        d[last] = value
