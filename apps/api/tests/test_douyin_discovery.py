"""Read-only Douyin search keeps private browser state outside search evidence."""
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest


def adapter():
    assert importlib.util.find_spec('app.douyin_discovery'), 'read-only native Douyin adapter must exist'
    from app import douyin_discovery
    return douyin_discovery


def browser():
    assert importlib.util.find_spec('app.content_skills.douyin_browser'), 'controlled public browser runner must exist'
    from app.content_skills import douyin_browser
    return douyin_browser


def request(**changes):
    from app.discovery import DiscoverySearch
    return DiscoverySearch(query='住宅', platforms=['douyin'], **changes)


def runtime(tmp_path, *, connected=True):
    from app.douyin_research_runtime import DouyinResearchRuntime
    rt = DouyinResearchRuntime(root=tmp_path, python=sys.executable)
    rt._cache = (time.monotonic(), {'python': True, 'browser': True})
    if connected:
        scope = rt.scope('tenant', 'project', create=True)
        (scope / 'profiles' / 'DouyinResearchProfile').mkdir(parents=True)
        rt._write(scope / 'status.json', {'state': 'success', 'loggedIn': True,
            'verified': True, 'ts': time.time()})
    return rt


WORK = {'aweme_id': '7559057198329981234', 'desc': '住宅三室户型，南向客厅',
        'create_time': 1728795600,
        'statistics': {'digg_count': 12000, 'comment_count': 0, 'collect_count': 400,
                       'share_count': 20},
        'author': {'nickname': '住宅观察', 'sec_uid': 'private-author-id', 'phone': 'private-phone'},
        'video': {'play_addr': {'url_list': ['https://media.invalid/private']}},
        'private_message': 'private-message', 'token': 'private-token'}


def test_actual_search_payload_preserves_public_dates_null_counts_and_discards_private_fields():
    parser = browser()
    result = parser.parse_public_payload({'status_code': 0, 'data': [{'type': 1, 'aweme_info': WORK}],
                                          'cookie': 'private-cookie'})
    assert len(result) == 1
    item = result[0]
    assert item['content_id'] == '7559057198329981234'
    assert item['url'] == 'https://www.douyin.com/video/7559057198329981234'
    assert item['published_at'] == '2024-10-13T05:00:00+00:00'
    assert item['metrics'] == {'likes': 12000, 'comments': 0, 'saves': 400, 'shares': 20, 'views': None}
    assert item['author'] == '住宅观察'
    assert item['metrics_source'] == 'douyin_public_search'
    assert all(value not in json.dumps(result) for value in ('private-cookie', 'private-token',
        'private-phone', 'private-author-id', 'private-message', 'media.invalid'))


def test_missing_invalid_dates_and_bool_counts_are_not_invented():
    data = {**WORK, 'create_time': True, 'statistics': {'digg_count': True, 'play_count': -1,
        'collect_count': '1.2万', 'comment_count': '0'}}
    item = browser().parse_public_payload({'aweme_list': [data]})[0]
    assert item['published_at'] is None
    assert item['metrics'] == {'likes': None, 'views': None, 'comments': None, 'saves': None, 'shares': None}
    assert item['metrics_observed_at'] is None
    assert item['qualification'] == 'candidate_unverified'


@pytest.mark.parametrize('url', [
    'https://www.douyin.com/aweme/v1/web/im/message/?keyword=住宅',
    'https://www.douyin.com/aweme/v1/web/general/search/single/?keyword=家居',
    'https://www.douyin.com.evil.test/aweme/v1/web/search/item/?keyword=住宅',
    'https://user:secret@www.douyin.com/aweme/v1/web/search/item/?keyword=住宅',
    'http://www.douyin.com/aweme/v1/web/search/item/?keyword=住宅',
])
def test_only_current_keyword_public_search_responses_can_be_observed(url):
    assert browser().allowed_search_response(url, '住宅') is False


def test_allowlisted_search_path_accepts_exact_query_without_emitting_url_tokens():
    assert browser().allowed_search_response(
        'https://www.douyin.com/aweme/v1/web/general/search/single/?keyword=%E4%BD%8F%E5%AE%85&msToken=secret', '住宅')


def test_parser_is_bounded_and_does_not_walk_arbitrary_private_objects():
    data = [{'aweme_info': {**WORK, 'aweme_id': str(7559057198329981200 + n)}} for n in range(30)]
    results = browser().parse_public_payload({'data': data, 'private': {'aweme_info': WORK}})
    assert len(results) == 20
    assert browser().parse_public_payload({'private': {'aweme_info': WORK}}) == []
    assert browser().parse_public_payload({'data': [{'aweme_info': {**WORK, 'aweme_id': '../credential'}}]}) == []


def test_unconnected_account_never_opens_browser(tmp_path, monkeypatch):
    module = adapter()
    monkeypatch.setattr(module, 'research_runtime', runtime(tmp_path, connected=False))
    monkeypatch.setattr(module, '_run_browser', lambda *a, **kw: pytest.fail('unconnected profile opened'))
    items, state = module.search_public_douyin('tenant', 'project', request())
    assert items == [] and state['status'] == 'login_required'


def test_search_uses_matching_private_project_profile_and_returns_only_current_public_data(tmp_path, monkeypatch):
    module = adapter()
    rt = runtime(tmp_path)
    monkeypatch.setattr(module, 'research_runtime', rt)
    def run(scope, query, sort, cancelled=None):
        assert scope == rt.scope('tenant', 'project')
        assert query == '住宅' and sort == 'engagement'
        return {'status': 'partial', 'items': browser().parse_public_payload({'data': [{'aweme_info': WORK}]})}
    monkeypatch.setattr(module, '_run_browser', run)
    items, state = module.search_public_douyin('tenant', 'project', request())
    assert len(items) == 1 and state['status'] == 'partial'
    assert state['adapter'] == 'douyin_public_browser'
    other, other_state = module.search_public_douyin('other-tenant', 'project', request())
    assert other == [] and other_state['status'] == 'login_required'
    assert str(tmp_path) not in json.dumps(state)


def test_search_serializes_against_research_login_and_probe(tmp_path, monkeypatch):
    module = adapter()
    rt = runtime(tmp_path)
    monkeypatch.setattr(module, 'research_runtime', rt)
    from app.douyin_research_runtime import DouyinResearchRuntime
    other = DouyinResearchRuntime(root=tmp_path, python=sys.executable)
    def run(scope, query, sort, cancelled=None):
        with pytest.raises(RuntimeError):
            with other.session_lock('tenant', 'project'):
                pass
        return {'status': 'challenge', 'items': [], 'reason': 'secret-token'}
    monkeypatch.setattr(module, '_run_browser', run)
    items, state = module.search_public_douyin('tenant', 'project', request())
    assert items == [] and state['status'] == 'challenge'
    assert 'secret-token' not in state['reason']
    with other.session_lock('tenant', 'project'):
        pass


