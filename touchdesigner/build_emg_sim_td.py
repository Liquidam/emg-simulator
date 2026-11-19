# Builds /EMG_SIM: a simple EMG simulator made of TouchDesigner CHOPs, with a panel and OSC out.
# Run inside TouchDesigner (Textport), with FOLDER = the folder of this file:
#   FOLDER = '/path/to/emg-simulator/touchdesigner'
#   exec(open(FOLDER + '/build_emg_sim_td.py', encoding='utf-8').read(),
#        {'op': op, 'td': td, 'parent': parent, 'ui': ui, 'BUILD_DIR': FOLDER})
# The Performer (random, human-like contractions) is the SAME code as the Python simulator:
# this script copies ../emgsim/performer.py into the component.
# Safe to run again: it rebuilds the component.
#
# The signal chain, one node per idea (open the component to see it):
#   effort (slider / SPACE / scenario) -> muscle noise x effort, shaped to the EMG band
#   + power-line hum + movement artifacts + baseline noise = raw (mV)
#   raw -> |x| -> low-pass = envelope;  raw -> ADC counts;  raw -> scope
# This is the classic "amplitude-modulated band-limited noise" model of surface EMG
# (Hogan & Mann 1980): simple, but it looks, sounds and analyses like the real thing.
import os
import td

HERE = globals().get('BUILD_DIR') or os.getcwd()
PERFORMER_PY = os.path.join(os.path.dirname(os.path.abspath(HERE)), 'emgsim', 'performer.py')
ROOT = op('/')
NAME = 'EMG_SIM'
FS = 2000
W, H = 980, 620

if ROOT.op(NAME):
    ROOT.op(NAME).destroy()
S = ROOT.create(td.containerCOMP, NAME)
S.par.w, S.par.h = W, H
S.par.bgcolorr, S.par.bgcolorg, S.par.bgcolorb, S.par.bgalpha = 0.075, 0.08, 0.09, 1
S.color = (0.85, 0.35, 0.55)
S.comment = 'EMG SIMULATOR (TouchDesigner). Right-click > View for the panel. Parameters = the controls.'
S.nodeX, S.nodeY = -1200, -1200

# ---------------------------------------------------------------- the controls (custom parameters)
pg = S.appendCustomPage('EMG Sim')


def fpar(name, label, lo, hi, val, clamp=True):
    p = pg.appendFloat(name, label=label)[0]
    p.normMin, p.normMax, p.default, p.val = lo, hi, val, val
    if clamp:
        p.min, p.max, p.clampMin, p.clampMax = lo, hi, True, True
    return p


def ipar(name, label, lo, hi, val):
    p = pg.appendInt(name, label=label)[0]
    p.normMin, p.normMax, p.min, p.max, p.clampMin, p.clampMax = lo, hi, lo, hi, True, True
    p.default, p.val = val, val
    return p


def mpar(name, label, items, val):
    p = pg.appendMenu(name, label=label)[0]
    p.menuNames = [i.lower() for i in items]
    p.menuLabels = items
    p.default, p.val = val, val
    return p


pg.appendHeader('Hperform', label='PERFORM   (SPACE / Effort always add on top)')
mpar('Mode', 'Scenario', ['Performer', 'Manual', 'Pulses', 'Ramp', 'Hold', 'Tremor', 'Improv'], 'performer')
fpar('Activity', 'Activity (performer)', 0, 1, 0.5)
fpar('Intensity', 'Intensity (performer)', 0, 1, 0.5)
pg.appendToggle('Together', label='Channels together (one player)')[0].val = True
ipar('Seed', 'Seed (0 = new random run)', 0, 9999, 0)
fpar('Effort', 'Effort', 0, 1, 0.0)
fpar('Level', 'Level / ceiling', 0, 1, 0.9)
fpar('Period', 'Period (s)', 0.2, 10, 2.0)
fpar('Tremorhz', 'Tremor (Hz)', 2, 14, 9.0)
ipar('Channels', 'Channels', 1, 4, 1)
pg.appendHeader('Hbody', label='BODY + ELECTRODE')
fpar('Fatigue', 'Fatigue', 0, 1, 0.0)
fpar('Contact', 'Electrode contact', 0, 1, 0.9)
pg.appendToggle('Hum', label='Power-line hum')[0].val = True
mpar('Mains', 'Mains (Hz)', ['50', '60'], '50')
fpar('Artifacts', 'Movement artifacts', 0, 1, 0.3)
pg.appendHeader('Hosc', label='OSC OUT')
pg.appendToggle('Oscactive', label='Send OSC')[0].val = True
mpar('Format', 'Format', ['Generic', 'EMG6_bio', 'WearPi_adc'], 'generic')
pg.appendStr('Oschost', label='Host')[0].val = '127.0.0.1'
ipar('Oscport', 'Port', 1, 65535, 9100)
pg.appendHeader('Hinfo', label='Generic /emgsim  |  EMG6 /bio  |  WearPi /adc')
pg.appendPulse('Help', label='Help  (how to use)')

