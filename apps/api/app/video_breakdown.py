"""Build a reviewable reference worksheet from supplied notes, without watching claims."""
import re
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .business import EDIT_ROLES, _scope, topics_data
from .core_models import Fact, Property
from .db import get_db
from .operations_models import ContentOpportunity, ContentPlan
from .security import Context, get_context, require_project


router = APIRouter(tags=['视频参考拆解'])
_STAMP = r'\d{1,2}:\d{2}(?:\.\d{1,3})?'
_TIME_RANGE = re.compile(rf'^({_STAMP})(?:\s*[-–—~至]\s*({_STAMP}))?$')
_PLAIN_TIMED_LINE = re.compile(rf'^({_STAMP}(?:\s*[-–—~至]\s*{_STAMP})?)\s+(.+)$')
_FOCUS = {'space': '空间尺度', 'light': '客厅尺度与采光资料', 'layout': '户型与动线',
          'kitchen': '厨房与客餐厅的连接', 'tour': '项目真实资料'}
_PLATFORMS = {'douyin': '抖音', 'xiaohongshu': '小红书', 'bilibili': 'B站'}
_TEMPLATES = {
    'question': ('问题引入', '提出住房选择问题 → 以本项目事实回答 → 对照日常需求 → 邀请具体提问'),
    'contrast': ('反差表达', '提出选择误区 → 对照资料与现场 → 说明适配条件 → 回到看房问题'),
    'numeric_anchor': ('具体数字入口', '以当前项目已核验数字建立尺度 → 展示测量或资料依据 → 核对使用场景 → 提醒适用条件'),
    'walkthrough': ('实拍动线', '沿真实空间路径导览 → 在关键位置核对项目事实 → 对照日常动线 → 邀请看房问题'),
    'evidence_sequence': ('证据对照', '提出待核对内容 → 展示本项目事实与实拍 → 提出适配问题 → 邀请具体提问'),
}


def _notes(value, field):
    rows = []
    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue
        matched = None
        if re.match(r'^\[-?\d+:', line):
            closing = line.find(']')
            if closing != -1:
                matched = _TIME_RANGE.fullmatch(line[1:closing].strip())
                line = line[closing + 1:].strip().lstrip('：:').strip()
            if matched is None:
                raise ValueError('时间标记格式不正确，请使用 [00:00-00:03] 内容')
        elif re.match(r'^-?\d+:', line):
            plain = _PLAIN_TIMED_LINE.fullmatch(line)
            if plain:
                matched = _TIME_RANGE.fullmatch(plain[1])
                line = plain[2].strip()
            if matched is None:
                raise ValueError('时间标记格式不正确，请使用 [00:00-00:03] 内容')
        start = end = None
        if matched:
            def seconds(stamp):
                raw_minute, raw_second = stamp.split(':')
                minute, second = int(raw_minute), float(raw_second)
                if second >= 60 or minute * 60 + second > 1800:
                    raise ValueError('时间须使用 00:00 格式，且在 30 分钟以内')
                value = minute * 60 + second
                return int(value) if value.is_integer() else value
            start = seconds(matched[1])
            end = seconds(matched[2]) if matched[2] else None
            if end is not None and end <= start:
                raise ValueError('片段结束时间必须晚于开始时间')
            if not line:
                raise ValueError('时间标记后请填写口播或画面观察')
        rows.append({'start_seconds': start, 'end_seconds': end, 'transcript': '', 'visual': '',
                     field: line, 'evidence_status': 'user_supplied'})
    if len(rows) > 500:
        raise ValueError('每项最多填写 500 个片段，请选取需要研究的部分')
    return rows


class VideoBreakdownRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    transcript: str = Field(default='', max_length=10000)
    observations: str = Field(default='', max_length=10000)
    target_seconds: int = Field(default=30, ge=15, le=600)
    topic_id: Literal['space', 'light', 'layout', 'kitchen', 'tour'] = 'tour'
    reference_id: str | None = Field(default=None, min_length=1, max_length=36)
    analysis_job_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator('target_seconds')
    @classmethod
    def duration(cls, value):
        from .production_contract import validate_duration
        return validate_duration(value)

    @field_validator('source_url')
    @classmethod
    def supported_source(cls, value):
        # Kept local: discovery includes this router after its URL helper exists.
        from .discovery import _canonical_work_url
        identity = _canonical_work_url(value.strip())
        if identity is None:
            raise ValueError('请提供抖音、小红书或 B站的公开作品完整链接')
        return identity[2]

    @field_validator('title', 'transcript', 'observations')
    @classmethod
    def plain_notes(cls, value, info):
        value = value.strip()
        if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('请使用可读文字')
        if info.field_name == 'title' and not value:
            raise ValueError('请填写参考作品标题')
        if info.field_name in ('transcript', 'observations'):
            _notes(value, 'transcript' if info.field_name == 'transcript' else 'visual')
        return value

    @model_validator(mode='after')
    def has_notes(self):
        if not self.transcript and not self.observations and not self.reference_id and not self.analysis_job_id:
            raise ValueError('请填写原片口播或画面观察；仅凭链接不能拆解视频')
        return self


