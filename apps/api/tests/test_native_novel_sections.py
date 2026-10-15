"""Resident long-novel section contract against real scope and usage records.

All model responses are controlled boundary fixtures, never commercial or
independent semantic acceptance. Actual failed prose is loaded only from a
private, optional local fixture; it must never be copied into this source file.
"""
import copy
import importlib.util
import json
from pathlib import Path
import re

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import provider
from app.agent_models import ModelUsage
from app.config import settings
from app.core_models import Fact, Membership
from app.content_skills import text
from app.content_skills.catalog import definition
from app.worker import LostLease
from test_native_skill_text import native_db, model_http


REQUEST = ('创作第一章，所有地点虚构，以阿岚的第三人称限知视角叙述。'
           '她必须在黎明到来之前把铜钥匙送到灰鸦港钟楼，保留未解决的门后秘密。'
           '正文1800至2200个中文字符，只交付小说标题和故事正文。')
CONTRACT = {'minimum': 1800, 'maximum': 2200, 'metric': 'cjk_characters',
            'body_only': False, 'per_variant': False}
TITLE = '雾港的钥匙'
STATES = [
    {'location': '灰鸦港岸边', 'time': '深夜', 'viewpoint': '阿岚第三人称限知', 'props': {'铜钥匙': '阿岚持有'}},
    {'location': '旧桥桥头', 'time': '深夜', 'viewpoint': '阿岚第三人称限知', 'props': {'铜钥匙': '阿岚持有'}},
    {'location': '钟楼台阶', 'time': '黎明前', 'viewpoint': '阿岚第三人称限知', 'props': {'铜钥匙': '阿岚持有', '蜡封信': '阿岚持有'}},
    {'location': '钟楼内廊', 'time': '黎明前', 'viewpoint': '阿岚第三人称限知', 'props': {'铜钥匙': '阿岚持有', '蜡封信': '阿岚持有'}},
    {'location': '钟楼暗门前', 'time': '黎明前', 'viewpoint': '阿岚第三人称限知', 'props': {'铜钥匙': '门锁内', '蜡封信': '阿岚持有'}},
]
PLAN = {'title': TITLE, 'stages': [
    {'index': 1, 'start_state': STATES[0], 'events': ['发现港口撤走的渡船', '选择旧桥'], 'end_state': STATES[1]},
    {'index': 2, 'start_state': STATES[1], 'events': ['避开桥上巡夜人', '取得蜡封信'], 'end_state': STATES[2]},
    {'index': 3, 'start_state': STATES[2], 'events': ['辨认台阶刻痕', '进入钟楼内廊'], 'end_state': STATES[3]},
    {'index': 4, 'start_state': STATES[3], 'events': ['把铜钥匙放入暗门锁', '留下门后秘密'], 'end_state': STATES[4]},
]}

