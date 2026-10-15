"""Bounded local novel repair, using real SQLite scope and metering records.

Model outputs below are controlled boundary responses, never live acceptance.
"""
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app import provider
from app.agent_models import ModelUsage
from app.config import settings
from app.core_models import Fact, Membership
from app.content_skills import text
from app.worker import LostLease
from test_native_skill_text import native_db, model_http


PARAGRAPHS = [
    '阿岚在灰鸦港醒来，窗外的雾遮住了钟楼。她握紧铜钥匙，必须在黎明到来之前找到那扇门。',
    '旧桥的木板随着脚步轻响。她停在断裂的栏杆前，发现昨夜留下的绳结已经被人解开。',
    '钟楼守门人只留下一盏熄灭的灯。阿岚摸到灯座底下的刻痕，决定沿着刻痕指向的暗道继续前行。',
    '暗门忽然打开，一张陌生的信从门缝滑出。信上只有一句话：拿钥匙的人并不是开门的人。',
]
BASE = '# 雾港的钥匙\n\n' + '\n\n'.join(PARAGRAPHS)
INSERT_ONE = '她蹲下查看绳结，指尖沾上了一点温热的蜡。有人刚刚来过，而且刻意把旧绳留在原处。她把蜡屑包好，借着桥边的微光辨认出一枚倒置的钟形印记。'
INSERT_TWO = '桥下传来两次敲击，间隔和钟形刻痕上的短线一致。阿岚没有答话，而是绕到桥柱背后，找到一截尚未熄灭的引线。她压住引线，听见暗道里有人急促地退开。'


def cjk(value):
    return text.text_metrics(value)['cjk_characters']


def request_for(minimum, maximum):
    return f'创作第一章，所有地点虚构，阿岚必须在黎明前把铜钥匙送到钟楼。正文{minimum}至{maximum}个中文字符，只交付故事。'


def mode(messages):
    try:
        return json.loads(messages[-1]['content']).get('generation_mode', 'full')
    except (json.JSONDecodeError, AttributeError):
        return 'full'


@pytest.fixture
def generation_boundary(model_http, monkeypatch):
    replies, calls = [], []
    during = [None]
    def generate(messages, **kwargs):
        ordinal = len(calls)
        calls.append({'messages': messages, **kwargs})
        if during[0]:
            during[0](ordinal)
        value = replies[ordinal] if ordinal < len(replies) else replies[-1]
        return {'model': 'local-novel-fixture', 'choices': [{'finish_reason': 'stop', 'message': {'content': value}}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 9}}
    monkeypatch.setattr(provider, 'chat', generate)
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(text, 'readiness', lambda _: {'status': 'ready'})
    return replies, calls, during


def test_short_local_novel_inserts_actual_middle_prose_and_preserves_every_original_paragraph(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.extend([BASE, INSERT_ONE])
    observed = []
    db.info['text_quality_observer'] = observed.append
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 15), context={})
    assert [mode(call['messages']) for call in calls] == ['full', 'novel_insert']
    response = result['response']
    assert response.startswith('# 雾港的钥匙') and response.endswith(PARAGRAPHS[-1])
    assert response.count(PARAGRAPHS[-1]) == 1 and response.count(INSERT_ONE) == 1
    positions = [response.index(paragraph) for paragraph in PARAGRAPHS]
    assert positions == sorted(positions)
    assert positions[0] < response.index(INSERT_ONE) < positions[-1]
    assert result['generation_attempts'] == 2
    assert result['usage']['input_tokens'] == 34 and result['usage']['output_tokens'] == 18
    rows = db.scalars(select(ModelUsage)).all()
    assert len(rows) == 2 and all((row.tenant_id, row.property_id) == ('t', 'p') for row in rows)
    assert observed[1]['generation_mode'] == 'novel_insert'
    assert observed[1]['raw_body_metrics']['cjk_characters'] == cjk(INSERT_ONE)
    assert observed[1]['body_metrics']['cjk_characters'] == sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE)
    assert observed[1]['raw_safe_excerpt'] == INSERT_ONE
    feedback = json.loads(calls[1]['messages'][-1]['content'])
    assert feedback['insert_contract']['minimum'] == minimum - sum(cjk(part) for part in PARAGRAPHS)
    assert '只输出' in calls[1]['messages'][0]['content']