def _timeline(body):
    rows = _notes(body.transcript, 'transcript')
    for visual in _notes(body.observations, 'visual'):
        matching = next((row for row in rows if visual['start_seconds'] is not None
                         and row['start_seconds'] == visual['start_seconds']
                         and row['end_seconds'] == visual['end_seconds']), None)
        if matching:
            matching['visual'] = '\n'.join(filter(None, (matching['visual'], visual['visual'])))
        else:
            rows.append(visual)
    return sorted(rows, key=lambda row: (row['start_seconds'] is None,
                                         row['start_seconds'] or 0, row['end_seconds'] or 0))


def _structure(timeline):
    known = [row for row in timeline if row['start_seconds'] is not None]
    opening = [row for row in known if row['start_seconds'] < 3]
    last = max((row['end_seconds'] if row['end_seconds'] is not None else row['start_seconds']
                for row in known), default=None)
    gaps, cursor = [], 0
    for row in known:
        if row['end_seconds'] is None:
            continue
        if row['start_seconds'] > cursor:
            gaps.append({'start_seconds': cursor, 'end_seconds': row['start_seconds']})
        cursor = max(cursor, row['end_seconds'])
    opening_text = '\n'.join('；'.join(filter(None, (row['transcript'], row['visual']))) for row in opening)
    clues = []
    for pattern, label in [(r'[？?]|为什么|如何|怎么|什么', '问题引入'),
                           (r'但是|却|不是|而是|看似|竟然', '反差表达'),
                           (r'\d+\s*(?:㎡|平方米|万元|万|米|房|室)', '具体数字入口')]:
        if re.search(pattern, opening_text):
            clues.append({'label': label, 'evidence': opening_text, 'status': 'hypothesis'})
    return {'known_clip_count': len(known), 'untimed_clip_count': len(timeline) - len(known),
            'last_known_seconds': last, 'gaps': gaps, 'opening_evidence': opening,
            'middle_evidence': [row for row in known if row['start_seconds'] >= 3],
            'last_recorded_evidence': [row for row in known if (row['end_seconds'] if row['end_seconds'] is not None
                                        else row['start_seconds']) == last], 'opening_hypotheses': clues}


def _reuse_template(structure, timeline):
    labels = [item['label'] for item in structure['opening_hypotheses']]
    if any(re.search(r'推镜|动线|走进|转场|沿着|跟随|从.{1,12}到', row['visual']) for row in timeline):
        labels.append('实拍动线')
    techniques = [label for _, (label, _) in _TEMPLATES.items() if label in labels]
    identity = next((key for key, (label, _) in _TEMPLATES.items() if label in techniques), 'evidence_sequence')
    label, description = _TEMPLATES[identity]
    return {'id': identity, 'label': label, 'description': description, 'techniques': techniques,
            'status': 'hypothesis' if techniques else 'proposed',
            'mapping_note': '借鉴用户记录中的表达线索；时间比例为拟拍建议，不代表原片效果已证实。'}


