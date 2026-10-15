"""Per-page and per-title contracts, not live model acceptance."""
import json
from pathlib import Path

import pytest

from app import provider
from app.content_skills import catalog, text
from test_native_skill_text import native_db, model_http


def case_input(skill_name):
    path = Path(__file__).resolve().parents[3] / 'scripts/skill_acceptance/text_research_cases.json'
    return next(row['input'] for row in json.loads(path.read_text())['cases'] if row['skill_name'] == skill_name)


CAROUSEL_INPUT = case_input('skill-carousel-planner')
HOOK_INPUT = case_input('skill-hook-generator')
TITLES = ['早餐先从哪开始', '先记下高峰时段', '观察交叉与等待', '比较不同的安排', '带着问题再看房']
BODIES = ['先记录一家人在准备早餐时分别做什么，暂不判断空间优劣。',
          '选一段全家同时出门的时间，记下谁先到达各个操作位置。',
          '用不同颜色画出各人的路径，标记交叉位置与等待时间。',
          '把备餐、取物、出门分开观察，再比较是否存在重复绕行。',
          '记下一个最想确认的问题，带着记录讨论下一步观察安排。']


def carousel(*, titles=TITLES, bodies=BODIES, ids=(1, 2, 3, 4, 5)):
    return '文字策划草稿，未生成图片。\n\n' + '\n\n'.join(
        f'## 第{number}页\n标题：{title}\n正文：{body}\n布局：上方标题，下方路线图与说明。\n配图建议：使用自制示意图。'
        for number, title, body in zip(ids, titles, bodies))


def hooks(*, count=12, title_override=None):
    categories = ['问题', '对比', '场景', '误区']
    values = ['早餐路径怎么观察', '同时备餐先看哪里', '先记等待再谈动线', '路径交叉说明什么',
              '备餐和取物有何不同', '换个顺序再观察', '忙碌早晨记下什么', '一起出门怎么安排',
              '取杯子时观察哪里', '绕行一定是缺点吗', '一张图能说明全部吗', '先问习惯再下判断']
    if title_override is not None:
        values[4] = title_override
    return '\n\n'.join(f'## 第{index+1}组\n标题：{values[index]}\n前3秒口播：先看自己的早餐步骤，再讨论哪里需要进一步观察。'
                        f'\n分类：{categories[index//3]}\n适用平台：抖音\n选择理由：把生活问题转为可以记录的观察。'
                        for index in range(count))


def test_original_carousel_case_has_per_page_maxima_not_a_full_document_exact_length(native_db, model_http):
    db, job = native_db
    model_http['content'] = carousel()
    result = text.execute_text(db, job, skill_name='skill-carousel-planner', input_text=CAROUSEL_INPUT, context={})
    assert result['quality_gate']['passed'] is True
    contract = result['quality_gate']['length_contract']
    assert 'minimum' not in contract and 'maximum' not in contract
    assert contract['pages'] == {'count': 5, 'title': {'maximum': 12, 'metric': 'cjk_characters'},
                                 'body': {'maximum': 60, 'metric': 'characters_without_whitespace'}}
    assert result['text_metrics']['characters_without_whitespace'] > 60
    system = model_http['calls'][0][1]['messages'][0]['content']
    assert '每页' in system and '## 第1页' in system


@pytest.mark.parametrize('invalid, expected', [
    (carousel(titles=[*TITLES[:2], '甲'*13, *TITLES[3:]]), '第3页标题'),
    (carousel(bodies=[*BODIES[:1], '甲'*58+'2026', *BODIES[2:]]), '第2页正文'),
    (carousel(ids=(1, 2, 3, 4)), '轮播必须'),
    (carousel(ids=(1, 2, 3, 4, 4)), '轮播必须'),
    (carousel(titles=[*TITLES[:4], TITLES[0]], bodies=[*BODIES[:4], BODIES[0]]), '重复页'),
    (carousel().replace('标题：观察交叉与等待', '标题：'), '第3页'),
    (carousel().replace('正文：选一段全家同时出门的时间，记下谁先到达各个操作位置。', '正文：'), '第2页'),
])
def test_carousel_rejects_missing_duplicate_or_oversized_individual_pages(native_db, model_http, invalid, expected):
    db, job = native_db
    model_http['content'] = invalid
    with pytest.raises(provider.ProviderError, match=expected):
        text.execute_text(db, job, skill_name='skill-carousel-planner', input_text=CAROUSEL_INPUT, context={})