def test_symlinked_profile_is_rejected_before_browser_launch(tmp_path, monkeypatch):
    module = adapter()
    rt = runtime(tmp_path)
    scope = rt.scope('tenant', 'project')
    profile = scope / 'profiles' / 'DouyinResearchProfile'
    profile.rmdir()
    external = tmp_path / 'global-profile'; external.mkdir()
    profile.symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(module, 'research_runtime', rt)
    monkeypatch.setattr(module, '_run_browser', lambda *a, **kw: pytest.fail('external profile opened'))
    with pytest.raises(ValueError):
        module.search_public_douyin('tenant', 'project', request())


def test_unverified_or_expired_account_requires_login(tmp_path, monkeypatch):
    module = adapter()
    rt = runtime(tmp_path)
    scope = rt.scope('tenant', 'project')
    rt._write(scope / 'status.json', {'state': 'success', 'loggedIn': True, 'verified': True, 'ts': 1})
    monkeypatch.setattr(module, 'research_runtime', rt)
    monkeypatch.setattr(module, '_run_browser', lambda *a, **kw: pytest.fail('expired session opened'))
    assert module.search_public_douyin('tenant', 'project', request())[1]['status'] == 'login_required'


def test_request_extra_credentials_or_target_urls_are_forbidden(tmp_path, monkeypatch):
    module = adapter()
    monkeypatch.setattr(module, 'research_runtime', runtime(tmp_path))
    for extra in ({'url': 'https://evil.test'}, {'profile': '/global'}, {'cookies': 'secret'}, {'query': '\x00bad'}):
        value = {'query': '住宅', 'platforms': ['douyin'], **extra}
        with pytest.raises(ValueError):
            module.search_public_douyin('tenant', 'project', value)


def test_dom_candidates_require_public_work_urls_and_never_gain_metrics():
    items = browser().parse_dom_candidates([
        {'url': 'javascript:alert(1)', 'title': '住宅'},
        {'url': 'https://www.douyin.com/video/7559057198329981234?secret=token', 'title': '住宅三室'},
        {'url': 'https://www.douyin.com/video/7559057198329981235', 'title': '住宅采光'},
    ])
    assert len(items) == 2
    assert items[0]['url'] == 'https://www.douyin.com/video/7559057198329981234'
    assert items[0]['metrics']['likes'] is None and items[0]['published_at'] is None
    assert items[0]['qualification'] == 'candidate_unverified'
    assert items[0]['metrics_source'] is None
    assert 'secret' not in json.dumps(items)


def test_runner_rejects_publisher_profile_and_accepts_only_dedicated_research_directory(tmp_path, monkeypatch):
    module = browser()
    rt = runtime(tmp_path)
    research_scope = rt.scope('tenant', 'project')
    research_profile = research_scope / 'profiles' / 'DouyinResearchProfile'
    monkeypatch.setenv('EASEL_ROOT', str(research_scope))
    assert module.validate_profile(research_profile) == research_profile
    with pytest.raises(ValueError):
        module.validate_profile(research_scope.parent / 'profiles' / 'DouyinProfile')


def install_fake_browser(monkeypatch, profile, *, blocker=None, responses=(), dom=(),
                         initial_status=200, page_url=None, on_wait=None):
    actions = []
    class Response:
        def __init__(self, body, path='/aweme/v1/web/general/search/single/', query='住宅', method='GET', status=200):
            self.url = 'https://www.douyin.com' + path + '?keyword=' + query
            self.status = status
            raw = json.dumps(body).encode()
            self.request = SimpleNamespace(method=method, sizes=lambda: {'responseBodySize': len(raw)})
            self.body = lambda: raw
    network = [Response(**value) for value in responses]
    class Page:
        url = 'https://www.douyin.com/search/%E4%BD%8F%E5%AE%85?type=video'
        def set_default_timeout(self, _): pass
        def on(self, event, callback): self.callback = callback
        def goto(self, url, **kwargs):
            actions.append(('goto', url))
            for response in network: self.callback(response)
            if page_url is not None: self.url = page_url
            return SimpleNamespace(status=initial_status)
        def evaluate(self, script):
            if script == browser().BLOCKER_SCRIPT: return blocker
            if script == browser().DOM_CANDIDATES_SCRIPT:
                actions.append(('read_visible_cards',))
                return list(dom)
            raise AssertionError('unapproved browser script')
        def wait_for_timeout(self, value):
            if on_wait is not None:
                return on_wait(value)
            raise AssertionError('a current response or challenge should finish without polling')
        def bring_to_front(self): actions.append(('show_official_window',))
        def close(self): actions.append(('close_page',))
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages = [page]; return page
        def close(self): actions.append(('close_context',))
    def launch(profile_value, **kwargs):
        assert profile_value == str(profile)
        assert kwargs['chromium_sandbox'] is True
        assert not any('disable' in flag and 'sandbox' in flag for flag in kwargs.get('args', []))
        assert not any('AutomationControlled' in flag for flag in kwargs.get('args', []))
        assert kwargs['accept_downloads'] is False
        actions.append(('launch',))
        return Context()
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Playwright))
    return actions


def research_profile(tmp_path, monkeypatch):
    from app.platform_runtime import PlatformRuntime
    rt = PlatformRuntime(root=tmp_path, python=sys.executable)
    scope = rt._scope('tenant', 'project', 'douyin', create=True) / 'research'
    profile = scope / 'profiles' / 'DouyinResearchProfile'; profile.mkdir(parents=True)
    monkeypatch.setenv('EASEL_ROOT', str(scope))
    return profile


