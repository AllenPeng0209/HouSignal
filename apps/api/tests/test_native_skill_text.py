"""Native methods use scoped project records and verified search receipts."""
import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import provider
from app.config import settings
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.operations_models import ContentAccount, ContentPlan
from app.agent_models import ModelUsage
from app.job_models import Job
from app.db import Base


EXPECTED_NAMES = set('''ai-image-gen ai-music ai-video-gen asset-manager audio-denoise audio-editing audio-mix audio-visualizer
auto-short-video auto-subtitle batch-process beat-sync-video card-design card-quote card-xiaohongshu chart-visualization clipify
comparison-card copywriting data-report doc-convert ecom-details-image green-screen gzh-design image-editing image-enhance
infographic meme-generator mindmap multi-voice-dubbing novel-writer paper-explainer post-formatter poster-hero remove-bg
roi-calculator short-drama skill-account-diagnosis skill-algorithm-updates skill-article-outline skill-audience-profiler
skill-bilibili-upload skill-brand-onboarding skill-campaign-planner skill-carousel-planner skill-channels-upload skill-collab-proposal
skill-comment-insights skill-community-ops skill-competitor-analysis skill-content-calendar-log skill-content-calendar
skill-content-gap-analysis skill-content-matrix skill-content-postmortem skill-content-repurposing skill-content-strategy
skill-cross-platform-diff skill-cross-platform-publish skill-data-tracker skill-douyin-upload skill-event-calendar skill-hook-generator
skill-kuaishou-upload skill-livestream skill-my-account skill-news-intelligence skill-persona-check skill-positioning-analysis
skill-post-scorer skill-profile-builder skill-profile-manager skill-publish-analytics skill-publish-checklist skill-publish-log
skill-publish-notify skill-publish-scheduler skill-quality-gate skill-risk-scanner skill-rss-aggregator skill-seo-quality skill-short-link
skill-social-performance-review skill-strategy-advisor skill-topic-evaluator skill-trend-rider skill-trending-topics skill-ugc-discovery
skill-voice-builder skill-wechat-publisher skill-xhs-analyzer skill-xhs-comment-reply skill-xhs-publisher skill-zhihu-answer
skill-zhihu-publisher slideshow-video social-content style-transfer subtitle-translate template-library text-condenser text-polisher
tts-voiceover video-chapters video-editing video-highlights video-intro-outro video-production video-reframe video-script
video-strategy video-to-article voice-clone xhs-note-creator'''.split())


def native_modules():
    try:
        available = importlib.util.find_spec('app.content_skills.catalog') is not None
    except ModuleNotFoundError:
        available = False
    assert available, 'native catalog is not implemented'
    from app.content_skills import catalog, text
    return catalog, text


@pytest.fixture
def native_db(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "native.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='别的团队'),
                    User(id='u', email='content@example.invalid', password_hash='unused', name='编辑')])
        db.flush()
        db.add_all([Membership(user_id='u', tenant_id='t', role='content'),
                    Property(id='p', tenant_id='t', name='项目甲', city='上海'),
                    Property(id='p2', tenant_id='t', name='其他项目', city='杭州'),
                    Fact(id='verified', tenant_id='t', property_id='p', semantic_key='area', label='面积', value='329', unit='㎡', source='测绘文件', status='verified'),
                    Fact(id='unverified', tenant_id='t', property_id='p', semantic_key='hidden', label='待核验', value='PRIVATE_UNVERIFIED', source='未知', status='unverified'),
                    Fact(id='foreign', tenant_id='t', property_id='p2', semantic_key='hidden', label='其他项目', value='PRIVATE_FOREIGN', source='未知', status='verified'),
                    ContentAccount(id='a', tenant_id='t', property_id='p', platform='抖音', name='项目账号', positioning='当前项目定位', voice='具体自然', audience='本地改善家庭', red_lines='不承诺收益'),
                    ContentAccount(id='a2', tenant_id='t', property_id='p2', platform='抖音', name='PRIVATE_GLOBAL_ACCOUNT'),
                    ContentPlan(id='plan', tenant_id='t', property_id='p', title='真实排期', angle='户型动线', channel='抖音', topic_id='tour')])
        db.commit()
        yield db, SimpleNamespace(id='native-job', tenant_id='t', property_id='p', user_id='u', payload={})
    engine.dispose()


@pytest.fixture
def model_http(monkeypatch):
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    state = {'calls': [], 'search_count': 1, 'search_results': [{'title': '行业真实公开资料', 'url': 'https://example.org/research'}],
             'content': '根据当前项目资料生成的中文草稿。', 'finish_reason': 'stop', 'tool_calls': None,
             'get_calls': [], 'public_status': 200}
    def boundary(request):
        if request.method == 'GET':
            state['get_calls'].append(str(request.url))
            if request.url.host == 'feeds.example.org':
                return httpx.Response(state['public_status'], text=state.get('feed_text') or '<rss version="2.0"><channel><item><title>真实住宅资讯</title>'
                    '<link>https://example.org/article</link><description>真实订阅摘要。</description>'
                    '<pubDate>Tue, 13 Oct 2026 01:00:00 GMT</pubDate></item></channel></rss>')
            if request.url.host == '60s.viki.moe':
                return httpx.Response(state['public_status'], json={'code': 200, 'data': [
                    {'title': '真实公开热榜话题', 'url': 'https://example.org/hot', 'hot': 3800}]})
            identity = '0123456789abcdef01234567'
            initial = {'note': {'noteDetailMap': {identity: {'note': {'noteId': identity, 'title': '真实住宅笔记',
                'desc': '公开作品的真实正文。', 'user': {'nickname': '公开作者'},
                'interactInfo': {'likedCount': '1200', 'commentCount': '8'}}}}}}
            return httpx.Response(state['public_status'], text='<script>window.__INITIAL_STATE__=' + json.dumps(initial) + '</script>')
        payload = json.loads(request.content)
        state['calls'].append((request.url.path, payload))
        if request.url.path.endswith('/generation'):
            return httpx.Response(200, json={'output': {'text': '模型生成的伪造链接 https://fabricated.invalid/',
                'search_info': {'search_results': state['search_results']}},
                'usage': {'input_tokens': 13, 'output_tokens': 8, 'plugins': {'search': {'count': state['search_count']}}}})
        return httpx.Response(200, json={'model': 'synthetic-model', 'choices': [{'finish_reason': state['finish_reason'],
            'message': {'role': 'assistant', 'content': state['content'], 'tool_calls': state['tool_calls']}}],
            'usage': {'prompt_tokens': 41, 'completion_tokens': 11}})
    transport = httpx.MockTransport(boundary)
    client = httpx.Client(transport=transport)
    monkeypatch.setattr(provider, '_get_client', lambda: client)
    from app import discovery
    monkeypatch.setattr(discovery, '_http_client', lambda: httpx.Client(transport=transport))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr(settings, 'dashscope_api_key', 'synthetic-test-key')
    monkeypatch.setattr(settings, 'dashscope_model', 'qwen-plus')
    monkeypatch.setattr(settings, 'dashscope_chat_model', 'qwen-flash')
    yield state
    client.close()