def _shots(body, facts, missing, template, structure):
    focus = _FOCUS[body.topic_id]
    duration = body.target_seconds
    cuts = [0, 3, round(duration * .45), round(duration * .8), duration]
    last = structure['last_known_seconds']
    phase = next((row['end_seconds'] for row in structure['middle_evidence']
                  if row['end_seconds'] is not None and last and 3 < row['end_seconds'] < last), None)
    if phase:
        cuts[2] = max(5, min(cuts[3] - 2, round(duration * max(.25, min(.65, phase / last)))))
    evidence = '；'.join(f"{fact['label']}为{fact['value']}{fact['unit']}" for fact in facts[:2])
    evidence = f'已核验项目资料记录：{evidence}。' if evidence else f"【待补充并核验：{'、'.join(missing)}】"
    rows = [
        ('开场问题', f'看房先看什么？今天先核对{focus}。', '授权实拍建立空间关系，字幕提出同一问题。', 'creative_draft'),
        ('事实与依据', evidence, '对应实拍与资料逐项对照；素材编号待选择，数值与单位保持完整。',
         'verified_project_fact' if facts else 'pending'),
        ('生活场景与核对', '把资料和现场实拍对照，再判断是否符合你的日常需求。', '用授权实拍说明观察位置与动线；未经测量不做效果承诺。', 'creative_draft'),
        ('回答与行动', '你还想核对哪一点？留言说出问题，我们按项目资料继续说明。', '镜头收束到本条问题，字幕邀请具体提问。', 'creative_draft'),
    ]
    identity = template['id']
    if identity == 'question':
        rows[0] = ('提问式开场', f'{focus}该怎么核对？先用这份项目资料回答。',
                   '沿用提问入口，字幕与实拍共同指向一个待核对问题。', 'creative_draft')
        rows[1] = (rows[1][0], rows[1][1], '围绕开场问题逐项展示资料与对应实拍，给出可核对的回答。', rows[1][3])
    elif identity == 'contrast':
        rows[0] = ('反差式开场', f'看{focus}，只凭第一眼还不够，先把资料和现场对照。',
                   '以第一印象与核对过程形成表达反差，不虚构项目缺点或比较结论。', 'creative_draft')
        rows[1] = (rows[1][0], rows[1][1], '用资料页与对应实拍交替呈现对照过程，避免无依据的前后效果比较。', rows[1][3])
    elif identity == 'numeric_anchor':
        anchor = (f"{facts[0]['label']}{facts[0]['value']}{facts[0]['unit']}"
                  if facts else f"【待补充并核验：{'、'.join(missing)}】")
        rows[0] = ('尺度锚点开场', f'{anchor}，这份项目记录怎样对应实际生活？',
                   '只展示本项目已核验数字与完整单位；缺少数字时保留待核验位置。', 'creative_draft')
        rows[1] = (rows[1][0], rows[1][1], '将本项目资料数值与实拍位置对应，标清测量或资料依据。', rows[1][3])
    elif identity == 'walkthrough':
        rows[0] = ('实拍导览开场', f'跟着实际看房路径，我们逐步核对{focus}。',
                   '先选择本项目授权素材中真实存在的起点，沿同一空间路径推进。', 'creative_draft')
        rows[1] = (rows[1][0], rows[1][1], '在导览路径的关键位置停留，逐项对应当前项目资料。', rows[1][3])
    return [{'start_seconds': cuts[index], 'end_seconds': cuts[index + 1], 'purpose': row[0],
             'voiceover': row[1], 'visual': row[2], 'evidence_status': row[3]} for index, row in enumerate(rows)]


def _time_label(row):
    def stamp(seconds):
        milliseconds = round(seconds * 1000)
        minute, rest = divmod(milliseconds, 60000)
        second, fraction = divmod(rest, 1000)
        return f'{minute:02d}:{second:02d}' + (f'.{fraction:03d}' if fraction else '')
    if row['start_seconds'] is None:
        return '时间待补充'
    value = stamp(row['start_seconds'])
    return value + ('–' + stamp(row['end_seconds']) if row['end_seconds'] is not None else '（结束待补充）')


def _reference_dump(body):
    """Keep old manual-note request identities stable across this extension."""
    return body.model_dump(exclude={key for key in ('reference_id', 'analysis_job_id')
                                    if getattr(body, key) is None})