def test_captcha_stops_browser_discards_any_received_results_and_closes_profile(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    actions = install_fake_browser(monkeypatch, profile, blocker='challenge',
        responses=[{'body': {'data': [{'aweme_info': WORK}]}}])
    result = browser().run_public_browser(profile, '住宅', 'engagement')
    assert result == {'status': 'challenge', 'items': [], 'http_status': None,
                      'restriction_reason': 'visible_challenge', 'challenge_visible': True}
    assert actions == [('launch',), ('goto', 'https://www.douyin.com/search/%E4%BD%8F%E5%AE%85?type=video'), ('close_context',)]


def test_browser_reads_only_matching_keyword_public_response_and_never_private_endpoint(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    actions = install_fake_browser(monkeypatch, profile, responses=[
        {'body': {'data': [{'aweme_info': {**WORK, 'desc': '私信'}}]}, 'path': '/aweme/v1/web/im/message/'},
        {'body': {'data': [{'aweme_info': {**WORK, 'desc': '家居'}}]}, 'query': '家居'},
        {'body': {'data': [{'aweme_info': WORK}]}},
    ])
    result = browser().run_public_browser(profile, '住宅', 'latest')
    assert result['status'] == 'partial'
    assert [item['title'] for item in result['items']] == ['住宅三室户型，南向客厅']
    assert [action[0] for action in actions] == ['launch', 'goto', 'close_context']


def test_browser_login_wall_requires_human_even_when_public_cards_have_loaded(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    install_fake_browser(monkeypatch, profile, blocker='login_required',
        responses=[{'body': {'data': [{'aweme_info': WORK}]}}])
    assert browser().run_public_browser(profile, '住宅', 'relevance') == {
        'status': 'login_required', 'items': [], 'http_status': None,
        'restriction_reason': 'visible_login_prompt', 'challenge_visible': False}


def test_public_identity_never_uses_a_video_author_as_the_authenticated_user():
    class Page:
        def evaluate(self, script):
            assert 'header' in script
            return {'loggedIn': False, 'name': '', 'avatar': ''}
    assert browser().public_identity(Page()) == {'loggedIn': False, 'verified': False, 'name': '', 'avatar': ''}


def test_probe_reads_only_www_homepage_closes_browser_and_returns_no_raw_profile_state(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    actions = []
    class Page:
        url = 'https://www.douyin.com/'
        def goto(self, url, **kwargs): actions.append(('goto', url)); return SimpleNamespace(status=200)
        def evaluate(self, script):
            if script == browser().BLOCKER_SCRIPT: return None
            if script == browser().PUBLIC_IDENTITY_SCRIPT:
                return {'loggedIn': True, 'name': '住宅观察', 'avatar': 'javascript:secret',
                        'phone': 'private-phone', 'cookie': 'private-cookie'}
            raise AssertionError('unknown public identity script')
        def close(self): pass
        def set_default_timeout(self, value): pass
        def wait_for_timeout(self, value): pass
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages = [page]; return page
        def close(self): actions.append(('close',))
    def launch(profile_value, **kwargs):
        assert profile_value == str(profile)
        assert kwargs['chromium_sandbox'] is True
        assert kwargs['accept_downloads'] is False
        return Context()
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Playwright))
    result = browser().run_public_probe(profile)
    assert result == {'loggedIn': True, 'verified': True, 'name': '住宅观察', 'avatar': ''}
    assert actions == [('goto', 'https://www.douyin.com/'), ('close',)]


def test_login_command_rejects_any_qr_or_status_path_outside_research_scope(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        browser().run_public_login(profile, qr_out=tmp_path/'other.png',
                                  status_file=tmp_path/'status.json', timeout=1)


def test_publisher_login_metadata_does_not_authorize_read_only_research(tmp_path, monkeypatch):
    from app.platform_runtime import PlatformRuntime
    publisher = PlatformRuntime(root=tmp_path, python=sys.executable)
    scope = publisher._scope('tenant', 'project', 'douyin', create=True)
    (scope / 'profiles' / 'DouyinProfile').mkdir(parents=True)
    publisher._write(scope / 'status.json', {'state': 'success'})
    publisher._write(scope / 'identity.json', {'verified': True, 'ts': time.time()})
    module = adapter()
    monkeypatch.setattr(module, 'research_runtime', runtime(tmp_path, connected=False))
    monkeypatch.setattr(module, '_run_browser', lambda *a, **kw: pytest.fail('publisher session reused'))
    items, state = module.search_public_douyin('tenant', 'project', request())
    assert items == [] and state['status'] == 'login_required'


def test_public_date_survives_even_when_no_public_metrics_are_available(tmp_path, monkeypatch):
    module = adapter()
    monkeypatch.setattr(module, 'research_runtime', runtime(tmp_path))
    rows = browser().parse_public_payload({'aweme_list': [{**WORK, 'statistics': {}}]})
    monkeypatch.setattr(module, '_run_browser', lambda *a, **kw: {'status': 'partial', 'items': rows})
    items, state = module.search_public_douyin('tenant', 'project', request())
    assert items[0]['published_at'] == '2024-10-13T05:00:00+00:00'
    assert items[0]['metrics']['likes'] is None
    assert items[0]['qualification'] == 'candidate_unverified'


def test_official_login_captures_only_visible_qr_and_success_closes_own_window(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    scope = profile.parent.parent
    actions = []
    class Node:
        def is_visible(self): return True
        def click(self, **kwargs):
            actions.append('open_official_login'); page.dialog_open = True
        def bounding_box(self): return {'x': 0, 'y': 0, 'width': 180, 'height': 180}
        def screenshot(self, **kwargs):
            actions.append('read_official_qr')
            return b'\x89PNG\r\n\x1a\nactual-platform-qr-fixture'
    class Nodes:
        first = Node()
        def count(self): return 1
        def nth(self, n): return self.first
    class Empty:
        def count(self): return 0
    class Region:
        def is_visible(self): return True
        def inner_text(self, **kwargs): return '扫码登录 打开抖音扫码登录'
        def locator(self, selector):
            return Nodes() if selector == '[data-e2e="login-qrcode"] canvas' else Empty()
    class Regions:
        def count(self): return 1 if page.dialog_open else 0
        def nth(self, n): return Region()
    class Page:
        dialog_open = False
        url = 'https://www.douyin.com/'
        identity_checks = 0
        def goto(self, url, **kwargs): actions.append(url)
        def evaluate(self, script):
            if script == browser().BLOCKER_SCRIPT: return 'login_required' if self.dialog_open else None
            if script == browser().PUBLIC_IDENTITY_SCRIPT:
                self.identity_checks += 1
                if self.identity_checks >= 3:
                    return {'loggedIn': True, 'name': '住宅观察', 'avatar': 'https://p3.douyinpic.com/avatar.png'}
                return {'loggedIn': False, 'name': '', 'avatar': ''}
            raise AssertionError('unexpected browser script')
        def set_default_timeout(self, timeout): pass
        def get_by_role(self, role, **kwargs):
            assert role == 'button' and kwargs == {'name': '登录', 'exact': True}
            return Nodes()
        def get_by_text(self, text, **kwargs):
            assert text == '登录' and kwargs == {'exact': True}
            return Nodes()
        def locator(self, selector):
            assert selector == browser().LOGIN_REGIONS_SELECTOR
            return Regions()
        def wait_for_timeout(self, value):
            if value == 500:
                assert json.loads((scope/'status.json').read_text())['state'] == 'qr_ready'
                assert (scope/'qr.png').stat().st_mode & 0o777 == 0o600
        def close(self): pass
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages = [page]; return page
        def close(self): actions.append('close_research_window')
    def launch(path, **kwargs):
        assert path == str(profile)
        assert kwargs['headless'] is False and kwargs['chromium_sandbox'] is True
        return Context()
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Playwright))
    result = browser().run_public_login(profile, qr_out=scope/'qr.png', status_file=scope/'status.json',
                                       timeout=1, headed=True)
    assert result['state'] == 'success' and result['verified'] is True
    assert actions == ['https://www.douyin.com/', 'open_official_login', 'read_official_qr', 'close_research_window']
    status = json.loads((scope/'status.json').read_text())
    assert status['state'] == 'success' and status['verified'] is True and status['loggedIn'] is True


def test_search_runner_argv_treats_keywords_as_data_and_does_not_invoke_shell(tmp_path, monkeypatch):
    module = adapter()
    rt = runtime(tmp_path)
    scope = rt.scope('tenant', 'project')
    runner = tmp_path/'fixture_owned_runner.py'
    runner.write_text("import json,sys,pathlib\npathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))\nprint(json.dumps({'status':'partial','items':[]}))\n")
    monkeypatch.setattr(module, 'research_runtime', rt)
    monkeypatch.setattr(module, '_RUNNER', runner)
    injected = str(tmp_path/'should-not-exist')
    query = '住宅;touch ' + injected
    result = module._run_browser(scope, query, 'engagement')
    assert result == {'status': 'partial', 'items': []}
    argv = json.loads((scope/'argv.json').read_text())
    assert argv == ['--op', 'search', '--profile', str(scope/'profiles/DouyinResearchProfile'),
                    '--query', query, '--sort', 'engagement']
    assert not Path(injected).exists()


def test_cancelled_search_closes_all_owned_runner_children_before_releasing_profile(tmp_path, monkeypatch):
    module = adapter()
    rt = runtime(tmp_path)
    scope = rt.scope('tenant', 'project')
    runner = tmp_path/'fixture_owned_runner.py'
    runner.write_text("import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "pathlib.Path('search-child.pid').write_text(str(child.pid))\n"
        "time.sleep(20)\n")
    monkeypatch.setattr(module, 'research_runtime', rt)
    monkeypatch.setattr(module, '_RUNNER', runner)
    result = module._run_browser(scope, '住宅', 'engagement',
        cancelled=lambda: (scope/'search-child.pid').exists())
    assert result == {'status': 'cancelled', 'items': []}
    child = int((scope/'search-child.pid').read_text())
    import subprocess
    state = subprocess.run(['ps','-p',str(child),'-o','stat='],capture_output=True,text=True).stdout.strip()
    assert not state or state.startswith('Z')


@pytest.mark.parametrize('failure_stage,error_type', [('launch', TypeError), ('navigate', TimeoutError),
                                                     ('login_dialog', RuntimeError), ('observe', ValueError)])
def test_login_failure_reports_safe_stage_and_exception_class_without_raw_error(tmp_path, monkeypatch,
                                                                              failure_stage, error_type):
    profile = research_profile(tmp_path, monkeypatch)
    scope = profile.parent.parent
    raw = 'cookie=private-cookie token=private-token https://private.invalid/secret /Users/private'
    def fail(): raise error_type(raw)
    class Empty:
        def count(self): return 0
    class Page:
        url = 'https://www.douyin.com/'
        def locator(self, selector): return Empty()
        def set_default_timeout(self, value): pass
        def goto(self, *args, **kwargs):
            if failure_stage == 'navigate': fail()
        def wait_for_timeout(self, value): pass
        def evaluate(self, script):
            if failure_stage == 'observe': fail()
            return {'loggedIn': False}
        def get_by_role(self, *args, **kwargs):
            if failure_stage == 'login_dialog': fail()
            return SimpleNamespace(count=lambda: 0)
        def get_by_text(self, *args, **kwargs): return SimpleNamespace(count=lambda: 0)
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages = [page]; return page
        def close(self): pass
    def launch(*args, **kwargs):
        if failure_stage == 'launch': fail()
        return Context()
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Playwright))
    result = browser().run_public_login(profile, qr_out=scope/'qr.png', status_file=scope/'status.json',
                                       timeout=1, headed=True)
    assert result['state'] == 'error' and result['stage'] == failure_stage
    assert result['error_code'] == error_type.__name__
    stored = json.loads((scope/'status.json').read_text())
    assert stored['stage'] == failure_stage and stored['error_code'] == error_type.__name__
    assert stored['loggedIn'] is False and stored['verified'] is False
    combined = json.dumps([result, stored])
    assert all(secret not in combined for secret in ('private-cookie', 'private-token', 'private.invalid', '/Users/private'))


