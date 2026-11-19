"""python -m emgsim [--preset NAME] [--config FILE] [--headless] [--seconds N] [--record FILE] [--tk]"""
import argparse
import sys
import time

from . import config as C


def main(argv=None):
    ap = argparse.ArgumentParser(prog='emgsim', description='Biophysical surface EMG simulator with OSC output.')
    ap.add_argument('--preset', help='preset name (see presets/), e.g. classroom_noisy')
    ap.add_argument('--config', help='settings file (.json) saved from the window')
    ap.add_argument('--headless', action='store_true', help='no window: run and send OSC')
    ap.add_argument('--seconds', type=float, default=0, help='headless: stop after N seconds (0 = until Ctrl+C)')
    ap.add_argument('--record', help='headless: save the signal to .csv or .wav when stopping')
    ap.add_argument('--host', help='override OSC destination host')
    ap.add_argument('--port', type=int, help='override OSC destination port')
    ap.add_argument('--list-presets', action='store_true')
    ap.add_argument('--tk', action='store_true', help='use the old tkinter window instead of the browser page')
    ap.add_argument('--ui-port', type=int, default=8765, help='port of the browser page (default 8765)')
    ap.add_argument('--no-browser', action='store_true', help='do not open the browser by itself')
    a = ap.parse_args(argv)

    if a.list_presets:
        print('\n'.join(C.presets()))
        return
    cfg = C.merge(C.DEFAULTS, {})
    if a.preset:
        cfg = C.merge(cfg, C.preset(a.preset))
    if a.config:
        cfg = C.merge(cfg, C.load(a.config))
    if a.host:
        cfg['osc']['host'] = a.host
    if a.port:
        cfg['osc']['port'] = a.port

    if not a.headless:
        if a.tk:
            from .gui import main as gui_main
            gui_main(cfg)
        else:
            from .web import main as web_main          # the window is a page in the browser
            web_main(cfg, a.ui_port, not a.no_browser)
        return

    from .engine import Runner
    r = Runner(cfg)
    if a.record:
        r.record_start()
    r.start()
    on = [k for k, s in cfg['osc']['streams'].items() if s.get('on')]
    print('EMG simulator running: %d channels at %d Hz -> %s:%s (%s). Control in on :%s. Ctrl+C to stop.'
          % (r.sim.ch, r.sim.fs, cfg['osc']['host'], cfg['osc']['port'], ', '.join(on), cfg['osc']['in_port']))
    if r.rx_error:
        print(r.rx_error)
    t0 = time.time()
    try:
        while not a.seconds or time.time() - t0 < a.seconds:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    r.stop()
    if a.record:
        print('saved %.1f s to %s' % (r.record_stop(a.record), a.record))


if __name__ == '__main__':
    sys.exit(main())