P = "parent().par."
X0, Y0, DX, DY = 0, 0, 180, 140


def node(t, name, x, y, comment=None):
    o = S.create(t, name)
    o.nodeX, o.nodeY = X0 + x * DX, Y0 - y * DY
    if comment:
        o.comment = comment
    return o


def audio_rate(o, chans=True):
    o.par.rate = FS
    o.par.timeslice = True
    if chans and hasattr(o.par, 'channelname'):         # the audio oscillator has one channel
        o.par.channelname.expr = "' '.join('ch%d' % (i + 1) for i in range(int(parent().par.Channels)))"


# ---------------------------------------------------------------- 1. EFFORT
key = node(td.keyboardinCHOP, 'key_space', 0, 0, 'SPACE bar = contract (at Level).')
key.par.keys = 'space'
man = node(td.constantCHOP, 'effort_manual', 1, 0, 'Manual: the Effort slider, or SPACE x Level.')
man.par.const0name = 'effort'
man.par.const0value.expr = "max(%sEffort, op('key_space')[0] * %sLevel if op('key_space').numChans else 0)" % (P, P)
lfo_sq = node(td.lfoCHOP, 'effort_pulses', 1, 1, 'Pulses: contract / relax.')
lfo_sq.par.wavetype = 'square'
lfo_sq.par.frequency.expr = '1 / max(0.05, %sPeriod)' % P
lfo_sq.par.amp.expr = '%sLevel / 2' % P
lfo_sq.par.offset.expr = '%sLevel / 2' % P
lfo_tri = node(td.lfoCHOP, 'effort_ramp', 1, 2, 'Ramp: slow rise and fall.')
lfo_tri.par.wavetype = 'tri'
lfo_tri.par.frequency.expr = '1 / max(0.05, %sPeriod)' % P
lfo_tri.par.amp.expr = '%sLevel / 2' % P
lfo_tri.par.offset.expr = '%sLevel / 2' % P
hold = node(td.constantCHOP, 'effort_hold', 1, 3, 'Hold: a sustained contraction.')
hold.par.const0name = 'effort'
hold.par.const0value.expr = '%sLevel' % P
trem = node(td.lfoCHOP, 'effort_tremor', 1, 4, 'Tremor: Level with an 8-12 Hz tremble.')
trem.par.wavetype = 'sin'
trem.par.frequency.expr = '%sTremorhz' % P
trem.par.amp.expr = '%sLevel * 0.15' % P
trem.par.offset.expr = '%sLevel' % P
impro = node(td.noiseCHOP, 'effort_improv', 1, 5, 'Improv: a wandering, musical effort.')
impro.par.type = 'harmonic'
impro.par.period = 2.5
impro.par.amp.expr = '%sLevel / 2' % P
impro.par.offset.expr = '%sLevel / 2' % P
for o in (lfo_sq, lfo_tri, trem, impro):
    o.par.channelname = 'effort'
lib = node(td.textDAT, 'performer_lib', 0, -2, 'The simulated player (same code as the Python simulator: emgsim/performer.py).')
lib.text = open(PERFORMER_PY, encoding='utf-8').read()
perf = node(td.constantCHOP, 'effort_performer', 1, -1, 'Performer: random, human-like contractions, one player per channel.')
for i in range(4):
    perf.par['const%dname' % i] = 'effort%d' % (i + 1)
