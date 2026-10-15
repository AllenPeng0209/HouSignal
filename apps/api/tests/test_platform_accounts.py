"""Account login boundaries and approved, project-scoped publication packages."""
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import zipfile
from contextlib import nullcontext

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


BASE = '/api/projects/p/operations/platform-accounts'


class RuntimeBoundary:
    """Only the external browser/CLI is replaced; API/database checks are real."""
    def __init__(self, root):
        self.root = root
        self.sessions = {}

    def publish_lock(self, tenant, project, platform):
        return nullcontext()

    def list_accounts(self, tenant, project):
        return [{'platform': name, 'name': label, 'backend': label, 'supported': True,
                 'loggedIn': self.sessions.get((tenant, project, name)) == 'logged_in',
                 'verified': False, 'ready': True, 'note': '', 'cookie': 'private-cookie'}
                for name, label in [('xiaohongshu', '小红书'), ('kuaishou', '快手'),
                                    ('weixin-channels', '微信视频号'), ('zhihu', '知乎'),
                                    ('bilibili', 'B站'), ('douyin', '抖音'), ('wechat-oa', '微信公众号')]]

    def login_status(self, tenant, project, platform):
        state = self.sessions.get((tenant, project, platform), 'idle')
        return {'state': state, 'message': '扫码登录', 'qrReady': state == 'waiting',
                'qrTs': 1, 'verified': state == 'logged_in', 'loggedIn': state == 'logged_in',
                'cookie': 'private-cookie', 'path': '/Users/private/profile'}

    def start_login(self, tenant, project, platform):
        self.sessions[(tenant, project, platform)] = 'waiting'
        return self.login_status(tenant, project, platform)

    def submit_sms(self, tenant, project, platform, code):
        if not code.isdigit():
            raise ValueError('private cookie=value path=/Users/private')
        self.sessions[(tenant, project, platform)] = 'logged_in'
        return self.login_status(tenant, project, platform)

    def whoami(self, tenant, project, platform):
        return self.login_status(tenant, project, platform)

    def logout(self, tenant, project, platform):
        self.sessions.pop((tenant, project, platform), None)
        return self.login_status(tenant, project, platform)

    def publish_status(self, tenant, project, platform):
        return {'state': 'sms_required', 'message': '输入验证码', 'cookie': 'private-cookie'}

    def submit_publish_sms(self, tenant, project, platform, code):
        return {'state': 'verifying', 'message': '验证码已提交', 'code': code}

    def qr_path(self, tenant, project, platform):
        if self.sessions.get((tenant, project, platform)) != 'waiting':
            return None
        path = self.root / f'{tenant}-{project}-{platform}.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\nscoped-login-qr')
        return path

    def build_publish_command(self, tenant, project, platform, **kwargs):
        return {'command': ['python', '/Users/private/publish.py', '--dry-run'],
                'cwd': '/Users/private', 'env': {'COOKIE': 'private-cookie'},
                'execute': kwargs.get('execute', False), 'draft_only': platform == 'wechat-oa'}


@pytest.fixture
def account_client(tmp_path, monkeypatch):
    from app.db import Base, get_db
    from app.core_models import Tenant, User, Property, Membership
    from app.security import Context, get_context
    from app.config import settings
    import app.business_models
    import app.agent_models
    import app.job_models
    import app.audit_models
    module = None
    if importlib.util.find_spec('app.platform_accounts'):
        from app import platform_accounts as module
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='另一团队'),
                User(id='u', email='content@test.cn', password_hash='unused', name='编辑', active=True)])
    db.flush()
    db.add(Membership(user_id='u', tenant_id='t', role='content', active=True))
    for pid, tenant in [('p', 't'), ('p2', 't'), ('foreign', 'other')]:
        db.add(Property(id=pid, tenant_id=tenant, name=pid, city='苏州', channel='抖音',
                        marketing_confirmed=True, marketing_evidence='项目书面授权'))
    db.commit()
    context = Context('u', 't', 'content', '编辑', 'content@test.cn', None)
    current = {'context': context}
    fake = RuntimeBoundary(tmp_path)
    if module:
        monkeypatch.setattr(module, 'runtime', fake)
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    from app import jobs
    monkeypatch.setattr(jobs, 'KINDS', jobs.KINDS | {'platform_prepare'})
    app = FastAPI()
    if module:
        app.include_router(module.router)
    def database():
        try:
            yield db
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: current['context']
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, current=current, context=context, module=module, root=tmp_path)
    db.close()
    engine.dispose()


