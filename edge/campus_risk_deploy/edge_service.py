"""Continuous USB capture, disk-backed inference, and reliable candidate delivery."""
import argparse
import datetime as dt
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile
import urllib.request
from urllib.parse import urlparse
from decision import POLICY, QWEN_POLICY, QWEN_POLICY_0715, CLEAN_QWEN_POLICIES, rule_for
from common import (atomic_json, candidate, event_id, load_json, make_bundle,
                    sha256, utc_now, valid_id)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'capture'))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Center redirects are refused; configure its final URL')

def upload_one(bundle, config, opener=None):
    bundle = Path(bundle)
    with zipfile.ZipFile(bundle) as z:
        if not candidate(json.loads(z.read('inference.json'))):
            raise ValueError('Queued evidence does not satisfy the required serial policy; preserved for migration')
    digest = sha256(bundle)
    url = config['center_url'].rstrip('/') + '/api/events'
    request = urllib.request.Request(url, bundle.read_bytes(), method='POST', headers={
        'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/zip',
        'X-Bundle-SHA256': digest})
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=config.get('upload_timeout_seconds', 20)) as response:
        ack = json.loads(response.read(65536))
    if not (ack.get('ok') is True and ack.get('event_id') == bundle.stem and
            ack.get('device_id') == config['device_id'] and ack.get('bundle_sha256') == digest):
        raise ValueError('Center acknowledgement does not match the uploaded evidence')
    return ack

def model_versions(base, with_macbert, qwen_config=None):
    paths = dict(sensevoice='orangepi_cpu/model.int8.onnx', rbt3='orangepi_baseline/rbt3_finetuned/model.safetensors')
    if with_macbert: paths['macbert'] = 'orangepi_macbert/macbert_finetuned/model.safetensors'
    if qwen_config is not None:
        from qwen_runtime import DEFAULTS
        paths['qwen35_2b'] = dict(DEFAULTS, **qwen_config)['model_path']
    info = {name: dict(filename=rel, sha256=sha256(base / rel)) for name, rel in paths.items()}
    if qwen_config is not None:
        from qwen_runtime import MODEL_SHA256, COMMIT
        if info['qwen35_2b']['sha256'] != MODEL_SHA256: raise ValueError('Qwen model SHA256 mismatch')
        info['qwen35_2b'].update(model_revision='clean_v2_20261117_102215', quantization='Q8_0', runtime_commit=COMMIT,
            prompt_sha256=sha256(ROOT/'qwen_system_prompt.txt'), probability_available=False)
    # Include all existing deployment manifests, so tokenizer/config hashes and fusion coefficients travel too.
    for name, rel in (('sensevoice', 'orangepi_cpu/manifest.json'),
                      ('rbt3', 'orangepi_baseline/manifest.json'), ('macbert', 'orangepi_macbert/manifest.json')):
        if name in info and (base / rel).exists(): info[name]['deployment_manifest'] = load_json(base / rel)
    return info

def package_result(wav, outbox, config, versions):
    """Atomic bundle first, handled marker second; safe to repeat after a crash."""
    wav = Path(wav)
    result = load_json(wav.with_suffix('.result.json'))
    if result.get('status') not in ('complete', 'no_usable_text'): return False
    bundle = make_bundle(wav, outbox, config, versions) if candidate(result) else None
    atomic_json(wav.with_suffix('.handled.json'), dict(handled_at=utc_now(), candidate=candidate(result),
        event_id=bundle.stem if bundle else None, reason='candidate packaged' if bundle else 'not a review candidate'))
    if bundle: logging.info('EVENT_QUEUED %s', bundle.stem)
    else: logging.info('LOCAL_ONLY %s status=%s', wav.name, result['status'])
    return True