def test_two_short_insertions_grow_monotonically_in_order_before_unchanged_ending(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.extend([BASE, INSERT_ONE, INSERT_TWO])
    observed = []
    db.info['text_quality_observer'] = observed.append
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE) + cjk(INSERT_TWO) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    assert [mode(call['messages']) for call in calls] == ['full', 'novel_insert', 'novel_insert']
    counts = [event['body_metrics']['cjk_characters'] for event in observed]
    assert counts[0] < counts[1] < counts[2]
    assert result['response'].index(INSERT_ONE) < result['response'].index(INSERT_TWO) < result['response'].index(PARAGRAPHS[-1])
    assert all(result['response'].count(paragraph) == 1 for paragraph in PARAGRAPHS)
    assert len(db.scalars(select(ModelUsage)).all()) == 3


def test_rejected_base_repeat_returns_exact_fragment_positions_and_retry_window(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    rejected = PARAGRAPHS[2]
    replies.extend([BASE, rejected, INSERT_ONE])
    observed = []
    db.info['text_quality_observer'] = observed.append
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    issues = observed[1]['issue_fragments']
    duplicate = next((row for row in issues if row.get('duplicate_kind') == 'retained_base_paragraph'), None)
    assert duplicate is not None, 'A rejected exact repeat needs the actual offending paragraph'
    assert duplicate['fragment'] == rejected and duplicate['truncated'] is False
    assert BASE[duplicate['base_start']:duplicate['base_end']] == rejected
    assert rejected[duplicate['insert_start']:duplicate['insert_end']] == rejected
    assert duplicate['position_unit'] == 'python_codepoint'
    feedback = json.loads(calls[2]['messages'][-1]['content'])
    assert feedback['specific_invalid_fragments'] == issues
    prior = feedback['previous_rejected_insert']
    assert prior['text'] == rejected and prior['is_untrusted'] is True
    assert prior['may_not_be_retained_base'] is True and prior['may_not_be_source'] is True
    window = feedback['insertion_window']
    assert window['base_length'] == len(BASE) and 0 < window['base_point'] < len(BASE)
    assert feedback['insert_after'] == BASE[:window['base_point']][-1000:]
    assert feedback['insert_before'] == BASE[window['base_point']:][:1000]
    assert '复演' in feedback['instruction'] and '后文' in feedback['instruction']
    assert calls[2]['messages'][-2]['content'] == BASE
    assert result['response'].count(rejected) == 1 and result['response'].count(INSERT_ONE) == 1
    assert result['response'].endswith(PARAGRAPHS[-1])
    assert not {'fiction_candidate', 'raw_fiction_candidate', 'previous_rejected_insert'} & result.keys()


def test_rejected_internal_repeat_keeps_both_insert_positions_in_precise_feedback(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    rejected = INSERT_ONE + '\n\n' + INSERT_ONE
    replies.extend([BASE, rejected, INSERT_TWO])
    observed = []
    db.info['text_quality_observer'] = observed.append
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_TWO) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    duplicate = next((row for row in observed[1]['issue_fragments']
                      if row.get('duplicate_kind') == 'repeated_insert_paragraph'), None)
    assert duplicate is not None, 'Two identical added paragraphs need both exact positions'
    assert duplicate['fragment'] == INSERT_ONE
    assert (duplicate['insert_start'], duplicate['insert_end']) == (0, len(INSERT_ONE))
    assert (duplicate['repeated_insert_start'], duplicate['repeated_insert_end']) == (len(INSERT_ONE) + 2, len(rejected))
    feedback = json.loads(calls[2]['messages'][-1]['content'])
    assert feedback['previous_rejected_insert']['text'] == rejected
    assert calls[2]['messages'][-2]['content'] == BASE
    assert INSERT_ONE not in result['response'] and INSERT_TWO in result['response']


def test_duplicate_coordinates_explicitly_reference_raw_insert_before_redaction(native_db, generation_boundary):
    db, job = native_db
    replies, _, _ = generation_boundary
    rejected = 'token=MOCK_SECRET /private/MOCK_PATH\n\n' + PARAGRAPHS[2]
    replies.extend([BASE, rejected, INSERT_ONE])
    observed = []
    db.info['text_quality_observer'] = observed.append
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE) - 1
    text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    duplicate = next(row for row in observed[1]['issue_fragments'] if row.get('duplicate_kind') == 'retained_base_paragraph')
    assert duplicate.get('position_scope') == 'retained_base_and_raw_insert_before_redaction'
    assert duplicate['insert_start'] == rejected.index(PARAGRAPHS[2])
    assert observed[1]['raw_fiction_candidate'].index(PARAGRAPHS[2]) != duplicate['insert_start']
    assert 'MOCK_SECRET' not in observed[1]['raw_fiction_candidate']
    assert 'MOCK_PATH' not in observed[1]['raw_fiction_candidate']


