"""Project accounts use real processes and isolated files without contacting platforms."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from types import SimpleNamespace

import pytest


EASEL_PYTHON = sys.executable
RUNNER = '''import argparse, json, os, pathlib, sys, time
p = argparse.ArgumentParser()
p.add_argument('action')
p.add_argument('--qr-out'); p.add_argument('--status-file'); p.add_argument('--cookie')
p.add_argument('--profile-base'); p.add_argument('--timeout'); p.add_argument('--platform')
p.add_argument('--sms-code-file'); p.add_argument('--no-proxy', action='store_true')
a = p.parse_args()
root = pathlib.Path.cwd()
if a.action in ('login', 'login-qr'):
    with (root / 'launches.txt').open('a') as f: f.write('launch\\n')
    pathlib.Path(a.qr_out).write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture')
    pathlib.Path(a.status_file).write_text(json.dumps({'state':'qr_ready','message':'请扫码'}))
    time.sleep(20)
elif a.action == 'whoami':
    print((root / 'identity-fixture.json').read_text() if (root / 'identity-fixture.json').exists()
          else json.dumps({'loggedIn':False,'name':'','avatar':''}))
'''


def make_runtime(tmp_path):
    # Catch the absent implementation as an assertion rather than an import error.
    assert importlib.util.find_spec('app.platform_runtime'), 'project account runtime must exist'
    from app.platform_runtime import PlatformRuntime
    scripts = tmp_path / 'vendor/easel_accounts/skills/shared/scripts'
    scripts.mkdir(parents=True)
    for name in ('xhs_publish', 'douyin_publish', 'web_publisher', 'bili_login', 'weixin_mp_stats'):
        (scripts / (name + '.py')).write_text(RUNNER)
    runtime = PlatformRuntime(root=tmp_path, python=EASEL_PYTHON)
    # This fixture runner needs no platform packages. Only bypass the external
    # dependency probe; process lifecycle, commands, locks and files stay real.
    runtime._readiness_cache = (time.monotonic(), {'python':True,'segno':True,'browser':True})
    return runtime


def wait_for_qr(rt, platform='bilibili'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = rt.login_status('tenant', 'project', platform)
        if state['qrReady'] and state['state'] == 'qr_ready':
            return state
        time.sleep(.03)
    raise AssertionError('runner did not produce its QR')


def test_seven_platforms_start_logged_out_without_scanning_global_credentials(tmp_path):
    rt = make_runtime(tmp_path)
    rows = rt.list_accounts('tenant', 'project')
    assert {r['platform'] for r in rows} == {'xiaohongshu', 'douyin', 'kuaishou',
        'weixin-channels', 'zhihu', 'bilibili', 'wechat-oa'}
    assert all(not r['loggedIn'] and not r['verified'] for r in rows)


def test_login_and_qr_are_scoped_private_and_do_not_mark_logged_in(tmp_path):
    rt = make_runtime(tmp_path)
    try:
        rt.start_login('tenant', 'project', 'bilibili')
        state = wait_for_qr(rt)
        assert state['state'] == 'qr_ready'
        assert state['loggedIn'] is False
        qr = rt.qr_path('tenant', 'project', 'bilibili')
        assert qr is not None and qr.is_file()
        assert '/runtime/platform-accounts/' in str(qr)
        assert rt.qr_path('other-tenant', 'project', 'bilibili') is None
        assert rt.qr_path('tenant', 'other-project', 'bilibili') is None
        assert qr.stat().st_mode & 0o777 == 0o600
        assert qr.parent.stat().st_mode & 0o777 == 0o700
        assert str(tmp_path) not in json.dumps(state)
    finally:
        rt.logout('tenant', 'project', 'bilibili')


def test_simultaneous_login_reuses_one_process_across_runtime_instances(tmp_path):
    rt = make_runtime(tmp_path)
    from app.platform_runtime import PlatformRuntime
    other = PlatformRuntime(root=tmp_path, python=EASEL_PYTHON)
    other._readiness_cache = rt._readiness_cache
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            states = list(pool.map(lambda r: r.start_login('tenant', 'project', 'bilibili'), [rt, other]))
        wait_for_qr(rt)
        qr = rt.qr_path('tenant', 'project', 'bilibili')
        assert (qr.parent / 'launches.txt').read_text().splitlines() == ['launch']
        assert all(s['state'] in {'starting', 'qr_ready'} for s in states)
    finally:
        rt.logout('tenant', 'project', 'bilibili')


def test_expired_and_malformed_status_never_claim_login_success(tmp_path):
    rt = make_runtime(tmp_path)
    try:
        rt.start_login('tenant', 'project', 'bilibili')
        wait_for_qr(rt)
        qr = rt.qr_path('tenant', 'project', 'bilibili')
        status = qr.parent / 'status.json'
        status.write_text('{broken')
        assert rt.login_status('tenant', 'project', 'bilibili')['loggedIn'] is False
        status.write_text(json.dumps({'state':'expired','message':'token=secret /Users/private cookies.json'}))
        state = rt.login_status('tenant', 'project', 'bilibili')
        assert state['loggedIn'] is False
        assert not any(s in state['message'] for s in ('secret', '/Users/private', 'cookies.json'))
    finally:
        rt.logout('tenant', 'project', 'bilibili')


def test_uncertain_whoami_preserves_last_login_but_does_not_verify_it(tmp_path):
    rt = make_runtime(tmp_path)
    rt.start_login('tenant', 'project', 'bilibili')
    wait_for_qr(rt)
    qr = rt.qr_path('tenant', 'project', 'bilibili')
    rt.logout('tenant', 'project', 'bilibili')
    qr.parent.mkdir(parents=True, exist_ok=True)
    (qr.parent / 'status.json').write_text(json.dumps({'state':'success'}))
    (qr.parent / 'identity-fixture.json').write_text(json.dumps({'loggedIn':False,
        'name':'','avatar':'','error':'network unavailable token=secret'}))
    result = rt.whoami('tenant', 'project', 'bilibili')
    assert result['loggedIn'] is True
    assert result['verified'] is False
    assert 'secret' not in json.dumps(result)


def test_verified_logged_out_identity_clears_old_success(tmp_path):
    rt = make_runtime(tmp_path)
    rt.start_login('tenant', 'project', 'bilibili')
    wait_for_qr(rt)
    qr = rt.qr_path('tenant', 'project', 'bilibili')
    rt.logout('tenant', 'project', 'bilibili')
    qr.parent.mkdir(parents=True, exist_ok=True)
    (qr.parent / 'status.json').write_text(json.dumps({'state':'success'}))
    result = rt.whoami('tenant', 'project', 'bilibili')
    assert result['loggedIn'] is False and result['verified'] is True
    assert rt.login_status('tenant', 'project', 'bilibili')['loggedIn'] is False


def test_sms_requires_current_sms_challenge_and_is_private(tmp_path):
    rt = make_runtime(tmp_path)
    try:
        rt.start_login('tenant', 'project', 'douyin')
        wait_for_qr(rt, 'douyin')
        with pytest.raises(ValueError):
            rt.submit_sms('tenant', 'project', 'douyin', '123456')
        qr = rt.qr_path('tenant', 'project', 'douyin')
        (qr.parent / 'status.json').write_text(json.dumps({'state':'sms_required'}))
        rt.submit_sms('tenant', 'project', 'douyin', '123456')
        code = qr.parent / 'sms.code'
        assert code.read_text() == '123456'
        assert code.stat().st_mode & 0o777 == 0o600
        for invalid in ('123', '12x3456', '123456789', '../123456'):
            with pytest.raises(ValueError):
                rt.submit_sms('tenant', 'project', 'douyin', invalid)
    finally:
        rt.logout('tenant', 'project', 'douyin')


def test_logout_does_not_signal_an_unowned_process(tmp_path):
    rt = make_runtime(tmp_path)
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
    try:
        rt.start_login('tenant', 'project', 'bilibili')
        wait_for_qr(rt)
        qr = rt.qr_path('tenant', 'project', 'bilibili')
        rt.logout('tenant', 'project', 'bilibili')
        qr.parent.mkdir(parents=True, exist_ok=True)
        (qr.parent / 'process.json').write_text(json.dumps({'pid':proc.pid,'command':['foreign']}))
        rt.logout('tenant', 'project', 'bilibili')
        assert proc.poll() is None
    finally:
        proc.terminate(); proc.wait(timeout=5)


@pytest.mark.parametrize('platform', ['xiaohongshu','douyin','kuaishou','weixin-channels','zhihu','bilibili'])
def test_publish_commands_default_to_dry_run_with_private_credentials(tmp_path, platform):
    rt = make_runtime(tmp_path)
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'fixture')
    package = rt.build_publish_command('tenant', 'project', platform,
        title='客厅采光', body='现场观察', media=[str(media)])
    assert package['execute'] is False and '--exec' not in package['command']
    assert any('/runtime/platform-accounts/' in arg for arg in package['command'])
    assert package['env']['EASEL_CALENDAR_AUTORECORD'] == '0'
    assert package['env']['EASEL_ROOT'] == package['cwd']


def test_wechat_preparation_never_builds_an_implicit_live_write(tmp_path):
    rt = make_runtime(tmp_path)
    html = tmp_path / 'article.html'; html.write_text('<p>现场观察</p>')
    cover = tmp_path / 'cover.png'; cover.write_bytes(b'fixture')
    package = rt.build_publish_command('tenant', 'project', 'wechat-oa',
        title='客厅采光', body='<p>现场观察</p>', media=[str(cover)])
    assert package['execute'] is False
    assert package['draft_only'] is True
    assert 'publish' not in package['command']


def test_unknown_platform_and_empty_scope_are_rejected(tmp_path):
    rt = make_runtime(tmp_path)
    with pytest.raises(ValueError): rt.login_status('tenant', 'project', '../../cookie')
    with pytest.raises(ValueError): rt.list_accounts('', 'project')


def test_publish_lock_allows_revalidation_and_blocks_login_mutation(tmp_path):
    rt = make_runtime(tmp_path)
    from app.platform_runtime import PlatformRuntime
    other = PlatformRuntime(root=tmp_path, python=EASEL_PYTHON)
    other._readiness_cache = rt._readiness_cache
    with rt.publish_lock('tenant', 'project', 'bilibili'):
        result = rt.whoami('tenant', 'project', 'bilibili')
        assert result['verified'] is True and result['loggedIn'] is False
        with pytest.raises(RuntimeError):
            other.start_login('tenant', 'project', 'bilibili')
        with pytest.raises(RuntimeError):
            other.logout('tenant', 'project', 'bilibili')


def test_publish_validation_rejects_swapped_command_and_scope(tmp_path):
    rt = make_runtime(tmp_path)
    media = tmp_path / 'clip.mp4'; media.write_bytes(b'fixture')
    spec = rt.build_publish_command('tenant', 'project', 'douyin',
                                   title='客厅采光', body='现场观察', media=[str(media)])
    assert rt.validate_publish_command(spec, 'tenant', 'project', 'douyin') is True
    altered = {**spec, 'command':[sys.executable, '-c', 'print(1)']}
    with pytest.raises(ValueError): rt.validate_publish_command(altered, 'tenant', 'project', 'douyin')
    with pytest.raises(ValueError): rt.validate_publish_command(spec, 'tenant', 'other', 'douyin')
    with pytest.raises(ValueError): rt.validate_publish_command({**spec,'env':{**spec['env'],
        'PYTHONPATH':'/external'}}, 'tenant', 'project', 'douyin')


def test_wechat_draft_uses_explicit_html_and_cover_without_auto_send(tmp_path):
    rt = make_runtime(tmp_path)
    html = tmp_path / 'reviewed.html'; html.write_text('<p>已审核</p>')
    cover = tmp_path / 'cover.png'; cover.write_bytes(b'fixture')
    spec = rt.build_publish_command('tenant', 'project', 'wechat-oa',
        title='客厅采光', body='已审核', media=[str(html), str(cover)], execute=True)
    assert spec['draft_only'] is True
    command = spec['command']
    assert command[command.index('--html')+1] == str(html)
    assert 'masssend' not in command and 'send' not in command
    assert rt.validate_publish_command(spec,'tenant','project','wechat-oa') is True


def test_account_list_returns_cached_verified_identity_under_platform_label(tmp_path):
    rt = make_runtime(tmp_path)
    rt.start_login('tenant','project','bilibili'); wait_for_qr(rt)
    qr = rt.qr_path('tenant','project','bilibili')
    rt.logout('tenant','project','bilibili')
    (qr.parent / 'identity-fixture.json').write_text(json.dumps({'loggedIn':True,
        'name':'江岸生活','avatar':'https://images.example.org/face.png'}))
    assert rt.whoami('tenant','project','bilibili')['verified'] is True
    account = next(r for r in rt.list_accounts('tenant','project') if r['platform']=='bilibili')
    assert account['name']=='B站'
    assert account['identity_name']=='江岸生活'
    assert account['avatar']=='https://images.example.org/face.png'


def test_xhs_network_risk_is_actionable_without_exposing_runner_details(tmp_path):
    rt = make_runtime(tmp_path)
    try:
        rt.start_login('tenant','project','xiaohongshu'); wait_for_qr(rt,'xiaohongshu')
        qr = rt.qr_path('tenant','project','xiaohongshu')
        (qr.parent/'status.json').write_text(json.dumps({'state':'error',
            'message':'IP 存在风险，需干净网络/代理 token=secret /Users/private'}))
        result=rt.login_status('tenant','project','xiaohongshu')
        assert '300012' in result['message'] and '网络' in result['message']
        assert 'secret' not in result['message'] and '/Users/private' not in result['message']
    finally:
        rt.logout('tenant','project','xiaohongshu')


def test_logout_cannot_interleave_between_identity_probe_and_persist(tmp_path, monkeypatch):
    rt = make_runtime(tmp_path)
    rt.start_login('tenant','project','bilibili'); wait_for_qr(rt)
    qr=rt.qr_path('tenant','project','bilibili')
    rt.logout('tenant','project','bilibili')
    (qr.parent/'identity-fixture.json').write_text(json.dumps({'loggedIn':True,'name':'江岸生活','avatar':''}))
    reached=threading.Event(); resume=threading.Event()
    original=rt._write
    def pause_persist(path, value):
        if path.name=='identity.json':
            reached.set()
            assert resume.wait(timeout=5)
        return original(path,value)
    monkeypatch.setattr(rt,'_write',pause_persist)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future=pool.submit(rt.whoami,'tenant','project','bilibili')
        try:
            assert reached.wait(timeout=5)
            with pytest.raises(RuntimeError):
                rt.logout('tenant','project','bilibili')
        finally:
            resume.set()
        assert future.result(timeout=5)['loggedIn'] is True
    rt.logout('tenant','project','bilibili')
    assert rt.login_status('tenant','project','bilibili')['loggedIn'] is False


def test_publish_sms_only_consumes_current_scoped_douyin_challenge(tmp_path):
    rt=make_runtime(tmp_path)
    video=tmp_path/'clip.mp4'; video.write_bytes(b'fixture')
    scope=Path(rt.build_publish_command('tenant','project','douyin',media=[str(video)])['cwd'])
    with pytest.raises(ValueError): rt.submit_publish_sms('tenant','project','douyin','123456')
    (scope/'publish-status.json').write_text(json.dumps({'state':'sms_required','message':'token=secret'}))
    state=rt.publish_status('tenant','project','douyin')
    assert state['state']=='sms_required' and 'secret' not in json.dumps(state)
    rt.submit_publish_sms('tenant','project','douyin','123456')
    assert (scope/'publish-sms.code').read_text()=='123456'
    assert (scope/'publish-sms.code').stat().st_mode & 0o777 == 0o600
    assert rt.publish_status('other','project','douyin')['state']=='unknown'
    with pytest.raises(ValueError): rt.submit_publish_sms('tenant','project','bilibili','123456')


def test_verified_legacy_login_gets_one_private_session_generation(tmp_path):
    rt=make_runtime(tmp_path)
    scope=rt._scope('tenant','project','bilibili',create=True)
    (scope/'status.json').write_text(json.dumps({'state':'success'}))
    (scope/'identity-fixture.json').write_text(json.dumps({'loggedIn':True,'name':'江岸生活','avatar':''}))
    assert not rt.login_status('tenant','project','bilibili').get('session_id')
    first=rt.whoami('tenant','project','bilibili')
    assert isinstance(first.get('session_id'),str) and len(first['session_id'])==64
    assert rt.whoami('tenant','project','bilibili')['session_id']==first['session_id']
    status=rt.login_status('tenant','project','bilibili')
    assert status['session_id']==first['session_id'] and status['verified'] is True
    assert (scope/'session.json').stat().st_mode & 0o777==0o600
    assert rt.login_status('other','project','bilibili').get('session_id') != first['session_id']


def test_status_displays_only_current_verified_public_identity(tmp_path):
    rt=make_runtime(tmp_path)
    scope=rt._scope('tenant','project','bilibili',create=True)
    (scope/'identity-fixture.json').write_text(json.dumps({'loggedIn':True,
        'name':'江岸\x00生活','avatar':'https://example.org/avatar.png'}))
    rt.whoami('tenant','project','bilibili')
    current=rt.login_status('tenant','project','bilibili')
    assert current.get('name')=='江岸生活' and current.get('avatar')=='https://example.org/avatar.png'
    with rt.publish_lock('tenant','project','bilibili'): rt._rotate_session(scope)
    changed=rt.login_status('tenant','project','bilibili')
    assert changed['verified'] is False and changed.get('name')==changed.get('avatar')==''


def test_stale_identity_status_omits_previous_account_name_and_avatar(tmp_path):
    rt=make_runtime(tmp_path)
    scope=rt._scope('tenant','project','bilibili',create=True)
    (scope/'identity-fixture.json').write_text(json.dumps({'loggedIn':True,
        'name':'江岸生活','avatar':'https://example.org/avatar.png'}))
    rt.whoami('tenant','project','bilibili')
    identity=json.loads((scope/'identity.json').read_text()); identity['ts']=time.time()-601
    (scope/'identity.json').write_text(json.dumps(identity))
    status=rt.login_status('tenant','project','bilibili')
    assert status['verified'] is False and status.get('name')==status.get('avatar')==''


def test_unknown_legacy_probe_cannot_migrate_to_authenticated_session(tmp_path):
    rt=make_runtime(tmp_path)
    scope=rt._scope('tenant','project','bilibili',create=True)
    (scope/'status.json').write_text(json.dumps({'state':'success'}))
    (scope/'identity-fixture.json').write_text(json.dumps({'loggedIn':True,'error':'network failed'}))
    result=rt.whoami('tenant','project','bilibili')
    assert result['verified'] is False and result.get('session_id')==''
    assert not (scope/'session.json').exists()


def test_new_login_and_logout_rotate_generation_but_live_login_reuses_it(tmp_path):
    rt=make_runtime(tmp_path)
    try:
        first=rt.start_login('tenant','project','bilibili')
        wait_for_qr(rt)
        assert isinstance(first.get('session_id'),str) and len(first['session_id'])==64
        assert rt.start_login('tenant','project','bilibili')['session_id']==first['session_id']
        logged_out=rt.logout('tenant','project','bilibili')
        assert len(logged_out['session_id'])==64 and logged_out['session_id']!=first['session_id']
        next_login=rt.start_login('tenant','project','bilibili')
        assert next_login['session_id'] not in {first['session_id'],logged_out['session_id']}
    finally:
        rt.logout('tenant','project','bilibili')


def test_publisher_logout_preserves_separate_douyin_research_session(tmp_path):
    rt=make_runtime(tmp_path)
    scope=rt._scope('tenant','project','douyin',create=True)
    research=scope/'research'; research.mkdir()
    marker=research/'status.json'; marker.write_text('independent research login')
    lock=research/'.session.lock'; lock.write_text('')
    lock_identity=lock.stat().st_ino
    publisher=scope/'profiles'; publisher.mkdir(); (publisher/'cookie-fixture').write_text('publisher fixture')
    rt.logout('tenant','project','douyin')
    assert marker.exists() and marker.read_text()=='independent research login'
    assert lock.stat().st_ino==lock_identity
    assert not publisher.exists()


def test_generic_publisher_timeout_screenshot_stays_in_scoped_root(tmp_path, monkeypatch):
    # Only replace the external browser. Execute the real vendored failure path.
    root=Path(__file__).resolve().parents[3]
    script=root/'vendor/easel_accounts/skills/shared/scripts/web_publisher.py'
    monkeypatch.syspath_prepend(str(script.parent))
    monkeypatch.setenv('EASEL_ROOT',str(tmp_path))
    spec=importlib.util.spec_from_file_location('scoped_web_publisher',script)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class BrowserTimeout(Exception): pass
    class Page:
        url='https://example.org'
        def goto(self,*args,**kwargs): raise BrowserTimeout('fixture timeout')
        def screenshot(self,*,path): Path(path).write_bytes(b'private fixture screenshot')
        def evaluate(self,*args): return ''
    page=Page()
    browser=SimpleNamespace(pages=[page],close=lambda:None)
    playwright=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a,**kw:browser))
    class BrowserSession:
        def __enter__(self): return playwright
        def __exit__(self,*args): pass
    monkeypatch.setitem(sys.modules,'playwright.sync_api',SimpleNamespace(sync_playwright=BrowserSession,TimeoutError=BrowserTimeout))
    args=SimpleNamespace(platform='zhihu',profile_base=str(tmp_path/'profiles'),
        media=None,title='客厅采光',desc='现场观察',tags='',cover=None,keep_open=False)
    with pytest.raises(SystemExit): module._run_browser(args,headed=False,do_publish=True)
    assert (tmp_path/'outputs/_login/zhihu-publish-fail.png').read_bytes()==b'private fixture screenshot'


def test_identity_timeout_reaps_its_browser_child_process(tmp_path, monkeypatch):
    import signal
    rt=make_runtime(tmp_path)
    video=tmp_path/'clip.mp4'; video.write_bytes(b'fixture')
    scope=Path(rt.build_publish_command('tenant','project','bilibili',media=[str(video)])['cwd'])
    runner=tmp_path/'vendor/easel_accounts/skills/shared/scripts/bili_login.py'
    runner.write_text("import subprocess,sys,time,pathlib\n"
        "time.sleep(.4)\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])\n"
        "pathlib.Path('browser-child.pid').write_text(str(child.pid))\n"
        "time.sleep(20)\n")
    original_popen=subprocess.Popen
    marker=scope/'browser-child.pid'
    def ready_process(command, **kwargs):
        process=original_popen(command,**kwargs)
        if len(command)>1 and command[1]==str(runner):
            # The test's short timeout measures cleanup of an existing child,
            # independently of Python/Playwright bootstrap speed under load.
            try:
                deadline=time.monotonic()+10
                while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
                    time.sleep(.02)
                assert marker.exists(), 'identity fixture did not start its browser child'
            except BaseException:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                process.communicate(timeout=5)
                raise
        return process
    monkeypatch.setattr(subprocess,'Popen',ready_process)
    with pytest.raises(subprocess.TimeoutExpired):
        rt._run_probe(scope,'bilibili',timeout=.3)
    child_pid=int((scope/'browser-child.pid').read_text())
    # A terminated child can briefly be a zombie while the host reaps it, but
    # it must never retain a running browser or occupied profile after timeout.
    state=subprocess.run(['ps','-p',str(child_pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
    assert not state or state.startswith('Z')


def test_identity_timeout_kills_term_ignoring_detached_stdio_child(tmp_path):
    rt=make_runtime(tmp_path)
    video=tmp_path/'clip.mp4'; video.write_bytes(b'fixture')
    scope=Path(rt.build_publish_command('tenant','project','bilibili',media=[str(video)])['cwd'])
    runner=tmp_path/'vendor/easel_accounts/skills/shared/scripts/bili_login.py'
    runner.write_text("import subprocess,sys,time,pathlib\n"
        "child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(20)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "pathlib.Path('browser-child.pid').write_text(str(child.pid))\n"
        "time.sleep(20)\n")
    child_pid=None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            rt._run_probe(scope,'bilibili',timeout=.3)
        child_pid=int((scope/'browser-child.pid').read_text())
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            state=subprocess.run(['ps','-p',str(child_pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not state or state.startswith('Z'):
                break
            time.sleep(.03)
        assert not state or state.startswith('Z')
    finally:
        # Keep a failing test from leaving its deliberately stubborn fixture.
        if child_pid:
            try: os.kill(child_pid,9)
            except ProcessLookupError: pass
