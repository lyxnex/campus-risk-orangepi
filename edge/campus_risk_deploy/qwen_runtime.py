"""Owned localhost llama.cpp CPU process, strict boolean output, no fallback label."""
import atexit
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request

MODEL_SHA256 = '415e7a97c4b68ab42e6494d4c17bef887217632e644a206ee61408f2e0bcca98'
COMMIT = 'aa75ff3239f9cc675b081697deda9e1b112345c6'
DEFAULTS = dict(server_path='qwen35_cpu_test/runtime/llama.cpp-aa75ff323/build-arm64/bin/llama-server',
                model_path='qwen35_cpu_test/clean_v2_20261117_102215/qwen35_2b_finetuned-Q8_0.gguf',
                threads=2, context=2048, port=18772, timeout_seconds=600, load_timeout_seconds=180)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''): value.update(block)
    return value.hexdigest()


def parse_response(response):
    choice = response['choices'][0]
    raw = choice['message'].get('content')
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Qwen did not stop normally: ' + repr(choice))
    if not isinstance(raw, str): raise ValueError('Qwen returned non-text content')
    try: value = json.loads(raw.strip())
    except ValueError as exc: raise ValueError('Qwen output is not JSON: ' + raw) from exc
    if not isinstance(value, dict) or set(value) != {'risk'} or type(value['risk']) is not bool:
        raise ValueError('Qwen must return exactly one JSON boolean: ' + raw)
    return value['risk'], raw


class QwenRuntime:
    def __init__(self, base, config, log_dir):
        self.base = Path(base)
        self.config = dict(DEFAULTS, **config)
        self.model = self.base / self.config['model_path']
        self.server = self.base / self.config['server_path']
        self.prompt_file = Path(__file__).with_name('qwen_system_prompt.txt')
        self.prompt = self.prompt_file.read_text(encoding='utf-8')
        self.prompt_sha256 = digest(self.prompt_file)
        self.port = int(self.config['port'])
        if not 1024 <= self.port <= 65535: raise ValueError('Invalid localhost Qwen port')
        if not 1 <= int(self.config['threads']) <= 4: raise ValueError('Use 1 to 4 CPU threads')
        if int(self.config['context']) != 2048: raise ValueError('Validated Q8 protocol uses context 2048')
        self.log_dir = Path(log_dir); self.log_dir.mkdir(parents=True, exist_ok=True)
        self.process = None; self.log = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        atexit.register(self.close)
        self.start()

    def api(self, path, payload=None, timeout=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}'+path, data=data,
                                         headers={'Content-Type':'application/json'})
        with self.opener.open(request, timeout=timeout or self.config['timeout_seconds']) as response:
            return json.load(response)

    def start(self):
        if self.process and self.process.poll() is None: return
        self.close()
        self.check_resources()
        if not self.server.is_file(): raise FileNotFoundError(self.server)
        if digest(self.model) != MODEL_SHA256: raise ValueError('Qwen model SHA256 differs from validated Q8')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', self.port))
        command = [str(self.server), '-m', str(self.model), '--host', '127.0.0.1', '--port', str(self.port),
                   '-c', str(self.config['context']), '-t', str(self.config['threads']),
                   '-tb', str(self.config['threads']), '-b', '64', '-ub', '64',
                   '-ngl', '0', '-np', '1', '--cache-ram', '256',
                   '--reasoning', 'off', '--chat-template-kwargs', '{"enable_thinking":false}', '--no-context-shift']
        self.log = (self.log_dir/'qwen-server.log').open('a', encoding='utf-8')
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=self.log,
                                            stderr=subprocess.STDOUT, start_new_session=True)
            began = time.monotonic()
            while True:
                self.check_resources()
                if self.process.poll() is not None: raise RuntimeError('Qwen server exited; see qwen-server.log')
                try:
                    if self.api('/health', timeout=2).get('status') == 'ok': break
                except Exception: pass
                if time.monotonic()-began > self.config['load_timeout_seconds']:
                    raise TimeoutError('Qwen model load timed out')
                time.sleep(.5)
        except BaseException:
            self.close()
            raise

    def classify(self, text):
        began = time.perf_counter()
        cooling_seconds = 0
        try:
            self.check_resources()
            self.start()
            cooling_seconds = self.cool_until_ready()
            response = self.monitored_request(dict(
                messages=[{'role':'system','content':self.prompt},
                          {'role':'user','content':'待分析转写：\n'+text}],
                temperature=0, top_p=1, seed=42, max_tokens=32, stream=False,
                cache_prompt=True, chat_template_kwargs={'enable_thinking':False}))
            risk, raw = parse_response(response)
            self.check_resources()
        except BaseException:
            # A timeout may leave inference running. Dispose only our process before retrying.
            self.close()
            raise
        return dict(status='complete', risk=risk, review_candidate=risk, format_valid=True,
                    raw_output=raw, seconds=time.perf_counter()-began, finish_reason='stop',
                    cooling_seconds=cooling_seconds,
                    usage=response.get('usage'), prompt_sha256=self.prompt_sha256,
                    model_sha256=MODEL_SHA256, model_revision='clean_v2_20261117_102215', quantization='Q8_0', backend='llama.cpp CPU',
                    runtime_commit=COMMIT, probability_available=False)

    def cool_until_ready(self):
        started = time.monotonic()
        while True:
            sample = self.check_resources()
            if max(sample['temperatures_c']) <= 45:
                return round(time.monotonic()-started, 3)
            if time.monotonic()-started >= 120:
                raise RuntimeError('Did not cool to 45 C within 120 seconds; audio retained')
            time.sleep(2)

    def monitored_request(self, payload):
        done = threading.Event()
        errors = []
        def watch():
            while not done.wait(.5):
                try: self.check_resources()
                except Exception as exc:
                    errors.append(exc)
                    self.close()  # Only our Popen process; interrupts the owned HTTP inference.
                    return
        monitor = threading.Thread(target=watch, name='qwen-resource-guard', daemon=True)
        monitor.start()
        try:
            try: response = self.api('/v1/chat/completions', payload)
            except Exception:
                if errors: raise errors[0]
                raise
        finally:
            done.set()
            monitor.join()  # Finish owned-process cleanup before any subsequent start().
        if errors: raise errors[0]
        return response

    def check_resources(self):
        temperatures = []
        for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
            try: temperature=int((zone/'temp').read_text())/1000
            except (OSError,ValueError): continue
            temperatures.append(temperature)
            if temperature >= 80: raise RuntimeError('Qwen paused at conservative 80 C threshold; audio retained')
        if not temperatures: raise RuntimeError('No temperature sensor available; audio retained')
        memory=Path('/proc/meminfo')
        available = None
        for line in memory.read_text().splitlines():
            if line.startswith('MemAvailable:'): available = int(line.split()[1])/1024
        if available is None: raise RuntimeError('MemAvailable missing; audio retained')
        if available < 700: raise RuntimeError('Qwen paused below 700 MiB available memory; audio retained')
        return dict(temperatures_c=temperatures, available_memory_mib=available)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        self.process = None
        if self.log: self.log.close(); self.log = None