def test_rejected_insert_feedback_is_bounded_and_redacted_without_retaining_bad_prose(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    rejected = 'token=MOCK_REJECTED_SECRET /private/MOCK_REJECTED_PATH\n\n' + '\n\n'.join([INSERT_ONE] * 90)
    replies.extend([BASE, rejected, INSERT_TWO])
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_TWO) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    feedback = json.loads(calls[2]['messages'][-1]['content'])
    prior = feedback['previous_rejected_insert']
    assert prior['truncated'] is True and len(prior['text']) <= 4000
    assert 'MOCK_REJECTED_SECRET' not in calls[2]['messages'][-1]['content']
    assert 'MOCK_REJECTED_PATH' not in calls[2]['messages'][-1]['content']
    assert 1 <= len(feedback['specific_invalid_fragments']) <= 8
    assert all(len(row['fragment']) <= 1000 for row in feedback['specific_invalid_fragments'])
    assert calls[2]['messages'][-2]['content'] == BASE
    assert INSERT_ONE not in result['response'] and result['response'].endswith(PARAGRAPHS[-1])


@pytest.mark.parametrize('bad_insert', ['## 线索分析\n钥匙是线索，以上补写达到字数要求。',
                                      '字数：2087\n' + INSERT_ONE,
                                      '阿岚转身走向上海黄浦江边。',
                                      BASE, '甲'*80 + '。', '本段补写共500字，已达到要求。', '#中段\n' + INSERT_ONE])
def test_bad_insert_never_becomes_approved_base_or_replaces_the_ending(native_db, generation_boundary, bad_insert):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.extend([BASE, bad_insert, INSERT_ONE])
    observed = []
    db.info['text_quality_observer'] = observed.append
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    assert result['generation_attempts'] == 3
    assert not observed[1]['passed'] and observed[2]['passed']
    assert calls[2]['messages'][-2]['content'] == BASE
    assert result['response'].endswith(PARAGRAPHS[-1]) and result['response'].count(PARAGRAPHS[-1]) == 1
    assert '线索分析' not in result['response'] and '上海' not in result['response'] and '字数：' not in result['response']
    assert len(db.scalars(select(ModelUsage)).all()) == 3


def test_oversized_insert_remains_rejected_without_truncating_any_sentence(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.extend([BASE, INSERT_ONE, INSERT_ONE])
    minimum = sum(cjk(part) for part in PARAGRAPHS) + 1
    with pytest.raises(provider.ProviderError, match='门槛'):
        text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 4), context={})
    assert len(calls) == 3 and mode(calls[-1]['messages']) == 'novel_insert'
    assert calls[-1]['messages'][-2]['content'] == BASE
    assert len(db.scalars(select(ModelUsage)).all()) == 3


def test_initial_novel_with_semantic_errors_uses_full_rewrite_not_insertion(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.append(BASE.replace('灰鸦港', '上海'))
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(400, 430), context={})
    assert len(calls) == 3 and all(mode(call['messages']) == 'full' for call in calls)


def test_single_paragraph_insertion_uses_a_complete_sentence_boundary(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    original = '# 雾港的钥匙\n' + ''.join(PARAGRAPHS)
    replies.extend([original, INSERT_ONE])
    minimum = sum(cjk(part) for part in PARAGRAPHS) + cjk(INSERT_ONE) - 1
    result = text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(minimum, minimum + 12), context={})
    response = result['response']
    inserted_at = response.index(INSERT_ONE)
    assert response[:inserted_at].rstrip().endswith(('。', '？', '！'))
    assert response.endswith(PARAGRAPHS[-1]) and mode(calls[-1]['messages']) == 'novel_insert'
    sentences = [part + '。' for part in ''.join(PARAGRAPHS).split('。') if part]
    assert [response.index(sentence) for sentence in sentences] == sorted(response.index(sentence) for sentence in sentences)