def test_carousel_field_counts_exclude_layout_image_notes_and_other_pages(native_db, model_http):
    db, job = native_db
    model_http['content'] = carousel().replace('使用自制示意图。', '建议画五组位置关系示意，标记入口与常用操作点。'*8)
    result = text.execute_text(db, job, skill_name='skill-carousel-planner', input_text=CAROUSEL_INPUT, context={})
    assert result['quality_gate']['passed'] is True


def test_original_hook_case_checks_each_title_only_and_keeps_twelve_groups(native_db, model_http):
    db, job = native_db
    model_http['content'] = hooks()
    result = text.execute_text(db, job, skill_name='skill-hook-generator', input_text=HOOK_INPUT, context={})
    contract = result['quality_gate']['length_contract']
    assert 'minimum' not in contract and 'maximum' not in contract
    assert contract['title_groups'] == {'count': 12, 'title': {'maximum': 18, 'metric': 'cjk_characters'}}
    assert result['text_metrics']['cjk_characters'] > 18
    assert '## 第1组' in model_http['calls'][0][1]['messages'][0]['content']


@pytest.mark.parametrize('invalid, expected', [
    (hooks(title_override='甲'*19), '第5组标题'),
    (hooks(count=11), '标题必须'),
    (hooks().replace('## 第12组', '## 第11组'), '标题必须'),
    (hooks(title_override='早餐路径怎么观察'), '重复标题'),
    (hooks().replace('标题：绕行一定是缺点吗', '标题：'), '第10组'),
])
def test_hook_rejects_missing_duplicate_or_oversized_individual_titles(native_db, model_http, invalid, expected):
    db, job = native_db
    model_http['content'] = invalid
    with pytest.raises(provider.ProviderError, match=expected):
        text.execute_text(db, job, skill_name='skill-hook-generator', input_text=HOOK_INPUT, context={})


def test_long_hook_speech_and_category_labels_are_not_counted_as_title_text(native_db, model_http):
    db, job = native_db
    model_http['content'] = hooks().replace('先看自己的早餐步骤，再讨论哪里需要进一步观察。',
                                         '用一段较长口播说明观察方法，标题本身仍然只有少量字符。'*5)
    result = text.execute_text(db, job, skill_name='skill-hook-generator', input_text=HOOK_INPUT, context={})
    assert result['quality_gate']['passed'] is True


@pytest.mark.parametrize('label, replacement', [('前3秒口播', '口播'), ('选择理由', '理由'), ('适用平台', '平台')])
def test_hook_supports_clear_field_aliases_without_mixing_their_text_into_title(native_db, model_http, label, replacement):
    db, job = native_db
    model_http['content'] = hooks().replace(label + '：', replacement + '：')
    assert text.execute_text(db, job, skill_name='skill-hook-generator', input_text=HOOK_INPUT, context={})['quality_gate']['passed'] is True


@pytest.mark.parametrize('field', ['前3秒口播', '选择理由', '适用平台', '分类'])
def test_hook_preserves_all_requested_group_fields(native_db, model_http, field):
    db, job = native_db
    lines = hooks().splitlines()
    del lines[next(index for index, line in enumerate(lines) if line.startswith(field + '：'))]
    model_http['content'] = '\n'.join(lines)
    with pytest.raises(provider.ProviderError, match='第1组'):
        text.execute_text(db, job, skill_name='skill-hook-generator', input_text=HOOK_INPUT, context={})


