"""RBT3 emotion gate followed by the validated fine-tuned Qwen Q8 runtime."""
import json
import math
import os
from pathlib import Path
from auto_inference import Engine as LegacyEngine, classify_windows
from decision import QWEN_POLICY, QWEN_RULE, QWEN_POLICY_0715, QWEN_RULE_0715
from qwen_runtime import QwenRuntime

PIPELINE_VERSION = 'usb_inference_v6_rbt3_qwen35_2b_clean_v2_q8'


class Engine(LegacyEngine):
    pipeline_version = PIPELINE_VERSION
    policy = QWEN_POLICY
    rule = QWEN_RULE
    fusion_threshold = .5

    def configure_gate(self, threshold):
        threshold = float(threshold)
        if threshold not in (.5, .715): raise ValueError("Supported fusion thresholds are 0.5 and 0.715")
        self.fusion_threshold = threshold
        self.policy = QWEN_POLICY_0715 if threshold == .715 else QWEN_POLICY
        self.rule = QWEN_RULE_0715 if threshold == .715 else QWEN_RULE
        self.pipeline_version = PIPELINE_VERSION + ("_gate0715" if threshold == .715 else "")

    def __init__(self, base=Path('/home/orangepi/campus-risk'), qwen_config=None, log_dir=None, fusion_threshold=.5):
        self.configure_gate(fusion_threshold)
        import numpy as np
        import sherpa_onnx
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        self.base = Path(base); self.np = np; self.torch = torch; self.models = {}
        os.environ['HF_HUB_OFFLINE'] = '1'
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        self.asr = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(self.base/'orangepi_cpu/model.int8.onnx'), tokens=str(self.base/'orangepi_cpu/tokens.txt'),
            language='zh', use_itn=True, num_threads=2, provider='cpu')
        directory = self.base/'orangepi_baseline/rbt3_finetuned'
        tokenizer = AutoTokenizer.from_pretrained(str(directory), local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(str(directory), local_files_only=True,
                                                                  use_safetensors=True).to('cpu').eval()
        if model.config.num_labels != 2: raise ValueError('RBT3 must have two labels')
        self.models['rbt3'] = (tokenizer, model)
        self.coeff = json.loads((self.base/'orangepi_baseline/manifest.json').read_text())['fusion_coefficients']
        expected = [-2.8304156791267867, 3.7664006008209423, 2.021735959561519]
        if len(self.coeff) != 3 or any(abs(a-b)>1e-10 for a,b in zip(self.coeff,expected)):
            raise ValueError('RBT3 fusion coefficients differ from validated deployment')
        self.qwen = QwenRuntime(self.base, qwen_config or {}, log_dir or self.base/'qwen35_cpu_test/logs')
        print('RESIDENT_MODELS_READY rbt3 qwen35_2b', flush=True)

    def classify_text(self, text, emotion):
        tokenizer, model = self.models['rbt3']
        first = classify_windows(text, tokenizer, model, self.torch)
        fused = 1/(1+math.exp(-(self.coeff[0]+self.coeff[1]*first['semantic_score']+
                               self.coeff[2]*int(emotion=='<|ANGRY|>'))))
        first.update(status='complete', fusion_score=fused, threshold=self.fusion_threshold, review_candidate=fused>=self.fusion_threshold,
                     candidate_rule='RBT3 emotion fusion >= ' + str(self.fusion_threshold))
        if fused >= self.fusion_threshold:
            second = self.qwen.classify(text)
            accepted = second['risk'] is True
            reason = 'both_stages_passed' if accepted else 'qwen_rejected'
        else:
            second = dict(status='skipped', reason='rbt3_fusion_below_threshold', seconds=0,
                          risk=None, review_candidate=None, probability_available=False)
            accepted = False; reason = 'rbt3_rejected'
        return dict(models=dict(rbt3=first,qwen35_2b=second,
                                ced_mini=dict(status='skipped',reason='observation_not_run_on_edge',seconds=0,used_in_risk_fusion=False)),
                    review_candidate=accepted, decision_policy=self.policy, decision_rule=self.rule,
                    decision=dict(stage1_pass=first['review_candidate'],stage2_executed=second['status']=='complete',
                                  stage2_pass=second['review_candidate'],reason=reason))

    def close(self):
        if getattr(self,'qwen',None): self.qwen.close()