prun = node(td.executeDAT, 'performer_run', 1, -2, 'Every frame: ask the players for their effort.')
prun.par.framestart = True
prun.text = '''# Every frame: one simulated player per channel (the first one is "the" player).
PLAYERS = {}


def onFrameStart(frame):
    P = parent().par
    lib = op('performer_lib').module
    t = absTime.seconds
    out = op('effort_performer')
    for i in range(4):
        p = PLAYERS.get(i)
        if p is None:
            # Seed 0 = a different run every time; any other number replays the same run
            s = int(P.Seed.eval())
            p = PLAYERS[i] = lib.Performer(seed=(s * 100 + i) if s > 0 else None)
        v = p.value(t, P.Activity.eval(), P.Intensity.eval(), P.Level.eval()) if P.Mode.eval() == 'performer' else 0.0
        out.par['const%dvalue' % i] = v
    return
'''
sw = node(td.switchCHOP, 'effort_switch', 2, 2, 'Scenario chooser (Scenario parameter).')
for o in (perf, man, lfo_sq, lfo_tri, hold, trem, impro):
    sw.inputConnectors[len(sw.inputs)].connect(o)
sw.par.index.expr = '%sMode.menuIndex' % P
lag = node(td.lagCHOP, 'effort_human', 3, 2, 'Muscles are not instant: ~60 ms up, ~90 ms down.')
lag.par.lag1, lag.par.lag2 = 0.06, 0.09
lag.inputConnectors[0].connect(sw)
clamp = node(td.limitCHOP, 'effort_clamp', 4, 2)
clamp.par.type = 'clamp'
clamp.par.min, clamp.par.max = 0, 1
clamp.inputConnectors[0].connect(lag)
eff = node(td.nullCHOP, 'effort', 5, 2, 'EFFORT 0..1 (the scenario; one channel, or one per player)')
eff.inputConnectors[0].connect(clamp)
echan = node(td.constantCHOP, 'effort_per_channel', 6, 2,
             'Effort of each muscle. Together: one player, neighbours co-contract a little weaker. '
             'Otherwise each channel follows its own player. Effort / SPACE always add on top.')
for i in range(4):
    echan.par['const%dname' % i] = 'ch%d' % (i + 1)
    echan.par['const%dvalue' % i].expr = (
        "max(op('effort_manual')[0], "
        "op('effort')[0] * %r if (parent().par.Together or op('effort').numChans <= %d) "
        "else op('effort')[%d])" % (0.85 ** i, i, i))
esel = node(td.selectCHOP, 'effort_channels', 7, 2, 'Only as many as Channels.')
esel.par.chop = 'effort_per_channel'
esel.par.channames.expr = "' '.join('ch%d' % (i + 1) for i in range(int(parent().par.Channels)))"

# ---------------------------------------------------------------- 2. MUSCLE
mn = node(td.noiseCHOP, 'muscle_noise', 0, 7, 'Thousands of motor-unit spikes add up to noise-like activity.')
mn.par.type = 'random'
mn.par.periodunit = 'samples'          # a new random value every sample = white noise
mn.par.period = 1
mn.par.amp = 1.0
audio_rate(mn)
hp = node(td.audiofilterCHOP, 'muscle_highpass', 1, 7, 'Surface EMG has little below 20 Hz.')
hp.par.filter = 'highpass'
hp.par.units = 'frequency'
hp.par.cutofffrequency = 20
hp.inputConnectors[0].connect(mn)
lp = node(td.audiofilterCHOP, 'muscle_lowpass', 2, 7,
          'Tissue low-passes the signal. Fatigue slows conduction: the band moves down.')
lp.par.filter = 'lowpass'
lp.par.units = 'frequency'
lp.par.cutofffrequency.expr = '150 * (1 - 0.45 * %sFatigue)' % P
lp.inputConnectors[0].connect(hp)
lp2 = node(td.audiofilterCHOP, 'muscle_lowpass2', 3, 7)
lp2.par.filter = 'lowpass'
lp2.par.units = 'frequency'
lp2.par.cutofffrequency.expr = '150 * (1 - 0.45 * %sFatigue)' % P
lp2.inputConnectors[0].connect(lp)
gain = node(td.mathCHOP, 'muscle_x_effort', 4, 7,
            'Amplitude follows each channel\'s effort (0.5 mV RMS at maximum); fatigue raises it a little.')