def _resolve_evidence(db, ctx, pid, body):
    from .core_models import Asset
    from .job_models import Job
    from .discovery import _canonical_work_url
    from .media import _verified_path
    reference = None
    if body.reference_id:
        reference = db.scalar(_scope(db, ContentOpportunity, ctx, pid).where(
            ContentOpportunity.id == body.reference_id))
        if reference is None:
            raise HTTPException(404, '参考作品不存在或无权访问')
        identity = _canonical_work_url(reference.source_url)
        if identity is None or identity[2] != body.source_url:
            raise HTTPException(409, '拆解来源与参考库作品不一致')
    analysis = None
    if body.analysis_job_id:
        job = db.scalar(_scope(db, Job, ctx, pid).where(Job.id == body.analysis_job_id,
            Job.kind == 'reference_analyze', Job.status == 'succeeded'))
        if job is None:
            raise HTTPException(409, '原片分析尚未完成或无权访问，请先完成分析')
        payload = job.payload or {}
        if reference is None:
            reference = db.scalar(_scope(db, ContentOpportunity, ctx, pid).where(
                ContentOpportunity.id == payload.get('reference_id')))
        if reference is None or reference.id != payload.get('reference_id'):
            raise HTTPException(409, '原片分析不属于当前参考作品')
        identity = _canonical_work_url(reference.source_url)
        analysis = (job.result or {}).get('reference_analysis')
        if (identity is None or identity[2] != body.source_url or payload.get('source_url') != body.source_url
                or not isinstance(analysis, dict) or analysis.get('schema_version') != 2
                or analysis.get('source', {}).get('url') != body.source_url
                or analysis.get('reference_id') != reference.id):
            raise HTTPException(409, '原片分析来源不一致，请重新分析')
        asset = db.scalar(_scope(db, Asset, ctx, pid).where(Asset.id == reference.media_asset_id))
        if (asset is None or asset.id != analysis.get('asset_id') or asset.id != payload.get('asset_id')
                or asset.sha256 != analysis.get('asset_sha256') or asset.sha256 != payload.get('asset_sha256')
                or asset.rights_revision != analysis.get('rights_revision')
                or asset.rights_revision != payload.get('rights_revision')
                or asset.rights != 'authorized' or not asset.rights_evidence.strip()
                or (asset.metadata_json or {}).get('model_processing_authorized') is not True):
            raise HTTPException(409, '参考原片或授权已更新，请重新核对后分析')
        try:
            _verified_path(asset)
        except (OSError, ValueError):
            raise HTTPException(409, '参考原片完整性校验失败，请重新上传并分析') from None
        from .reference_media import analysis_notes
        needed = {key for key in ('transcript', 'observations') if not getattr(body, key)}
        notes = {}
        if needed:
            selected = dict(analysis)
            if 'transcript' not in needed:
                selected['transcript_segments'] = []
            if 'observations' not in needed:
                selected['frames'] = []
            try:
                notes = analysis_notes(selected)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
    else:
        notes = (reference.research_notes or {}) if reference else {}
    updates = {key: getattr(body, key) or notes.get(key, '') for key in ('transcript', 'observations')}
    if not updates['transcript'] and not updates['observations']:
        raise HTTPException(422, '请先分析原片，或填写并保存原片口播和画面笔记')
    resolved = VideoBreakdownRequest.model_validate(_reference_dump(body) | updates)
    return resolved, analysis, reference


def validate_reference_source(db, ctx, pid, snapshot):
    """A copied plan does not retain processing permission after revocation."""
    analysis = snapshot.get('reference_analysis') if isinstance(snapshot, dict) else None
    provenance = analysis.get('asset_provenance') if isinstance(analysis, dict) else None
    if not provenance:
        return
    raw = analysis.get('reference')
    if not isinstance(raw, dict):
        raise HTTPException(409, '计划的原片证据格式已失效，请重新拆解')
    try:
        body = VideoBreakdownRequest.model_validate(raw)
    except ValueError:
        raise HTTPException(409, '计划的原片证据格式已失效，请重新拆解') from None
    db.expire_all()
    _resolve_evidence(db, ctx, pid, body)


