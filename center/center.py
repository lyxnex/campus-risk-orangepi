"""Authenticated LAN evidence receiver and human review page (Python standard library)."""
import argparse
import datetime as dt
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import ssl
import tempfile
import threading
from urllib.parse import urlparse, parse_qs
import wave
import zipfile
from common import SCHEMA, FILES, atomic_json, load_json, sha256, utc_now, valid_id
from decision import POLICY, RULE, RULES, CLEAN_QWEN_POLICIES, serial_candidate, rule_for
from web_auth import WebAuth, RateLimited, session_cookie

class Conflict(ValueError):
    pass

def decode_bundle(path):
    """Validate the entire bundle before writing any extracted content."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 6 or set(names) != set(FILES) | {'manifest.json'}:
            raise ValueError('Unexpected, duplicate or missing archive entries')
        if any(i.flag_bits & 1 or i.file_size > 4 * 1024 * 1024 for i in z.infolist()):
            raise ValueError('Encrypted/oversized archive entry')
        if sum(i.file_size for i in z.infolist()) > 8 * 1024 * 1024:
            raise ValueError('Expanded bundle too large')
        data = {name: z.read(name) for name in names}
    manifest = json.loads(data['manifest.json'])
    event = json.loads(data['event.json'])
    inference = json.loads(data['inference.json'])
    capture = json.loads(data['capture.json'])
    if not all(isinstance(value, dict) for value in (manifest, event, inference, capture)):
        raise ValueError('JSON documents must be objects')
    if event.get('schema') != SCHEMA or manifest.get('schema') != SCHEMA:
        raise ValueError('Wrong event schema')
    valid_id(event.get('event_id')); valid_id(event.get('device_id')); valid_id(event.get('scene_id'))
    if manifest.get('event_id') != event['event_id'] or set(manifest.get('files', {})) != set(FILES):
        raise ValueError('Manifest identity mismatch')
    for name in FILES:
        expected = manifest['files'][name]
        if not isinstance(expected, dict): raise ValueError('Invalid manifest file entry')
        if expected.get('bytes') != len(data[name]) or expected.get('sha256') != hashlib.sha256(data[name]).hexdigest():
            raise ValueError('Checksum mismatch: ' + name)
    if event.get('candidate') is not True or inference.get('review_candidate') is not True or inference.get('status') != 'complete':
        raise ValueError('Only completed review candidates are accepted')
    if (not serial_candidate(inference) or event.get('decision_policy') != inference.get('decision_policy') or
            event.get('decision_rule') != rule_for(inference.get('decision_policy'))):
        raise ValueError('Both serial stages must pass before upload')
    if event.get('text') != inference.get('text') or data['transcript.txt'].decode('utf-8') != inference.get('text'):
        raise ValueError('Transcript mismatch')
    if event.get('scores') != inference.get('models'):
        raise ValueError('Model result mismatch')
    if not isinstance(event.get('audio'), dict) or event['audio'].get('sha256') != hashlib.sha256(data['audio.wav']).hexdigest():
        raise ValueError('Audio hash mismatch')
    try:
        start = dt.datetime.fromisoformat(event['capture_start_utc'])
        end = dt.datetime.fromisoformat(event['capture_end_utc'])
        if start.utcoffset() is None or end.utcoffset() is None or abs((end-start).total_seconds()-15) > 0.01:
            raise ValueError('Invalid time range')
        for key in ('capture_start_utc', 'capture_end_utc'):
            if event[key] != capture.get(key):
                raise ValueError('Capture timestamp mismatch')
    except (KeyError, TypeError) as e:
        raise ValueError('Missing time range') from e
    with wave.open(io.BytesIO(data['audio.wav']), 'rb') as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes(), w.getcomptype()) != (2, 2, 16000, 240000, 'NONE'):
            raise ValueError('Expected complete 15-second stereo PCM16 16kHz audio')
        if len(w.readframes(240000)) != 960000:
            raise ValueError('Truncated audio')
    return event, data

class Center:
    def __init__(self, config, config_dir):
        self.config = config
        self.list_policy = config.get('list_decision_policy')
        if self.list_policy is not None: rule_for(self.list_policy)
        self.root = (Path(config_dir) / config['data_dir']).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.inbox = self.root / 'events'; self.inbox.mkdir(exist_ok=True)
        self.incoming = self.root / 'incoming'; self.incoming.mkdir(exist_ok=True)
        self.capture_start_after = None
        epoch_path = self.root / 'recording_epoch.json'
        if epoch_path.exists():
            self.capture_start_after = dt.datetime.fromisoformat(load_json(epoch_path)['capture_start_after_utc'])
            if self.capture_start_after.utcoffset() is None: raise ValueError('Recording epoch must include a timezone')
        self.lock = threading.Lock()
        self.web_auth=WebAuth(config.get('web_password_hash'))
        self.config_dir=Path(config_dir).resolve()
        self.ced_stop=threading.Event();self.ced_thread=None
        if len(config.get('token', '')) < 32:
            raise ValueError('Generate a token with init_config.py first')

    def start_ced_worker(self):
        model_dir=self.config.get('ced_model_dir')
        if not model_dir:return
        def observe():
            import sys
            sys.path.insert(0,str(Path(__file__).resolve().parent/'capture'))
            observer=None
            while not self.ced_stop.is_set():
                try:
                    if observer is None:
                        from ced_observer import CedObserver
                        observer=CedObserver(self.config_dir/model_dir)
                    for path in self.inbox.glob('*/*/event.json'):
                        if self.ced_stop.is_set():break
                        out=path.parent/'ced_mini.json'
                        if out.exists():continue
                        event=load_json(path)
                        if event.get('scores',{}).get('ced_mini',{}).get('status')=='complete':continue
                        audio=path.parent/'audio.wav'
                        result=observer.process_wav(audio)
                        digest=sha256(audio)
                        if digest!=event['audio']['sha256']:raise ValueError('CED source audio checksum mismatch')
                        result.update(computed_at=utc_now(),computed_on='PC center observation',audio_sha256=digest)
                        atomic_json(out,result)
                        logging.info('CENTER_CED_READY %s %s',event['event_id'],result['top_label'])
                except Exception:logging.exception('Center CED observation failed; original evidence preserved')
                self.ced_stop.wait(5)
        self.ced_thread=threading.Thread(target=observe,name='center-ced-observer',daemon=True)
        self.ced_thread.start()

    def store(self, path, digest):
        event, files = decode_bundle(path)
        if event['device_id'] not in self.config['allowed_devices']:
            raise ValueError('Device is not allowed')
        if self.capture_start_after is not None and dt.datetime.fromisoformat(event['capture_start_utc']) < self.capture_start_after:
            # The user explicitly cleared the old collection. Acknowledge intentional
            # discard so an older outbox cannot repopulate the reset webpage forever.
            logging.info('OLD_SESSION_UPLOAD_DISCARDED %s', event['event_id'])
            return dict(ok=True, duplicate=False, ignored=True, discard_reason='before_recording_reset',
                event_id=event['event_id'], device_id=event['device_id'], bundle_sha256=digest,
                received_at=utc_now(), capture_start_after_utc=self.capture_start_after.isoformat())
        destination = self.inbox / event['device_id'] / event['event_id']
        with self.lock:
            if destination.exists():
                old = load_json(destination / 'receipt.json')
                if old['bundle_sha256'] != digest:
                    raise Conflict('Event ID exists with different content')
                return dict(ok=True, duplicate=True, **old)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = Path(tempfile.mkdtemp(prefix='event-', dir=self.incoming))
            try:
                for name, content in files.items():
                    with (temp / name).open('wb') as f:
                        f.write(content); f.flush()
                        import os
                        os.fsync(f.fileno())
                shutil.copyfile(path, temp / 'bundle.zip')
                receipt = dict(event_id=event['event_id'], device_id=event['device_id'],
                               bundle_sha256=digest, received_at=utc_now())
                atomic_json(temp / 'receipt.json', receipt)
                atomic_json(temp / 'review.json', dict(status='pending', updated_at=None, note=''))
                temp.rename(destination)
            finally:
                if temp.exists(): shutil.rmtree(temp)
            return dict(ok=True, duplicate=False, **receipt)

    def event_dir(self, device, eid):
        valid_id(device); valid_id(eid)
        p = self.inbox / device / eid
        if not (p / 'event.json').exists(): raise FileNotFoundError('Event not found')
        return p

    def list_events(self):
        rows = []
        for path in self.inbox.glob('*/*/event.json'):
            try:
                e = load_json(path)
                if self.list_policy is not None:
                    visible_policies = CLEAN_QWEN_POLICIES if self.list_policy in CLEAN_QWEN_POLICIES else (self.list_policy,)
                    if e.get('decision_policy') not in visible_policies: continue
                scores=dict(e.get('scores',{}))
                ced=path.parent/'ced_mini.json'
                if ced.exists():scores['ced_mini']=load_json(ced)
                rows.append(dict(event_id=e['event_id'], device_id=e['device_id'],
                    scene_name=e['scene_name'], capture_start_utc=e['capture_start_utc'],
                    capture_end_utc=e['capture_end_utc'], text=e.get('text', ''),
                    scores=scores, emotion=e.get('emotion'), decision_policy=e.get('decision_policy'),
                    timestamp_quality=e.get('timestamp_quality'),
                    received_at=load_json(path.parent / 'receipt.json')['received_at'],
                    review=load_json(path.parent / 'review.json')))
            except (OSError, ValueError, KeyError):
                logging.exception('Cannot read event %s', path)
        rows.sort(key=lambda x: x['capture_start_utc'], reverse=True)
        return dict(total=len(rows), events=rows[:200])

def handler_for(center):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup(); self.connection.settimeout(30)

        def log_message(self, fmt, *args):
            logging.info('%s %s', self.client_address[0], fmt % args)

        def reply(self, code, payload, content_type='application/json; charset=utf-8',headers=None):
            body = json.dumps(payload, ensure_ascii=False).encode() if not isinstance(payload, bytes) else payload
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.end_headers()
            self.wfile.write(body)

        def auth(self,device=False,csrf=False):
            if device:
                value=self.headers.get('Authorization','')
                if hmac.compare_digest(value,'Bearer '+center.config['token']):return True
            else:
                session=center.web_auth.get(self.headers.get('Cookie'))
                if session:
                    if csrf and (not self.same_origin() or not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf'])):
                        self.reply(403,dict(error='Request verification failed'));return False
                    self.web_session=session;return True
            self.reply(401,dict(error='请先登录' if not device else 'Device authentication required'));return False

        def same_origin(self):
            origin=self.headers.get('Origin')
            scheme='https' if isinstance(self.connection,ssl.SSLSocket) else 'http'
            return origin is None or origin==scheme+'://'+self.headers.get('Host','')

        def login(self):
            if not self.same_origin():return self.reply(403,dict(error='Request origin rejected'))
            try:
                length=int(self.headers.get('Content-Length','-1'))
                if not 0<length<=2048:return self.reply(413,dict(error='Invalid login body'))
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError('Incomplete body')
                body=json.loads(raw)
                if not isinstance(body,dict):raise ValueError('Invalid login body')
                if not center.config.get('web_password_hash'):
                    return self.reply(503,dict(error='请先在电脑运行 set_web_password.py 设置网页密码'))
                result=center.web_auth.login(body.get('password'),self.client_address[0])
                if result is None:return self.reply(401,dict(error='密码不正确'))
                sid,record=result
                return self.reply(200,dict(ok=True,csrf=record['csrf'],expires_at=record['expires']),headers={
                    'Set-Cookie':session_cookie(sid,isinstance(self.connection,ssl.SSLSocket),center.web_auth.lifetime)})
            except RateLimited as e:return self.reply(429,dict(error=str(e)),headers={'Retry-After':'60'})
            except (ValueError,TypeError):return self.reply(400,dict(error='Invalid login body'))

        def do_GET(self):
            route = urlparse(self.path)
            if route.path == '/':
                return self.reply(200, Path(__file__).with_name('dashboard.html').read_bytes(), 'text/html; charset=utf-8')
            if route.path == '/healthz': return self.reply(200, dict(ok=True, code_version='clean_v2_q8_deploy_20261117', accepted_decision_policies=list(RULES), list_decision_policy=center.list_policy, capture_start_after_utc=center.capture_start_after.isoformat() if center.capture_start_after is not None else None))
            if route.path == '/api/device-check':
                if not self.auth(device=True): return
                query = parse_qs(route.query)
                device = query.get('device', [''])[0]
                if device not in center.config['allowed_devices']:
                    return self.reply(403, dict(error='Device is not registered at this center'))
                return self.reply(200, dict(ok=True, device_id=device,
                    code_version='clean_v2_q8_deploy_20261117', accepted_decision_policies=list(RULES)))
            if not self.auth(): return
            try:
                if route.path=='/api/session':return self.reply(200,dict(ok=True,csrf=self.web_session['csrf'],expires_at=self.web_session['expires']))
                if route.path == '/api/events': return self.reply(200, center.list_events())
                if route.path == '/api/status':
                    return self.reply(200, dict(server_time=utc_now(), disk_free_gib=round(shutil.disk_usage(center.root).free / 1024**3, 2)))
                if route.path == '/api/file':
                    q = parse_qs(route.query)
                    p = center.event_dir(q['device'][0], q['event'][0])
                    name = q['name'][0]
                    if name not in set(FILES) | {'manifest.json', 'receipt.json', 'review.json', 'bundle.zip','ced_mini.json'}:
                        raise ValueError('Invalid filename')
                    kind = 'audio/wav' if name == 'audio.wav' else ('application/zip' if name == 'bundle.zip' else 'application/json; charset=utf-8')
                    if name == 'transcript.txt': kind = 'text/plain; charset=utf-8'
                    return self.reply(200, (p / name).read_bytes(), kind)
                self.reply(404, dict(error='Unknown route'))
            except FileNotFoundError: self.reply(404, dict(error='Not found'))
            except (KeyError, ValueError, IndexError): self.reply(400, dict(error='Invalid request'))

        def do_POST(self):
            path = urlparse(self.path).path
            if path=='/api/login':return self.login()
            if path=='/api/logout':
                if not self.auth(csrf=True):return
                center.web_auth.logout(self.headers.get('Cookie'))
                return self.reply(200,dict(ok=True),headers={'Set-Cookie':session_cookie('',isinstance(self.connection,ssl.SSLSocket),0)})
            if path not in ('/api/events', '/api/review'): return self.reply(404, dict(error='Unknown route'))
            if not self.auth(device=path=='/api/events',csrf=path=='/api/review'):return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                limit = center.config.get('max_upload_bytes', 8 * 1024 * 1024) if path == '/api/events' else 16384
                if not 0 < length <= limit: return self.reply(413, dict(error='Invalid content length'))
                if shutil.disk_usage(center.root).free < center.config.get('min_free_gib', 1) * 1024**3 + length:
                    return self.reply(503, dict(error='Center disk space low'))
                if path == '/api/review':
                    raw = self.rfile.read(length)
                    if len(raw) != length: raise ValueError('Incomplete body')
                    r = json.loads(raw)
                    p = center.event_dir(r['device_id'], r['event_id'])
                    if r['status'] not in ('pending', 'confirmed', 'dismissed', 'uncertain'):
                        raise ValueError('Invalid review status')
                    note = r.get('note', '')
                    if not isinstance(note, str) or len(note) > 2000: raise ValueError('Invalid note')
                    with center.lock:
                        old = load_json(p / 'review.json')
                        row = dict(status=r['status'], note=note, updated_at=utc_now())
                        with (p / 'review_history.jsonl').open('a', encoding='utf-8') as f:
                            f.write(json.dumps(dict(previous=old, current=row), ensure_ascii=False) + '\n')
                        atomic_json(p / 'review.json', row)
                    return self.reply(200, dict(ok=True, review=row))
                with tempfile.NamedTemporaryFile(dir=center.incoming, suffix='.zip', delete=False) as f:
                    temp = Path(f.name)
                    try:
                        remaining = length
                        while remaining:
                            data = self.rfile.read(min(65536, remaining))
                            if not data: raise ValueError('Incomplete upload')
                            f.write(data); remaining -= len(data)
                    except BaseException:
                        f.close(); temp.unlink(missing_ok=True); raise
                try:
                    digest = sha256(temp)
                    if not hmac.compare_digest(digest, self.headers.get('X-Bundle-SHA256', '')):
                        raise ValueError('Bundle SHA256 mismatch')
                    ack = center.store(temp, digest)
                    self.reply(200, ack)
                finally: temp.unlink(missing_ok=True)
            except Conflict as e: self.reply(409, dict(error=str(e)))
            except FileNotFoundError: self.reply(404, dict(error='Event not found'))
            except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, EOFError, wave.Error) as e:
                self.reply(400, dict(error=str(e)))
            except OSError:
                logging.exception('Storage/connection error')
                try: self.reply(503, dict(error='Storage or connection error; retry later'))
                except OSError: pass
    return Handler

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=Path(__file__).with_name('center_config.json'))
    p.add_argument('--list-policy', choices=list(RULES), help='Only list uploads produced by this decision policy')
    a = p.parse_args()
    config = load_json(a.config)
    if a.list_policy: config['list_decision_policy'] = a.list_policy
    if not config.get('web_password_hash'):
        raise SystemExit('Set the webpage password first: python set_web_password.py --config '+str(a.config))
    center = Center(config, a.config.resolve().parent)
    log = RotatingFileHandler(center.root / 'center.log', maxBytes=5*1024**2, backupCount=5, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[log, logging.StreamHandler()], format='%(asctime)s %(message)s')
    server = ThreadingHTTPServer((config['bind'], config['port']), handler_for(center))
    server.daemon_threads = True
    if config.get('tls_cert'):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config['tls_cert'], config['tls_key'])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    logging.info('CENTER_READY port=%s data=%s; dashboard http(s)://127.0.0.1:%s/', config['port'], center.root, config['port'])
    center.start_ced_worker()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        center.ced_stop.set()
        if center.ced_thread:center.ced_thread.join(timeout=10)
        server.server_close()

if __name__ == '__main__': main()