gain.par.chopop = 'mul'
gain.par.match = 'index'
gain.par.gain.expr = "op('calib')['k'] * (1 + 0.3 * %sFatigue)" % P
gain.inputConnectors[0].connect(lp2)
gain.inputConnectors[1].connect(esel)
cal = node(td.constantCHOP, 'calib', 4, 6, 'Scale so that effort 1 = 0.5 mV RMS (set by measurement).')
cal.par.const0name = 'k'
cal.par.const0value = 1.733          # measured: white noise through 20-150 Hz = 0.2885 RMS -> 0.5 mV

# ---------------------------------------------------------------- 3. THE WORLD
hum = node(td.audiooscillatorCHOP, 'powerline_hum', 0, 9, 'Mains hum: grows fast when the contact is poor.')
hum.par.wavetype = 'sin'
hum.par.frequency.expr = "50 if %sMains.menuIndex == 0 else 60" % P
hum.par.amp.expr = "(%sHum and 1 or 0) * (0.004 + 0.08 * (1 - %sContact) ** 2)" % (P, P)
audio_rate(hum)
mot = node(td.noiseCHOP, 'movement_artifact', 1, 9, 'Slow bumps from cable and electrode movement (< 20 Hz).')
mot.par.type = 'harmonic'
mot.par.period = 0.3
mot.par.harmon = 2
mot.par.amp.expr = "%sArtifacts * (0.03 + 0.5 * (1 - %sContact)) * (0.3 + op('effort')[0])" % (P, P)
audio_rate(mot)
base = node(td.noiseCHOP, 'baseline_noise', 2, 9, 'Amplifier + electrode noise, about 3 uV RMS.')
base.par.type = 'random'
base.par.periodunit = 'samples'
base.par.period = 1
base.par.amp = 0.005
base.par.seed = 7
audio_rate(base)
add = node(td.mathCHOP, 'sum', 5, 8, 'Everything the electrode sees.')
add.par.chopop = 'add'
add.par.match = 'index'
for o in (gain, hum, mot, base):
    add.inputConnectors[len(add.inputs)].connect(o)
raw = node(td.nullCHOP, 'raw_mV', 6, 8, 'RAW EMG in millivolts (2000 samples per second).')
raw.inputConnectors[0].connect(add)

# ---------------------------------------------------------------- 4. READ IT
rect = node(td.mathCHOP, 'rectify', 7, 7, 'Envelope step 1: absolute value.')
rect.par.preop = 'pos'
rect.inputConnectors[0].connect(raw)
smooth = node(td.audiofilterCHOP, 'smooth', 8, 7, 'Envelope step 2: low-pass 5 Hz.')
smooth.par.filter = 'lowpass'
smooth.par.units = 'frequency'
smooth.par.cutofffrequency = 5
smooth.inputConnectors[0].connect(rect)
envn = node(td.mathCHOP, 'env_normalise', 9, 7, '1 = maximal effort.')
envn.par.gain = 1.0 / (0.5 * 0.8)
envn.inputConnectors[0].connect(smooth)
env = node(td.nullCHOP, 'envelope', 10, 7, 'ENVELOPE 0..1')
env.inputConnectors[0].connect(envn)
adc = node(td.mathCHOP, 'to_adc_counts', 7, 9, 'Like our ESP32 sensor: 12 bit, rest at 2160, 1400 counts per mV (matched to EMG6 live data, 2026-11-01).')
adc.par.gain = 1400
adc.par.postoff = 2160
adc.inputConnectors[0].connect(raw)
cnt = node(td.nullCHOP, 'adc_counts', 8, 9)
cnt.inputConnectors[0].connect(adc)
trail = node(td.trailCHOP, 'scope', 7, 11, 'Last 6 seconds of RAW for the panel.')
trail.par.wlength = 6
trail.par.wlengthunit = 'seconds'
trail.inputConnectors[0].connect(raw)
etrail = node(td.trailCHOP, 'scope_env', 8, 11)
etrail.par.wlength = 6
etrail.par.wlengthunit = 'seconds'
etrail.inputConnectors[0].connect(env)