def build_breakdown(db, ctx, pid, body):
    project = require_project(db, ctx, pid, EDIT_ROLES)
    body, asset_analysis, reference_record = _resolve_evidence(db, ctx, pid, body)
    topic = next(topic for topic in topics_data(db, ctx, pid) if topic['id'] == body.topic_id)
    selected_ids = {ref['id'] for ref in topic['fact_refs']}
    facts = [{'id': fact.id, 'revision': fact.revision, 'label': fact.label, 'value': fact.value,
              'unit': fact.unit, 'source': fact.source}
             for fact in db.scalars(_scope(db, Fact, ctx, pid)).all() if fact.id in selected_ids]
    # A malformed/oversized fact cannot turn a useful worksheet into an unbounded prompt.
    usable = [fact for fact in facts if len(fact['label']) + len(fact['value']) + len(fact['unit']) <= 800]
    missing = list(topic['missing']) + [fact['label'] + '（资料过长，请先整理）' for fact in facts if fact not in usable]
    timeline = _timeline(body)
    structure = _structure(timeline)
    template = _reuse_template(structure, timeline)
    if asset_analysis:
        techniques = [item for item in asset_analysis.get('techniques', []) if isinstance(item, dict)
                      and item.get('id') in _TEMPLATES and item.get('evidence_refs')]
        if techniques:
            identity = techniques[0]['id']
            label, description = _TEMPLATES[identity]
            template = {'id': identity, 'label': label, 'description': description,
                'techniques': list(dict.fromkeys(_TEMPLATES[item['id']][0] for item in techniques)),
                'status':'hypothesis', 'mapping_note':'依据原片转写与采样画面的模型分析，表达效果仍需核对。'}
    opening = [row for row in timeline if row['start_seconds'] is not None and row['start_seconds'] < 3]
    opening_evidence = '\n'.join('；'.join(filter(None, (row['transcript'], row['visual']))) for row in opening)
    hook = {'evidence': opening_evidence, 'status': 'user_supplied' if opening_evidence else 'pending',
            'questions': ['前 3 秒是否提出明确的看房问题或冲突？', '第一句话与第一幅画面是否指向同一问题？',
                          '片尾是否回应开场，且能用原片时间点核对？']}
    shots = _shots(body, usable, missing, template, structure) if body.target_seconds <= 60 else []
    adaptation = {'topic_id': body.topic_id, 'eligible': bool(topic['eligible']) and not missing,
                  'missing_facts': missing, 'fact_refs': usable, 'shots': shots, 'reuse_template': template}
    if body.target_seconds > 60:
        from .production_contract import chapter_count
        labels = ['开场问题与观看收益','家庭需求与资料范围','面积数字的核对方法','开间与日常使用条件',
                  '户型与私密动线','厨餐关系与操作流程','示意走查与证据缺口','不同需求下的选择取舍',
                  '重新检验最初问题','总结清单与下一步']
        count=chapter_count(body.target_seconds)
        selected=[labels[round(index*(len(labels)-1)/(count-1))] for index in range(count)]
        durations=[body.target_seconds//count+(index<body.target_seconds%count) for index in range(count)]
        adaptation.update(needs_chapter_expansion=True,
            chapters=[{'id':f'ch-{index+1:02d}','title':label,'target_seconds':durations[index],
                'purpose':'提出观众问题，解释核对方法，呈现条件取舍，再回答本章问题',
                'evidence_status':'proposed'} for index,label in enumerate(selected)])
    from .discovery import _canonical_work_url
    platform = _canonical_work_url(body.source_url)[0]
    limit_note = ('仅依据用户提供的口播与画面观察；系统未观看原片，内容及时间标记未经核验。'
                  '互动表现、留存与爆款成因尚未核验。原片楼盘、面积、价格与承诺不得移植到当前项目。')
    if asset_analysis:
        limit_note = ('原片已通过本地ASR与采样画面分析，人工修订的笔记仍需复核。'
            + asset_analysis.get('coverage', {}).get('note', '采样之外的画面未知。')
            + '采样不等于完整观看，互动表现与爆款成因尚未核验；不移植原片楼盘事实。')
    timeline_lines = [f"{_time_label(row)} | 口播：{row['transcript'] or '待补充'} | 画面：{row['visual'] or '待补充'}"
                      for row in timeline]
    shot_lines = [f"{row['start_seconds']}–{row['end_seconds']}秒 · {row['purpose']}\n口播：{row['voiceover']}\n画面：{row['visual']}"
                  for row in shots]
    fact_lines = [f"{fact['label']}：{fact['value']}{fact['unit']}（{fact['source']}，v{fact['revision']}）"
                  for fact in usable[:8]]
    target_plan = ('\n\n'.join(f"{index+1}. {chapter['title']}（计划{chapter['target_seconds']}秒）\n{chapter['purpose']}"
                   for index,chapter in enumerate(adaptation['chapters']))
                   + '\n\n这是长片章纲，采用后逐章生成完整口播与30个以上可拍镜头；尚不是完整脚本。'
                   if body.target_seconds>60 else '\n\n'.join(shot_lines))
    brief = '\n\n'.join([
        f'住宅参考作品：{body.source_url}', limit_note,
        f"来源表达线索：{'、'.join(template['techniques']) or '待补充'}。\n可复用结构：{template['description']}。"
        + '此结构为创作建议，非原片已证实规律；只借鉴表达技法，项目事实另行核验。',
        f'目标：{body.target_seconds}秒；{_FOCUS[body.topic_id]}。',
        ('长视频章节创作简报：\n' if body.target_seconds>60 else '当前项目分秒拍摄草稿：\n') + target_plan,
        '事实依据：\n' + ('\n'.join(fact_lines) if fact_lines else '待补充并核验项目事实'),
        '待补充：' + ('、'.join(missing) if missing else '选择授权素材，核对画面与事实对应关系'),
        '验收：查看原片补齐开场/转折/片尾时间点；拍摄后核对字幕、素材授权和事实版本；发布后记录实际留存、收藏和咨询数据再验证假设。',
    ])
    # Only the bounded target brief reaches generation. Original property claims
    # remain in the separate source worksheet and never become target copy.
    if len(brief) > 12000:
        raise HTTPException(422, '当前项目事实过长，请整理资料后再生成拆解简报')
    report = '\n\n'.join([
        f'参考作品：{body.title}\n{body.source_url}', '证据范围：' + limit_note,
        f"原片结构记录：{structure['known_clip_count']} 个带时间片段，{structure['untimed_clip_count']} 个时间待补充；"
        + (f"最后时间标记 {_time_label({'start_seconds':structure['last_known_seconds'],'end_seconds':structure['last_known_seconds']}).split('–')[0]}。"
           if structure['last_known_seconds'] is not None else '尚无可定位时间。')
        + '最后记录仅代表已提供片段，完整片尾未知。',
        '时间缺口（仅按已标记范围计算）：' + ('、'.join(_time_label(gap) for gap in structure['gaps'])
                                         if structure['gaps'] else '未发现可计算缺口；未标记区间仍待核对。'),
        '原片分秒记录（用户提供，待核对）：\n' + '\n'.join(timeline_lines),
        '始于前 3 秒的开场记录（跨越 3 秒的记录不能定位每句发生时间）：\n'
        + (opening_evidence or '待补充带时间标记的前 3 秒口播或画面，不能据此判断开场钩子。'),
        '开场文本线索（待验证假设）：' + ('、'.join(clue['label'] for clue in structure['opening_hypotheses'])
                                        if structure['opening_hypotheses'] else '暂无可辨识的文本线索')
        + '。线索不证明留存效果或爆款成因；须结合原片与实际数据核对。',
        '开场与留存核对问题：\n' + '\n'.join(hook['questions']),
        f"拟借鉴表达结构：{template['label']}\n{template['description']}\n{template['mapping_note']}",
        '可复用内容结构与当前项目拍摄草稿：\n' + target_plan,
        '项目事实依据：\n' + ('\n'.join(fact_lines) if fact_lines else '待补充并核验'),
        '下一步：核对原片观察与真实互动来源，再把拍摄草稿转为内容计划；脚本和成片仍需人工审核。',
    ])
    long_planning = ({'content_type':'professional','subject':_FOCUS[body.topic_id],
        'target_seconds':body.target_seconds,'skill_ids':['topic-matrix','titles-and-hooks','series-planning'],
        'hook':f'{_FOCUS[body.topic_id]}应该怎样核对？从资料、示意与不同需求逐步讲清。',
        'outline':[chapter['title'] for chapter in adaptation['chapters']],
        'cta':'带着核对清单补充实际资料，预约时间需进一步确认。'} if body.target_seconds>60 else None)
    return {'source': {'url': body.source_url, 'title': body.title, 'watched': False,
                       'evidence_basis': 'asset_asr_and_sampled_frames' if asset_analysis else 'user_supplied_notes'},
            'asset_analysis': asset_analysis, 'reference_id': reference_record.id if reference_record else None,
            'timeline': timeline, 'hook': hook, 'structure': structure,
            'adaptation': adaptation, 'report': report,
            'plan_seed': {'title': f'{project.name}：{topic["title"]}'[:300], 'angle': topic['angle'],
                          'brief': brief, 'topic_id': body.topic_id, 'channel': _PLATFORMS[platform],
                          **({'planning':long_planning} if long_planning else {})}}


@router.post('/breakdown')
def breakdown_post(pid: str, body: VideoBreakdownRequest, db: Session = Depends(get_db),
                   ctx: Context = Depends(get_context)):
    return build_breakdown(db, ctx, pid, body)


class BreakdownPlanFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=300)
    angle: str = Field(default='', max_length=6000)
    brief: str = Field(default='', max_length=12000)
    topic_id: Literal['space', 'light', 'layout', 'kitchen', 'tour']
    channel: str = Field(min_length=1, max_length=40)
    planning: dict | None = None

    @field_validator('planning')
    @classmethod
    def planning_config(cls,value):
        if value is None:return None
        from .content_planning import normalize_planning
        return normalize_planning(value)

    @field_validator('title', 'angle', 'brief', 'channel')
    @classmethod
    def cleaned(cls, value, info):
        value = value.strip()
        if info.field_name in ('title', 'channel') and not value:
            raise ValueError('标题和渠道不可为空')
        if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('请使用可读文字')
        return value


class BreakdownPlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reference: VideoBreakdownRequest
    plan: BreakdownPlanFields
    opportunity_id: str | None = Field(default=None, min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=36, max_length=36)

    @field_validator('idempotency_key')
    @classmethod
    def uuid_key(cls, value):
        try:
            return str(UUID(value))
        except ValueError:
            raise ValueError('请提供有效的 UUID 保存编号') from None

    @model_validator(mode='after')
    def same_topic(self):
        if self.plan.topic_id != self.reference.topic_id:
            raise ValueError('计划选题必须与当前拆解选题一致，请重新预览')
        if self.plan.planning and self.plan.planning['target_seconds']!=self.reference.target_seconds:
            raise ValueError('计划时长必须与当前拆解时长一致，请重新预览')
        return self


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def save_breakdown_plan(db, ctx, pid, body):
    from .operations import create_plan, plan_dict
    from .discovery import _canonical_work_url
    require_project(db, ctx, pid, EDIT_ROLES)
    library_id = body.reference.reference_id
    if body.reference.reference_id:
        if body.opportunity_id and body.opportunity_id != body.reference.reference_id:
            raise HTTPException(409, '计划关联与参考作品不一致')
        # Reusable reference research has a non-unique relationship. The older
        # one-time opportunity conversion keeps its separate unique contract.
        body = body.model_copy(update={'opportunity_id':None})
    # SQLite ignores FOR UPDATE. Acquire its write lock with a no-op scoped
    # update before looking up the key; PostgreSQL locks just this project row.
    # Both locks are held until the single route commit, including key-only saves.
    project_scope = (Property.id == pid, Property.tenant_id == ctx.tenant_id)
    if db.get_bind().dialect.name == 'sqlite':
        locked = db.execute(update(Property).where(*project_scope).values(name=Property.name)).rowcount == 1
    else:
        locked = db.scalar(select(Property).where(*project_scope).with_for_update()) is not None
    if not locked:
        raise HTTPException(404, '项目不存在或无权访问')
    request_data = body.model_dump(exclude={'idempotency_key'})
    request_data['reference'] = _reference_dump(body.reference)
    request_hash = _digest(request_data)
    existing = db.scalar(_scope(db, ContentPlan, ctx, pid).where(
        ContentPlan.source_snapshot['reference_analysis']['idempotency_key'].as_string() == body.idempotency_key))
    if existing:
        analysis = existing.source_snapshot['reference_analysis']
        if analysis.get('request_hash') != request_hash:
            raise HTTPException(409, '该保存编号已用于不同内容，请使用新的保存编号')
        return plan_dict(existing)
    if body.opportunity_id:
        opportunity = db.scalar(_scope(db, ContentOpportunity, ctx, pid).where(
            ContentOpportunity.id == body.opportunity_id).with_for_update())
        if opportunity is None:
            raise HTTPException(404, '机会不存在或无权访问')
        source = _canonical_work_url(opportunity.source_url)
        if source is None or source[2] != body.reference.source_url:
            raise HTTPException(409, '参考作品与已保存发现的来源不一致，请核对后再保存')
        if db.scalar(_scope(db, ContentPlan, ctx, pid).where(
                ContentPlan.opportunity_id == body.opportunity_id)) is not None:
            raise HTTPException(409, '该发现已有内容计划，请打开原计划继续编辑')
    breakdown = build_breakdown(db, ctx, pid, body.reference)
    fields=body.plan.model_dump(exclude_none=True)
    if body.reference.target_seconds>60:
        fields['planning']=body.plan.planning or breakdown['plan_seed']['planning']
    created = create_plan(db, ctx, pid, fields | {'opportunity_id': body.opportunity_id})
    record = db.scalar(_scope(db, ContentPlan, ctx, pid).where(ContentPlan.id == created['id']))
    snapshot = dict(record.source_snapshot or {})
    if snapshot.get('reference_analysis'):
        raise HTTPException(409, '该发现已保存拆解证据，请打开原计划继续编辑')
    resolved_body, asset_analysis, source_record = _resolve_evidence(db, ctx, pid, body.reference)
    if library_id:
        from .operations import opportunity_dict
        record.reference_id = library_id
        snapshot = opportunity_dict(source_record)
        source_record.status = 'planned'
    reference = _reference_dump(resolved_body)
    snapshot['reference_analysis'] = {'schema_version': 1, 'reference': reference,
        'source': breakdown['source'], 'timeline': breakdown['timeline'], 'structure': breakdown['structure'],
        'hook': breakdown['hook'], 'reuse_template': breakdown['adaptation']['reuse_template'],
        'observed_at': datetime.now(timezone.utc).isoformat(), 'content_sha256': _digest(reference),
        'idempotency_key': body.idempotency_key, 'request_hash': request_hash}
    if asset_analysis:
        snapshot['reference_analysis']['asset_analysis'] = asset_analysis
        snapshot['reference_analysis']['asset_provenance'] = {key:asset_analysis[key]
            for key in ('job_id','reference_id','asset_id','asset_sha256','rights_revision')}
    if fields.get('planning'):
        snapshot['planning']=fields['planning']
    record.source_snapshot = snapshot
    db.flush()
    return plan_dict(record)


