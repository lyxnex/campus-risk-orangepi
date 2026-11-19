"""CED-mini acoustic-event observation. This never votes on language risk."""
import hashlib
import json
from pathlib import Path
import time
import wave

class CedObserver:
    def __init__(self,model_dir,threads=2):
        import numpy as np
        import onnxruntime as ort
        self.np=np;self.root=Path(model_dir)
        config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        prep=json.loads((self.root/'preprocessor_config.json').read_text(encoding='utf-8'))
        self.rate=int(prep['sampling_rate'])
        if self.rate!=16000:raise ValueError('Expected the deployed 16kHz CED-mini')
        self.labels={int(k):v for k,v in config['id2label'].items()}
        self.digest=hashlib.sha256((self.root/'model.onnx').read_bytes()).hexdigest()
        options=ort.SessionOptions();options.intra_op_num_threads=threads;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(self.root/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        inputs=self.session.get_inputs()
        if len(inputs)!=1 or inputs[0].name!='waveform' or inputs[0].type!='tensor(float)':
            raise ValueError('Unexpected CED-mini ONNX input')

    def process(self,audio):
        np=self.np
        if len(audio)==0:raise ValueError('Empty CED input')
        maximum=10*self.rate
        starts=[0] if len(audio)<=maximum else list(range(0,len(audio)-maximum+1,maximum))
        if len(audio)>maximum and starts[-1]!=len(audio)-maximum:starts.append(len(audio)-maximum)
        outputs=[];windows=[];began=time.perf_counter()
        for start in starts:
            chunk=audio[start:start+maximum];length=len(chunk)
            if length<self.rate:chunk=np.pad(chunk,(0,self.rate-length))
            scores=np.asarray(self.session.run(None,{'waveform':chunk[None,:].astype(np.float32)})[0])
            if scores.shape!=(1,len(self.labels)) or not np.isfinite(scores).all() or (scores<0).any() or (scores>1).any():
                raise ValueError('Invalid CED output probabilities')
            outputs.append(scores[0]);windows.append(dict(start_seconds=start/self.rate,audio_seconds=length/self.rate))
        scores=np.maximum.reduce(outputs);top=np.argsort(scores)[-5:][::-1]
        top5=[dict(label=self.labels[int(i)],score=float(scores[i])) for i in top]
        wanted={'Speech','Singing','Music','Shout','Yell','Screaming','Slap','Thump'}
        return dict(status='complete',model='CED-mini',model_sha256=self.digest,seconds=time.perf_counter()-began,
                    top_label=top5[0]['label'],top_score=top5[0]['score'],top5=top5,
                    event_scores={self.labels[i]:float(scores[i]) for i in self.labels if self.labels[i] in wanted},
                    used_in_risk_fusion=False,score_meaning='audio event score, not a violence probability',
                    windows=windows,aggregation='per_label_max',channel=0,sample_rate=self.rate)

    def process_wav(self,path):
        with wave.open(str(path),'rb') as w:
            if w.getframerate()!=self.rate or w.getsampwidth()!=2 or w.getcomptype()!='NONE':raise ValueError('Expected 16kHz PCM16')
            audio=self.np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').reshape(-1,w.getnchannels())[:,0].astype(self.np.float32)/32768
        return self.process(audio)