# ---------------------------------------------------------------- 5. OSC OUT
osc = node(td.oscoutDAT, 'osc_out', 10, 9, 'Destination = Host / Port parameters.')
osc.par.address.expr = '%sOschost' % P
osc.par.port.expr = '%sOscport' % P
osc.par.active.expr = '%sOscactive' % P
send = node(td.executeDAT, 'osc_send', 11, 9, 'Every frame: send the new samples in the chosen format.')
send.par.framestart = False
send.par.frameend = True
send.text = r'''# Every frame: send this frame's new samples in the chosen format.
# The CHOPs run at 2000 Hz in time slices, so each frame holds about 2000 / fps samples.
STATE = {'seq': 0, 'phase': {}}


def _ticks(name, n, step):
    """Sample indices of this frame for a stream that emits every `step` samples."""
    ph = STATE['phase'].get(name, 0.0)
    idx = []
    while ph < n:
        idx.append(int(ph))
        ph += step
    STATE['phase'][name] = ph - n
    return idx


def onFrameEnd(frame):
    P = parent().par
    # CHOPs cook only when something asks: keep the scope trails continuous
    op('scope').cook()
    op('scope_env').cook()
    if not P.Oscactive.eval():
        return
    o = op('osc_out')
    raw, env, cnt = op('raw_mV'), op('envelope'), op('adc_counts')
    n = raw.numSamples
    fmt = P.Format.eval()
    if fmt == 'generic':
        for k in _ticks('raw', n, 4):                       # 2000 / 4 = 500 Hz
            o.sendOSC('/emgsim/raw', [float(c[k]) for c in raw.chans()])
        o.sendOSC('/emgsim/env', [float(c[n - 1]) for c in env.chans()])
    elif fmt == 'emg6_bio':
        ch = cnt.chans()[0]
        for k in _ticks('bio', n, 10):                      # 2000 / 10 = 200 Hz
            STATE['seq'] += 1
            t_us = int((absTime.seconds + k / 2000.0) * 1e6) % (2 ** 31)
            e = int(min(4095, max(0, round(ch[k]))))
            o.sendOSC('/bio', [STATE['seq'] % (2 ** 31), t_us, e, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
    else:
        v = [float(c[n - 1]) for c in env.chans()]
        v = [min(1.0, max(0.0, x)) for x in v] + [0.0] * (4 - len(v))
        o.sendOSC('/adc', v[:4])
    return
'''

# ---------------------------------------------------------------- 6. THE PANEL
title = S.create(td.textCOMP, 'title')
title.par.x, title.par.y, title.par.w, title.par.h = 16, H - 50, W - 32, 36
title.par.text = 'EMG SIMULATOR    |    hold SPACE to contract, or drag Effort'
title.par.fontsize = 16
title.par.bold = True
title.par.bgalpha = 0
title.par.fontcolorr, title.par.fontcolorg, title.par.fontcolorb = 0.9, 0.92, 0.95
title.par.alignx, title.par.aligny = 'left', 'center'
title.nodeX, title.nodeY = 0, 400
# the scope: CHOP -> texture -> a small GLSL drawing (min/max per pixel, envelope in gold)
tex = node(td.choptoTOP, 'scope_tex', 9, 11, 'RAW trail as a texture (one row per channel).')
tex.par.chop = 'scope'                 # relative: works wherever the .tox is dropped
tex.par.dataformat = 'r'
etex = node(td.choptoTOP, 'scope_env_tex', 10, 11)
etex.par.chop = 'scope_env'
etex.par.dataformat = 'r'
for o in (tex, etex):
    o.par.format = 'mono32float'          # keep the mV values, no 8-bit clipping
