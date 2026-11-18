"""Read-only export of deployed source, redacted settings, dependencies and optional assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import time

REL='qwen35_clean_v2_deploy_20261117'
Q8_SHA='415e7a97c4b68ab42e6494d4c17bef887217632e644a206ee61408f2e0bcca98'
CODE=['common.py','decision.py','edge_service.py','qwen_runtime.py','qwen_system_prompt.txt','check_edge.py',
      'capture/capture.py','capture/auto_protocol.py','capture/reliable_usb.py','capture/host_health.py',
      'capture/auto_inference.py','capture/qwen_inference.py']
SETTINGS=['decision_policy','rbt3_fusion_threshold','with_macbert','with_ced','upload_enabled',
          'capture_cpu_affinity','upload_timeout_seconds','retry_max_seconds','min_free_gib',
          'resume_free_gib','ordinary_retention_hours','acknowledged_retention_days','housekeeping_seconds']
QWEN=['server_path','model_path','threads','context','port','timeout_seconds','load_timeout_seconds']


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def inside(base, relative):
    path=base/relative
    resolved=path.resolve()
    if not resolved.is_relative_to(base.resolve()) or path.is_symlink():
        raise ValueError('Asset path outside project or symlink: '+str(relative))
    return path


def redacted_config(config):
    result={key:config[key] for key in SETTINGS if key in config}
    result.update(device_id='REPLACE_DEVICE_ID',scene_id='REPLACE_SCENE_ID',scene_name='REPLACE_SCENE_NAME',
        center_url='http://CENTER_HOST:8765',token='REPLACE_WITH_PRIVATE_SITE_TOKEN',
        base_dir='/home/orangepi/campus-risk',state_dir='/home/orangepi/campus-risk/deployment_data_SITE_DEVICE',
        port='/dev/serial/by-id/REPLACE_WITH_THIS_MICROPHONE_ESP_USB',
        qwen={key:config.get('qwen',{})[key] for key in QWEN if key in config.get('qwen',{})})
    return result


def json_write(path, data):
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,default=Path('/home/orangepi/campus-risk'))
    p.add_argument('--with-assets',action='store_true',help='Also copy weights/runtime to separate local release files')
    args=p.parse_args();base=args.base.resolve()
    config=json.loads((base/REL/'deployment_config.json').read_text('utf-8'))
    if config.get('decision_policy')!='rbt3_fusion0715_then_qwen35_2b_clean_v2_q8' or config.get('rbt3_fusion_threshold')!=.715:
        raise ValueError('Expected current 0.715 deployment; inspect before exporting another version')
    directory=base/'migration_exports'/('export_'+str(time.time_ns()))
    directory.mkdir(parents=True,mode=0o700)
    source=directory/'source';source.mkdir()
    identities={}
    for name in CODE:
        old=inside(base,REL+'/campus_risk_deploy/'+name)
        target=source/'edge/campus_risk_deploy'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        before=sha(old);shutil.copyfile(old,target)
        if sha(target)!=before or sha(old)!=before:raise RuntimeError('Code changed during snapshot: '+name)
        identities['edge/campus_risk_deploy/'+name]={'sha256':before,'bytes':target.stat().st_size}
    json_write(source/'edge.redacted.example.json',redacted_config(config))
    python=base/'.venv/bin/python'
    packages=json.loads(subprocess.check_output([str(python),'-m','pip','list','--format=json','--disable-pip-version-check'],text=True,timeout=60))
    lines=[]
    for package in sorted(packages,key=lambda x:x['name'].lower()):
        name=package['name'];version=package['version']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+',version):
            raise ValueError('Nonstandard dependency metadata; inspect locally')
        lines.append(name+'=='+version)
    (source/'requirements.edge.lock.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    system=json.loads(subprocess.check_output([str(python),'-c',
        'import json,platform,sys;print(json.dumps(dict(python=sys.version,machine=platform.machine(),release=platform.release(),system=platform.system())))'],text=True,timeout=15))
    # Model/tokenizer names are allowlisted. Never walk recordings, state, flash backups or .venv.
    groups={
      'sensevoice':['orangepi_cpu/model.int8.onnx','orangepi_cpu/tokens.txt','orangepi_cpu/manifest.json'],
      'rbt3':['orangepi_baseline/manifest.json'],
      'qwen':[config['qwen']['model_path']],
      'runtime':[config['qwen']['server_path']]}
    rbt=base/'orangepi_baseline/rbt3_finetuned'
    for name in ('model.safetensors','config.json','tokenizer.json','tokenizer_config.json',
                 'special_tokens_map.json','vocab.txt','added_tokens.json'):
        if (rbt/name).is_file():groups['rbt3'].append('orangepi_baseline/rbt3_finetuned/'+name)
    if not (rbt/'model.safetensors').is_file() or not (rbt/'config.json').is_file():raise ValueError('Incomplete RBT3 assets')
    server=inside(base,config['qwen']['server_path'])
    linked=subprocess.run(['ldd',str(server)],capture_output=True,text=True,check=True,timeout=15).stdout
    if 'not found' in linked:raise ValueError('Runtime dependency missing')
    for linked_path in re.findall(r'(?:=>\s+|^\s*)(/[^\s]+)',linked,re.MULTILINE):
        path=Path(linked_path).resolve()
        if path.is_relative_to(base):
            relative=path.relative_to(base).as_posix()
            if relative not in groups['runtime']:groups['runtime'].append(relative)
    for name in ('LICENSE','LICENSE.txt'):
        location='qwen35_cpu_test/runtime/llama.cpp-aa75ff323/'+name
        if (base/location).is_file():groups['runtime'].append(location)
    assets=[]
    for group,files in groups.items():
        for name in files:
            file=inside(base,name)
            digest=sha(file)
            if group=='qwen' and (digest!=Q8_SHA or file.stat().st_size!=2012012128):
                raise ValueError('Current Q8 identity differs')
            assets.append(dict(group=group,path=name,bytes=file.stat().st_size,sha256=digest))
    json_write(source/'assets.lock.json',dict(files=assets,qwen_sha256=Q8_SHA))
    json_write(source/'snapshot.json',dict(status='read_only_snapshot',source_files=identities,
        platform=system,recordings_included=False,private_pairing_included=False,
        runtime_system_dependencies=linked.splitlines()))
    code_archive=directory/'board-source-snapshot.tar.gz'
    with tarfile.open(code_archive,'w:gz',compresslevel=1) as out:out.add(source,arcname='board-source',recursive=True)
    attachments=[]
    if args.with_assets:
        total=sum(row['bytes'] for row in assets)
        if shutil.disk_usage(directory).free<total+512*1024**2:raise RuntimeError('Not enough room for release attachments; snapshot retained')
        for group,names in groups.items():
            artifact=directory/('qwen35_2b_finetuned-Q8_0.gguf' if group=='qwen' else group+'-assets.tar')
            print('EXPORTING_RELEASE_ASSET',group,flush=True)
            if group=='qwen':shutil.copyfile(inside(base,names[0]),artifact)
            else:
                # Uncompressed TAR avoids CPU-heavy compression while capture is running.
                with tarfile.open(artifact,'w') as out:
                    for name in names:out.add(inside(base,name),arcname=name,recursive=False)
            digest=sha(artifact)
            if group=='qwen' and digest!=Q8_SHA:raise RuntimeError('Q8 copy checksum mismatch')
            attachments.append(dict(name=artifact.name,bytes=artifact.stat().st_size,sha256=digest))
        json_write(directory/'release-attachments.json',dict(files=attachments))
    print('BOARD_EXPORT_COMPLETE',json.dumps(dict(directory=str(directory),source_archive=str(code_archive),
        source_sha256=sha(code_archive),asset_files=len(assets),release_attachments=len(attachments),
        recordings_included=False,private_pairing_included=False,service_restarted=False)),flush=True)


if __name__=='__main__':main()