@pytest.mark.parametrize('content', ['不构成现场实测记录，仅为内容计划。', '流程为计划设计，未实拍执行。'])
def test_tight_capture_denials_are_not_completed_capture_claims(native_db, model_http, content):
    db, job = native_db
    model_http['content'] = content
    assert text.execute_text(db, job, skill_name='skill-content-strategy', input_text='说明内容计划当前状态。', context={})['quality_gate']['passed'] is True


@pytest.mark.parametrize('content', ['未实拍，但现在已经真实拍摄。',
                                   '建议以后核验，尚未实拍，但现在已经真实拍摄。',
                                   '模拟方案，不构成现场实测记录，但现在已经真实拍摄。'])
def test_capture_denial_does_not_wash_a_later_affirmative_capture_claim(native_db, model_http, content):
    db, job = native_db
    model_http['content'] = content
    with pytest.raises(provider.ProviderError, match='实拍'):
        text.execute_text(db, job, skill_name='skill-content-strategy', input_text='说明内容计划当前状态。', context={})


@pytest.mark.parametrize('content', [
    '| 实拍视频 | ❌ 不适用 | 没有实拍素材，当前仅有已授权照片 |',
    '| **实拍视频** | ❌ 不适用 | 真实空间感强，动态流畅 | 无实拍视频素材，且项目为“自制模拟验收”，无真实拍摄条件支持 |',
    '实拍视频未采用，当前仅使用自制示意。',
    '| 现场记录 | 未执行 | 当前没有现场核验回执 |',
])
def test_explicit_unused_capture_modes_do_not_claim_completed_capture(native_db, model_http, content):
    db, job = native_db
    model_http['content'] = content
    assert text.execute_text(db, job, skill_name='video-strategy', input_text='比较可用素材和未采用的模式。', context={})['quality_gate']['passed'] is True


@pytest.mark.parametrize('content', [
    '实拍视频已经采用，但画质不适用。',
    '| 实拍视频 | 适用 | 已完成 |',
    '实拍视频未采用，但现场记录已经完成。',
    '实拍视频：已完成；下次不适用。',
])
def test_unused_mode_labels_do_not_qualify_other_positive_capture_claims(native_db, model_http, content):
    db, job = native_db
    model_http['content'] = content
    with pytest.raises(provider.ProviderError, match='实拍'):
        text.execute_text(db, job, skill_name='video-strategy', input_text='比较可用素材和未采用的模式。', context={})


def test_existing_body_summary_novel_and_formatter_lengths_stay_separate_from_item_maxima():
    for skill_name, request, minimum, maximum in [
        ('copywriting', '约300字正文。', 270, 330),
        ('text-condenser', '摘要80至100个非空白字符。', 80, 100),
        ('novel-writer', '正文1800至2200个中文字符。', 1800, 2200),
        ('post-formatter', 'PAS与BAB每种约250字。', 225, 275),
    ]:
        contract = text._length_contract(catalog.definition(skill_name), request)
        assert (contract['minimum'], contract['maximum']) == (minimum, maximum)
        assert 'pages' not in contract and 'title_groups' not in contract


def test_body_only_summary_does_not_force_a_markdown_section(native_db, model_http):
    db, job = native_db
    body = '看房不能只看面积或照片，还需核对厨房到餐桌、玄关到储物区及多人同时活动的路径。不同家庭人数、习惯与需求各异，应结合实际空间和生活方式判断适配，不能由面积或单张照片作出保证。'
    assert 80 <= text.text_metrics(body)['characters_without_whitespace'] <= 100
    model_http['content'] = body
    result = text.execute_text(db, job, skill_name='text-condenser', input_text=case_input('text-condenser'), context={})
    assert result['response'] == body
    system = model_http['calls'][0][1]['messages'][0]['content']
    assert '只输出摘要正文' in system
    assert '使用对应的独立Markdown二级标题' not in system