# Distinct local story data. The model critic in these tests is intentionally
# controlled; these paragraphs are not a substitute for whole-story SEM review.
SEEDS = [
    ('阿岚在灰鸦港岸边醒来，窗外的雾遮住了钟楼。铜钥匙硌着掌心，她记得老人说过，必须在黎明到来之前送到那扇门前。'
     '渡船已经离岸，码头边只剩一条断开的缆绳。她问卖灯的妇人有没有别的路，妇人指向雾里那座旧桥，却没有收下她的铜钱。'
     '阿岚把钥匙藏进内袋，沿着岸边前行。她看见船钩上挂着干枯的海草，桩子上的水线比昨夜更高。'
     '离开码头前，她把湿披风系紧，听见钟楼传来一次低沉的响声。桥头没有灯，她只能借着海面的微光辨认木板。'),
    ('旧桥的栏杆断了一截，阿岚停下来寻找稳固的落脚处。桥上巡夜人提着灯走来，她缩进桥柱的阴影，手指始终按着内袋中的铜钥匙。'
     '巡夜人抱怨风太大，灯罩不停作响。他走过之后，桥下响起两次敲击。阿岚没有应声，而是蹲下查看脚边绳结上的蜡屑。'
     '蜡屑里嵌着一根蓝线，线头一直通向栏杆背面。她顺着线找到一封蜡封信，信上只有钟楼的印记，没有寄信人的名字。'
     '她把信收好，绕过松动的木板，沿着桥另一头的窄路登上钟楼台阶。雾仍然很浓，天空没有亮起。'),
    ('阿岚在钟楼台阶前停下，铜钥匙仍握在手中，蜡封信藏在袖口。门上的铁环被风吹得轻轻碰撞，她不敢让响声传得更远。'
     '台阶边缘有三道细刻痕，最后一道朝着右侧的石缝。她用指尖抹掉潮湿的砂粒，发现石缝里有一根能转动的短杆。'
     '短杆转动后，侧门开出一道窄缝。阿岚先听了一会儿，只有楼里齿轮缓慢啮合的声音。她侧身进入，门在背后停住，没有关严。'
     '内廊尽头透出一点灯光，她看见墙上挂着一幅倒置的钟面。她没有拆信，只把封口上的图案同钟面的边缘对照。'),
    ('阿岚沿着内廊来到暗门前，钥匙和蜡封信都还在她手边。门锁上缺了一小片铜，形状正好与钥匙柄上的凹口相合。'
     '她把钥匙插进锁里，没有立刻转动。门后的气流带着咸味，像港口退潮时露出的石滩。她重新看了一眼信上的印记，确认缺口的位置。'
     '第一声短促的铃响从高处传来，黎明尚未到来，她终于把钥匙转了半圈。暗门向里面退开，她把手放在门边，不让门撞上墙壁。'
     '里面有一张空椅子，椅背搭着她父亲曾经穿过的披风。阿岚没有跨过门槛，因为椅子底下正传来一个陌生人的呼吸声。'),
]
DETAILS = [
    ['盐雾沾湿眉毛', '石缝渗出冷水', '灯影落在码头', '远船没入雾中', '缆绳贴着木桩', '风声盖住脚步', '浪花拍向岸边', '街角传来犬吠'],
    ['桥柱留着刻痕', '旧木泛起潮气', '灯光越过栏杆', '绳头垂入水面', '板缝透出微光', '蜡封贴着掌心', '鞋底沾着泥沙', '雾气压低声音'],
    ['齿轮藏在高处', '石壁积着冷灰', '灯芯轻轻摇动', '门缝留着砂砾', '衣袖擦过墙面', '铜环映出微光', '钟面指着夜半', '地砖印着足迹'],
    ['暗门边缘发冷', '信封压着衣襟', '椅腿陷入阴影', '锁芯响了一声', '披风垂在地上', '微光照着门槛', '钟声隔着石墙', '潮气掠过指尖'],
]