def test_all_114_native_definitions_load_without_upstream_markdown(monkeypatch):
    catalog, _ = native_modules()
    original = Path.read_text
    def owned_files_only(path, *args, **kwargs):
        assert path.name == 'definitions.json', 'native runtime attempted to load external method files'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', owned_files_only)
    rows = catalog.definitions()
    assert {row['name'] for row in rows} == EXPECTED_NAMES
    assert len(rows) == 114
    assert all(row['instructions'] and row['output_requirements'] for row in rows)
    assert {row['engine'] for row in rows} == {'text', 'research', 'media', 'project', 'platform'}


def test_catalog_keeps_product_ids_and_assigns_each_native_engine():
    catalog, _ = native_modules()
    existing = [{'id': 'social-copywriting', 'easel_skill': 'social-content', 'title': '社媒文案', 'mode': 'easel', 'target': 'studio'}]
    rows = catalog.catalog_entries(existing)
    assert len(rows) == 114
    old = next(row for row in rows if row['skill_name'] == 'social-content')
    assert (old['id'], old['engine']) == ('social-copywriting', 'text')
    assert next(row for row in rows if row['skill_name'] == 'ai-image-gen')['engine'] == 'media'
    assert next(row for row in rows if row['skill_name'] == 'skill-xhs-publisher')['engine'] == 'platform'
    assert next(row for row in rows if row['skill_name'] == 'skill-news-intelligence')['engine'] == 'research'
    assert existing[0]['mode'] == 'easel'