def test_app_download_qr_without_a_login_region_is_never_saved_as_login_qr(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    output = profile.parent.parent/'qr.png'
    class Empty:
        def count(self): return 0
    class Page:
        def locator(self, selector):
            # An App download QR exists on the page, but no login dialog exists.
            if 'role="dialog"' in selector or 'login-modal' in selector or 'login-dialog' in selector:
                return Empty()
            class AppQR:
                def count(self): return 1
                def nth(self, n): return self
                def is_visible(self): return True
                def bounding_box(self): return {'width': 180, 'height': 180}
                def screenshot(self, **kwargs): return b'\x89PNG\r\n\x1a\napp-download-qr'
            return AppQR()
    assert browser()._capture_login_qr(Page(), output) is False
    assert not output.exists()


@pytest.mark.parametrize('automatic_dialog,click_times_out,public_path',
    [(True, False, '/'), (False, True, '/'), (True, False, '/jingxuan')])
def test_existing_login_dialog_or_blocked_login_click_preserves_official_window_for_human(tmp_path, monkeypatch,
                                                                                        automatic_dialog, click_times_out, public_path):
    profile = research_profile(tmp_path, monkeypatch)
    scope = profile.parent.parent
    actions = []
    class Empty:
        def count(self): return 0
    class QR:
        def is_visible(self): return True
        def bounding_box(self): return {'width': 180, 'height': 180}
        def screenshot(self, **kwargs):
            actions.append('capture_login_qr')
            return b'\x89PNG\r\n\x1a\ntrusted-official-login-qr'
    class QRNodes:
        def count(self): return 1
        def nth(self, n): return QR()
    class Region:
        def is_visible(self): return True
        def inner_text(self, **kwargs): return '扫码登录 打开抖音扫码登录'
        def locator(self, selector):
            return QRNodes() if selector == '[data-e2e="login-qrcode"] canvas' else Empty()
    class Regions:
        def count(self): return 1 if automatic_dialog else 0
        def nth(self, n): return Region()
    class Button:
        def is_visible(self): return True
        def click(self, **kwargs):
            actions.append('click_login_once')
            if click_times_out:
                raise TimeoutError('private overlay URL token=secret')
    class Buttons:
        first = Button()
        def count(self): return 1
    class Page:
        url = 'https://www.douyin.com' + public_path
        identity_count = 0
        def set_default_timeout(self, value): pass
        def goto(self, url, **kwargs): actions.append(url)
        def evaluate(self, script):
            if script == browser().BLOCKER_SCRIPT:
                return 'login_required' if automatic_dialog else None
            if script == browser().PUBLIC_IDENTITY_SCRIPT:
                self.identity_count += 1
                logged = self.identity_count >= (2 if automatic_dialog else 3)
                return {'loggedIn': logged, 'name': '住宅观察' if logged else '', 'avatar': ''}
            raise AssertionError('unexpected browser script')
        def locator(self, selector):
            assert 'role="dialog"' in selector or 'login-modal' in selector or 'login-dialog' in selector
            return Regions()
        def get_by_role(self, *args, **kwargs):
            assert not automatic_dialog, 'existing official login dialog must not cause a login click'
            return Buttons()
        def get_by_text(self, *args, **kwargs): return Buttons()
        def wait_for_timeout(self, value):
            if value == 500 and click_times_out:
                stored = json.loads((scope/'status.json').read_text())
                assert stored['state'] == 'window_login'
                assert stored['error_code'] == 'TimeoutError' and stored['stage'] == 'login_dialog'
        def close(self): pass
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages = [page]; return page
        def close(self): actions.append('close_window_after_human_success')
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a,**kw:Context()))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Playwright))
    result = browser().run_public_login(profile, qr_out=scope/'qr.png', status_file=scope/'status.json',
                                       timeout=1, headed=True)
    assert result['state'] == 'success'
    assert actions[-1] == 'close_window_after_human_success'
    assert actions.count('click_login_once') == (0 if automatic_dialog else 1)
    assert actions.count('capture_login_qr') == (1 if automatic_dialog else 0)