class Agent:
    def __init__(self, config):
        self.config = config
        self.base = Path(config['base_dir']).resolve()
        self.state = Path(config['state_dir']).resolve()
        self.recordings = self.state / 'recordings'
        self.outbox = self.state / 'outbox'
        self.logs = self.state / 'logs'
        for p in (self.recordings, self.outbox, self.logs): p.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.disk_paused = threading.Event()
        self.lock = threading.Lock()
        self.status = dict(started_at=utc_now(), capture='starting', inference='starting', upload='starting',
                           device_id=config['device_id'], scene_id=config['scene_id'])
        try:
            r = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], capture_output=True, text=True, timeout=3)
            self.time_sync = r.stdout.strip() if r.returncode == 0 else 'unknown'
        except (OSError, subprocess.TimeoutExpired): self.time_sync = 'unknown'

    def update(self, **values):
        with self.lock:
            self.status.update(values)
            self.status['updated_at'] = utc_now()
            atomic_json(self.state / 'status.json', self.status)

    def capture_loop(self):
        while not self.stop.is_set():
            if self.disk_paused.is_set():
                self.update(capture='paused_low_disk'); self.stop.wait(3); continue
            command = [sys.executable, '-u', str(ROOT/'capture/capture.py'), '--arm', '--seconds', '0',
                       '--port', self.config['port'], '--out-dir', str(self.recordings),
                       '--metrics-every', '30', '--clock-sync', self.time_sync]
            if self.config.get('capture_cpu_affinity'):
                command = ['taskset', '-c', self.config['capture_cpu_affinity']] + command
            process = None
            try:
                process = subprocess.Popen(command, cwd=str(self.base), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
                self.update(capture='connecting', capture_pid=process.pid)
                def read_output():
                    for line in process.stdout:
                        line = line.rstrip()
                        if line.startswith('VAD_STATUS'): continue
                        logging.info('CAPTURE %s', line)
                        if line.startswith('ARMED_READY'): self.update(capture='armed')
                        if line.startswith('AUTO_WAV_OK'): self.update(last_wav=line, last_wav_at=utc_now())
                reader = threading.Thread(target=read_output, daemon=True); reader.start()
                while process.poll() is None and not self.stop.wait(.5):
                    if self.disk_paused.is_set(): break
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate(); process.wait(timeout=5)
                reader.join(timeout=2)
                self.update(capture='stopped' if self.stop.is_set() else 'reconnecting', capture_exit=process.returncode)
            except Exception:
                logging.exception('Capture process failed; retrying')
                self.update(capture='error_retrying')
            finally:
                if process and process.poll() is None: process.kill(); process.wait()
                if process and process.stdout: process.stdout.close()
            self.stop.wait(5)

    def inference_loop(self):
        engine = None; versions = None
        try:
            while not self.stop.is_set():
                if engine is None:
                    try:
                        from auto_inference import Engine, PIPELINE_VERSION
                        self.update(inference='loading_models')
                        if self.config.get('decision_policy', POLICY) in CLEAN_QWEN_POLICIES:
                            from qwen_inference import Engine, PIPELINE_VERSION
                            versions = model_versions(self.base, False, self.config.get('qwen', {}))
                            engine = Engine(base=self.base, qwen_config=self.config.get('qwen', {}), log_dir=self.logs,
                                fusion_threshold=self.config.get('rbt3_fusion_threshold', .5))
                            PIPELINE_VERSION = engine.pipeline_version
                        else:
                            versions = model_versions(self.base, self.config.get('with_macbert', True))
                            engine = Engine(base=self.base, with_macbert=self.config.get('with_macbert', True))
                        atomic_json(self.state/'model_versions.json', versions)
                        self.update(inference='ready')
                    except Exception:
                        logging.exception('Model initialization failed; capture and upload remain independent')
                        self.update(inference='model_error_retrying'); self.stop.wait(15); continue
                found = False
                for wav in sorted(self.recordings.glob('usb_auto_*.wav')):
                    if self.stop.is_set(): break
                    if wav.with_suffix('.handled.json').exists() or not wav.with_suffix('.json').exists(): continue
                    # A WAV is not inference-ready until its complete capture metadata exists.
                    found = True
                    failure_path = wav.with_suffix('.failure.json')
                    failures = load_json(failure_path) if failure_path.exists() else {}
                    if failures.get('retry_after', 0) > time.time(): continue
                    try:
                        result_path = wav.with_suffix('.result.json')
                        old = load_json(result_path) if result_path.exists() else {}
                        if old.get('pipeline_version')!=PIPELINE_VERSION or old.get('status') not in ('complete', 'no_usable_text'):
                            self.update(inference='processing', active_wav=wav.name)
                            engine.process(wav)
                        package_result(wav, self.outbox, self.config, versions)
                        failure_path.unlink(missing_ok=True)
                        self.update(inference='ready', active_wav=None, last_processed=wav.name)
                    except Exception as e:
                        count = failures.get('attempts', 0) + 1
                        atomic_json(failure_path, dict(attempts=count, error=str(e), updated_at=utc_now(),
                                                      retry_after=time.time()+min(300, 5*2**min(count, 6))))
                        logging.exception('Inference/package failed for %s; raw audio preserved', wav.name)
                        self.update(inference='error_retrying', last_inference_error=str(e))
                self.stop.wait(1 if found else 2)
        finally:
            if engine is not None and hasattr(engine, 'close'): engine.close()

    def upload_loop(self):
        while not self.stop.is_set():
            if self.config.get('upload_enabled', True) is not True:
                self.update(upload='disabled_trial'); self.stop.wait(2); continue
            pending = 0
            for bundle in sorted(self.outbox.glob('*.zip')):
                if self.stop.is_set(): break
                ack_path = bundle.with_suffix('.ack.json')
                if ack_path.exists(): continue
                pending += 1
                retry_path = bundle.with_suffix('.retry.json')
                state = {}
                try:
                    state = load_json(retry_path) if retry_path.exists() else {}
                    if state.get('retry_after', 0) > time.time(): continue
                    self.update(upload='sending', active_event=bundle.stem)
                    ack = upload_one(bundle, self.config)
                    atomic_json(ack_path, dict(**ack, acknowledged_at=utc_now()))
                    retry_path.unlink(missing_ok=True)
                    logging.info('UPLOAD_ACK %s duplicate=%s', bundle.stem, ack.get('duplicate'))
                    self.update(upload='ready', active_event=None, last_uploaded=bundle.stem)
                except Exception as e:
                    count = state.get('attempts', 0)+1
                    delay = min(self.config.get('retry_max_seconds', 60), 2**min(count, 10))
                    atomic_json(retry_path, dict(attempts=count, last_error=str(e),
                        updated_at=utc_now(), retry_after=time.time()+delay))
                    logging.warning('UPLOAD_RETRY %s in %ss: %s', bundle.stem, delay, e)
                    self.update(upload='retry_wait', last_upload_error=str(e))
            self.update(pending_uploads=pending)
            self.stop.wait(2)

    def housekeeping(self):
        now = time.time()
        for wav in self.recordings.glob('usb_auto_*.wav'):
            handled_path = wav.with_suffix('.handled.json')
            if not handled_path.exists(): continue
            try:
                handled = load_json(handled_path)
                if handled['candidate']:
                    eid = valid_id(handled['event_id'])
                    ack_path = self.outbox/(eid+'.ack.json')
                    if not ack_path.exists(): continue  # Never expire unacknowledged evidence.
                    ack = load_json(ack_path)
                    stamp = dt.datetime.fromisoformat(ack['acknowledged_at']).timestamp()
                    expire = self.config.get('acknowledged_retention_days', 7)*86400
                else:
                    stamp = dt.datetime.fromisoformat(handled['handled_at']).timestamp()
                    expire = self.config.get('ordinary_retention_hours', 24)*3600
                if expire <= 0 or now-stamp < expire: continue  # 0 means retain indefinitely.
                for suffix in ('.wav', '.json', '.result.json', '.failure.json'):
                    wav.with_suffix(suffix).unlink(missing_ok=True)
                # The acknowledged ZIP may also expire. Preserve the tiny ACK as a delivery ledger.
                if handled['candidate']:
                    (self.outbox/(eid+'.zip')).unlink(missing_ok=True)
                handled_path.unlink(missing_ok=True)
                logging.info('RETENTION_REMOVED %s candidate=%s', wav.name, handled['candidate'])
            except (OSError, ValueError, KeyError): logging.exception('Retention check failed: %s', wav.name)

    def run(self, upload_only=False):
        import fcntl  # The edge runs on Linux; the center runs on Windows or Linux.
        with (self.state/'agent.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise SystemExit('Another edge agent already owns this state directory')
            actions = [self.upload_loop] if upload_only else [self.capture_loop, self.inference_loop, self.upload_loop]
            threads = [threading.Thread(target=f, name=f.__name__) for f in actions]
            for t in threads: t.start()
            last_cleanup = 0
            try:
                while not self.stop.wait(2):
                    free = shutil.disk_usage(self.state).free/1024**3
                    if free < self.config.get('min_free_gib', 1): self.disk_paused.set()
                    elif free >= self.config.get('resume_free_gib', 2): self.disk_paused.clear()
                    if time.monotonic()-last_cleanup >= self.config.get('housekeeping_seconds', 60):
                        self.housekeeping(); last_cleanup=time.monotonic()
                    self.update(disk_free_gib=round(free, 2), capture_paused_low_disk=self.disk_paused.is_set(),
                                thread_health={t.name:t.is_alive() for t in threads})
                    if not all(t.is_alive() for t in threads):
                        raise RuntimeError('An agent worker stopped; supervisor will restart the service')
            finally:
                self.stop.set()
                for t in threads: t.join()
                self.update(service='stopped')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'edge_config.json')
    p.add_argument('--run-seconds', type=int, default=0, help='Stop this trial after the specified duration')
    p.add_argument('--upload-only', action='store_true', help='Retry existing outbox without microphone/models')
    a = p.parse_args(); c = load_json(a.config)
    if a.run_seconds < 0: p.error('run-seconds must be nonnegative')
    valid_id(c['device_id']); valid_id(c['scene_id'])
    if len(c['device_id']) > 60: p.error('device-id must be at most 60 characters')
    u = urlparse(c['center_url'])
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.path not in ('','/') or u.query or u.fragment:
        p.error('Invalid center URL')
    if len(c.get('token','')) < 32: p.error('Missing token; run init_config.py')
    rule_for(c.get('decision_policy', POLICY))
    if c.get('decision_policy') in CLEAN_QWEN_POLICIES:
        expected_gate = .715 if c['decision_policy'] == QWEN_POLICY_0715 else .5
        if float(c.get('rbt3_fusion_threshold', .5)) != expected_gate:
            p.error('Decision policy and RBT3 fusion threshold disagree')
    if c.get('decision_policy', POLICY) == POLICY and c.get('with_macbert') is not True:
        p.error('MacBERT policy requires with_macbert=true')
    if c.get('resume_free_gib',2) <= c.get('min_free_gib',1): p.error('resume_free_gib must exceed min_free_gib')
    agent = Agent(c)
    handler = RotatingFileHandler(agent.logs/'edge.log', maxBytes=10*1024**2, backupCount=5, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()], format='%(asctime)s %(levelname)s %(message)s')
    def halt(*_): agent.stop.set()
    signal.signal(signal.SIGTERM, halt); signal.signal(signal.SIGINT, halt)
    agent.update(service='running', time_sync=agent.time_sync)
    logging.info('EDGE_READY %s scene=%s center=%s', c['device_id'], c['scene_id'], c['center_url'])
    timer = threading.Timer(a.run_seconds, agent.stop.set) if a.run_seconds else None
    if timer: timer.start()
    try: agent.run(upload_only=a.upload_only)
    finally:
        if timer: timer.cancel()

if __name__ == '__main__': main()
