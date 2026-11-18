"""Required two-stage edge policy; scores stay separate and are not averaged."""
import math

POLICY = 'rbt3_fusion_then_macbert_v1'
RULE = 'RBT3 emotion fusion >= 0.5 THEN MacBERT semantic score >= 0.5'
OLD_QWEN_POLICY = 'rbt3_fusion_then_qwen35_2b_q8_v1'
QWEN_POLICY = 'rbt3_fusion_then_qwen35_2b_clean_v2_q8'
QWEN_RULE = 'RBT3 emotion fusion >= 0.5 THEN fine-tuned Qwen3.5-2B Q8_0 risk=true'
QWEN_POLICY_0715 = 'rbt3_fusion0715_then_qwen35_2b_clean_v2_q8'
QWEN_RULE_0715 = 'RBT3 emotion fusion >= 0.715 THEN fine-tuned Qwen3.5-2B Q8_0 risk=true'
CLEAN_QWEN_POLICIES = (QWEN_POLICY, QWEN_POLICY_0715)
RULES = {POLICY: RULE, OLD_QWEN_POLICY: QWEN_RULE, QWEN_POLICY: QWEN_RULE, QWEN_POLICY_0715: QWEN_RULE_0715}


def rule_for(policy):
    if policy not in RULES: raise ValueError('Unknown decision policy: ' + str(policy))
    return RULES[policy]

def serial_candidate(result):
    if not isinstance(result, dict): return False
    policy = result.get('decision_policy')
    if (result.get('status') != 'complete' or policy not in RULES or
            result.get('review_candidate') is not True):
        return False
    models = result.get('models', {})
    if not isinstance(models, dict): return False
    required = [('rbt3', 'fusion_score')]
    if policy == POLICY: required.append(('macbert', 'semantic_score'))
    for name, field in required:
        row = models.get(name, {})
        if not isinstance(row, dict): return False
        score = row.get(field)
        minimum = .715 if name == 'rbt3' and policy == QWEN_POLICY_0715 else .5
        if (row.get('status') != 'complete' or row.get('review_candidate') is not True or
                isinstance(score, bool) or not isinstance(score, (int, float)) or
                not math.isfinite(score) or not minimum <= score <= 1):
            return False
    if policy in (OLD_QWEN_POLICY, QWEN_POLICY, QWEN_POLICY_0715):
        row = models.get('qwen35_2b', {})
        if (not isinstance(row, dict) or row.get('status') != 'complete' or
                row.get('risk') is not True or row.get('review_candidate') is not True or
                row.get('format_valid') is not True):
            return False
    return True