def cjk(value):
    return len(re.findall(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]', value))


def story_stage(index):
    prose = SEEDS[index]
    for detail in DETAILS[index]:
        if cjk(prose) >= 470:
            break
        prose += f'她留意到{detail}，先停住脚步观察，再确认身后的来路仍然清楚，才继续向前。'
    assert 450 <= cjk(prose) <= 550
    return prose


PROSE = [story_stage(index) for index in range(4)]
WHOLE = '# ' + TITLE + '\n\n' + '\n\n'.join(PROSE)


def section_module():
    name = 'app.content_skills.tools.novel_sections'
    assert importlib.util.find_spec(name) is not None, 'resident novel section generation is not implemented'
    from app.content_skills.tools import novel_sections
    return novel_sections


def phase(messages):
    try:
        value = json.loads(messages[-1]['content'])
    except (TypeError, ValueError):
        return {'generation_mode': 'full'}
    return value if isinstance(value, dict) else {'generation_mode': 'full'}


def stage_index(data):
    current = data.get('current_stage')
    return current.get('index') if isinstance(current, dict) else current


@pytest.fixture
def section_boundary(model_http, monkeypatch):
    state = {'calls': [], 'counts': {}, 'plan': copy.deepcopy(PLAN), 'during': None, 'full': WHOLE,
             'generation': {}, 'review': {}, 'whole_review': {'issues': []}}

    def generate(messages, **kwargs):
        data = phase(messages)
        mode, index = data.get('generation_mode', 'full'), stage_index(data)
        assert mode in {'full', 'novel_plan', 'novel_section', 'novel_section_review', 'novel_whole_review'}, 'Unknown novel generation phase'
        if mode in {'novel_section', 'novel_section_review'}:
            assert type(index) is int and 1 <= index <= 4, 'Section phase requires a valid planned stage index'
        key = (mode, index)
        ordinal = state['counts'].get(key, 0)
        state['counts'][key] = ordinal + 1
        state['calls'].append({'messages': copy.deepcopy(messages), 'data': copy.deepcopy(data), **kwargs})
        if state['during']:
            state['during'](mode, index, ordinal)
        if mode == 'novel_plan':
            value = state['plan']
        elif mode == 'novel_section':
            value = state['generation'].get((index, ordinal), PROSE[index - 1])
        elif mode == 'novel_section_review':
            value = state['review'].get((index, ordinal), {'issues': [], 'state_after': STATES[index]})
        elif mode == 'novel_whole_review':
            value = state['whole_review']
        else:
            value = state['full']
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return {'model': 'ppu:novel-fixture', 'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 9}}

    monkeypatch.setattr(provider, 'chat', generate)
    monkeypatch.setattr(settings, 'native_text_backend', 'resident_ppu')
    monkeypatch.setattr(text, 'readiness', lambda _: {'status': 'ready'})
    return state


def execute(db, job, request=REQUEST):
    return text.execute_text(db, job, skill_name='novel-writer', input_text=request, context={})


def calls_in(boundary, mode, index=None):
    return [call for call in boundary['calls'] if call['data'].get('generation_mode', 'full') == mode
            and (index is None or stage_index(call['data']) == index)]


def assert_metered(db, count):
    rows = db.scalars(select(ModelUsage)).all()
    assert len(rows) == count
    assert len({row.id for row in rows}) == count
    assert all((row.tenant_id, row.property_id, row.job_id) == ('t', 'p', 'native-job') for row in rows)
    assert all((row.model, row.input_tokens, row.output_tokens) == ('ppu:novel-fixture', 17, 9) for row in rows)
    assert sum(row.input_tokens for row in rows) == count * 17
    assert sum(row.output_tokens for row in rows) == count * 9


def test_1800_to_2200_cjk_allocation_is_four_independently_bounded_sections():
    module = section_module()
    assert module.section_contracts(CONTRACT) == [
        {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'},
        {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'},
        {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'},
        {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'},
    ]


@pytest.mark.parametrize(('name', 'contract', 'backend', 'expected'), [
    ('novel-writer', CONTRACT, 'resident_ppu', True),
    ('novel-writer', {**CONTRACT, 'minimum': 1200}, 'resident_ppu', True),
    ('novel-writer', {**CONTRACT, 'minimum': 1199}, 'resident_ppu', False),
    ('novel-writer', {**CONTRACT, 'metric': 'characters_without_whitespace'}, 'resident_ppu', False),
    ('novel-writer', CONTRACT, 'mlx', False),
    ('novel-writer', CONTRACT, 'dashscope', False),
    ('copywriting', CONTRACT, 'resident_ppu', False),
    ('novel-writer', None, 'resident_ppu', False),
])
def test_section_routing_requires_explicit_long_cjk_resident_novel(name, contract, backend, expected):
    assert section_module().applies(definition(name), contract, backend) is expected


@pytest.mark.parametrize(('backend', 'input_text'), [
    ('mlx', REQUEST), ('dashscope', REQUEST),
    ('resident_ppu', REQUEST.replace('1800至2200', '450至550')),
])
def test_existing_backend_and_short_novel_paths_keep_single_full_generation(native_db, section_boundary, monkeypatch, backend, input_text):
    db, job = native_db
    monkeypatch.setattr(settings, 'native_text_backend', backend)
    if backend == 'resident_ppu':
        section_boundary['full'] = '# ' + TITLE + '\n\n' + PROSE[0]
    result = execute(db, job, input_text)
    assert result['response'] == section_boundary['full']
    assert [call['data'].get('generation_mode', 'full') for call in section_boundary['calls']] == ['full']
    assert_metered(db, 1)


def test_long_novel_accepts_four_stages_then_whole_review_and_meters_all_ten_calls(native_db, section_boundary):
    db, job = native_db
    result = execute(db, job)
    modes = [call['data'].get('generation_mode', 'full') for call in section_boundary['calls']]
    assert modes == ['novel_plan', 'novel_section', 'novel_section_review', 'novel_section',
                     'novel_section_review', 'novel_section', 'novel_section_review',
                     'novel_section', 'novel_section_review', 'novel_whole_review']
    assert result['response'] == WHOLE
    assert result['model_calls'] == 10
    assert result['usage']['input_tokens'] == 170 and result['usage']['output_tokens'] == 90
    assert result['quality_gate']['passed'] is True
    for index, call in enumerate(calls_in(section_boundary, 'novel_section'), 1):
        data = call['data']
        assert data['accepted_prose'] == '\n\n'.join(PROSE[:index - 1])
        assert data['tracked_state'] == STATES[index - 1]
        assert data['section_contract'] == {'minimum': 450, 'maximum': 550, 'metric': 'cjk_characters'}
        assert data['user_request'] == REQUEST
        assert call.get('model') is None
    for call in calls_in(section_boundary, 'novel_section_review'):
        index = stage_index(call['data'])
        assert call['data']['candidate'] == PROSE[index - 1]
        assert call.get('response_format') == {'type': 'json_object'}
    assert calls_in(section_boundary, 'novel_whole_review')[0]['data']['candidate'] == WHOLE
    assert_metered(db, 10)


def test_short_stage_rewrites_only_that_stage_without_retaining_bad_prose(native_db, section_boundary):
    db, job = native_db
    bad = '阿岚在旧桥前停下。她握紧铜钥匙，听见桥下传来一声轻响。'
    section_boundary['generation'][(2, 0)] = bad
    result = execute(db, job)
    stage_calls = calls_in(section_boundary, 'novel_section')
    assert [stage_index(call['data']) for call in stage_calls] == [1, 2, 2, 3, 4]
    rewritten = stage_calls[2]['data']
    assert rewritten['accepted_prose'] == PROSE[0]
    assert bad not in rewritten['accepted_prose']
    assert rewritten['tracked_state'] == STATES[1]
    assert bad not in result['response'] and result['response'] == WHOLE
    assert_metered(db, len(section_boundary['calls']))


@pytest.mark.parametrize('kind', ['replayed_event', 'future_event', 'prop_continuity', 'viewpoint',
                                 'time_deadline', 'missing_requirement', 'state_boundary'])
def test_model_review_rejection_has_precise_quote_and_cannot_enter_next_stage(native_db, section_boundary, kind):
    db, job = native_db
    bad = PROSE[1].replace('她顺着线找到一封蜡封信', '她又在栏杆后拿到同一封蜡封信')
    quote = '她又在栏杆后拿到同一封蜡封信'
    issue = {'kind': kind, 'quote': quote, 'reason': '该动作与已接受事件或当前叙事边界矛盾，需要局部重写。'}
    section_boundary['generation'][(2, 0)] = bad
    section_boundary['review'][(2, 0)] = {'issues': [issue], 'state_after': STATES[2]}
    result = execute(db, job)
    rewritten_calls = calls_in(section_boundary, 'novel_section', 2)
    assert len(rewritten_calls) == 2, 'Only the rejected stage must receive one local rewrite'
    rewrite = rewritten_calls[1]['data']
    assert rewrite['accepted_prose'] == PROSE[0]
    assert quote in json.dumps(rewrite, ensure_ascii=False)
    assert kind in json.dumps(rewrite, ensure_ascii=False)
    next_calls = calls_in(section_boundary, 'novel_section', 3)
    assert len(next_calls) == 1, 'Next stage must be generated exactly once after acceptance'
    assert next_calls[0]['data']['accepted_prose'] == '\n\n'.join(PROSE[:2])
    assert bad not in result['response'] and result['response'] == WHOLE
    assert_metered(db, 12)


def test_actual_failed_prose_and_insert_supply_private_paraphrase_replay_regression(native_db, section_boundary):
    fixture = Path(__file__).resolve().parents[3] / '.runtime/novel_sections_regression_fixture.json'
    if not fixture.is_file():
        pytest.skip('Actual failed chapter fixture is deliberately private and excluded from source export')
    assert fixture.stat().st_mode & 0o777 == 0o600
    private = json.loads(fixture.read_text(encoding='utf-8'))
    original, insert, quote = private['original_prose'], private['rejected_insert'], private['replay_quote']
    def private_check(condition, message):
        if not condition:
            raise AssertionError(message)
    private_check(original and insert and quote in insert and quote not in original,
                  'Private fixture must preserve actual prose and a changed replay quote')
    private_check(quote in private['section_candidate'] and 450 <= cjk(private['section_candidate']) <= 550
                  and 450 <= cjk(private['accepted_stage']) <= 550,
                  'Private actual excerpts must reach the model critic within section bounds')
    db, job = native_db
    section_boundary['generation'][(1, 0)] = private['accepted_stage']
    section_boundary['generation'][(2, 0)] = private['section_candidate']
    section_boundary['review'][(2, 0)] = {'issues': [{'kind': 'replayed_event', 'quote': quote,
        'reason': '此段换词复演原章里已经发生的取信与开门事件，不能作为新的连续推进。'}], 'state_after': STATES[2]}
    result = execute(db, job)
    rewritten_calls = calls_in(section_boundary, 'novel_section', 2)
    assert len(rewritten_calls) == 2, 'Actual changed replay must receive a local rewrite'
    assert len(calls_in(section_boundary, 'novel_section_review', 2)) == 2, 'Changed replay must reach the model critic'
    rewrite = rewritten_calls[1]['data']
    private_check(rewrite['accepted_prose'] == private['accepted_stage'],
                  'Actual accepted prose must be the exact retained context')
    private_check(quote not in rewrite['accepted_prose'] and quote in json.dumps(rewrite, ensure_ascii=False),
                  'Private changed replay must remain rejected while precise feedback is preserved')
    expected = '# ' + TITLE + '\n\n' + '\n\n'.join([private['accepted_stage'], *PROSE[1:]])
    private_check(quote not in result['response'] and result['response'] == expected,
                  'Rejected private replay must not enter final delivered prose')
    assert_metered(db, 12)


def test_second_bad_stage_exhausts_only_one_rewrite_and_never_calls_future_stages(native_db, section_boundary):
    db, job = native_db
    bad = PROSE[1]
    issue = {'kind': 'prop_continuity', 'quote': '她顺着线找到一封蜡封信',
             'reason': '前段已经持有同一封信，本段不能再次取得而没有交代转移。'}
    for ordinal in (0, 1):
        section_boundary['review'][(2, ordinal)] = {'issues': [issue], 'state_after': STATES[2]}
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert len(calls_in(section_boundary, 'novel_section', 2)) == 2
    assert len(calls_in(section_boundary, 'novel_section_review', 2)) == 2
    assert not calls_in(section_boundary, 'novel_section', 3)
    assert not calls_in(section_boundary, 'novel_whole_review')
    assert_metered(db, 7)


@pytest.mark.parametrize('mutation', ['missing_stages', 'five_stages', 'nonsequential', 'missing_start',
                                   'state_not_object', 'too_many_props', 'overlong_state', 'unknown_top_key'])
def test_malformed_plan_fails_closed_after_consumed_plan_call(native_db, section_boundary, mutation):
    db, job = native_db
    plan = section_boundary['plan']
    if mutation == 'missing_stages':
        del plan['stages']
    elif mutation == 'five_stages':
        plan['stages'].append(copy.deepcopy(plan['stages'][-1]))
    elif mutation == 'nonsequential':
        plan['stages'][1]['index'] = 3
    elif mutation == 'missing_start':
        del plan['stages'][0]['start_state']
    elif mutation == 'state_not_object':
        plan['stages'][0]['end_state'] = '桥头'
    elif mutation == 'too_many_props':
        plan['stages'][0]['start_state']['props'] = {f'道具{i}': '阿岚持有' for i in range(9)}
    elif mutation == 'overlong_state':
        plan['stages'][0]['start_state']['location'] = '地点' * 81
    else:
        plan['passed'] = True
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert len(section_boundary['calls']) == 1
    assert_metered(db, 1)


@pytest.mark.parametrize('review', [
    {'issues': []},
    {'passed': True, 'issues': [], 'state_after': STATES[1]},
    {'issues': [], 'state_after': '桥头'},
    {'issues': [{'kind': 'unsupported_fact', 'quote': '阿岚在灰鸦港岸边醒来', 'reason': '此枚举不属于叙事连续性合同。'}], 'state_after': STATES[1]},
    {'issues': [{'kind': 'prop_continuity', 'quote': '候选里面根本不存在这个引用', 'reason': '不能接受不位于候选的引用。'}], 'state_after': STATES[1]},
    {'issues': [{'kind': 'viewpoint', 'quote': '阿岚', 'reason': '引用短于八字符不能精确描述问题。'}], 'state_after': STATES[1]},
    {'issues': [{'kind': 'viewpoint', 'quote': '阿岚在灰鸦港岸边醒来', 'reason': '短'}], 'state_after': STATES[1]},
    '{"issues":[],"issues":[],"state_after":{}}',
])
def test_malformed_review_does_not_authorize_acceptance_and_consumed_calls_stay_metered(native_db, section_boundary, review):
    db, job = native_db
    section_boundary['review'][(1, 0)] = review
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert not calls_in(section_boundary, 'novel_section', 2)
    assert_metered(db, 3)


def test_reviewer_actual_state_is_carried_to_next_stage_instead_of_predicted_plan_end(native_db, section_boundary):
    db, job = native_db
    actual = {**STATES[1], 'props': {**STATES[1]['props'], '灯笼': '阿岚持有'}}
    section_boundary['review'][(1, 0)] = {'issues': [], 'state_after': actual}
    execute(db, job)
    next_calls = calls_in(section_boundary, 'novel_section', 2)
    assert len(next_calls) == 1, 'An accepted review must authorize the next stage'
    assert next_calls[0]['data']['tracked_state'] == actual


def test_whole_review_is_required_even_when_all_individual_reviews_pass(native_db, section_boundary):
    db, job = native_db
    quote = '椅背搭着她父亲曾经穿过的披风'
    section_boundary['whole_review'] = {'issues': [{'kind': 'missing_requirement', 'quote': quote,
        'reason': '整章人物线索前后失配，单段通过不能替代完整语义验收。'}]}
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert len(calls_in(section_boundary, 'novel_whole_review')) == 1
    assert_metered(db, 10)


def test_original_whole_quality_gate_rejects_real_city_even_when_model_review_is_empty(native_db, section_boundary):
    db, job = native_db
    section_boundary['generation'][(2, 0)] = PROSE[1].replace('旧桥', '上海旧桥')
    section_boundary['generation'][(2, 1)] = PROSE[1].replace('旧桥', '上海旧桥')
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert not calls_in(section_boundary, 'novel_whole_review') or len(calls_in(section_boundary, 'novel_whole_review')) == 1
    assert_metered(db, len(section_boundary['calls']))


@pytest.mark.parametrize(('change', 'at_mode', 'expected_count'), [
    ('membership', 'novel_plan', 1), ('snapshot', 'novel_plan', 1), ('cancel', 'novel_plan', 1),
    ('membership', 'novel_section_review', 3), ('snapshot', 'novel_section_review', 3),
    ('cancel', 'novel_section_review', 3), ('cancel', 'novel_whole_review', 10),
])
def test_scope_cancel_and_membership_fence_next_phase_and_keep_consumed_ledger(native_db, section_boundary, change, at_mode, expected_count):
    db, job = native_db
    cancelled = [False]
    db.info['job_cancelled'] = lambda: cancelled[0]

    def revoke(mode, index, ordinal):
        if mode != at_mode or ordinal != 0 or (index not in (None, 1)):
            return
        if change == 'cancel':
            cancelled[0] = True
            return
        # Revoke externally, retaining the caller's cached identity map. A
        # correct phase guard must refresh persisted permission/project data.
        db.commit()
        with Session(db.get_bind(), expire_on_commit=False) as external:
            if change == 'membership':
                external.scalar(select(Membership).where(Membership.user_id == 'u', Membership.tenant_id == 't')).active = False
            else:
                external.get(Fact, 'verified').value = '330'
            external.commit()

    section_boundary['during'] = revoke
    with pytest.raises(LostLease if change == 'cancel' else HTTPException):
        execute(db, job)
    assert len(section_boundary['calls']) == expected_count
    assert_metered(db, expected_count)


def test_backend_drift_after_plan_never_falls_back_to_paid_provider(native_db, section_boundary, monkeypatch):
    db, job = native_db
    section_boundary['during'] = lambda mode, index, ordinal: (
        monkeypatch.setattr(settings, 'native_text_backend', 'dashscope') if mode == 'novel_plan' else None)
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert len(section_boundary['calls']) == 1
    assert_metered(db, 1)


@pytest.mark.parametrize(('at_mode', 'expected_count'), [
    ('novel_plan', 1), ('novel_section', 2), ('novel_section_review', 3), ('novel_whole_review', 10),
])
@pytest.mark.parametrize('expired_at', [1000.0, 1001.0])
def test_deadline_expiry_after_consumed_phase_prevents_delivery_and_retains_ledger(native_db, section_boundary, monkeypatch, at_mode, expected_count, expired_at):
    db, job = native_db
    module = section_module()
    now = [100.0]
    monkeypatch.setattr(module, 'monotonic', lambda: now[0])

    def exhaust(mode, index, ordinal):
        if mode == at_mode:
            now[0] = expired_at

    section_boundary['during'] = exhaust
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert len(section_boundary['calls']) == expected_count
    assert_metered(db, expected_count)


def test_every_phase_timeout_is_bounded_by_hard_remaining_deadline(native_db, section_boundary, monkeypatch):
    db, job = native_db
    module = section_module()
    now = [100.0]
    monkeypatch.setattr(module, 'monotonic', lambda: now[0])

    def advance(mode, index, ordinal):
        now[0] += 80.0

    section_boundary['during'] = advance
    execute(db, job)
    assert len(section_boundary['calls']) == 10
    for ordinal, call in enumerate(section_boundary['calls']):
        assert 0 < call['timeout_seconds'] <= 900 - ordinal * 80
    assert_metered(db, 10)


def test_provider_failure_receipt_remains_metered_without_retry_or_paid_fallback(native_db, section_boundary, monkeypatch):
    db, job = native_db
    original = provider.chat

    def fail_review(messages, **kwargs):
        if phase(messages).get('generation_mode') == 'novel_section_review':
            raise provider.ProviderError('controlled consumed inference failure', usage_receipt={
                'model': 'ppu:novel-fixture', 'usage': {'prompt_tokens': 17, 'completion_tokens': 9}})
        return original(messages, **kwargs)

    monkeypatch.setattr(provider, 'chat', fail_review)
    with pytest.raises(provider.ProviderError):
        execute(db, job)
    assert len(section_boundary['calls']) == 2
    assert_metered(db, 3)
