"""Show service state without displaying the shared token."""
import argparse
from pathlib import Path
from common import load_json
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--config',type=Path,default=Path(__file__).with_name('edge_config.json'))
a=p.parse_args(); c=load_json(a.config); root=Path(c['state_dir'])
print('DECISION_POLICY',c.get('decision_policy'))
print('RBT3_FUSION_THRESHOLD',c.get('rbt3_fusion_threshold',.5))
print('UPLOAD_ENABLED',c.get('upload_enabled',True))
print('CENTER_URL',c['center_url'])
print('STATUS')
if (root/'status.json').exists():
    import json
    print(json.dumps(load_json(root/'status.json'),ensure_ascii=False,indent=2))
else: print('No agent status yet')
bundles=list((root/'outbox').glob('*.zip'))
pending=[p for p in bundles if not p.with_suffix('.ack.json').exists()]
print('PENDING_UPLOADS',len(pending))
recordings=list((root/'recordings').glob('usb_auto_*.wav'))
print('RECORDINGS',len(recordings))
print('PENDING_INFERENCE',sum(not path.with_suffix('.handled.json').exists() for path in recordings))
print('INFERENCE_FAILURES',len(list((root/'recordings').glob('*.failure.json'))))
for path in pending[:20]:
    retry=path.with_suffix('.retry.json')
    print(path.name, load_json(retry) if retry.exists() else 'waiting')