def approve_fixture(env, channel='抖音', video_payload=None):
    from app.core_models import Asset, Fact
    from app.business_models import ContentVersion
    from app.business import content_snapshot, snapshot_sha256, approve_content
    env.db.add_all([Fact(id='fact', tenant_id='t', property_id='p', semantic_key='area',
                        label='面积', value='420', unit='㎡', source='实测资料', status='verified',
                        revision=1, required=True),
                    Asset(id='photo', tenant_id='t', property_id='p', name='实拍', kind='image',
                          storage_key='photo.jpg', mime_type='image/jpeg', sha256='photo-digest',
                          byte_size=3, rights='authorized', rights_evidence='实拍授权', origin_type='actual')])
    version = ContentVersion(id='content', tenant_id='t', property_id='p', created_by='u',
        script='420㎡实拍空间', fact_refs=[{'id': 'fact', 'revision': 1}],
        storyboard=[{'asset_id': 'photo', 'duration': 3, 'caption': '420㎡空间'}],
        cover_title='江岸实拍', channel=channel, topic='空间', status='rendered')
    env.db.add(version)
    env.db.flush()
    payload = video_payload or b'approved-mp4-payload'
    (env.root / 'render.mp4').write_bytes(payload)
    env.db.add(Asset(id='render', tenant_id='t', property_id='p', name='成片', kind='video',
        storage_key='render.mp4', mime_type='video/mp4', sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload), rights='authorized', rights_evidence='成片授权', origin_type='actual',
        metadata_json={'renderer': 'ffmpeg', 'content_version_id': 'content',
                       'content_snapshot_sha256': snapshot_sha256(content_snapshot(env.db, env.context, version))}))
    version.render_asset_id = 'render'
    env.db.flush()
    reviewer = replace(env.context, role='admin')
    approve_content(env.db, reviewer, 'p', 'content', {'confirmed': True})
    env.db.commit()
    return version


def test_seven_cards_omit_credentials_and_use_project_session_namespace(account_client):
    env = account_client
    response = env.client.get(BASE)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 7
    assert 'private-cookie' not in response.text
    assert all(row['loggedIn'] is False for row in rows)
    assert env.client.post(BASE + '/douyin/login').status_code == 200
    status = env.client.get(BASE + '/douyin/status')
    assert status.json()['state'] == 'waiting'
    assert 'private-cookie' not in status.text and '/Users/' not in status.text
    other = env.client.get('/api/projects/p2/operations/platform-accounts/douyin/status')
    assert other.json()['state'] == 'idle'


@pytest.mark.parametrize('method,suffix,body', [('get', '', None), ('get', '/douyin/status', None),
    ('get', '/douyin/qr', None), ('post', '/douyin/login', {}),
    ('post', '/douyin/sms', {'code': '123456'}), ('post', '/douyin/verify', {}),
    ('post', '/douyin/logout', {}), ('post', '/douyin/prepare', {'content_version_id': 'content'})])
def test_every_account_action_checks_tenant_and_project_scope(account_client, method, suffix, body):
    env = account_client
    env.current['context'] = replace(env.context, project_ids=['p2'])
    response = getattr(env.client, method)(BASE + suffix, **({'json': body} if body is not None else {}))
    assert response.status_code == 404
    env.current['context'] = replace(env.context, tenant_id='other')
    response = getattr(env.client, method)(BASE + suffix, **({'json': body} if body is not None else {}))
    assert response.status_code == 404


@pytest.mark.parametrize('suffix,body', [('/douyin/login', {}), ('/douyin/verify', {}),
    ('/douyin/logout', {}), ('/douyin/sms', {'code': '123456'}),
    ('/douyin/prepare', {'content_version_id': 'content'})])
def test_viewers_cannot_mutate_account_or_prepare_publication(account_client, suffix, body):
    env = account_client
    env.current['context'] = replace(env.context, role='viewer')
    assert env.client.post(BASE + suffix, json=body).status_code == 403