@pytest.mark.parametrize('url,wanted', [
    ('https://www.douyin.com/recommend/?token=private-token&phone=private-phone', '/recommend/'),
    ('https://douyin.com/follow?msToken=private-token', '/follow'),
    ('https://www.douyin.com/?token=private-token', '/'),
    ('https://www.douyin.com/user/7559057198329981234?token=private-token', 'unknown_public_path'),
    ('https://www.douyin.com/private-token-value-with-more-than-thirty-letters', 'unknown_public_path'),
    ('https://private.invalid/?token=private-token', 'external_origin'),
    ('http://www.douyin.com/recommend?token=private-token', 'external_origin'),
    ('https://www.douyin.com.evil.invalid/recommend', 'external_origin'),
])
def test_unexpected_navigation_diagnostic_contains_only_public_route_or_safe_category(url, wanted):
    assert browser().safe_public_route(url) == wanted


def test_login_unexpected_navigation_records_public_route_without_tokens_or_identity(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    scope = profile.parent.parent
    class Empty:
        def count(self): return 0
    class Page:
        url = 'https://www.douyin.com/'
        def set_default_timeout(self, value): pass
        def goto(self, *args, **kwargs): pass
        def wait_for_timeout(self, value):
            if value == 1500:
                self.url = 'https://www.douyin.com/recommend?msToken=private-token&phone=private-phone'
        def evaluate(self, script):
            if script == browser().BLOCKER_SCRIPT: return None
            return {'loggedIn': False}
        def locator(self, selector): return Empty()
        def get_by_role(self, *args, **kwargs): return Empty()
        def get_by_text(self, *args, **kwargs): return Empty()
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages = [page]; return page
        def close(self): pass
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a,**kw:Context()))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=Playwright))
    result = browser().run_public_login(profile, qr_out=scope/'qr.png', status_file=scope/'status.json',
                                       timeout=1, headed=True)
    assert result['state'] == 'error' and result['error_code'] == 'ValueError'
    assert result['stage'] == 'observe' and result['public_route'] == '/recommend'
    persisted = json.loads((scope/'status.json').read_text())
    assert persisted['public_route'] == '/recommend'
    output = json.dumps([result, persisted])
    assert all(secret not in output for secret in ('private-token', 'private-phone', 'msToken', 'https://'))


def test_evidenced_official_jingxuan_route_is_allowed_for_login_and_probe_only():
    module = browser()
    page = SimpleNamespace(url='https://www.douyin.com/jingxuan?msToken=private-token')
    assert module._public_location(page) is True
    assert module._public_location(page, search=True) is False
    for url in ('https://www.douyin.com/recommend', 'https://www.douyin.com/jingxuan/unknown',
                'https://www.douyin.com/jingxuan/', 'https://www.douyin.com/user/7559057198329981234',
                'https://private.invalid/jingxuan', 'http://www.douyin.com/jingxuan'):
        assert module._public_location(SimpleNamespace(url=url)) is False


def operating_runtime(tmp_path, *, verified=True, session_id='a'*64):
    from app.platform_runtime import PlatformRuntime
    rt = PlatformRuntime(root=tmp_path, python=sys.executable)
    rt._readiness_cache = (time.monotonic(), {'python': True, 'segno': True, 'browser': True})
    scope = rt._scope('tenant', 'project', 'douyin', create=True)
    (scope/'profiles'/'DouyinProfile').mkdir(parents=True)
    rt._write(scope/'status.json', {'state': 'success'})
    rt._write(scope/'session.json', {'session_id': session_id})
    rt._write(scope/'identity.json', {'verified': verified, 'loggedIn': True,
        'session_id': session_id, 'ts': time.time(), 'name': '住宅观察'})
    return rt


def test_explicit_operating_search_uses_current_verified_session_without_creating_research_session(tmp_path, monkeypatch):
    module = adapter()
    assert hasattr(module, 'search_operating_douyin'), 'operations search must consume the authorized operating account'
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant', 'project', 'douyin')
    monkeypatch.setattr(module, 'runtime', rt)
    def run(actual_scope, query, sort, cancelled=None):
        assert actual_scope == scope and query == '住宅' and sort == 'engagement'
        return {'status': 'partial', 'items': browser().parse_public_payload({'aweme_list': [WORK]})}
    monkeypatch.setattr(module, '_run_operating_browser', run)
    items, state = module.search_operating_douyin('tenant', 'project', request())
    assert items[0]['title'] == '住宅三室户型，南向客厅'
    assert state['adapter'] == 'douyin_operating_public_browser' and state['session_id'] == 'a'*64
    assert state['status'] == 'partial'
    assert not (scope/'research').exists()
    assert str(tmp_path) not in json.dumps(state)