shader = node(td.textDAT, 'scope_shader', 11, 12, 'GLSL that draws the scope.')
shader.text = """// lanes = channels; pink/blue/green/gold = raw EMG (min-max per pixel); gold contour = envelope
uniform float uScale;      // 1 / full scale in mV
uniform int uCh;
out vec4 fragColor;
vec3 laneCol(int c){
    if (c == 0) return vec3(1.0, 0.44, 0.66);
    if (c == 1) return vec3(0.45, 0.78, 0.95);
    if (c == 2) return vec3(0.61, 0.89, 0.48);
    return vec3(1.0, 0.84, 0.45);
}
void main(){
    vec2 uv = vUV.st;
    int ch = max(uCh, 1);
    float lane = 1.0 / float(ch);
    int c = clamp(int(floor((1.0 - uv.y) / lane)), 0, ch - 1);
    float yc = 1.0 - (float(c) + 0.5) * lane;
    float y = (uv.y - yc) / (lane * 0.45);
    vec2 inRes = uTD2DInfos[0].res.zw;
    float W = uTDOutputInfo.res.z;
    float x0 = uv.x * inRes.x;
    float dx = inRes.x / W;
    float mn = 1e9, mx = -1e9;
    for (int i = 0; i < 16; i++) {
        float v = texelFetch(sTD2DInputs[0], ivec2(int(x0 + dx * float(i) / 16.0), c), 0).r;
        mn = min(mn, v); mx = max(mx, v);
    }
    float px = 1.2 / (uTDOutputInfo.res.w * lane * 0.45);
    float lo = clamp(mn * uScale, -1.0, 1.0), hi = clamp(mx * uScale, -1.0, 1.0);
    float on = step(lo - px, y) * step(y, hi + px);
    vec2 eRes = uTD2DInfos[1].res.zw;
    float e = texelFetch(sTD2DInputs[1], ivec2(int(uv.x * eRes.x), c), 0).r;
    float ey = clamp(e * 0.5, 0.0, 1.0);
    float eline = smoothstep(2.5 * px, 0.0, abs(abs(y) - ey));
    vec3 col = vec3(0.055, 0.063, 0.075);
    col += vec3(0.08) * smoothstep(px, 0.0, abs(y));                     // centre line
    col = mix(col, laneCol(c), on);
    col = mix(col, vec3(1.0, 0.9, 0.55), eline * 0.9);
    if (abs(uv.y - (1.0 - float(c) * lane)) < 0.002 && c > 0) col = vec3(0.16);   // lane divider
    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
"""
draw = node(td.glslTOP, 'scope_draw', 11, 11, 'The scope image shown on the panel.')
draw.par.pixeldat = 'scope_shader'
draw.par.outputresolution = 'custom'
draw.par.resolutionw, draw.par.resolutionh = 1200, 860
draw.par.format = 'rgba8fixed'          # colour out (the inputs are mono float)
draw.inputConnectors[0].connect(tex)
draw.inputConnectors[1].connect(etex)
draw.par.uniname0 = 'uScale'
draw.par.value0x = 0.5
draw.par.uniname1 = 'uCh'
draw.par.value1x.expr = 'parent().par.Channels'
try:
    draw.par.chopuniname0 = ''
except Exception:
    pass
sp = S.create(td.containerCOMP, 'scope_panel')
sp.par.x, sp.par.y, sp.par.w, sp.par.h = 16, 16, 600, H - 76
sp.par.top = 'scope_draw'
sp.nodeX, sp.nodeY = 180, 400
lbl = S.create(td.textCOMP, 'scope_label')
lbl.par.x, lbl.par.y, lbl.par.w, lbl.par.h = 22, H - 82, 588, 22
lbl.par.text = 'RAW EMG, last 6 s  (+-2 mV per lane)     gold = envelope'
lbl.par.fontsize = 10
lbl.par.bgalpha = 0
lbl.par.fontcolorr, lbl.par.fontcolorg, lbl.par.fontcolorb = 0.55, 0.58, 0.63
lbl.par.alignx, lbl.par.aligny = 'left', 'center'
lbl.par.layer = 2
lbl.nodeX, lbl.nodeY = 360, 400
pc = S.create(td.parameterCOMP, 'controls')
pc.par.op.expr = 'parent()'
pc.par.builtin = False
pc.par.custom = True
pc.par.header = False
pc.par.x, pc.par.y, pc.par.w, pc.par.h = 632, 16, W - 648, H - 70
pc.nodeX, pc.nodeY = 540, 400
for o in (title, sp, lbl, pc):
    o.color = (0.3, 0.4, 0.55)