def test_sms_validation_qr_and_logout_never_leak_browser_profile(account_client):
    env = account_client
    assert env.client.get(BASE + '/douyin/qr').status_code == 404
    assert env.client.post(BASE + '/douyin/login').status_code == 200
    qr = env.client.get(BASE + '/douyin/qr')
    assert qr.status_code == 200 and qr.content.startswith(b'\x89PNG')
    assert '/Users/' not in str(qr.headers)
    assert env.client.post(BASE + '/douyin/sms', json={'code': 'a'}).status_code == 422
    assert env.client.post(BASE + '/douyin/sms', json={'code': '123456', 'cookie': 'bad'}).status_code == 422
    assert env.client.post(BASE + '/douyin/sms', json={'code': '123456'}).json()['verified'] is True
    assert env.client.post(BASE + '/douyin/verify').json()['loggedIn'] is True
    assert env.client.post(BASE + '/douyin/logout').json()['loggedIn'] is False


def test_prepare_rejects_unapproved_versions_and_channel_mismatch(account_client):
    env = account_client
    assert env.client.post(BASE + '/douyin/prepare', json={'content_version_id': 'missing'}).status_code == 404
    approve_fixture(env)
    assert env.client.post(BASE + '/zhihu/prepare', json={'content_version_id': 'content'}).status_code == 409
    from app.core_models import Fact
    env.db.get(Fact, 'fact').revision = 2
    env.db.commit()
    assert env.client.post(BASE + '/douyin/prepare', json={'content_version_id': 'content'}).status_code == 409
    from app.job_models import Job
    assert env.db.scalars(select(Job)).all() == []


def test_prepare_builds_real_approved_zip_and_download_rechecks_current_approval(account_client):
    env = account_client
    approve_fixture(env)
    response = env.client.post(BASE + '/douyin/prepare', json={'content_version_id': 'content'})
    assert response.status_code == 200, response.text
    from app.job_models import Job
    job = env.db.get(Job, response.json()['job_id'])
    result = env.module.handle_prepare(env.db, job)
    assert result['dry_run'] is True and result['publication_state'] == 'not_published'
    assert '/Users/' not in json.dumps(result) and 'private-cookie' not in json.dumps(result)
    job.result, job.status = result, 'succeeded'
    env.db.commit()
    download = env.client.get(result['package']['download_url'])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as package:
        assert package.read('video.mp4') == b'approved-mp4-payload'
        assert package.read('script.txt').decode() == '420㎡实拍空间'
        manifest = json.loads(package.read('manifest.json'))
        assert manifest['publication_state'] == 'not_published'
        assert manifest['release_id'] == result['release_id']
    assert len(env.client.get(BASE + '/tasks').json()) == 1
    env.current['context'] = replace(env.context, project_ids=['p2'])
    assert env.client.get(result['package']['download_url']).status_code == 404
    env.current['context'] = env.context
    from app.core_models import Asset
    env.db.get(Asset, 'photo').rights = 'revoked'
    env.db.commit()
    assert env.client.get(result['package']['download_url']).status_code == 409


def test_agent_account_tools_scope_inputs_and_start_login_without_credentials(account_client):
    env = account_client
    assert env.module is not None
    listed = env.module.run_agent_tool(env.db, env.context, 'p', 'get_platform_accounts', {})
    assert len(listed['data']) == 7 and listed['sources'][0]['page'] == 'accounts'
    login = env.module.run_agent_tool(env.db, env.context, 'p', 'start_platform_login', {'platform': 'douyin'})
    assert login['data']['state'] == 'waiting'
    assert 'cookie' not in login['data']
    with pytest.raises((HTTPException, ValueError)):
        env.module.run_agent_tool(env.db, env.context, 'p', 'get_platform_accounts', {'tenant_id': 'other'})
    with pytest.raises(HTTPException) as forbidden:
        env.module.run_agent_tool(env.db, replace(env.context, role='viewer'), 'p', 'start_platform_login', {'platform': 'douyin'})
    assert forbidden.value.status_code == 403


def test_prepare_uses_real_runtime_media_list_contract_without_executing_browser(account_client, monkeypatch):
    env = account_client
    approve_fixture(env)
    response = env.client.post(BASE + '/douyin/prepare', json={'content_version_id': 'content'})
    assert response.status_code == 200
    from app.platform_runtime import PlatformRuntime
    monkeypatch.setattr(env.module, 'runtime', PlatformRuntime(root=env.root, python='/usr/bin/python3'))
    from app.job_models import Job
    result = env.module.handle_prepare(env.db, env.db.get(Job, response.json()['job_id']))
    assert result['dry_run'] is True and result['package']['byte_size'] > 0


