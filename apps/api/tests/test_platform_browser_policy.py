"""Account child processes keep Chromium sandbox and visible automation defaults."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.platform_runtime import PlatformRuntime


def policy_module():
    path=Path(__file__).resolve().parents[1]/'app/platform_browser_policy/browser_policy.py'
    assert path.is_file(), 'account browser policy must exist'
    spec=importlib.util.spec_from_file_location('account_browser_policy_test',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_scoped_child_bootstrap_protects_real_playwright_without_global_environment_change(tmp_path,monkeypatch):
    monkeypatch.setenv('PYTHONPATH','/untrusted-user-path')
    rt=PlatformRuntime(root=tmp_path,python=sys.executable)
    scope=rt._scope('tenant','project','douyin',create=True)
    env=rt._env(scope)
    assert env.get('PYTHONPATH') != '/untrusted-user-path' and env.get('PYTHONPATH')
    command="from playwright.sync_api import BrowserType; import json; print(json.dumps(bool(getattr(BrowserType.launch,'_housignal_browser_policy',False))))"
    result=subprocess.run([sys.executable,'-c',command],env=env,cwd=scope,capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and json.loads(result.stdout) is True
    assert os.environ['PYTHONPATH']=='/untrusted-user-path' and result.stderr==''


def test_sync_and_async_chromium_delegation_removes_unsafe_flags_and_keeps_proxy(monkeypatch):
    import playwright.sync_api
    import playwright.async_api
    class SyncBrowser:
        name='chromium'
        def launch(self,**kwargs): return kwargs
        def launch_persistent_context(self,path,**kwargs): return (path,kwargs)
    class AsyncBrowser:
        name='chromium'
        async def launch(self,**kwargs): return kwargs
        async def launch_persistent_context(self,path,**kwargs): return (path,kwargs)
    monkeypatch.setattr(playwright.sync_api,'BrowserType',SyncBrowser)
    monkeypatch.setattr(playwright.async_api,'BrowserType',AsyncBrowser)
    policy_module().install()
    options={'args':['--no-sandbox','--disable-setuid-sandbox','--disable-web-security',
        '--disable-blink-features=AutomationControlled','--lang=zh-CN'],
        'chromium_sandbox':False,'ignore_default_args':['--enable-automation','--mute-audio'],
        'proxy':{'server':'http://127.0.0.1:8888'},'headless':True}
    expected={'args':['--lang=zh-CN'],'chromium_sandbox':True,
        'ignore_default_args':['--mute-audio'],'proxy':{'server':'http://127.0.0.1:8888'},'headless':True}
    assert SyncBrowser().launch(**options)==expected
    assert SyncBrowser().launch_persistent_context('/private/project-profile',**options)==('/private/project-profile',expected)
    assert asyncio.run(AsyncBrowser().launch(**options))==expected
    assert asyncio.run(AsyncBrowser().launch_persistent_context('/private/project-profile',**options))==('/private/project-profile',expected)
    assert options['chromium_sandbox'] is False and '--no-sandbox' in options['args']


def test_policy_failure_stops_child_before_vendor_script(tmp_path):
    source=Path(__file__).resolve().parents[1]/'app/platform_browser_policy/sitecustomize.py'
    assert source.is_file(), 'fail-closed startup must exist'
    bootstrap=tmp_path/'policy'; bootstrap.mkdir()
    (bootstrap/'sitecustomize.py').write_bytes(source.read_bytes())
    (bootstrap/'browser_policy.py').write_text('def install():\n raise RuntimeError("fixture policy failure")\n')
    marker=tmp_path/'vendor-started'
    result=subprocess.run([sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).touch()'],
        env={**os.environ,'PYTHONPATH':str(bootstrap)},capture_output=True,text=True,timeout=10)
    assert result.returncode!=0 and not marker.exists()
    assert result.stdout==result.stderr==''


def test_missing_project_policy_refuses_to_construct_unprotected_account_environment(tmp_path):
    rt=PlatformRuntime(root=tmp_path,python=sys.executable)
    rt.browser_policy=tmp_path/'missing-policy'
    with pytest.raises(RuntimeError): rt._env(tmp_path)


def test_invalid_bootstrap_is_rejected_before_python_can_suppress_sitecustomize_error(tmp_path):
    rt=PlatformRuntime(root=tmp_path,python=sys.executable)
    policy=tmp_path/'invalid-policy'; policy.mkdir()
    (policy/'sitecustomize.py').write_text('not valid python (')
    (policy/'browser_policy.py').write_text('def install(): pass\n')
    rt.browser_policy=policy
    with pytest.raises(RuntimeError): rt._env(tmp_path)