# ---------------------------------------------------------------- 7. HELP POPUP
HELP = S.create(td.textDAT, 'help_text')
HELP.text = """EMG SIMULATOR  -  how to use

1  WATCH
   A simulated player contracts at random, like a real person
   (Scenario = Performer). Rests, normal squeezes, accents,
   strong bursts, long holds, ramps, quick flurries.

2  PLAY IT YOURSELF
   Click the panel, then hold SPACE to contract.
   Or drag Effort. Both add on top of any scenario.

3  SHAPE IT
   Activity   how often the player contracts
   Intensity  how hard (more strong bursts)
   Channels   1-4 muscles;  Channels together = one player
   Electrode contact  lower = hum and movement artifacts
   Fatigue    the sound of a tired muscle (lower frequencies)
   Seed       0 = a new random run every start

4  SEND IT (OSC)
   Send OSC on, then Format, Host and Port:
   Generic     /emgsim/raw ch1 ch2 ...  (mV, 500 Hz)
               /emgsim/env e1 e2 ...    (0..1, every frame)
   EMG6_bio    /bio  (ESP32 sensor format, 200 Hz)
   WearPi_adc  /adc a1 a2 a3 a4  (0..1)

5  USE IT ANYWHERE
   Drag EMG_SIM.tox into any TouchDesigner project.

Open this again: the Help button in the controls.
"""
pe = S.create(td.parameterexecuteDAT, 'help_popup')
pe.par.op.expr = 'parent()'           # watch this component's Help button
pe.par.pars = 'Help'
pe.par.valuechange = False
pe.par.onpulse = True
pe.text = """# The Help button: show the instructions in a popup.
def onPulse(par):
    ui.messageBox('EMG Simulator', op('help_text').text, buttons=['OK'])
    return
"""
# ---------------------------------------------------------------- 8. README + LAYOUT
readme = S.create(td.textDAT, 'README')
readme.text = """EMG SIMULATOR (TouchDesigner)
==============================
Open the panel: right-click this component (EMG_SIM) > View.  Controls = its parameters.

What it is
  A simple, real-time surface EMG simulator built from TouchDesigner operators only.
  Model: amplitude-modulated, band-limited noise (Hogan & Mann 1980): many motor units
  firing at once add up to a noise-like signal whose size follows the effort.
  The effort comes from a simulated PERFORMER (random, human-like gestures) or by hand.

Where the randomness is
  * performer_run / performer_lib : the player's gestures (when, which kind, how strong,
    how long). Seed 0 = a new run every start; any other number replays the same run.
  * muscle_noise   : the motor-unit activity (white noise, a new value every sample).
  * movement_artifact, baseline_noise : the world around the electrode.

Read the network top to bottom: bands 1-7.
Same OSC addresses as the Python simulator (../emgsim): /emgsim/raw /emgsim/env, /bio, /adc.
Rebuild everything from: build_emg_sim_td.py
"""