def test_verify_returns_safe_identity_and_normalized_state(account_client, monkeypatch):
    env = account_client
    monkeypatch.setattr(env.module.runtime, 'whoami', lambda *args: {
        'loggedIn': True, 'verified': True, 'name': '江岸账号',
        'avatar': 'https://images.example.org/avatar.png', 'cookie': 'hidden-cookie'})
    response = env.client.post(BASE + '/douyin/verify')
    assert response.status_code == 200
    assert response.json()['state'] == 'success'
    assert response.json()['name'] == '江岸账号'
    assert response.json()['avatar'] == 'https://images.example.org/avatar.png'
    assert 'hidden-cookie' not in response.text


def test_cached_account_identity_preserves_platform_label_and_filters_credentials(account_client, monkeypatch):
    env = account_client
    monkeypatch.setattr(env.module.runtime, 'list_accounts', lambda *args: [{
        'platform': 'douyin', 'name': 'must-not-replace-platform', 'identity_name': '江岸账号',
        'avatar': 'https://images.example.org/avatar.png', 'verified': True, 'loggedIn': True,
        'supported': True, 'ready': True, 'cookie': 'hidden-cookie'}])
    response = env.client.get(BASE)
    row = next(item for item in response.json() if item['platform'] == 'douyin')
    assert row['name'] == '抖音' and row['identity_name'] == '江岸账号'
    assert row['avatar'] == 'https://images.example.org/avatar.png'
    assert 'hidden-cookie' not in response.text


def test_package_integrity_and_revalidation_at_execution_block_stale_or_tampered_data(account_client):
    env = account_client
    approve_fixture(env)
    response = env.client.post(BASE + '/douyin/prepare', json={'content_version_id': 'content'})
    from app.job_models import Job
    job = env.db.get(Job, response.json()['job_id'])
    from app.core_models import Fact
    fact = env.db.get(Fact, 'fact')
    fact.revision = 2
    env.db.commit()
    with pytest.raises(HTTPException) as changed:
        env.module.handle_prepare(env.db, job)
    assert changed.value.status_code == 409
    fact.revision = 1
    env.db.commit()
    result = env.module.handle_prepare(env.db, job)
    job.result, job.status = result, 'succeeded'
    env.db.commit()
    path = env.module._package_path(job)
    path.write_bytes(b'tampered-package')
    assert env.client.get(result['package']['download_url']).status_code == 409


def test_runtime_errors_are_safe_and_login_qr_is_not_available_to_viewers(account_client, monkeypatch):
    env = account_client
    def failed(*args):
        raise RuntimeError('cookie=private-session /Users/private/browser')
    monkeypatch.setattr(env.module.runtime, 'start_login', failed)
    response = env.client.post(BASE + '/douyin/login')
    assert response.status_code == 409
    assert 'private-session' not in response.text and '/Users/' not in response.text
    env.current['context'] = replace(env.context, role='viewer')
    assert env.client.get(BASE + '/douyin/qr').status_code == 403
    env.current['context'] = env.context
    assert env.client.get(BASE + '/unknown/status').status_code == 404


def test_prepare_retry_owns_distinct_files_so_stale_cleanup_cannot_remove_successor(account_client):
    env = account_client
    approve_fixture(env)
    response = env.client.post(BASE + '/douyin/prepare', json={'content_version_id': 'content'})
    from app.job_models import Job
    job = env.db.get(Job, response.json()['job_id'])
    first = env.module.handle_prepare(env.db, job)
    job.result = first
    first_path = env.module._package_path(job)
    second = env.module.handle_prepare(env.db, job)
    job.result = second
    second_path = env.module._package_path(job)
    assert first_path != second_path
    first_path.unlink()
    assert second_path.is_file()


