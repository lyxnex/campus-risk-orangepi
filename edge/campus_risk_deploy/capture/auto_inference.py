"""Resident CPU inference and a bounded queue for complete automatic USB WAVs."""
from contextlib import contextmanager
import datetime
import json
import math
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import wave
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from decision import POLICY, RULE
PIPELINE_VERSION='usb_inference_v3_serial'

def usable_text(text):
    return any(c.isalnum() for c in text)

def classify_windows(text,tok,model,torch):
    if not tok.is_fast:raise ValueError('Fast tokenizer required for complete overflow windows')
    tokens=len(tok(text,add_special_tokens=True)['input_ids'])
    t=time.perf_counter()
    encoded=tok(text,padding='max_length',truncation=True,max_length=64,stride=16,
                return_overflowing_tokens=True,return_tensors='pt')
    inputs={k:v for k,v in encoded.items() if k in ('input_ids','attention_mask','token_type_ids')}
    with torch.inference_mode():
        scores=torch.softmax(model(**inputs).logits,dim=-1)[:,1].tolist()
    if not scores:raise ValueError('Tokenizer returned no classification windows')
    return dict(semantic_score=max(scores),seconds=time.perf_counter()-t,input_tokens=tokens,
                max_length=64,truncated=False,window_count=len(scores),window_overlap_tokens=16,
                window_semantic_scores=scores,aggregation='max_window',
                long_text_aggregation_calibrated=False if len(scores)>1 else None,threshold=0.5)

def result_path(wav):
    return Path(wav).with_suffix('.result.json')

def write_json(path,data):
    path=Path(path)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)