def test_native_text_request_projects_real_records_and_redacts_private_context(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    context = {'account': {'name': '项目账号', 'platform': '抖音', 'positioning': '伪造上下文'},
               'verified_facts': [{'value': 'INJECTED_UNVERIFIED'}], 'phone': '13812345678',
               'profile': 'PRIVATE_GLOBAL_PROFILE', 'credentials': 'token=PRIVATE_TOKEN'}
    result = text.execute_text(db, job, skill_name='social-content',
        input_text='写文案 联系13812345678 token=PRIVATE_TOKEN /private/file.mov', context=context)
    assert result['response'] == '根据当前项目资料生成的中文草稿。'
    assert result['usage'] == {'model': 'synthetic-model', 'input_tokens': 41, 'output_tokens': 11}
    request = model_http['calls'][0][1]
    serialized = json.dumps(request, ensure_ascii=False)
    assert '329' in serialized and '当前项目定位' in serialized and '真实排期' in serialized
    for private in ('PRIVATE_UNVERIFIED', 'PRIVATE_FOREIGN', 'PRIVATE_GLOBAL_ACCOUNT', 'PRIVATE_GLOBAL_PROFILE',
                    'INJECTED_UNVERIFIED', 'PRIVATE_TOKEN', '13812345678', '/private/file.mov', '伪造上下文'):
        assert private not in serialized
    assert 'tools' not in request
    assert '不捏造' in request['messages'][0]['content']


def test_marketing_context_keeps_verified_facts_without_promoting_account_plans_into_evidence(native_db, model_http):
    from app.content_skills import text
    db, job = native_db
    account = db.get(ContentAccount, 'a')
    account.voice = '短句、自然；实拍空间已经核验 PRIVATE_STYLE_CAPTURE'
    account.positioning = 'PRIVATE_INTERNAL_POSITIONING'
    account.audience = 'PRIVATE_INTERNAL_ACCEPTANCE_TEAM'
    plan = db.get(ContentPlan, 'plan')
    plan.title = 'PRIVATE_PLANNED_PHOTOGRAPHY 采光实拍计划'
    db.commit()
    text.execute_text(db, job, skill_name='copywriting', input_text='为改善家庭写咨询文案。', context={})
    payload = json.loads(model_http['calls'][0][1]['messages'][1]['content'])
    projected = payload['current_project_context']
    assert any(row['value'] == '329' for row in projected['verified_facts'])
    assert projected['account']['platform'] == '抖音'
    assert '短句' in projected['account']['style_preferences']
    serialized = json.dumps(projected, ensure_ascii=False)
    for invented_evidence in ('PRIVATE_STYLE_CAPTURE', 'PRIVATE_PLANNED_PHOTOGRAPHY',
                              'PRIVATE_INTERNAL_POSITIONING', 'PRIVATE_INTERNAL_ACCEPTANCE_TEAM'):
        assert invented_evidence not in serialized
    assert payload['user_request'] == '为改善家庭写咨询文案。'


def test_local_text_daily_allowance_does_not_increase_or_consume_cloud_allowance(native_db, monkeypatch):
    from app.content_skills import text
    db, job = native_db
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    monkeypatch.setattr(settings, 'native_local_text_daily_token_budget', 2000, raising=False)
    db.add_all([ModelUsage(id='local-spend', job_id='local-job', tenant_id='t', property_id='p', model='local:Qwen3', input_tokens=1100, output_tokens=200),
                ModelUsage(id='cloud-spend', job_id='cloud-job', tenant_id='t', property_id='p', model='qwen-plus', input_tokens=900, output_tokens=50),
                ModelUsage(id='foreign-local-spend', job_id='foreign-job', tenant_id='other', property_id='p2', model='local:Qwen3', input_tokens=5000, output_tokens=0)])
    db.flush()
    text.check_budget(db, job, 100, local_generation=True)
    text.check_budget(db, job, 20)
    with pytest.raises(HTTPException) as exhausted:
        text.check_budget(db, job, 50)
    assert exhausted.value.status_code == 429
    with pytest.raises(HTTPException) as local_exhausted:
        text.check_budget(db, job, 700, local_generation=True)
    assert local_exhausted.value.status_code == 429


def test_local_allowance_flag_cannot_raise_cloud_budget_when_backend_is_cloud(native_db, monkeypatch):
    from app.content_skills import text
    db, job = native_db
    monkeypatch.setattr(settings, 'native_text_backend', 'dashscope')
    monkeypatch.setattr(settings, 'app_env', 'development')
    monkeypatch.setattr(settings, 'development_token_budget', 1000)
    monkeypatch.setattr(settings, 'native_local_text_daily_token_budget', 10000, raising=False)
    db.add(ModelUsage(id='cloud-exhausted', job_id='cloud-job', tenant_id='t', property_id='p', model='qwen-plus', input_tokens=1000, output_tokens=0))
    db.flush()
    with pytest.raises(HTTPException) as exhausted:
        text.check_budget(db, job, local_generation=True)
    assert exhausted.value.status_code == 429


@pytest.mark.parametrize('unquoted_claim', [False, True])
@pytest.mark.parametrize('skill_name', ['skill-account-diagnosis', 'skill-audience-profiler', 'skill-brand-onboarding'])
def test_diagnosis_can_quote_registered_voice_but_cannot_turn_it_into_photography_evidence(native_db, model_http, unquoted_claim, skill_name):
    from app.content_skills import text
    db, job = native_db
    account = db.get(ContentAccount, 'a')
    account.voice = '用短句说明实拍空间与可核对事实'
    db.commit()
    model_http['content'] = '账号的表达风格字段为“用短句说明实拍空间与可核对事实”，这只是风格设定，需要补充实际素材。'
    if unquoted_claim:
        model_http['content'] += '当前项目全部实拍空间已经核验。'
        with pytest.raises(Exception) as rejected:
            text.execute_text(db, job, skill_name=skill_name, input_text='分析账号现有风格。', context={})
        assert '实拍' in str(rejected.value)
    else:
        result = text.execute_text(db, job, skill_name=skill_name, input_text='分析账号现有风格。', context={})
        assert result['response'] == model_http['content']


@pytest.mark.parametrize('name', ['skill-xhs-publisher', 'ai-video-gen', 'skill-profile-manager', 'paper-explainer', 'xhs-note-creator'])
def test_text_engine_cannot_impersonate_media_or_platform_mutations(native_db, model_http, name):
    _, text = native_modules()
    db, job = native_db
    with pytest.raises(HTTPException) as error:
        text.execute_text(db, job, skill_name=name, input_text='马上完成', context={})
    assert error.value.status_code == 409
    assert model_http['calls'] == []


def test_research_uses_native_search_receipts_before_writing_and_returns_real_sources(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    result = text.execute_text(db, job, skill_name='skill-news-intelligence', input_text='公开行业住宅资讯', context={})
    assert [path for path, _ in model_http['calls']] == [
        '/api/v1/services/aigc/text-generation/generation', '/compatible-mode/v1/chat/completions']
    search = model_http['calls'][0][1]
    assert search['parameters']['enable_search'] is True
    assert search['parameters']['search_options']['forced_search'] is True
    assert '项目甲' not in json.dumps(search, ensure_ascii=False)
    assert 'https://example.org/research' in result['response']
    assert result['source_refs'][0]['url'] == 'https://example.org/research'
    assert result['source_refs'][0]['retrieved_at']
    assert 'fabricated.invalid' not in result['response']


@pytest.mark.parametrize(('count', 'sources'), [(0, [{'title': '假来源', 'url': 'https://example.org/research'}]),
                                              (1, []), (1, [{'title': '危险地址', 'url': 'http://127.0.0.1/private'}])])
def test_research_refuses_generated_text_without_valid_search_evidence(native_db, model_http, count, sources):
    _, text = native_modules()
    db, job = native_db
    model_http.update(search_count=count, search_results=sources)
    with pytest.raises(HTTPException) as error:
        text.execute_text(db, job, skill_name='skill-news-intelligence', input_text='行业资讯', context={})
    assert error.value.status_code == 503
    assert len(model_http['calls']) == 1


def test_text_rechecks_project_membership_before_model_work(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    db.query(Membership).filter_by(user_id='u').one().active = False
    db.commit()
    with pytest.raises(HTTPException) as error:
        text.execute_text(db, job, skill_name='social-content', input_text='写文案', context={})
    assert error.value.status_code == 403
    assert model_http['calls'] == []


@pytest.mark.parametrize('finish', ['length', 'tool_calls'])
def test_native_text_rejects_incomplete_or_tool_executing_replies(native_db, model_http, finish):
    _, text = native_modules()
    db, job = native_db
    model_http['finish_reason'] = finish
    if finish == 'tool_calls':
        model_http['tool_calls'] = [{'id': 'call', 'type': 'function', 'function': {'name': 'publish', 'arguments': '{}'}}]
    with pytest.raises((HTTPException, provider.ProviderError)):
        text.execute_text(db, job, skill_name='social-content', input_text='写文案', context={})


def test_readiness_requires_model_configuration_without_starting_requests(monkeypatch, model_http):
    _, text = native_modules()
    assert text.readiness('social-content')['status'] == 'ready'
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    assert text.readiness('social-content')['status'] == 'setup'
    assert model_http['calls'] == []


def test_rss_research_parses_actual_feed_without_model_or_generated_sources(native_db, model_http, monkeypatch):
    native_modules()
    from app.content_skills import research
    db, job = native_db
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    result = research.execute(db, job, skill_name='skill-rss-aggregator', input_text='', context={},
        parameters={'feeds': 'https://feeds.example.org/rss'})
    assert result['source_refs'][0]['title'] == '真实住宅资讯'
    assert result['source_refs'][0]['url'] == 'https://example.org/article'
    assert result['source_refs'][0]['published_at'] == '2026-10-13T01:00:00+00:00'
    assert '真实订阅摘要' in result['response']
    assert model_http['get_calls'] == ['https://feeds.example.org/rss']
    assert model_http['calls'] == []


def test_rss_rejects_private_hosts_before_network_work(native_db, model_http):
    native_modules()
    from app.content_skills import research
    db, job = native_db
    with pytest.raises(HTTPException) as error:
        research.execute(db, job, skill_name='skill-rss-aggregator', input_text='', context={},
            parameters={'feeds': 'http://127.0.0.1/private'})
    assert error.value.status_code == 422
    assert model_http['get_calls'] == []


def test_atom_uses_article_link_and_preserves_nested_summary(native_db, model_http):
    native_modules()
    from app.content_skills import research
    db, _ = native_db
    model_http['feed_text'] = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>真实条目</title>'
        '<link rel="alternate" href="https://example.org/article"/>'
        '<link rel="self" href="https://feeds.example.org/entry.xml"/>'
        '<summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>真实嵌套摘要。</p></div></summary>'
        '<published>2026-10-14T01:00:00Z</published></entry></feed>')
    rows = research._feeds(db, {'feeds': 'https://feeds.example.org/atom'}, '')
    assert rows[0]['url'] == 'https://example.org/article'
    assert rows[0]['excerpt'] == '真实嵌套摘要。'


def test_feed_without_article_links_cannot_substitute_feed_url_as_evidence(native_db, model_http):
    native_modules()
    from app.content_skills import research
    db, _ = native_db
    model_http['feed_text'] = '<rss><channel><item><title>无原文链接条目</title><description>内容线索</description></item></channel></rss>'
    with pytest.raises(HTTPException) as error:
        research._feeds(db, {'feeds': 'https://feeds.example.org/rss'}, '')
    assert error.value.status_code == 503


def test_public_hot_list_uses_fixed_live_source_and_short_cache(native_db, model_http, monkeypatch):
    native_modules()
    from app.content_skills import research
    monkeypatch.setattr(research, '_TREND_CACHE', {}, raising=False)
    db, job = native_db
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    result = research.execute(db, job, skill_name='skill-trending-topics', input_text='', context={},
        parameters={'platform': 'weibo', 'limit': 5})
    again = research.execute(db, job, skill_name='skill-trending-topics', input_text='', context={},
        parameters={'platform': 'weibo', 'limit': 5})
    assert result['source_refs'][0]['title'] == '真实公开热榜话题'
    assert result['source_refs'][0]['heat'] == '3800'
    assert result['source_refs'][0]['retrieved_at'] == again['source_refs'][0]['retrieved_at']
    assert model_http['get_calls'] == ['https://60s.viki.moe/v2/weibo']
    assert model_http['calls'] == []


def test_public_hot_list_without_keyword_matches_is_not_a_successful_deliverable(native_db, model_http, monkeypatch):
    native_modules()
    from app.content_skills import research
    monkeypatch.setattr(research, '_TREND_CACHE', {}, raising=False)
    db, job = native_db
    with pytest.raises(HTTPException) as error:
        research.execute(db, job, skill_name='skill-trending-topics', input_text='未出现在榜单中的关键词', context={},
                         parameters={'platform': 'weibo', 'limit': 5})
    assert error.value.status_code == 503
    assert model_http['calls'] == []


def test_bilibili_trends_use_the_observed_first_party_hotword_contract(native_db, monkeypatch):
    from app.content_skills import research
    from app import discovery
    db, job = native_db
    monkeypatch.setattr(research, '_TREND_CACHE', {})
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    def transport(request):
        assert str(request.url) == 'https://s.search.bilibili.com/main/hotword'
        assert not request.headers.get('authorization') and not request.headers.get('cookie')
        return httpx.Response(200,json={'code':0,'timestamp':1791915186,
            'list':[{'keyword':'空间实测','show_name':'空间实测','heat_score':0.2,'pos':1}]})
    monkeypatch.setattr(discovery,'_http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    result=research.execute(db,job,skill_name='skill-trending-topics',input_text='',context={},
                            parameters={'platform':'bilibili','limit':5})
    source=result['source_refs'][0]
    assert source['title'] == '空间实测'
    assert source['url'] == 'https://s.search.bilibili.com/main/hotword'
    assert source['heat'] == ''  # Provider ranking scores are not measured heat units.
    assert source['retrieved_at']


def test_bilibili_trends_reject_first_party_api_error_codes(native_db, monkeypatch):
    from app.content_skills import research
    from app import discovery
    db, job = native_db
    monkeypatch.setattr(research,'_TREND_CACHE',{})
    monkeypatch.setattr(discovery,'_http_client',lambda:httpx.Client(transport=httpx.MockTransport(
        lambda request:httpx.Response(200,json={'code':-412,'list':[{'keyword':'不可采信'}]}))))
    with pytest.raises(HTTPException) as error:
        research.execute(db,job,skill_name='skill-trending-topics',input_text='',context={},
                         parameters={'platform':'bilibili','limit':5})
    assert error.value.status_code == 503


def test_xhs_analysis_requires_actual_public_note_data(native_db, model_http):
    native_modules()
    from app.content_skills import research
    db, job = native_db
    result = research.execute(db, job, skill_name='skill-xhs-analyzer',
        input_text='https://www.xiaohongshu.com/explore/0123456789abcdef01234567', context={})
    source = result['source_refs'][0]
    assert source['evidence'] == 'platform_public_note'
    assert source['metrics']['likes'] == 1200
    assert source['metrics']['comments'] == 8
    assert source['excerpt'] == '公开作品的真实正文。'
    assert model_http['calls'][0][0].endswith('/chat/completions')


def test_xhs_blocked_public_note_does_not_become_analytics(native_db, model_http):
    native_modules()
    from app.content_skills import research
    db, job = native_db
    model_http['public_status'] = 403
    with pytest.raises(HTTPException) as error:
        research.execute(db, job, skill_name='skill-xhs-analyzer',
            input_text='https://www.xiaohongshu.com/explore/0123456789abcdef01234567', context={})
    assert error.value.status_code == 503
    assert model_http['calls'] == []


def test_research_rejects_model_generated_unlisted_source_links(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '研究结论见 https://fabricated.invalid/report'
    with pytest.raises(HTTPException) as error:
        text.execute_text(db, job, skill_name='skill-news-intelligence', input_text='公开研究', context={})
    assert error.value.status_code == 503


def test_selected_document_context_contains_only_authorized_project_extracted_text(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    db.add(Asset(id='paper', tenant_id='t', property_id='p', name='授权论文.pdf', kind='document',
                 storage_key='PRIVATE_STORAGE_PATH', mime_type='application/pdf', sha256='0' * 64, byte_size=1,
                 rights='authorized', rights_evidence='作者许可', metadata_json={'model_processing_authorized': True},
                 extracted_text='论文表1：平均误差为0.25；研究仅覆盖模拟数据。'))
    db.commit()
    job.payload = {'asset_ids': ['paper'], 'model_processing_authorized': True}
    serialized = json.dumps(text.project_context(db, job, {}), ensure_ascii=False)
    assert '研究仅覆盖模拟数据' in serialized
    assert 'PRIVATE_STORAGE_PATH' not in serialized


def test_selected_documents_require_separate_asset_model_permission_before_model_work(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    db.add(Asset(id='paper', tenant_id='t', property_id='p', name='未许可论文.pdf', kind='document',
                 storage_key='PRIVATE_STORAGE_PATH', mime_type='application/pdf', sha256='0' * 64, byte_size=1,
                 rights='authorized', rights_evidence='阅读许可', metadata_json={'model_processing_authorized': False},
                 extracted_text='PRIVATE_UNAUTHORIZED_PAPER'))
    db.commit()
    job.payload = {'asset_ids': ['paper'], 'model_processing_authorized': True}
    with pytest.raises(HTTPException) as error:
        text.project_context(db, job, {})
    assert error.value.status_code == 409
    assert model_http['calls'] == []


@pytest.mark.parametrize('error', [HTTPException(403, '访问权限失效')])
def test_provider_boundary_preserves_project_permission_errors(native_db, model_http, monkeypatch, error):
    _, text = native_modules()
    db, job = native_db
    def denied(*args, **kwargs):
        raise error
    monkeypatch.setattr(provider, 'chat', denied)
    with pytest.raises(HTTPException) as caught:
        text.execute_text(db, job, skill_name='social-content', input_text='写文案', context={})
    assert caught.value is error


def test_provider_boundary_preserves_cancelled_lease(native_db, model_http, monkeypatch):
    _, text = native_modules()
    from app.worker import LostLease
    db, job = native_db
    error = LostLease()
    def cancelled(*args, **kwargs):
        raise error
    monkeypatch.setattr(provider, 'chat', cancelled)
    with pytest.raises(LostLease) as caught:
        text.execute_text(db, job, skill_name='social-content', input_text='写文案', context={})
    assert caught.value is error


def test_novel_continuation_uses_only_completed_native_chapters_in_current_project(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    created = datetime(2026, 10, 14, tzinfo=timezone.utc)
    def chapter(identifier, response, *, tenant='t', project='p', status='succeeded', name='novel-writer', engine='housignal_native', kind='content_skill'):
        return Job(id=identifier, tenant_id=tenant, property_id=project, user_id='u', kind=kind, status=status,
                   payload={'skill_name': name}, result={'response': response, 'execution_engine': engine, 'skill_name': name},
                   created_at=created)
    db.add_all([
        chapter('chapter-1', '阿岚把铜钥匙藏在旧桥栏杆里，约定黎明回到钟楼。'),
        chapter('foreign-project', 'PRIVATE_FOREIGN_CHAPTER', project='p2'),
        chapter('foreign-tenant', 'PRIVATE_OTHER_TENANT_CHAPTER', tenant='other'),
        chapter('cancelled', 'PRIVATE_CANCELLED_CHAPTER', status='cancelled'),
        chapter('other-task', 'PRIVATE_OTHER_TASK', name='social-content'),
        chapter('legacy-task', 'PRIVATE_GLOBAL_STORY', engine='external_runtime'),
        chapter(job.id, 'PRIVATE_CURRENT_JOB'),
    ])
    db.commit()
    text.execute_text(db, job, skill_name='novel-writer', input_text='续写下一章，保持人物与线索一致。', context={})
    request = model_http['calls'][0][1]
    serialized = json.dumps(request, ensure_ascii=False)
    assert '铜钥匙' in serialized and '黎明回到钟楼' in serialized
    assert 'PRIVATE_' not in serialized
    assert '文学虚构' in request['messages'][0]['content']
    assert request['max_tokens'] == 6000
    model_http['calls'].clear()
    text.execute_text(db, job, skill_name='social-content', input_text='写社媒文案', context={})
    assert '铜钥匙' not in json.dumps(model_http['calls'][0][1], ensure_ascii=False)


def test_novel_history_has_a_fixed_total_input_budget(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    for index in range(8):
        db.add(Job(id=f'chapter-{index}', tenant_id='t', property_id='p', user_id='u', kind='content_skill', status='succeeded',
            payload={'skill_name': 'novel-writer'}, result={'response': f'第{index}章开场。' + ('小说前文。' * 2400) + f'第{index}章结尾。',
                'execution_engine': 'housignal_native', 'skill_name': 'novel-writer'},
            created_at=datetime(2026, 10, 14, tzinfo=timezone.utc) + timedelta(minutes=index)))
    db.commit()
    text.execute_text(db, job, skill_name='novel-writer', input_text='续写下一章', context={})
    request = json.loads(model_http['calls'][0][1]['messages'][1]['content'])
    history = request['current_project_context'].get('fiction_history', [])
    assert 1 <= len(history) <= 3
    assert sum(len(entry['excerpt']) for entry in history) <= 18000
    assert '第7章结尾' in history[-1]['excerpt']


def test_text_metrics_are_computed_from_displayed_content(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '你好，世界！\n\nHello world.\n\n第三段。'
    result = text.execute_text(db, job, skill_name='text-condenser', input_text='摘要并说明真实字数', context={})
    metrics = result.get('text_metrics', {})
    assert metrics.get('characters') == 26
    assert metrics.get('characters_without_whitespace') == 21
    assert metrics.get('cjk_characters') == 7
    assert metrics.get('latin_words') == 2
    assert metrics.get('word_count') == 9
    assert metrics.get('paragraphs') == 3


def test_complete_long_novel_reply_is_not_silently_truncated(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = ('正文。' * 4300) + '章末完整句。'
    result = text.execute_text(db, job, skill_name='novel-writer', input_text='完成这一章', context={})
    assert result['response'] == model_http['content']
    assert result['text_metrics']['characters'] == len(model_http['content'])


def test_project_test_and_material_provenance_are_explicit_in_model_context(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    db.get(Property, 'p').is_test = True
    db.get(ContentPlan, 'plan').title = '采光实拍计划'
    db.add(Asset(id='synthetic-photo', tenant_id='t', property_id='p', name='合成验收图片', kind='image',
        storage_key='PRIVATE_FIXTURE_PATH', mime_type='image/png', sha256='0'*64, byte_size=1,
        rights='authorized', rights_evidence='验收合成素材', origin_type='test'))
    db.commit()
    context = text.project_context(db, job)
    assert context['project'].get('is_test') is True
    assert context.get('material_provenance', {}).get('verified_real_capture_available') is False
    assert context['plans'][0].get('evidence_type') == 'plan_not_execution_proof'
    assert 'PRIVATE_FIXTURE_PATH' not in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize('content', ['验收示例。所有内容基于真实拍摄与现场记录，实拍采光与动线。',
                                    '厨房与客厅相邻，隐形收纳让客厅更通透。'])
def test_unsupported_real_capture_and_spatial_relations_fail_quality_gate(native_db, model_http, content):
    _, text = native_modules()
    db, job = native_db
    db.get(Property, 'p').is_test = True
    db.add(Fact(id='relation', tenant_id='t', property_id='p', semantic_key='kitchen_dining', label='厨房关系',
                value='厨房与餐厅相邻', source='验收户型示例', status='verified'))
    db.commit()
    model_http['content'] = content
    with pytest.raises(provider.ProviderError) as error:
        text.execute_text(db, job, skill_name='copywriting', input_text='写验收项目文案', context={})
    assert 'PRIVATE_' not in str(error.value)
    assert len(model_http['calls']) == 1


def test_explicit_summary_character_range_is_enforced_by_actual_count(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '短摘要。'
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='text-condenser', input_text='摘要限定80至100个非空白字符，只输出摘要正文。原文：生活动线影响体验。', context={})
    model_http['content'] = '甲' * 80
    result = text.execute_text(db, job, skill_name='text-condenser', input_text='摘要限定80至100个非空白字符，只输出摘要正文。原文：生活动线影响体验。', context={})
    assert result['quality_gate']['passed'] is True
    assert result['text_metrics']['characters_without_whitespace'] == 80


def test_copywriting_checks_body_length_without_counting_faq_and_titles(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '## 标题\n1. 标题一\n2. 标题二\n3. 标题三\n\n## 正文\n短正文。\n\n## FAQ\n' + ('说明。' * 150)
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='copywriting', input_text='3个标题、约300字正文、FAQ 3问。', context={})


def test_local_retry_is_bounded_and_keeps_actual_usage_for_all_attempts(native_db, model_http, monkeypatch):
    _, text = native_modules()
    db, job = native_db
    replies = iter(['短摘要。', '甲'*80])
    calls = []
    def generated(messages, **kwargs):
        calls.append(messages)
        return {'model':'local-test','choices':[{'finish_reason':'stop','message':{'content':next(replies)}}],
                'usage':{'prompt_tokens':20,'completion_tokens':10}}
    monkeypatch.setattr(provider, 'chat', generated)
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(text, 'readiness', lambda _: {'status':'ready'})
    result = text.execute_text(db, job, skill_name='text-condenser', input_text='摘要限定80至100个非空白字符，只输出摘要正文。', context={})
    assert len(calls) == 2
    assert result['generation_attempts'] == 2
    assert result['usage']['input_tokens'] == 40
    assert result['usage']['output_tokens'] == 20
    assert result['quality_gate']['passed'] is True


def test_novel_prompt_omits_real_project_place_and_rejects_faked_word_count(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '阿岚在上海黄浦江边想：黎明是谁？\n字数：2087'
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='novel-writer', input_text='所有地点必须虚构，正文1800到2200个中文字符。阿岚必须在黎明去钟楼。', context={})
    request = json.loads(model_http['calls'][0][1]['messages'][1]['content'])
    assert '上海' not in json.dumps(request['current_project_context'], ensure_ascii=False)


def test_transform_does_not_receive_or_insert_unrelated_project_facts(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '这套329㎡的户型带来全新体验。'
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='post-formatter', input_text='改写受控原文：看房要结合家庭实际习惯。', context={})
    request = json.loads(model_http['calls'][0][1]['messages'][1]['content'])
    assert '329' not in json.dumps(request['current_project_context'], ensure_ascii=False)
    assert '真实排期' not in json.dumps(request['current_project_context'], ensure_ascii=False)


def test_account_diagnosis_uses_user_requested_dimensions(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '按定位、互动频次、粉丝转化和内容类型分析。'
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='skill-account-diagnosis', input_text='按定位、选题垂直度、形式、受众一致性四维诊断。', context={})
    request = json.loads(model_http['calls'][0][1]['messages'][1]['content'])
    assert request['requested_dimensions'] == ['定位','选题垂直度','形式','受众一致性']


def test_new_novel_first_chapter_ignores_earlier_and_rejected_chapters(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    db.add(Job(id='old-first', tenant_id='t', property_id='p', user_id='u', kind='content_skill', status='succeeded',
               payload={'skill_name':'novel-writer'}, result={'response':'不应出现在新小说中的旧人物。','skill_name':'novel-writer',
                   'execution_engine':'housignal_native','native_data':{'semantic_verdict':'failed'}}))
    db.commit()
    text.execute_text(db, job, skill_name='novel-writer', input_text='创作全新小说第一章。', context={})
    request = json.loads(model_http['calls'][0][1]['messages'][1]['content'])
    assert request['current_project_context']['fiction_history'] == []
    assert text.project_context(db, job, skill_name='novel-writer')['fiction_history'] == []


def test_pas_action_is_rejected_and_variant_length_is_measured_separately(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '## PAS\nProblem\n' + ('甲'*80) + '\nAction\n行动吧。\nSolution\n解决吧。\n## BAB\nBefore\n'+('乙'*90)+'\nAfter\n未来。\nBridge\n桥梁。'
    with pytest.raises(provider.ProviderError) as error:
        text.execute_text(db, job, skill_name='post-formatter', input_text='原文：生活要按习惯安排。分别用PAS和BAB，每种80至120个中文字符。', context={})
    assert 'PAS' in str(error.value)


def test_approximate_copywriting_words_count_nonblank_punctuation_and_digits(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    body = '甲'*250 + '，'*40 + '2026'*3
    model_http['content'] = '## 正文\n' + body
    result = text.execute_text(db, job, skill_name='copywriting', input_text='写约300字正文。', context={})
    assert result['quality_gate']['length_contract']['metric'] == 'characters_without_whitespace'
    assert result['text_metrics']['body_cjk_characters'] == 250
    assert result['text_metrics']['body_characters_without_whitespace'] == 302


@pytest.mark.parametrize('content', ['验收示例。实拍素材尚未提供，当前只有合成示意图。',
                                     '验收示例。缺乏真实拍摄证据，正式宣传前需核验。',
                                     '验收示例。计划将使用实拍图，目前需补充素材。'])
def test_capture_negation_and_future_plan_are_not_completed_capture_claims(native_db, model_http, content):
    _, text = native_modules()
    db, job = native_db
    db.get(Property, 'p').is_test=True;db.commit()
    model_http['content']=content
    result=text.execute_text(db,job,skill_name='copywriting',input_text='写验收项目准备说明。',context={})
    assert result['quality_gate']['passed'] is True


def test_quality_observer_preserves_safe_fragments_and_both_actual_body_counts(native_db, model_http):
    _, text = native_modules()
    db,job=native_db
    observed=[];db.info['text_quality_observer']=observed.append
    model_http['content']='## 正文\n' + '甲'*250 + '，'*40 + '2026'*3
    result=text.execute_text(db,job,skill_name='copywriting',input_text='约300字正文。',context={})
    assert result['quality_gate']['passed'] is True
    assert len(observed)==1
    assert observed[0]['body_metrics']['cjk_characters']==250
    assert observed[0]['body_metrics']['characters_without_whitespace']==302
    assert observed[0]['safe_excerpt']


def test_relation_question_and_negative_answer_do_not_claim_an_unsupported_relation(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    db.add(Fact(id='relation-question', tenant_id='t', property_id='p', semantic_key='kitchen_dining', label='厨房关系',
                value='厨房与餐厅相邻', source='验收户型示例', status='verified'))
    db.commit()
    model_http['content'] = ('厨房与客厅是否相邻？\n项目中厨房与餐厅相邻，未提供厨房与客厅相邻关系，该信息缺资料。')
    result = text.execute_text(db, job, skill_name='copywriting', input_text='只回答资料核验问题。', context={})
    assert result['quality_gate']['passed'] is True
    model_http['content'] += '\n厨房与餐厅相邻，实拍验证空间通透性。'
    with pytest.raises(provider.ProviderError, match='实拍'):
        text.execute_text(db, job, skill_name='copywriting', input_text='只回答资料核验问题。', context={})


@pytest.mark.parametrize('content', ['当前无已发布内容或实拍素材，无法评估平台表现。',
                                    '需先确认无实拍素材这一资料缺项，正式发布前核验。'])
def test_diagnosis_negative_capture_and_publication_statements_are_not_completed_claims(native_db, model_http, content):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = content
    result = text.execute_text(db, job, skill_name='skill-account-diagnosis', input_text='说明账号证据缺项。', context={})
    assert result['quality_gate']['passed'] is True


def test_diagnosis_cannot_invent_observed_south_facing_light(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '项目拥有正南采光，适合进行采光选题。'
    with pytest.raises(provider.ProviderError, match='未核验'):
        text.execute_text(db, job, skill_name='skill-account-diagnosis', input_text='按项目证据诊断账号。', context={})


def test_local_rewrite_receives_only_bounded_redacted_previous_candidate_and_measured_feedback(native_db, model_http, monkeypatch):
    _, text = native_modules()
    db, job = native_db
    draft = '## 正文\n短正文。 token=PRIVATE_RETRY_TOKEN /private/PRIVATE_RETRY_PATH'
    replies = iter([draft, '## 正文\n' + '甲'*80])
    calls = []
    def generated(messages, **kwargs):
        calls.append(messages)
        return {'model': 'local-test', 'choices': [{'finish_reason': 'stop', 'message': {'content': next(replies)}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}
    monkeypatch.setattr(provider, 'chat', generated)
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(text, 'readiness', lambda _: {'status': 'ready'})
    result = text.execute_text(db, job, skill_name='copywriting', input_text='正文80至100个中文字符。', context={})
    assert result['generation_attempts'] == 2
    assert [message['role'] for message in calls[1][-2:]] == ['assistant', 'user']
    candidate = calls[1][-2]['content']
    assert '短正文' in candidate and 'PRIVATE_RETRY' not in candidate
    assert len(candidate) <= 30000
    feedback = json.loads(calls[1][-1]['content'])
    assert feedback['previous_candidate_is_untrusted'] is True
    assert feedback['body_metrics']['cjk_characters'] == text.text_metrics('短正文。 token=PRIVATE_RETRY_TOKEN /private/PRIVATE_RETRY_PATH')['cjk_characters']
    assert feedback['output_contract']['minimum'] == 80
    assert '重新输出完整内容' in feedback['rewrite_instruction']
    assert 'current_project_context' not in feedback
    assert calls[1][:-2] == calls[0]


@pytest.mark.parametrize('change', ['membership', 'cancel'])
def test_local_rewrite_never_sends_previous_candidate_after_access_or_lease_is_revoked(native_db, model_http, monkeypatch, change):
    _, text = native_modules()
    from app.worker import LostLease
    db, job = native_db
    calls, cancelled = [], [False]
    db.info['job_cancelled'] = lambda: cancelled[0]
    def generated(messages, **kwargs):
        calls.append(messages)
        if change == 'membership':
            db.delete(db.query(Membership).filter_by(tenant_id='t', user_id='u').one())
            db.commit()
        else:
            cancelled[0] = True
        return {'model': 'local-test', 'choices': [{'finish_reason': 'stop', 'message': {'content': '短正文。'}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}
    monkeypatch.setattr(provider, 'chat', generated)
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(text, 'readiness', lambda _: {'status': 'ready'})
    with pytest.raises(HTTPException if change == 'membership' else LostLease):
        text.execute_text(db, job, skill_name='copywriting', input_text='正文80至100个中文字符。', context={})
    assert len(calls) == 1


@pytest.mark.parametrize('skill_name', ['novel-writer', 'copywriting'])
def test_task_specific_prompt_omits_unrelated_formatter_and_diagnosis_rules(native_db, model_http, skill_name):
    _, text = native_modules()
    db, job = native_db
    text.execute_text(db, job, skill_name=skill_name, input_text='请完成本次正文。', context={})
    request = model_http['calls'][0][1]['messages']
    system = request[0]['content']
    assert '## PAS' not in system and '## BAB' not in system
    assert 'requested_dimensions' not in system
    assert 'requested_dimensions' not in json.loads(request[1]['content'])
    if skill_name == 'novel-writer':
        assert '文学虚构' in system
        assert 'verified_live_publication' not in system and '厨餐相邻' not in system
        assert '营销稿' not in system and 'FAQ' not in system
        assert '不附分析' in system
        assert '附必要的连续性说明' not in json.dumps(json.loads(request[1]['content'])['output_requirements'], ensure_ascii=False)


@pytest.mark.parametrize('heading', ['## PAS', '## PAS（Problem-Agitate-Solution）'])
def test_novel_framework_appendix_is_rejected_and_never_counts_as_story_length(native_db, model_http, heading):
    _, text = native_modules()
    db, job = native_db
    observed = []
    db.info['text_quality_observer'] = observed.append
    model_http['content'] = '# 雾港钟楼\n' + '甲'*70 + '\n' + heading + '\n' + '乙'*25 + '\n## BAB\n' + '丙'*25
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='novel-writer', input_text='创作第一章，正文100至120个中文字符。', context={})
    assert observed[0]['body_metrics']['cjk_characters'] == 70
    assert any('正文实际计数70' in error for error in observed[0]['errors'])
    assert any('故事' in error and 'PAS' in error for error in observed[0]['errors'])
    assert all('FAQ' not in error for error in observed[0]['errors'])


@pytest.mark.parametrize('metatext', ['## 线索分析\n钥匙是线索。', '## 连续性说明\n本章与下一章一致。',
                                     '## 字数统计\n已达到要求。'])
def test_novel_meta_appendices_are_rejected_even_if_narrative_itself_meets_length(native_db, model_http, metatext):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '# 雾港钟楼\n' + '甲'*100 + '\n' + metatext
    with pytest.raises(provider.ProviderError):
        text.execute_text(db, job, skill_name='novel-writer', input_text='创作第一章，正文100至120个中文字符。', context={})


def test_novel_appendix_cannot_hide_after_an_independent_body_section(native_db, model_http):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = '## 正文\n' + '甲'*100 + '\n## PAS\n' + '乙'*50
    with pytest.raises(provider.ProviderError, match='故事'):
        text.execute_text(db, job, skill_name='novel-writer', input_text='创作第一章，正文100至120个中文字符。', context={})


@pytest.mark.parametrize('content', ['阿岚终于明白，真正黎明不是时间，而是自己的选择。',
                                    '她知道，真正的黎明，不是太阳升起，而是她的选择。'])
def test_novel_cannot_reinterpret_explicit_dawn_deadline_as_nontemporal(native_db, model_http, content):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = content
    with pytest.raises(provider.ProviderError, match='黎明'):
        text.execute_text(db, job, skill_name='novel-writer', input_text='阿岚必须在黎明前把钥匙送到钟楼。', context={})


def _planned_outline(amounts=(300, 400, 500, 450, 350)):
    return '\n\n'.join(f'## 第{index}节 生活动线问题\n论点：从日常习惯梳理需求。\n证据：需收集家庭动线记录，当前缺资料。'
                         f'\n示例：假设家庭先准备早餐，再出门。\n预计字数：{amount}字。'
                         for index, amount in enumerate(amounts, 1))


@pytest.mark.parametrize('user_request', ['为2000字文章设计大纲，包含5节、每节预计字数。',
                                    '为2000字文章制定写作计划，包含5节、每节预计字数。',
                                    '为2000字文章写提纲，包含5节、每节预计字数。'])
def test_planned_article_length_is_not_the_outline_deliverable_length(native_db, model_http, user_request):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = _planned_outline()
    result = text.execute_text(db, job, skill_name='skill-article-outline', input_text=user_request, context={})
    assert result['quality_gate']['passed'] is True
    contract = json.loads(model_http['calls'][0][1]['messages'][1]['content'])['output_contract']
    assert 'minimum' not in contract
    assert contract['planned_article']['target_minimum'] == 2000
    assert contract['planned_article']['section_count'] == 5
    assert '不是大纲正文' in model_http['calls'][0][1]['messages'][0]['content']
    assert result['text_metrics']['characters_without_whitespace'] != 2000


def test_outline_can_have_separate_planned_fulltext_and_actual_deliverable_lengths():
    catalog, text = native_modules()
    contract = text._length_contract(catalog.definition('skill-article-outline'),
        '为2000字文章写大纲，包含5节每节预计字数；大纲自身80至100字。')
    assert contract['minimum'] == 80 and contract['maximum'] == 100
    assert contract['planned_article']['target_minimum'] == 2000


@pytest.mark.parametrize('content', [_planned_outline((100, 100, 100, 100, 100)),
                                    _planned_outline((400, 400, 400, 400)),
                                    _planned_outline((350, 350, 350, 350, 350, 250)),
                                    _planned_outline().replace('预计字数：350字。', '预计字数：待分配。'),
                                    _planned_outline().replace('## 第5节', '## 第4节')])
def test_outline_requires_requested_sections_and_allocations_near_target(native_db, model_http, content):
    _, text = native_modules()
    db, job = native_db
    model_http['content'] = content
    with pytest.raises(provider.ProviderError, match='小节|预计字数|分配'):
        text.execute_text(db, job, skill_name='skill-article-outline',
            input_text='为2000字文章写大纲，包含5节、每节论点/证据/示例/预计字数。', context={})


def test_real_article_outline_case_is_classified_as_an_article_plan():
    catalog, text = native_modules()
    path = Path(__file__).resolve().parents[3] / 'scripts/skill_acceptance/text_research_cases.json'
    case = next(row for row in json.loads(path.read_text())['cases'] if row['skill_name'] == 'skill-article-outline')
    contract = text._length_contract(catalog.definition('skill-article-outline'), case['input'])
    assert 'minimum' not in contract
    assert contract['planned_article']['section_count'] == 5
    assert contract['planned_article']['target_minimum'] == 2000


@pytest.mark.parametrize('user_request', ['写2000字文章，不要大纲。', '写2000字文章正文，不能附提纲。'])
def test_full_article_length_does_not_become_planning_length_when_outlines_are_forbidden(user_request):
    catalog, text = native_modules()
    contract = text._length_contract(catalog.definition('skill-article-outline'), user_request)
    assert contract['minimum'] == 2000 and contract['maximum'] == 2000
    assert 'planned_article' not in contract