def test_package_preview_contains_exact_publisher_text_and_article_cover(account_client, monkeypatch):
    env = account_client
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg is required for approved-render derivative cover integration')
    source = env.root / 'approved-source.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.1',
                    '-pix_fmt', 'yuv420p', str(source)], capture_output=True, check=True)
    approve_fixture(env, channel='微信公众号', video_payload=source.read_bytes())
    response = env.client.post(BASE + '/wechat-oa/prepare', json={'content_version_id': 'content'})
    assert response.status_code == 200, response.text
    from app.platform_runtime import PlatformRuntime
    monkeypatch.setattr(env.module, 'runtime', PlatformRuntime(root=env.root, python='/usr/bin/python3'))
    from app.job_models import Job
    job = env.db.get(Job, response.json()['job_id'])
    result = env.module.handle_prepare(env.db, job)
    assert result['publication_preview'] == {'title': '江岸实拍', 'body': '<p>420㎡实拍空间</p>',
        'draft_only': True, 'content_format': 'html'}
    job.result = result
    with zipfile.ZipFile(env.module._package_path(job)) as archive:
        assert archive.read('article.html').decode() == '<p>420㎡实拍空间</p>'
        assert archive.read('cover.jpg').startswith(b'\xff\xd8')
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['publication_preview'] == result['publication_preview']
        assert manifest['cover_source'] == 'approved_render_first_frame'


def test_publish_sms_requires_active_authorized_submission_and_never_persists_code(account_client):
    env = account_client
    response = env.client.get(BASE + '/douyin/publish-status')
    assert response.status_code == 200
    assert response.json()['state'] == 'sms_required' and 'private-cookie' not in response.text
    assert env.client.post(BASE + '/douyin/publish-sms', json={'code': '123456'}).status_code == 409
    from app.job_models import Job
    job = Job(id='active-publish', tenant_id='t', property_id='p', user_id='other-user',
              kind='platform_publish', status='running', payload={'platform': 'douyin'})
    env.db.add(job)
    env.db.commit()
    assert env.client.post(BASE + '/douyin/publish-sms', json={'code': '123456'}).status_code == 409
    job.user_id = 'u'
    env.db.commit()
    response = env.client.post(BASE + '/douyin/publish-sms', json={'code': '123456'})
    assert response.status_code == 200 and response.json()['state'] == 'verifying'
    assert '123456' not in response.text and '123456' not in json.dumps(job.payload)
    job.status = 'succeeded'
    env.db.commit()
    assert env.client.post(BASE + '/douyin/publish-sms', json={'code': '123456'}).status_code == 409


def test_publish_sms_respects_project_roles_and_ascii_code_validation(account_client):
    env = account_client
    env.current['context'] = replace(env.context, project_ids=['p2'])
    assert env.client.get(BASE + '/douyin/publish-status').status_code == 404
    assert env.client.post(BASE + '/douyin/publish-sms', json={'code': '123456'}).status_code == 404
    env.current['context'] = replace(env.context, role='viewer')
    assert env.client.post(BASE + '/douyin/publish-sms', json={'code': '123456'}).status_code == 403
    env.current['context'] = env.context
    assert env.client.post(BASE + '/douyin/sms', json={'code': '١٢٣٤٥٦'}).status_code == 422


def test_task_history_identifies_scoped_platform_and_current_sms_permission_without_payload(account_client):
    env = account_client
    from app.job_models import Job
    env.db.add_all([
        Job(id='own', tenant_id='t', property_id='p', user_id='u', kind='platform_publish',
            status='running', payload={'platform': 'douyin', 'content_version_id': 'content',
                'release_id': 'release', 'cookie': 'private-cookie', 'authorization': 'private-token'}),
        Job(id='other-actor', tenant_id='t', property_id='p', user_id='another', kind='platform_publish',
            status='running', payload={'platform': 'xiaohongshu'}),
        Job(id='other-project', tenant_id='t', property_id='p2', user_id='u', kind='platform_publish',
            status='running', payload={'platform': 'douyin'}),
    ])
    env.db.commit()
    response = env.client.get(BASE + '/tasks')
    assert response.status_code == 200
    rows = {row['id']: row for row in response.json()}
    assert set(rows) == {'own', 'other-actor'}
    assert rows['own']['platform'] == 'douyin' and rows['own']['can_verify'] is True
    assert rows['own']['content_version_id'] == 'content' and rows['own']['release_id'] == 'release'
    assert rows['other-actor']['platform'] == 'xiaohongshu' and rows['other-actor']['can_verify'] is False
    assert 'private-cookie' not in response.text and 'private-token' not in response.text
    env.current['context'] = replace(env.context, role='admin')
    assert all(row['can_verify'] for row in env.client.get(BASE + '/tasks').json())
    env.current['context'] = replace(env.context, role='viewer')
    assert all(not row['can_verify'] for row in env.client.get(BASE + '/tasks').json())