@pytest.mark.parametrize('verified,session_id', [(False,'a'*64), (True,''), (True,'private-cookie-token')])
def test_operating_search_never_launches_without_current_verified_opaque_session(tmp_path, monkeypatch, verified, session_id):
    module = adapter()
    assert hasattr(module, 'search_operating_douyin')
    monkeypatch.setattr(module, 'runtime', operating_runtime(tmp_path, verified=verified, session_id=session_id))
    monkeypatch.setattr(module, '_run_operating_browser', lambda *a,**kw:pytest.fail('unverified operating profile opened'))
    items, state = module.search_operating_douyin('tenant', 'project', request())
    assert items == [] and state['status'] == 'login_required'
    assert 'private-cookie-token' not in json.dumps(state)


def test_operating_search_holds_publication_lock_and_does_not_use_creator_probe(tmp_path, monkeypatch):
    module = adapter()
    assert hasattr(module, 'search_operating_douyin')
    rt = operating_runtime(tmp_path)
    monkeypatch.setattr(module, 'runtime', rt)
    monkeypatch.setattr(rt, 'whoami', lambda *a,**kw:pytest.fail('search must not open creator/probe'))
    from app.platform_runtime import PlatformRuntime
    other = PlatformRuntime(root=tmp_path, python=sys.executable)
    def run(scope, query, sort, cancelled=None):
        with pytest.raises(RuntimeError):
            with other.publish_lock('tenant', 'project', 'douyin'):
                pass
        return {'status': 'challenge', 'items': [{'cookie': 'private-cookie'}]}
    monkeypatch.setattr(module, '_run_operating_browser', run)
    items, state = module.search_operating_douyin('tenant', 'project', request())
    assert items == [] and state['status'] == 'challenge' and state['session_id'] == 'a'*64
    with other.publish_lock('tenant', 'project', 'douyin'):
        pass


def test_operating_search_does_not_authorize_another_tenant_or_project(tmp_path, monkeypatch):
    module = adapter()
    assert hasattr(module, 'search_operating_douyin')
    rt = operating_runtime(tmp_path)
    monkeypatch.setattr(module, 'runtime', rt)
    monkeypatch.setattr(module, '_run_operating_browser', lambda *a,**kw:pytest.fail('wrong project profile opened'))
    for tenant, project in [('other-tenant','project'), ('tenant','other-project')]:
        items, state = module.search_operating_douyin(tenant, project, request())
        assert items == [] and state['status'] == 'login_required' and state['session_id'] == ''


def test_operating_browser_profile_mode_is_strict_and_cannot_login_or_probe(tmp_path, monkeypatch):
    module = browser()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant', 'project', 'douyin')
    profile = scope/'profiles'/'DouyinProfile'
    monkeypatch.setenv('EASEL_ROOT', str(scope))
    assert module.validate_profile(profile, account_scope='operations', op='search') == profile
    for op in ('login', 'probe', 'publish'):
        with pytest.raises(ValueError):
            module.validate_profile(profile, account_scope='operations', op=op)
    for target in (scope/'research/profiles/DouyinResearchProfile', tmp_path/'global/DouyinProfile'):
        with pytest.raises(ValueError):
            module.validate_profile(target, account_scope='operations', op='search')
    with pytest.raises(ValueError):
        module.validate_profile(profile)


def test_operations_runner_argv_is_search_only_and_uses_existing_browser_policy(tmp_path, monkeypatch):
    module = adapter()
    assert hasattr(module, '_run_operating_browser')
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant', 'project', 'douyin')
    runner = tmp_path/'fixture_operating_runner.py'
    runner.write_text("import json,sys,pathlib,os\n"
        "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "pathlib.Path('policy.json').write_text(json.dumps({'scope':os.environ['EASEL_ROOT'],'policy':os.environ['PYTHONPATH']}))\n"
        "print(json.dumps({'status':'partial','items':[]}))\n")
    monkeypatch.setattr(module, 'runtime', rt)
    monkeypatch.setattr(module, '_RUNNER', runner)
    assert module._run_operating_browser(scope, '住宅', 'engagement') == {'status':'partial','items':[]}
    argv = json.loads((scope/'argv.json').read_text())
    assert argv == ['--op','search','--profile',str(scope/'profiles/DouyinProfile'),
                    '--query','住宅','--sort','engagement','--account-scope','operations']
    env = json.loads((scope/'policy.json').read_text())
    assert env == {'scope':str(scope), 'policy':str(rt.browser_policy)}


def test_operations_browser_search_keeps_fixed_public_target_and_sandbox(tmp_path, monkeypatch):
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant', 'project', 'douyin')
    profile = scope/'profiles'/'DouyinProfile'
    monkeypatch.setenv('EASEL_ROOT', str(scope))
    actions = install_fake_browser(monkeypatch, profile, responses=[{'body':{'aweme_list':[WORK]}}])
    result = browser().run_public_browser(profile, '住宅', 'engagement', account_scope='operations')
    assert len(result['items']) == 1 and result['status'] == 'partial'
    assert actions == [('launch',),('goto','https://www.douyin.com/search/%E4%BD%8F%E5%AE%85?type=video'),('close_context',)]


def test_queued_operating_search_cannot_read_replaced_account_session(tmp_path, monkeypatch):
    module = adapter()
    assert hasattr(module, 'search_operating_douyin')
    rt = operating_runtime(tmp_path, session_id='b'*64)
    monkeypatch.setattr(module, 'runtime', rt)
    monkeypatch.setattr(module, '_run_operating_browser', lambda *a,**kw:pytest.fail('new account read by old job'))
    items, state = module.search_operating_douyin('tenant', 'project', request(), expected_session_id='a'*64)
    assert items == [] and state['status'] == 'session_changed'
    assert state['account_scope'] == 'operations' and state['session_id'] == 'a'*64


def test_operating_search_rejects_client_style_credentials_as_expected_session(tmp_path, monkeypatch):
    module = adapter()
    assert hasattr(module, 'search_operating_douyin')
    monkeypatch.setattr(module, 'runtime', operating_runtime(tmp_path))
    monkeypatch.setattr(module, '_run_operating_browser', lambda *a,**kw:pytest.fail('credential passed as fence'))
    with pytest.raises(ValueError):
        module.search_operating_douyin('tenant', 'project', request(), expected_session_id='cookie=secret')


def test_operations_cli_rejects_login_before_browser_or_publisher_status_write(tmp_path, monkeypatch, capsys):
    module = browser()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant', 'project', 'douyin')
    profile = scope/'profiles'/'DouyinProfile'
    original = (scope/'status.json').read_bytes()
    monkeypatch.setenv('EASEL_ROOT', str(scope))
    monkeypatch.setattr(sys, 'argv', ['runner','--account-scope','operations','--op','login',
        '--profile',str(profile),'--qr-out',str(scope/'qr.png'),'--status-file',str(scope/'status.json')])
    monkeypatch.setattr(module, 'run_public_login', lambda *a,**kw:pytest.fail('operating login opened'))
    module.main()
    value = json.loads(capsys.readouterr().out)
    assert value['state'] == 'error' and value['error_code'] == 'ValueError'
    assert (scope/'status.json').read_bytes() == original
    assert not (scope/'qr.png').exists()


