# EMG Simulator for TouchDesigner

The simple sibling of the Python simulator: built only from TouchDesigner operators, one node
per idea, so the whole signal chain can be read (and taught) in the network editor.

| file | use it to |
|---|---|
| `EMG_SIM.toe` | open and play: the panel opens by itself |
| `EMG_SIM.tox` | drag into **any** project (it works under any name, at any depth) |
| `build_emg_sim_td.py` | rebuild the component from scratch (the source of truth) |

TouchDesigner 2025 or newer. No Python packages, nothing to install.

When `EMG_SIM.toe` opens, the panel appears and then a **How to use** popup. Open it again with
the **Help** button at the bottom of the controls (it works the same in the `.tox`).

## The panel

* **Scope**: the last 6 seconds of raw EMG per channel, with the envelope in gold.
* **Controls** (the component's own parameters):

| parameter | does |
|---|---|
| Scenario | **Performer** (default): a simulated player who contracts at random like a real person (rests, normal squeezes, accents, strong bursts, holds, ramps, flurries, shaky contractions). Also Manual, Pulses, Ramp, Hold, Tremor, Improv |
| Activity, Intensity | Performer: how busy, how hard (more strong bursts) |
| Channels together | on: one player, neighbouring muscles co-contract; off: every channel has its own player |
| Seed | 0 = a new random run every start (default); any other number replays the same performer run |
| Effort / SPACE | always add on top of the scenario |
| Level, Period, Tremor (Hz) | ceiling and shape of the scenarios |
| Channels | 1-4 muscles (independent signals) |
| Fatigue | 0..1: the spectrum moves down, amplitude rises a little |
| Electrode contact | 1 = perfect; lower = more hum and more movement artifacts |
| Power-line hum, Mains | 50 or 60 Hz |
| Movement artifacts | slow bumps from cables and electrodes |
| Send OSC, Format, Host, Port | where and how the signal goes out |

## OSC formats

| Format | messages |
|---|---|
| **Generic** | `/emgsim/raw ch1 ch2 …` (mV, 500 Hz) and `/emgsim/env e1 e2 …` (0..1, once per frame) |
| **EMG6_bio** | `/bio seq t_us emg_raw 0 0 1 0 0 0` at 200 Hz: our ESP32 sensor format. EMG in ADC counts (rest 2160, 1400 counts per mV, matched to live sensor data). Point it at EMG6, port 9100 |
| **WearPi_adc** | `/adc a1 a2 a3 a4`: envelopes 0..1, Federico Visi's wearpi-py format |

These are the same addresses the Python simulator uses, so a patch made for one works with the other.

## Simulation and randomness

The signal is generated live, not played back. Random parts: the performer's gestures
(`performer_run` / `performer_lib`, Seed 0 = new run each start), the motor-unit activity
(`muscle_noise`, a new random value every sample), movement artifacts and baseline noise.
What it does not simulate (unlike the Python simulator): individual motor units, recruitment,
real fatigue physiology. It is a statistical model of the signal, the classic one for surface EMG.

## The network

Double-click EMG_SIM: the nodes sit in numbered, annotated bands, top to bottom, and a README
DAT inside explains the component.

| band | contents |
|---|---|
| 0 START HERE | README |
| 1 EFFORT | the performer, SPACE / Effort, the other scenarios, the human lag, effort per channel |
| 2 MUSCLE | white noise -> 20 Hz high-pass -> 150 Hz low-pass x2 -> x effort |
| 3 THE WORLD | hum, movement artifacts, baseline noise -> raw_mV |
| 4 READ IT | envelope, ADC counts |
| 5 SCOPE | trails -> textures -> GLSL drawing |
| 6 OSC OUT | osc_send (every frame) -> osc_out |
| 7 PANEL | title, scope, label, controls |

## The chain (open the component)

```
EFFORT   effort_performer (performer_run + performer_lib = emgsim/performer.py) / manual / pulses / ramp / hold / tremor / improv -> effort_switch
         -> effort_human (lag: ~60 ms up, ~90 ms down) -> effort_clamp -> effort -> effort_per_channel (+ SPACE) -> effort_channels
MUSCLE   muscle_noise (white, 2000 Hz) -> muscle_highpass 20 Hz -> muscle_lowpass x2 (150 Hz, lower with fatigue)
         -> muscle_x_effort (x each channel's effort; calib: effort 1 = 0.5 mV RMS)
WORLD    powerline_hum + movement_artifact + baseline_noise
         -> sum -> raw_mV
READ IT  rectify -> smooth (5 Hz) -> env_normalise -> envelope
         to_adc_counts -> adc_counts
         scope / scope_env (trails) -> scope_tex -> scope_draw (GLSL) -> panel
OUT      osc_send (every frame) -> osc_out
```

The model is the classic "amplitude-modulated, band-limited noise" description of surface EMG:
many motor units firing at once add up to a noise-like signal whose amplitude follows the effort.
It does not model individual motor units, recruitment or real fatigue physiology; for those, use the
Python simulator (`../README.md`).

## Measured (2026-11-19)

* Mean / median frequency at full effort: 82 / 71 Hz (surface EMG: about 50-150 Hz).
* Effort 1 = 0.5 mV RMS; channels independent (correlation 0.02).
* Into EMG6 (`EMG6_bio`, pulses at 0.8): EMG6's envelope 385 on average while contracting
  (95th percentile 528), 10 at rest. The real sensor on 2026-11-01: 400-650 squeezing, 12-23 at rest.
* Dropped into another project under another name: all internal references stay inside the copy.