def test_novel_without_a_complete_internal_boundary_is_not_split_mid_sentence(native_db, generation_boundary):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.append('阿岚望着雾中的钟楼，想起门上那些难以辨认的刻痕，也想起出发前老人留在灯下的钥匙，直到第一声钟响从桥的另一端传来。')
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(100, 120), context={})
    assert len(calls) == 3 and all(mode(call['messages']) == 'full' for call in calls)


def test_cloud_novel_does_not_gain_extra_insertion_calls(native_db, generation_boundary, monkeypatch):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.append(BASE)
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(400, 430), context={})
    assert len(calls) == 1 and mode(calls[0]['messages']) == 'full'


@pytest.mark.parametrize('skill_name', ['copywriting', 'skill-audience-profiler'])
def test_copy_and_profile_keep_their_existing_full_generation_path(native_db, generation_boundary, skill_name):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.append('## 正文\n资料待补充。')
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name=skill_name, input_text='正文100至120个中文字符。', context={})
    assert len(calls) == 3 and all(mode(call['messages']) == 'full' for call in calls)


@pytest.mark.parametrize('change', ['membership', 'cancel', 'snapshot'])
def test_insertion_call_is_fenced_after_permission_cancellation_or_project_change(native_db, generation_boundary, change):
    db, job = native_db
    replies, calls, during = generation_boundary
    replies.extend([BASE, INSERT_ONE])
    cancelled = [False]
    db.info['job_cancelled'] = lambda: cancelled[0]
    def mutate(ordinal):
        if ordinal:
            return
        if change == 'membership':
            db.scalar(select(Membership).where(Membership.user_id == 'u', Membership.tenant_id == 't')).active = False
        elif change == 'snapshot':
            db.get(Fact, 'verified').value = '330'
        else:
            cancelled[0] = True
        db.commit()
    during[0] = mutate
    with pytest.raises(LostLease if change == 'cancel' else HTTPException):
        text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(400, 430), context={})
    assert len(calls) == 1 and len(db.scalars(select(ModelUsage)).all()) == 1


@pytest.mark.parametrize('change', ['cancel', 'membership'])
def test_consumed_partial_is_metered_before_late_revocation_and_never_delivered(native_db, generation_boundary, change):
    db, job = native_db
    replies, calls, during = generation_boundary
    replies.extend([BASE, INSERT_ONE])
    cancelled = [False]
    db.info['job_cancelled'] = lambda: cancelled[0]
    def revoke(ordinal):
        if ordinal != 1:
            return
        if change == 'cancel':
            cancelled[0] = True
        else:
            db.scalar(select(Membership).where(Membership.user_id == 'u', Membership.tenant_id == 't')).active = False
            db.commit()
    during[0] = revoke
    with pytest.raises(LostLease if change == 'cancel' else HTTPException):
        text.execute_text(db, job, skill_name='novel-writer', input_text=request_for(400, 430), context={})
    assert len(calls) == 2 and mode(calls[-1]['messages']) == 'novel_insert'
    assert len(db.scalars(select(ModelUsage)).all()) == 2


@pytest.mark.parametrize('denial', ['不构成', '不代表', '不意味着', '不能证明'])
def test_tight_publication_denial_is_not_an_affirmative_claim(native_db, generation_boundary, denial):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.append(f'所有内容为草稿计划，{denial}已发布内容或实际执行证明。')
    result = text.execute_text(db, job, skill_name='skill-campaign-planner', input_text='说明方案当前状态。', context={})
    assert result['quality_gate']['passed'] is True and len(calls) == 1


@pytest.mark.parametrize('denial', ['不构成', '不代表', '不意味着', '不能证明'])
def test_tight_denial_does_not_hide_a_later_real_publication_assertion(native_db, generation_boundary, denial):
    db, job = native_db
    replies, calls, _ = generation_boundary
    replies.append(f'草稿计划{denial}已发布内容，但已经发布三篇作品。')
    with pytest.raises(provider.ProviderError, match='发布'):
        text.execute_text(db, job, skill_name='skill-campaign-planner', input_text='说明方案当前状态。', context={})
    assert len(calls) == 3


def test_publication_disclaimer_cannot_shelter_unsupported_capture_or_light_claims(native_db, generation_boundary):
    db, job = native_db
    replies, _, _ = generation_boundary
    replies.append('草稿计划不构成已发布内容，但动线实拍体现采光充足。')
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='skill-campaign-planner', input_text='说明方案当前状态。', context={})