def test_operating_search_cancellation_never_returns_private_rows_or_session_data(tmp_path, monkeypatch):
    module = adapter()
    rt = operating_runtime(tmp_path)
    monkeypatch.setattr(module, 'runtime', rt)
    monkeypatch.setattr(module, '_run_operating_browser', lambda *a,**kw:pytest.fail('cancelled search started'))
    items, state = module.search_operating_douyin('tenant','project',request(),lambda:True,
                                                expected_session_id='a'*64)
    assert items == [] and state['status'] == 'cancelled' and state['session_id'] == 'a'*64


def test_operating_profile_symlink_cannot_point_to_other_project_or_global_account(tmp_path, monkeypatch):
    module = adapter()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant','project','douyin')
    profile = scope/'profiles'/'DouyinProfile'; profile.rmdir()
    global_profile = tmp_path/'global-profile'; global_profile.mkdir()
    profile.symlink_to(global_profile, target_is_directory=True)
    monkeypatch.setattr(module,'runtime',rt)
    monkeypatch.setattr(module,'_run_operating_browser',lambda *a,**kw:pytest.fail('foreign profile opened'))
    with pytest.raises(ValueError):
        module.search_operating_douyin('tenant','project',request())


@pytest.mark.parametrize('human_resolves,http_status,wanted',
    [(True, 200, 'ready'), (False, 200, 'expired'), (True, 403, 'ready'),
     (True, 429, 'ready')])
def test_operating_verify_search_keeps_challenge_window_for_human_without_verifying_or_touching_identity(tmp_path,
        monkeypatch, human_resolves, http_status, wanted):
    module = browser()
    assert hasattr(module, 'run_public_verify_search'), 'a specific public verification window must exist'
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant','project','douyin')
    profile = scope/'profiles'/'DouyinProfile'
    monkeypatch.setenv('EASEL_ROOT', str(scope))
    original = {name:(scope/name).read_bytes() for name in ('status.json','identity.json','session.json')}
    status_file = scope/'public-search-status.json'
    actions, ticks = [], [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda:ticks[0])
    class Page:
        url = 'https://www.douyin.com/search/%E4%BD%8F%E5%AE%85?type=video'
        def set_default_timeout(self, value): pass
        def on(self, event, callback): pass
        def goto(self, url, **kwargs): actions.append(('navigate',url)); return SimpleNamespace(status=http_status)
        def bring_to_front(self): actions.append(('show_official_window',))
        def evaluate(self, script):
            if script == module.BLOCKER_SCRIPT:
                return None if human_resolves and ticks[0] >= .5 else 'challenge'
            if script == module.DOM_CANDIDATES_SCRIPT:
                return [{'url':'https://www.douyin.com/video/7559057198329981234','title':'住宅三室'}]
            pytest.fail('unapproved verification script')
        def wait_for_timeout(self, value):
            stored = json.loads(status_file.read_text())
            assert stored['state'] == 'challenge'
            assert actions[-1][0] != 'closed'
            ticks[0] += .5
        def close(self): pass
    page = Page()
    class Context:
        pages = []
        def new_page(self): self.pages=[page]; return page
        def close(self): actions.append(('closed',))
    def launch(profile_value, **kwargs):
        assert profile_value == str(profile)
        assert kwargs['headless'] is False and kwargs['chromium_sandbox'] is True
        return Context()
    class Playwright:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules,'playwright.sync_api',SimpleNamespace(sync_playwright=Playwright))
    result = module.run_public_verify_search(profile,'住宅',status_file=status_file,timeout=1,headed=True)
    assert result['state'] == wanted and result['status'] == wanted
    assert actions == [('navigate','https://www.douyin.com/search/%E4%BD%8F%E5%AE%85?type=video'),
                       ('show_official_window',),('closed',)]
    for name, data in original.items(): assert (scope/name).read_bytes() == data
    stored = json.loads(status_file.read_text())
    assert stored['state'] == wanted and stored['status'] == wanted and type(stored['ts']) is int
    assert '住宅' not in json.dumps(stored,ensure_ascii=False)
    assert not (scope/'research').exists()


def test_verify_search_requires_operating_mode_headed_and_exact_separate_status_file(tmp_path, monkeypatch):
    module = browser()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant','project','douyin')
    profile = scope/'profiles'/'DouyinProfile'
    monkeypatch.setenv('EASEL_ROOT',str(scope))
    assert module.validate_profile(profile,account_scope='operations',op='verify-search') == profile
    with pytest.raises(ValueError): module.validate_profile(profile,account_scope='research',op='verify-search')
    for path in (scope/'status.json',scope/'identity.json',tmp_path/'public-search-status.json'):
        with pytest.raises(ValueError):
            module.run_public_verify_search(profile,'住宅',status_file=path,timeout=1,headed=True)
    with pytest.raises(ValueError):
        module.run_public_verify_search(profile,'住宅',status_file=scope/'public-search-status.json',timeout=1,headed=False)


def test_verify_search_cli_rejects_headless_and_preserves_publisher_metadata(tmp_path, monkeypatch, capsys):
    module = browser()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant','project','douyin')
    profile = scope/'profiles'/'DouyinProfile'
    original = {name:(scope/name).read_bytes() for name in ('status.json','identity.json','session.json')}
    monkeypatch.setenv('EASEL_ROOT',str(scope))
    monkeypatch.setattr(sys,'argv',['runner','--account-scope','operations','--op','verify-search',
        '--profile',str(profile),'--query','住宅','--status-file',str(scope/'public-search-status.json')])
    module.main()
    result = json.loads(capsys.readouterr().out)
    assert result['state'] == 'error' and result['status'] == 'error'
    assert result['error_code'] == 'ValueError'
    for name,data in original.items(): assert (scope/name).read_bytes() == data


def test_verify_search_refuses_a_symlinked_status_before_any_browser_or_state_write(tmp_path, monkeypatch):
    module = browser()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant','project','douyin')
    profile = scope/'profiles'/'DouyinProfile'
    victim = tmp_path/'external-status.json'; victim.write_text('untouched')
    (scope/'public-search-status.json').symlink_to(victim)
    monkeypatch.setenv('EASEL_ROOT',str(scope))
    with pytest.raises(ValueError):
        module.run_public_verify_search(profile,'住宅',status_file=scope/'public-search-status.json',timeout=1,headed=True)
    assert victim.read_text() == 'untouched'


