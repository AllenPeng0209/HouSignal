"""A structural projection of reference evidence, distinct from property facts."""
import math
import re

from .native_skills import _public_text

_FUNCTIONS = [
    ('问题引入', '提出观众要解决的问题', '用具体疑问建立观看目标，并在后面回应'),
    ('证据展开', '把判断转为可以核对的依据', '解释资料、观察与结论之间的关系'),
    ('对照判断', '让观众理解不同选择的取舍', '按相同标准对照条件，避免直接宣布哪一种更好'),
    ('过程演示', '把问题转为可核对的步骤', '展示观察顺序、操作过程与需要补充的证据'),
    ('阶段总结', '回答本段问题并连接下一段', '收束本段发现，解释仍然未知的条件'),
    ('行动收束', '把内容转成具体下一步', '回应开场，给出核对清单或沟通动作'),
]


def compact_reference_beats(beats, limit=12):
    """Carry the whole reference in bounded chapter context, including its tail.

    Full analysis remains in the saved reference; every grouped beat retains the
    original beat and evidence identifiers. Mixed evidence stays hypothetical.
    """
    if len(beats) <= limit:
        return beats
    result=[]
    for index in range(limit):
        group=beats[index*len(beats)//limit:(index+1)*len(beats)//limit]
        known=[row for row in group if _finite(row.get('start_seconds')) and _finite(row.get('end_seconds'))]
        observed=all(row.get('status')=='observed' for row in group)
        unique=lambda key: list(dict.fromkeys(str(row.get(key) or '') for row in group))
        result.append({'id':f'group-{index+1:02d}', 'label':' / '.join(unique('label'))[:80],
            'purpose':'；'.join(unique('purpose'))[:160], 'summary':'；'.join(unique('summary'))[:200],
            'start_seconds':min((row['start_seconds'] for row in known),default=None),
            'end_seconds':max((row['end_seconds'] for row in known),default=None),
            'source_beat_ids':[row['id'] for row in group],
            'evidence_refs':list(dict.fromkeys(ref for row in group for ref in row.get('evidence_refs',[]))),
            'evidence_basis':'observed_structure' if observed else 'structure_hypothesis',
            'status':'observed' if observed else 'hypothesis',
            'analysis_job_id':group[0].get('analysis_job_id'),'source_digest':group[0].get('source_digest')})
    return result


def _safe(value, limit=220):
    text = _public_text(value, limit * 2)
    text = re.sub(r'\d+(?:\.\d+)?\s*(?:平方米|平米|㎡|米|万元|亿元|元|万|亿|房|室|卫)', '原片指标', text)
    text = re.sub(r'\d+(?:\.\d+)?', '原片数量', text)
    return text[:limit]


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 3600


def _function(text, index, total):
    if index == total - 1 and re.search('留言|预约|关注|下次|总结|最后|下一步', text):
        return _FUNCTIONS[5]
    if re.search('对比|相比|反差|不是.*而是|同样|取舍', text):
        return _FUNCTIONS[2]
    if re.search('走进|先看|再看|步骤|沿着|动线|过程|实拍', text):
        return _FUNCTIONS[3]
    if re.search('[？?]|为什么|怎么|是否|问题', text):
        return _FUNCTIONS[0]
    return _FUNCTIONS[0] if index == 0 else _FUNCTIONS[4] if index == total - 1 else _FUNCTIONS[1]


def reference_beats(reference):
    analysis = reference.get('analysis') or reference.get('asset_analysis') or {}
    segments = analysis.get('transcript_segments') or []
    frames = analysis.get('frames') or []
    evidence = {}
    for item in segments:
        if isinstance(item, dict) and item.get('id') and _finite(item.get('start_seconds')) and _finite(item.get('end_seconds')):
            evidence[item['id']] = (item['start_seconds'], item['end_seconds'])
    for item in frames:
        if isinstance(item, dict) and item.get('id') and _finite(item.get('time_seconds')):
            evidence[item['id']] = (item['time_seconds'], item['time_seconds'])
    supplied = analysis.get('narrative_beats')
    if isinstance(supplied, list) and supplied:
        rows = []
        for index, item in enumerate(supplied[:80]):
            if not isinstance(item, dict):
                continue
            refs = [v for v in item.get('evidence_refs', []) if isinstance(v, str) and v in evidence]
            observed = bool(refs) and item.get('status') == 'observed'
            start = min((evidence[v][0] for v in refs), default=None)
            end = max((evidence[v][1] for v in refs), default=None)
            rows.append({'id': str(item.get('id') or f'b{index+1:03d}')[:80],
                'label': _safe(item.get('label') or '结构段落', 80),
                'purpose': _safe(item.get('purpose') or '组织观众问题与核对过程'),
                'summary': _safe(item.get('summary') or '参考表达结构，当前项目事实另行核验'),
                'start_seconds': start, 'end_seconds': end,
                'evidence_basis': 'observed_structure' if observed else 'structure_hypothesis',
                'evidence_refs': refs, 'analysis_job_id': analysis.get('job_id'),
                'source_digest': reference.get('content_sha256'), 'status': 'observed' if observed else 'hypothesis'})
        if rows:
            return rows
    # Real ASR groups carry real evidence times. Narration function is inferred;
    # grouping does not claim to have watched all intervening frames.
    groups = []
    for segment in segments:
        if not isinstance(segment, dict) or segment.get('id') not in evidence:
            continue
        if not groups or segment['start_seconds'] - groups[-1]['start'] >= 25:
            groups.append({'start': segment['start_seconds'], 'end': segment['end_seconds'], 'refs': [], 'text': ''})
        group = groups[-1];group['end'] = max(group['end'], segment['end_seconds'])
        group['refs'].append(segment['id']);group['text'] += str(segment.get('text') or '')
    if not groups:
        notes = reference.get('research_notes') or reference.get('reference') or {}
        lines = [line.strip() for line in str(notes.get('transcript') or '').splitlines() if line.strip()]
        lines += [line.strip() for line in str(notes.get('observations') or '').splitlines() if line.strip()]
        for line in lines[:60]:
            groups.append({'start': None, 'end': None, 'refs': [], 'text': line})
    if groups:
        rows = []
        for index, group in enumerate(groups[:80]):
            label, purpose, summary = _function(group['text'], index, len(groups))
            rows.append({'id': f'rb{index+1:03d}', 'label': label, 'purpose': purpose, 'summary': summary,
                'start_seconds': group['start'], 'end_seconds': group['end'],
                'evidence_basis': 'asr_structural_hypothesis' if group['refs'] else 'user_notes_hypothesis',
                'evidence_refs': group['refs'], 'analysis_job_id': analysis.get('job_id'),
                'source_digest': reference.get('content_sha256'), 'status': 'hypothesis'})
        return rows
    return [{'id': f'proposed-{index+1}', 'label': label, 'purpose': purpose, 'summary': summary,
             'start_seconds': None, 'end_seconds': None, 'evidence_basis': 'structure_hypothesis',
             'evidence_refs': [], 'analysis_job_id': None, 'source_digest': reference.get('content_sha256'), 'status': 'proposed'}
            for index, (label, purpose, summary) in enumerate(_FUNCTIONS)]