def test_body_only_summary_rejects_extraneous_heading(native_db, model_http):
    db, job = native_db
    body = '看房不能只看面积或照片，还需核对厨房到餐桌、玄关到储物区及多人同时活动的路径。不同家庭人数、习惯与需求各异，应结合实际空间和生活方式判断适配，不能由面积或单张照片作出保证。'
    model_http['content'] = '## 正文\n' + body
    with pytest.raises(provider.ProviderError, match='只输出摘要正文'):
        text.execute_text(db, job, skill_name='text-condenser', input_text=case_input('text-condenser'), context={})


def test_article_outline_accepts_complete_bold_numeric_word_budgets():
    content = '\n\n'.join(f'## 第{index}节\n论点：先提出待核对问题。\n**预计字数：400**\n证据：待补资料。' for index in range(1, 6))
    errors, _, _ = text._quality_errors(content, catalog.definition('skill-article-outline'),
                                      case_input('skill-article-outline'), {})
    assert not any('预计字数' in error for error in errors)


def test_article_outline_still_rejects_non_numeric_or_duplicate_word_budgets():
    valid = '\n\n'.join(f'## 第{index}节\n预计字数：400\n证据：待补资料。' for index in range(1, 6))
    for invalid in (valid.replace('预计字数：400', '**预计字数：很多**', 1),
                    valid.replace('预计字数：400', '**预计字数：400**\n预计字数：300', 1)):
        errors, _, _ = text._quality_errors(invalid, catalog.definition('skill-article-outline'),
                                          case_input('skill-article-outline'), {})
        assert any('预计字数' in error for error in errors)


@pytest.mark.parametrize('content', [
    '| 场景假设 | “采光实拍候选日历” | 仅为草稿计划，不代表已经拍摄 |',
    '“采光实拍候选日历”对应的方案还未执行，仅供内部讨论。',
])
def test_registered_quoted_plan_title_is_not_a_capture_assertion(content):
    context = {'plans': [{'title': '采光实拍候选日历', 'status': 'draft'}]}
    errors, _, _ = text._quality_errors(content, catalog.definition('skill-audience-profiler'), '提出明确假设。', context)
    assert not any('实拍证据' in error for error in errors)


@pytest.mark.parametrize('content', [
    '已经拍摄“采光实拍候选日历”。',
    '根据“采光实拍候选日历”完成真实拍摄。',
])
def test_registered_plan_title_does_not_prove_actual_execution(content):
    context = {'plans': [{'title': '采光实拍候选日历', 'status': 'draft'}]}
    errors, _, _ = text._quality_errors(content, catalog.definition('skill-audience-profiler'), '提出明确假设。', context)
    assert any('实拍证据' in error for error in errors)


@pytest.mark.parametrize('content', [
    '所有内容均以示意素材为依据，不承诺实拍或真实体验。',
    '所有内容均基于示意素材制作，未核验实拍证据，不承诺真实拍摄或现场记录。',
])
def test_tight_denied_capture_lists_are_not_actual_execution(content):
    errors, _, _ = text._quality_errors(content, catalog.definition('skill-campaign-planner'), '规划内部演练。', {})
    assert not any('实拍证据' in error for error in errors)


def test_capture_denial_list_does_not_hide_a_positive_claim_after_contrast():
    errors, _, _ = text._quality_errors('不承诺实拍或现场记录，但现在已经真实拍摄。',
                                      catalog.definition('skill-campaign-planner'), '规划内部演练。', {})
    assert any('实拍证据' in error for error in errors)


def test_capture_material_gap_heading_is_not_a_capture_claim():
    content = '4. **实拍素材**：所有素材为“自制场景示意”，无法验证空间真实尺度与动线流畅度。'
    errors, _, _ = text._quality_errors(content, catalog.definition('skill-article-outline'), '列出资料缺口。', {})
    assert not any('实拍证据' in error for error in errors)


def test_capture_material_heading_still_rejects_positive_completed_capture():
    errors, _, _ = text._quality_errors('4. **实拍素材**：已完成真实拍摄，场景为自制示意。',
                                      catalog.definition('skill-article-outline'), '列出资料缺口。', {})
    assert any('实拍证据' in error for error in errors)