class Engine:
    def __init__(self,base=Path('/home/orangepi/campus-risk'),with_macbert=True):
        if not with_macbert:raise ValueError('Serial policy requires both RBT3 and MacBERT')
        import numpy as np
        import sherpa_onnx
        import torch
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        self.np=np;self.torch=torch;self.models={}
        torch.set_num_threads(4)
        print('Loading resident SenseVoice...',flush=True)
        self.asr=sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(base/'orangepi_cpu/model.int8.onnx'),tokens=str(base/'orangepi_cpu/tokens.txt'),
            language='zh',use_itn=True,num_threads=4,provider='cpu')
        choices=[('rbt3','orangepi_baseline/rbt3_finetuned')]
        if with_macbert:choices.append(('macbert','orangepi_macbert/macbert_finetuned'))
        for name,rel in choices:
            print('Loading resident',name,flush=True)
            tok=AutoTokenizer.from_pretrained(str(base/rel),local_files_only=True)
            model=AutoModelForSequenceClassification.from_pretrained(
                str(base/rel),local_files_only=True,use_safetensors=True).to('cpu').eval()
            if model.config.num_labels!=2:raise ValueError('Expected two labels, risk at index 1')
            self.models[name]=(tok,model)
        self.coeff=json.loads((base/'orangepi_baseline/manifest.json').read_text())['fusion_coefficients']
        print('RESIDENT_MODELS_READY',list(self.models),flush=True)

    def classify_text(self,text,emotion):
        tok,model=self.models['rbt3']
        first=classify_windows(text,tok,model,self.torch)
        fusion=1/(1+math.exp(-(self.coeff[0]+self.coeff[1]*first['semantic_score']+
                               self.coeff[2]*int(emotion=='<|ANGRY|>'))))
        first.update(status='complete',fusion_score=fusion,threshold=.5,
                     candidate_rule='original RBT3 emotion fusion >= 0.5',review_candidate=fusion>=.5)
        if first['review_candidate']:
            tok,model=self.models['macbert']
            second=classify_windows(text,tok,model,self.torch)
            second.update(status='complete',threshold=.5,candidate_rule='MacBERT semantic score >= 0.5 (serial second stage)',
                          review_candidate=second['semantic_score']>=.5)
            reason='both_stages_passed' if second['review_candidate'] else 'macbert_rejected'
        else:
            second=dict(status='skipped',reason='rbt3_fusion_below_threshold',seconds=0,
                        semantic_score=None,threshold=.5,review_candidate=None)
            reason='rbt3_rejected'
        accepted=first['review_candidate'] and second['review_candidate'] is True
        return dict(models=dict(rbt3=first,macbert=second),review_candidate=accepted,
                    decision_policy=POLICY,decision_rule=RULE,
                    decision=dict(stage1_pass=first['review_candidate'],stage2_executed=second['status']=='complete',
                                  stage2_pass=second['review_candidate'],reason=reason))

    def process(self,wav):
        import resource
        wav=Path(wav)
        with wave.open(str(wav),'rb') as w:
            if (w.getframerate(),w.getsampwidth(),w.getcomptype())!=(16000,2,'NONE'):
                raise ValueError('Expected uncompressed 16kHz PCM16')
            channels=w.getnchannels()
            audio=self.np.frombuffer(w.readframes(w.getnframes()),dtype='<i2')
            audio=audio.reshape(-1,channels)[:,0].astype(self.np.float32)/32768
        if not len(audio):raise ValueError('Empty audio')
        if len(audio)!=240000:raise ValueError('Expected a complete 15-second automatic segment')
        inference_started_at=datetime.datetime.now().astimezone().isoformat()
        t=time.perf_counter();stream=self.asr.create_stream()
        stream.accept_waveform(16000,audio);self.asr.decode_stream(stream)
        pipeline_version=getattr(self,'pipeline_version',PIPELINE_VERSION)
        result=dict(status='complete',pipeline_version=pipeline_version,file=str(wav.resolve()),channel=0,audio_seconds=len(audio)/16000,
                    text=stream.result.text,emotion=stream.result.emotion,
                    audio_event=getattr(stream.result,'event',None),
                    asr_seconds=time.perf_counter()-t,models={},
                    output_meaning='candidate for human review, not an incident determination',
                    completed_at=datetime.datetime.now().astimezone().isoformat())
        if usable_text(result['text']):
            result.update(self.classify_text(result['text'],result['emotion']))
        else:
            result.update(status='no_usable_text',review_candidate=None,
                          decision_policy=getattr(self,'policy',POLICY),decision_rule=getattr(self,'rule',RULE),
                          reason='ASR returned no letters or digits; text classification skipped')
        result['inference_started_at']=inference_started_at
        result['completed_at']=datetime.datetime.now().astimezone().isoformat()
        result['inference_seconds']=result['asr_seconds']+sum(x['seconds'] for x in result['models'].values())
        result['peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
        out=result_path(wav)
        if out.exists():
            old=json.loads(out.read_text(encoding='utf-8'))
            if old.get('pipeline_version')!=pipeline_version and old.get('status') in ('complete','no_speech_text','no_usable_text'):
                stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                shutil.copy2(out,out.with_name(out.stem+'.before_serial_'+stamp+out.suffix))
        write_json(out,result)
        print('RESULT_READY',json.dumps(result,ensure_ascii=False),flush=True)
        return result

class InferenceQueue:
    def __init__(self,engine,capacity=4):
        self.engine=engine
        self.queue=queue.Queue(maxsize=capacity)
        self.submitted=0;self.backlog=0;self.processed=0;self.failed=0
        self.active=threading.Event()
        self.worker=threading.Thread(target=self.run,name='inference-worker',daemon=False)
        self.worker.start()
    def submit(self,wav):
        wav=Path(wav)
        write_json(result_path(wav),dict(status='queued',file=str(wav.resolve())))
        self.submitted+=1
        try:self.queue.put_nowait(wav)
        except queue.Full:
            self.backlog+=1
            write_json(result_path(wav),dict(status='backlog',file=str(wav.resolve()),
                                           reason='inference queue full; WAV preserved for backfill'))
            return False
        return True
    def run(self):
        while True:
            wav=self.queue.get()
            try:
                if wav is None:return
                self.active.set()
                try:
                    self.engine.process(wav)
                    self.processed+=1
                except Exception as e:
                    self.failed+=1
                    print('INFERENCE_FAILED',wav,str(e),flush=True)
                    try:write_json(result_path(wav),dict(status='error',file=str(wav),error=str(e)))
                    except OSError as ioe:print('RESULT_WRITE_FAILED',str(ioe),flush=True)
            finally:
                self.active.clear()
                self.queue.task_done()
    def stats(self):
        return dict(submitted=self.submitted,processed=self.processed,failed=self.failed,
                    backlog=self.backlog,queued=self.queue.qsize(),active=self.active.is_set(),
                    capacity=self.queue.maxsize)
    def close(self):
        print('Finishing queued inference...',flush=True)
        self.queue.put(None)
        self.worker.join()
        print('INFERENCE_SUMMARY',json.dumps(self.stats()),flush=True)

@contextmanager
def inference_context(enabled,with_macbert=True):
    worker=InferenceQueue(Engine(with_macbert=with_macbert)) if enabled else None
    try:yield worker
    finally:
        if worker:worker.close()
