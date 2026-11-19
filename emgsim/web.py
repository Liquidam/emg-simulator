"""The simulator's window, in the web browser (works the same on Mac, Windows, Linux).

A tiny local web server (Python standard library only) serves one page and a JSON state.
Nothing leaves this computer: it listens on 127.0.0.1 only.
"""
import json
import os
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from . import config as C
from .engine import Runner
from .scenarios import MODES

HERE = os.path.dirname(os.path.abspath(__file__))
REC_DIR = os.path.join(os.path.dirname(HERE), 'recordings')
POOL = ('n_mu', 'rr', 'mfr', 'pfr_first', 'pfr_last', 'isi_cv', 'muap_width_ms', 'depth_max', 'mvc_rms_mV')


def kind_for(path):
    k = path.split('.')
    if k[0] in ('fs', 'channels', 'seed'):
        return 'all'
    if k[0] == 'physiology' and len(k) > 1 and k[1] in POOL:
        return 'physiology'
    if k[0] == 'device' or path == 'noise.mains_hz':
        return 'device'
    if path == 'osc.in_port':
        return 'osc_in'
    return 'live'


class WebApp:
    def __init__(self, cfg, port=8765):
        self.cfg = cfg
        self.runner = Runner(cfg)
        self.cfg_version = 0
        self.runner.on_remote_change = self._bump
        self.stop_flag = threading.Event()
        self.last_rec = ''
        app = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path in ('/', '/index.html'):
                    with open(os.path.join(HERE, 'web_index.html'), 'rb') as f:
                        self._send(200, f.read(), 'text/html; charset=utf-8')
                elif u.path == '/state':
                    since = int(parse_qs(u.query).get('since', ['-1'])[0])
                    self._send(200, json.dumps(app.state(since)))
                elif u.path == '/config':
                    self._send(200, json.dumps({'cfg': app.cfg, 'version': app.cfg_version,
                                                'presets': C.presets(), 'modes': MODES}))
                elif u.path == '/settings.json':
                    self.send_response(200)
                    body = json.dumps(app.cfg, indent=2).encode('utf-8')
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Disposition', 'attachment; filename="emgsim_settings.json"')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send(404, '{}')

            def do_POST(self):
                n = int(self.headers.get('Content-Length', 0))
                try:
                    msg = json.loads(self.rfile.read(n) or b'{}')
                    res = app.handle(msg)
                    self._send(200, json.dumps(res or {'ok': True}))
                except Exception as e:                       # report, never crash the server
                    self._send(400, json.dumps({'ok': False, 'error': str(e)}))

        for p in range(port, port + 20):
            try:
                self.server = ThreadingHTTPServer(('127.0.0.1', p), H)
                self.port = p
                break
            except OSError:
                continue
        else:
            raise OSError('no free port between %d and %d' % (port, port + 19))

    def _bump(self):
        self.cfg_version += 1

    # ------------------------------------------------------------------ data for the page
    def state(self, since):
        r = self.runner
        with r.lock:
            n = r.view_n
            have = r.view['signal'].shape[1]
            k = have if since < 0 else max(0, min(have, n - since))
            sig = r.view['signal'][:, have - k:] if k else r.view['signal'][:, :0]
            drv = r.view['drive'][:, have - k:] if k else r.view['drive'][:, :0]
            info = list(r.last_info)
            feats = r.out.last.get('features')
            env = r.out.last.get('env')
            ch = r.sim.ch
        o = self.cfg['osc']
        return {
            'n': n, 'rate': r.view_rate, 'ch': ch,
            'sig': [[round(float(v), 4) for v in row] for row in sig],
            'drive': [[round(float(v), 3) for v in row] for row in drv],
            'info': [{'active': i['active'], 'rate': round(i['mean_rate'], 1),
                      'fatigue': round(i['fatigue'], 3), 'force': round(i['force'], 3)} for i in info],
            'feats': [[round(float(f[0]), 4), round(float(f[5]), 1), round(float(f[6]), 1)] for f in feats]
            if feats is not None else [],
            'env': [round(float(e), 3) for e in env] if env is not None else [],
            'status': {'host': o['host'], 'port': o['port'], 'in_port': o['in_port'],
                       'streams': [s for s, v in o['streams'].items() if v.get('on')],
                       'msgs': round(r.out.msgs_per_s), 'cpu': round(r.load_ms),
                       'paused': r.paused, 'recording': r.recording is not None,
                       'rx_error': r.rx_error, 'last_rec': self.last_rec},
            'version': self.cfg_version,
        }

    # ------------------------------------------------------------------ actions from the page
    def handle(self, m):
        op = m.get('op')
        r = self.runner
        if op == 'set':
            path, value = m['path'], m['value']
            with r.lock:
                C.set_path(self.cfg, path, value)
            kind = kind_for(path)
            if kind != 'live':
                r.rebuild(kind)
            if kind == 'all':
                self._bump()
        elif op == 'drive':
            ch = m.get('channel')
            r.set_manual(float(m['value']), None if ch is None else int(ch))
        elif op == 'preset':
            self._replace(C.merge(C.DEFAULTS, C.preset(m['name'])))
        elif op == 'load':
            self._replace(C.merge(C.DEFAULTS, m['cfg']))
        elif op == 'fatigue':
            r.reset_fatigue()
        elif op == 'pause':
            r.paused = not r.paused
        elif op == 'record':
            if r.recording is None:
                r.record_start()
            else:
                os.makedirs(REC_DIR, exist_ok=True)
                path = os.path.join(REC_DIR, 'emgsim_%s.%s' % (time.strftime('%Y%m%d_%H%M%S'),
                                                              m.get('format', 'csv')))
                secs = r.record_stop(path)
                self.last_rec = '%.1f s saved to %s' % (secs, path)
        elif op == 'quit':
            threading.Thread(target=self.shutdown, daemon=True).start()
        return {'ok': True, 'version': self.cfg_version}

    def _replace(self, new):
        r = self.runner
        with r.lock:
            self.cfg.clear()
            self.cfg.update(new)
            r.sim.cfg = self.cfg
        r.rebuild('all')
        r.rebuild('osc_in')
        self._bump()

    # ------------------------------------------------------------------ run
    def shutdown(self):
        time.sleep(0.2)
        self.stop_flag.set()
        self.server.shutdown()

    def run(self, open_browser=True):
        self.runner.start()
        url = 'http://127.0.0.1:%d/' % self.port
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        print('EMG simulator: open %s in your browser (Ctrl+C or the Quit button to stop).' % url)
        if open_browser:
            webbrowser.open(url)
        try:
            while not self.stop_flag.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        self.runner.stop()
        try:
            self.server.shutdown()
        except Exception:
            pass
        print('stopped.')


def main(cfg, port=8765, open_browser=True):
    WebApp(cfg, port).run(open_browser)