@pytest.mark.parametrize('http_status,wanted,reason', [
    (403, 'blocked', 'http_denied'), (429, 'rate_limited', 'rate_limited')])
@pytest.mark.parametrize('from_response', [False, True])
def test_http_restriction_without_visible_challenge_never_claims_human_verification(
        tmp_path, monkeypatch, http_status, wanted, reason, from_response):
    profile = research_profile(tmp_path, monkeypatch)
    actions = install_fake_browser(monkeypatch, profile,
        initial_status=200 if from_response else http_status,
        responses=[{'body': {}, 'status': http_status}] if from_response else [])
    result = browser().run_public_browser(profile, '住宅', 'relevance')
    assert result['status'] == wanted and result['items'] == []
    assert result['http_status'] == http_status
    assert result['restriction_reason'] == reason
    assert result['challenge_visible'] is False
    assert len([action for action in actions if action[0] == 'goto']) == 1


@pytest.mark.parametrize('http_status', [403, 429])
def test_current_visible_cards_after_http_restriction_are_unverified_with_restriction_evidence(
        tmp_path, monkeypatch, http_status):
    profile = research_profile(tmp_path, monkeypatch)
    actions = install_fake_browser(monkeypatch, profile, initial_status=http_status,
        responses=[{'body': {'data': [{'aweme_info': WORK}]}}],
        dom=[{'url': 'https://www.douyin.com/video/7559057198329981234', 'title': '住宅三室户型'}])
    result = browser().run_public_browser(profile, '住宅', 'relevance')
    assert result['status'] == 'partial'
    assert result['http_status'] == http_status and result['challenge_visible'] is False
    assert len(result['items']) == 1
    item = result['items'][0]
    assert item['qualification'] == 'candidate_unverified'
    assert item['metrics'] == {'views': None, 'likes': None, 'comments': None,
                               'saves': None, 'shares': None}
    assert item['published_at'] is None and item['metrics_source'] is None
    assert len([action for action in actions if action[0] == 'read_visible_cards']) == 1


@pytest.mark.parametrize('blocker', ['challenge', 'login_required'])
def test_visible_challenge_or_login_never_reads_covered_cards_after_http_denial(
        tmp_path, monkeypatch, blocker):
    profile = research_profile(tmp_path, monkeypatch)
    actions = install_fake_browser(monkeypatch, profile, blocker=blocker, initial_status=403,
        dom=[{'url': 'https://www.douyin.com/video/7559057198329981234', 'title': '住宅三室户型'}])
    result = browser().run_public_browser(profile, '住宅', 'relevance')
    assert result['status'] == blocker and result['items'] == []
    assert result['http_status'] == 403
    assert result['challenge_visible'] is (blocker == 'challenge')
    assert not any(action[0] == 'read_visible_cards' for action in actions)


def test_redirect_to_different_search_query_never_accepts_other_cards(tmp_path, monkeypatch):
    profile = research_profile(tmp_path, monkeypatch)
    actions = install_fake_browser(monkeypatch, profile, initial_status=403,
        page_url='https://www.douyin.com/search/%E5%AE%B6%E5%B1%85?type=video',
        dom=[{'url': 'https://www.douyin.com/video/7559057198329981234', 'title': '住宅三室户型'}])
    result = browser().run_public_browser(profile, '住宅', 'relevance')
    assert result['items'] == [] and result['status'] == 'blocked'
    assert result['restriction_reason'] == 'unexpected_public_route'
    assert not any(action[0] == 'read_visible_cards' for action in actions)


@pytest.mark.parametrize('http_status,wanted,reason', [
    (403, 'blocked', 'http_denied'), (429, 'rate_limited', 'rate_limited')])
def test_adapter_retains_only_safe_http_restriction_diagnostics(
        tmp_path, monkeypatch, http_status, wanted, reason):
    module = adapter()
    monkeypatch.setattr(module, 'research_runtime', runtime(tmp_path))
    monkeypatch.setattr(module, '_run_browser', lambda *args, **kwargs: {
        'status': wanted, 'items': [], 'http_status': http_status,
        'restriction_reason': reason, 'challenge_visible': False,
        'request_headers': {'cookie': 'private-cookie'}, 'url': 'https://secret.invalid/?token=secret'})
    items, source = module.search_public_douyin('tenant', 'project', request())
    assert items == [] and source['status'] == wanted
    assert source['http_status'] == http_status and source['restriction_reason'] == reason
    assert source['challenge_visible'] is False
    assert '扫码' not in source['reason'] and '滑块' not in source['reason']
    assert 'private-cookie' not in json.dumps(source) and 'secret.invalid' not in json.dumps(source)


@pytest.mark.parametrize('untrusted_reason', ['private-token', {'cookie': 'private-cookie'}, ['private-cookie']])
def test_adapter_drops_untrusted_restriction_details_but_keeps_real_items(tmp_path, monkeypatch,
        untrusted_reason):
    module = adapter()
    monkeypatch.setattr(module, 'research_runtime', runtime(tmp_path))
    item = browser().parse_dom_candidates([{'url': 'https://www.douyin.com/video/7559057198329981234',
        'title': '住宅三室户型'}])[0]
    monkeypatch.setattr(module, '_run_browser', lambda *args, **kwargs: {
        'status': 'partial', 'items': [item], 'http_status': True,
        'restriction_reason': untrusted_reason, 'challenge_visible': 'private-cookie'})
    items, source = module.search_public_douyin('tenant', 'project', request())
    assert len(items) == 1
    assert source.get('http_status') is None and source.get('restriction_reason') is None
    assert source.get('challenge_visible') is not True
    assert 'private-' not in json.dumps(source)


@pytest.mark.parametrize('http_status,wanted', [(403, 'blocked'), (429, 'rate_limited')])
def test_verification_http_refusal_without_controls_or_candidates_preserves_actual_status(
        tmp_path, monkeypatch, http_status, wanted):
    module = browser()
    rt = operating_runtime(tmp_path)
    scope = rt._scope('tenant', 'project', 'douyin')
    profile = scope / 'profiles' / 'DouyinProfile'
    monkeypatch.setenv('EASEL_ROOT', str(scope))
    ticks = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: ticks[0])
    def advance(value): ticks[0] += value / 1000
    install_fake_browser(monkeypatch, profile, initial_status=http_status, on_wait=advance)
    result = module.run_public_verify_search(profile, '住宅',
        status_file=scope / 'public-search-status.json', timeout=1, headed=True)
    assert result['state'] == wanted and result['status'] == wanted
    assert result['http_status'] == http_status and result['challenge_visible'] is False
    assert json.loads((scope / 'public-search-status.json').read_text())['state'] == wanted