BANDS = [
    ('0', 'START HERE', 'README explains the whole component. The panel (band 7) = right-click EMG_SIM > View.',
     (0.30, 0.55, 0.35), [['README', 'help_text', 'help_popup']]),
    ('1', 'EFFORT  -  what the player does',
     'performer_run asks the simulated player (performer_lib, same code as the Python simulator) for '
     'its effort every frame -> effort_performer (one player per channel). The other scenarios are '
     'LFOs / noise. effort_switch picks one (Scenario parameter), effort_human adds human rise and '
     'fall, effort_per_channel adds Effort / SPACE on top and spreads it over the channels.',
     (0.75, 0.40, 0.55),
     [['performer_lib', 'performer_run', 'effort_performer', 'key_space', 'effort_manual'],
      ['effort_pulses', 'effort_ramp', 'effort_hold', 'effort_tremor', 'effort_improv'],
      ['effort_switch', 'effort_human', 'effort_clamp', 'effort', 'effort_per_channel', 'effort_channels']]),
    ('2', 'MUSCLE  -  motor-unit activity shaped to the EMG band',
     'White noise (thousands of motor-unit spikes) -> 20 Hz high-pass -> 150 Hz low-pass x2 '
     '(lower with Fatigue) -> multiplied by each channel\'s effort. calib: effort 1 = 0.5 mV RMS.',
     (0.85, 0.45, 0.45),
     [['muscle_noise', 'muscle_highpass', 'muscle_lowpass', 'muscle_lowpass2', 'muscle_x_effort', 'calib']]),
    ('3', 'THE WORLD  -  what else the electrode picks up',
     'Power-line hum (grows when Electrode contact drops), movement artifacts (slow bumps), '
     'baseline noise. sum = raw_mV, the RAW EMG in millivolts at 2000 samples per second.',
     (0.80, 0.65, 0.30),
     [['powerline_hum', 'movement_artifact', 'baseline_noise', 'sum', 'raw_mV']]),
    ('4', 'READ IT  -  envelope and ADC counts',
     'Envelope = |raw| -> 5 Hz low-pass -> normalised (1 = maximal effort). ADC counts imitate our '
     'ESP32 sensor: rest 2160, 1400 counts per mV.',
     (0.40, 0.65, 0.80),
     [['rectify', 'smooth', 'env_normalise', 'envelope'], ['to_adc_counts', 'adc_counts']]),
    ('5', 'SCOPE  -  the picture on the panel',
     'The last 6 s as a texture (one row per channel) -> scope_shader (GLSL) draws min/max per pixel '
     'and the envelope in gold.',
     (0.45, 0.45, 0.70),
     [['scope', 'scope_env', 'scope_tex', 'scope_env_tex', 'scope_shader', 'scope_draw']]),
    ('6', 'OSC OUT',
     'osc_send runs every frame: Generic (/emgsim/raw 500 Hz + /emgsim/env), EMG6_bio (/bio 200 Hz) '
     'or WearPi_adc (/adc). osc_out = destination (Host / Port).',
     (0.35, 0.60, 0.55),
     [['osc_send', 'osc_out']]),
    ('7', 'PANEL', 'title, scope_panel (shows scope_draw), scope_label, controls (= this component\'s parameters).',
     (0.50, 0.50, 0.50),
     [['title', 'scope_panel', 'scope_label', 'controls']]),
]
# text COMPs make empty callback DATs by themselves: not needed
for o in (S.op('title'), S.op('scope_label')):
    cb = o.par.callbacks.eval()
    o.par.callbacks = ''
    if cb is not None:
        cb.destroy()
LDX, LDY, HEAD, GAP = 200, 150, 120, 70
ytop = 0
for num, ttl, body, col, rows in BANDS:
    bh = HEAD + LDY * len(rows) + 30
    bw = 140 + LDX * max(len(r) for r in rows)
    for ri, row in enumerate(rows):
        for ci, n in enumerate(row):
            o = S.op(n)
            if o is None:
                continue
            o.nodeX = 60 + ci * LDX
            o.nodeY = ytop - HEAD - ri * LDY - 90
            o.color = tuple(0.35 + 0.5 * c for c in col)
    a = S.create(td.annotateCOMP, 'sign_%s' % num)
    a.par.Titletext = '%s   %s' % (num, ttl)
    a.par.Bodytext = body
    a.par.Backcolorr, a.par.Backcolorg, a.par.Backcolorb = col
    a.par.Backcoloralpha = 0.22
    if hasattr(a.par, 'Bodyfontsize'):
        a.par.Bodyfontsize = 11
    if hasattr(a.par, 'Titleheight'):
        a.par.Titleheight = 40
    a.nodeX, a.nodeY, a.nodeWidth, a.nodeHeight = 0, ytop - bh, max(bw, 1250), bh
    ytop -= bh + GAP
unplaced = [o.name for o in S.children if o.OPType != 'annotateCOMP' and o.nodeX == 0 and o.nodeY == 0]

result = 'built %s' % S.path
