"""Event contract shared by the edge agent and the temporary PC center."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile
from decision import POLICY, RULE, serial_candidate, rule_for

SCHEMA = 'campus-risk-event-v1'
FILES = ('event.json', 'audio.wav', 'capture.json', 'inference.json', 'transcript.txt')
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')

def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')

def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def valid_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid device/event identifier')
    return value

def event_id(device_id, scene_id, wav):
    valid_id(device_id)
    key = device_id + '\n' + scene_id + '\n' + Path(wav).name
    return device_id + '-' + hashlib.sha256(key.encode()).hexdigest()[:32]

def candidate(result):
    return serial_candidate(result)

def make_bundle(wav, outbox, device, models):
    wav = Path(wav)
    result = load_json(wav.with_suffix('.result.json'))
    if not candidate(result):
        return None
    capture = load_json(wav.with_suffix('.json'))
    if not capture.get('capture_start_utc') or not capture.get('capture_end_utc'):
        raise ValueError('Missing capture timestamps; do not upload old untimed recordings')
    eid = event_id(device['device_id'], device['scene_id'], wav)
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    target = outbox / (eid + '.zip')
    if target.exists():
        return target
    audio_hash = sha256(wav)
    event = dict(schema=SCHEMA, event_id=eid, device_id=device['device_id'],
                 scene_id=device['scene_id'], scene_name=device['scene_name'],
                 candidate=True, decision_policy=result['decision_policy'],
                 decision_rule=rule_for(result['decision_policy']),decision=result.get('decision'),
                 meaning='待人工复核的风险语言线索，不是事件事实认定',
                 capture_start_utc=capture['capture_start_utc'],
                 capture_end_utc=capture['capture_end_utc'],
                 capture_start_local=capture.get('capture_start_local'),
                 capture_end_local=capture.get('capture_end_local'),
                 segment_start_received_at=capture.get('segment_start_received_at'),
                 audio_saved_at=capture.get('audio_saved_at'),
                 inference_started_at=result.get('inference_started_at'),
                 inference_completed_at=result.get('completed_at'),
                 packaged_at=utc_now(), timestamp_quality=capture.get('timestamp_quality'),
                 time_sync=capture.get('time_sync'),
                 audio=dict(filename='audio.wav', sha256=audio_hash, sample_rate=16000,
                            channels=2, sample_bits=16, duration_seconds=15,
                            inference_channel=result.get('channel', 0)),
                 source=dict(session=capture.get('session'), filename=wav.name,
                             firmware=capture.get('firmware'), protocol=capture.get('protocol'),
                             usb_port=capture.get('usb_port'),
                             start_sample=capture.get('start_sample'),
                             overlap_with_previous_verified=capture.get('overlap_with_previous_verified')),
                 pipeline_version=result.get('pipeline_version'), model_versions=models,
                 scores=result.get('models', {}), emotion=result.get('emotion'),
                 audio_event=result.get('audio_event'),
                 text=result.get('text', ''))
    values = {'event.json': json.dumps(event, ensure_ascii=False, indent=2).encode(),
              'capture.json': wav.with_suffix('.json').read_bytes(),
              'inference.json': wav.with_suffix('.result.json').read_bytes(),
              'transcript.txt': result.get('text', '').encode('utf-8')}
    values['audio.wav'] = wav.read_bytes()
    manifest = dict(schema=SCHEMA, event_id=eid,
                    files={name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                           for name, data in values.items()})
    temp = target.with_suffix('.zip.tmp')
    with temp.open('wb') as f:
        with zipfile.ZipFile(f, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in values.items():
                z.writestr(name, data)
            z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, target)
    return target