def generation_reference_context(snapshot):
    """Project only closed technique labels and bounded timing; never source copy."""
    if not isinstance(snapshot, dict):
        return {}
    analysis = snapshot.get('reference_analysis')
    if not isinstance(analysis, dict) or type(analysis.get('schema_version')) is not int or analysis['schema_version'] != 1:
        return {}
    reference = analysis.get('reference')
    template = analysis.get('reuse_template')
    structure = analysis.get('structure')
    digest = analysis.get('content_sha256')
    if (not isinstance(reference, dict) or not isinstance(template, dict) or not isinstance(structure, dict)
            or not isinstance(template.get('id'), str) or template['id'] not in _TEMPLATES or not isinstance(digest, str)
            or not re.fullmatch(r'[a-f0-9]{64}', digest)):
        return {}
    from .discovery import _canonical_work_url
    source = _canonical_work_url(reference.get('source_url'))
    if source is None:
        return {}
    def bounded(value, maximum=1800):
        return value if type(value) in (int,float) and math.isfinite(value) and 0 <= value <= maximum else None
    timing = {key: bounded(structure.get(key), 1000) or 0 for key in ('known_clip_count', 'untimed_clip_count')}
    timing['last_known_seconds'] = bounded(structure.get('last_known_seconds'))
    gaps = structure.get('gaps')
    gaps = gaps[:200] if isinstance(gaps, list) else []
    timing['gaps'] = [{'start_seconds': item['start_seconds'], 'end_seconds': item['end_seconds']}
                      for item in gaps if isinstance(item, dict)
                      and bounded(item.get('start_seconds')) is not None
                      and bounded(item.get('end_seconds')) is not None
                      and item['start_seconds'] < item['end_seconds']]
    raw_labels = template.get('techniques', [])
    labels = [label for label in raw_labels if isinstance(label, str)] if isinstance(raw_labels, list) else []
    hypotheses = structure.get('opening_hypotheses', [])
    if isinstance(hypotheses, list):
        labels += [item.get('label') for item in hypotheses if isinstance(item, dict)]
    techniques = [label for label, _ in _TEMPLATES.values() if label in labels]
    identity = template['id']
    label, description = _TEMPLATES[identity]
    from .reference_structure import reference_beats
    beats=reference_beats({'reference':reference,'asset_analysis':analysis.get('asset_analysis'),
        'content_sha256':digest})
    return {'source_url': source[2], 'reference_analysis': {'schema_version': 1, 'source_digest': digest,
        'techniques': techniques, 'template': {'id': identity, 'label': label, 'description': description},
        'timing': timing,'beats':beats, 'evidence_note': '用户提供记录，未经独立核验；仅借鉴表达结构，不移植参考楼盘事实或承诺。'}}


@router.post('/breakdown/plan')
def breakdown_plan_post(pid: str, body: BreakdownPlanRequest, db: Session = Depends(get_db),
                        ctx: Context = Depends(get_context)):
    result = save_breakdown_plan(db, ctx, pid, body)
    db.commit()
    return result
