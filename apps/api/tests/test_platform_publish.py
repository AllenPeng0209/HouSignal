"""Reviewed releases produce at most one external submission across durable retries.

Only the external process boundary is replaced; scope, approval, private files,
account runtime command building, database queue and receipt ledger are real.
"""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import concurrent.futures
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.business_models import ContentVersion, Publication
from app.job_models import Job
from app.security import context_for_user


@pytest.fixture
def publish_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    settings.storage_root.mkdir()
    engine = create_engine('sqlite:///'+str(tmp_path/'publish.db'),
        connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Tenant(id='t', name='团队'), Tenant(id='other', name='其他团队'),
        User(id='u', email='u@test.cn', password_hash='unused', name='内容负责人', active=True)])
    db.flush()
    db.add(Membership(id='m', tenant_id='t', user_id='u', role='admin', active=True))
    db.add_all([Property(id='p', tenant_id='t', name='江岸', city='苏州',
        property_type='豪宅', owner_name='负责人', audience='改善', sales_owner_name='负责人',
        channel='抖音', marketing_confirmed=True, marketing_evidence='真实书面授权', is_test=False),
        Fact(id='f', tenant_id='t', property_id='p', semantic_key='area', label='面积',
            value='420', unit='㎡', source='测绘资料', status='verified', revision=1, required=True),
        Asset(id='a', tenant_id='t', property_id='p', name='实拍素材', kind='image',
            storage_key='photo.jpg', mime_type='image/jpeg', sha256='abc', byte_size=3,
            rights='authorized', rights_evidence='实拍授权', origin_type='actual', metadata_json={})])
    content = ContentVersion(id='c', tenant_id='t', property_id='p', series_id='s', version=1,
        topic='空间', script='420㎡实拍空间', fact_refs=[{'id':'f','revision':1}],
        storyboard=[{'asset_id':'a','duration':3,'caption':'420㎡实拍空间'}], channel='抖音',
        cover_title='江岸空间', aspect_ratio='9:16', subtitle_style='minimal', created_by='u')
    db.add(content); db.commit()
    ctx = context_for_user(db, 'u', 't')
    from app.business import content_snapshot, snapshot_sha256, approve_content
    payload = b'validated rendered fixture; decode covered in media tests'
    (settings.storage_root / 'render.mp4').write_bytes(payload)
    db.add(Asset(id='render', tenant_id='t', property_id='p', name='成片.mp4', kind='video',
        storage_key='render.mp4', mime_type='video/mp4', sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload), rights='authorized', rights_evidence='获批实拍素材成片',
        origin_type='actual', metadata_json={'renderer':'ffmpeg','content_version_id':'c',
            'content_snapshot_sha256':snapshot_sha256(content_snapshot(db,ctx,content))}))
    content.render_asset_id='render'; content.status='rendered'; db.commit()
    release = approve_content(db, ctx, 'p', 'c', {'confirmed':True}); db.commit()
    yield SimpleNamespace(db=db, ctx=ctx, content=content, release=release, root=tmp_path)
    db.close(); engine.dispose()


def module():
    return importlib.import_module('app.platform_publish')


def install_runtime(s, monkeypatch, *, logged_in=True, verified=True, output=None, failure=None):
    from app.platform_runtime import PlatformRuntime
    rt = PlatformRuntime(root=s.root, python='/Users/pengyanlun/workdir/Easel/.venv/bin/python')
    scripts = s.root / 'vendor/easel_accounts/skills/shared/scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ('xhs_publish','douyin_publish','web_publisher','bili_login','weixin_mp_stats', 'weixin_mp'):
        (scripts/(name+'.py')).write_text('# isolated fixture script')
    submitted=[]
    def probe_value():
        value={'loggedIn':logged_in,'name':'项目账号','avatar':'',
            'auth_state':'authenticated' if logged_in else 'logged_out',
            'auth_evidence':'visible_creator_publish_control' if logged_in else 'visible_official_login_prompt'}
        if not verified: value['error']='network unavailable token=private'
        return value
    def external(args, *pos, **kwargs):
        if 'whoami' in args:
            return subprocess.CompletedProcess(args, 0, json.dumps(probe_value()), '')
        if args[0] == settings.ffmpeg_binary:
            Path(args[-1]).write_bytes(b'cover frame fixture')
            return subprocess.CompletedProcess(args, 0, '', '')
        raise AssertionError('publisher must use an owned process group instead of blocking run')
    class ExternalProcess:
        def __init__(self,args,**kwargs):
            self.probe='whoami' in args
            if not self.probe: submitted.append(list(args))
            self.pid=99999997
            self.returncode=None
        def communicate(self,timeout=None):
            if self.probe:
                self.returncode=0
                return json.dumps(probe_value()),''
            if failure: raise failure
            self.returncode=0
            return json.dumps(output or {'status':'review_pending'}),''
        def kill(self): self.returncode=-9
        def wait(self,timeout=None): self.returncode=-9; return self.returncode
    monkeypatch.setattr(subprocess, 'run', external)
    monkeypatch.setattr(subprocess, 'Popen', ExternalProcess)
    monkeypatch.setattr(module(), 'runtime', rt)
    # The external fixture represents credential-bearing profiles. Provision
    # their opaque generations through the real locked runtime boundary.
    from app.platform_runtime import PLATFORMS
    for platform in PLATFORMS:
        with rt.publish_lock('t','p',platform):
            scope=rt._scope('t','p',platform)
            if not rt._session_id(scope): rt._rotate_session(scope)
    if isinstance(failure,subprocess.TimeoutExpired):
        monkeypatch.setattr(module(),'PUBLISH_TIMEOUT_SECONDS',.01)
    return submitted


def publish_body(s, key='publish-1234', platform='douyin'):
    return {'content_version_id':'c','confirmed':True,'request_key':key,
        'expected_session_id':module().runtime.login_status('t','p',platform)['session_id']}


def queue(s, key='publish-1234'):
    return module().enqueue_publish(s.db,s.ctx,'p','douyin',
        publish_body(s,key))


def test_requires_explicit_human_confirmation_and_no_queue_side_effect(publish_scenario):
    s=publish_scenario
    with pytest.raises(HTTPException) as exc:
        module().enqueue_publish(s.db,s.ctx,'p','douyin',
            {'content_version_id':'c','confirmed':False,'request_key':'publish-1234'})
    assert exc.value.status_code==422
    assert s.db.scalars(select(Job)).all()==[]


def test_approved_matching_release_enqueues_one_durable_job_per_request(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch)
    first=queue(s); s.db.commit()
    second=queue(s); s.db.commit()
    assert first.id==second.id
    assert first.kind=='platform_publish' and first.status=='queued'
    assert first.payload['release_id']==s.release['id']
    assert len(s.db.scalars(select(Job)).all())==1


@pytest.mark.parametrize('mutation', ['role','scope','facts','channel','render'])
def test_enqueue_rejects_inaccessible_changed_or_wrong_channel_release(publish_scenario,monkeypatch,mutation):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    if mutation=='role': s.db.get(Membership,'m').role='sales'; s.db.commit(); s.ctx=context_for_user(s.db,'u','t')
    elif mutation=='scope': s.db.get(Membership,'m').project_ids=[]; s.db.commit(); s.ctx=context_for_user(s.db,'u','t')
    elif mutation=='facts': s.db.get(Fact,'f').revision=2; s.db.commit()
    elif mutation=='channel': s.content.channel='小红书'; s.db.commit()
    elif mutation=='render': (settings.storage_root/'render.mp4').write_bytes(b'changed')
    with pytest.raises(HTTPException): queue(s)
    assert s.db.scalars(select(Job)).all()==[] and submitted==[]


def test_unverified_or_logged_out_account_cannot_enqueue(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch,logged_in=False)
    with pytest.raises(HTTPException) as exc: queue(s)
    assert exc.value.status_code==409 and submitted==[]


def test_execution_revalidates_approval_before_external_submit(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit()
    s.db.get(Fact,'f').revision=2; s.db.commit()
    with pytest.raises(HTTPException): module().handle_publish(s.db,job)
    assert submitted==[]


def test_successful_process_without_public_receipt_never_creates_verified_live_publication(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit(); result=module().handle_publish(s.db,job)
    assert len(submitted)==1
    assert result['status']=='review_pending'
    assert s.db.scalars(select(Publication)).all()==[]
    assert str(s.root) not in json.dumps(result)


def test_only_known_public_url_creates_pending_unverified_receipt(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch,output={'status':'published',
        'url':'https://www.douyin.com/video/123456789?access_token=secret'})
    job=queue(s); s.db.commit(); result=module().handle_publish(s.db,job)
    publication=s.db.scalar(select(Publication))
    assert result['status']=='submitted'
    assert result['url']=='https://www.douyin.com/video/123456789'
    assert publication.platform_state=='pending' and publication.receipt_status=='unverified'
    assert publication.metrics=={'D7':None,'D30':None}


def test_timeout_retry_and_new_request_cannot_submit_same_release_again(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch,
        failure=subprocess.TimeoutExpired(['private/path','token=secret'],3))
    job=queue(s); s.db.commit()
    first=module().handle_publish(s.db,job); s.db.rollback()
    second=module().handle_publish(s.db,job)
    assert first['status']==second['status']=='reconciliation_required'
    assert len(submitted)==1
    with pytest.raises(HTTPException) as exc: queue(s,'publish-5678')
    assert exc.value.status_code==409
    assert not any(word in json.dumps(first) for word in ('secret','private/path'))


def test_receipt_survives_worker_business_transaction_rollback(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch,output={'status':'published',
        'url':'https://www.douyin.com/video/123456789'})
    job=queue(s); s.db.commit()
    first=module().handle_publish(s.db,job); s.db.rollback()
    second=module().handle_publish(s.db,job)
    assert first['status']=='submitted' and second['status']=='submitted'
    assert len(submitted)==1
    assert len(s.db.scalars(select(Publication)).all())==1


def test_cancelled_lease_before_external_call_leaves_no_attempt_marker(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit(); s.db.info['job_cancelled']=lambda: True
    with pytest.raises(ValueError): module().handle_publish(s.db,job)
    assert submitted==[]


def test_agent_created_payload_cannot_skip_http_human_confirmation(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=Job(tenant_id='t',property_id='p',user_id='u',kind='platform_publish',
        payload={'platform':'douyin','content_version_id':'c','confirmed':True,
            'request_key':'forged-1234','release_id':s.release['id']},result={})
    s.db.add(job); s.db.commit()
    with pytest.raises(ValueError): module().handle_publish(s.db,job)
    assert submitted==[]


def change_channel(s, channel):
    from app.business import content_snapshot, snapshot_sha256, approve_content
    s.content.channel=channel
    render=s.db.get(Asset,'render')
    render.metadata_json={**render.metadata_json,
        'content_snapshot_sha256':snapshot_sha256(content_snapshot(s.db,s.ctx,s.content))}
    s.db.commit()
    s.release=approve_content(s.db,s.ctx,'p','c',{'confirmed':True}); s.db.commit()


@pytest.mark.parametrize(('platform','channel','expected'), [
    ('xiaohongshu','小红书','review_pending'),('kuaishou','快手','review_pending'),
    ('weixin-channels','微信视频号','review_pending'),('bilibili','B站','review_pending'),
    ('zhihu','知乎','review_pending'),('wechat-oa','微信公众号','draft_saved')])
def test_all_platforms_submit_only_reviewed_format_and_mp_saves_draft(publish_scenario,monkeypatch,platform,channel,expected):
    s=publish_scenario; change_channel(s,channel)
    output={'success':True,'media_id':'private-draft-id'} if platform=='wechat-oa' else {'status':'review_pending'}
    submitted=install_runtime(s,monkeypatch,output=output)
    job=module().enqueue_publish(s.db,s.ctx,'p',platform,
        publish_body(s,platform=platform))
    s.db.commit(); result=module().handle_publish(s.db,job)
    assert result['status']==expected
    assert len(submitted)==1 and s.db.scalars(select(Publication)).all()==[]
    if platform=='zhihu':
        assert '--media' not in submitted[0]
        assert submitted[0][submitted[0].index('--desc')+1]=='420㎡实拍空间'
    elif platform=='wechat-oa':
        assert result['draft_only'] is True
        article=Path(submitted[0][submitted[0].index('--html')+1])
        cover=Path(submitted[0][submitted[0].index('--cover')+1])
        assert article.read_text()=='<p>420㎡实拍空间</p>'
        assert article.stat().st_mode & 0o777 == 0o600
        assert cover.stat().st_mode & 0o777 == 0o600
        assert str(s.root) not in json.dumps(result)


def test_mp_zero_exit_without_explicit_saved_draft_receipt_stays_uncertain(publish_scenario,monkeypatch):
    s=publish_scenario; change_channel(s,'微信公众号')
    install_runtime(s,monkeypatch,output={'status':'ok'})
    job=module().enqueue_publish(s.db,s.ctx,'p','wechat-oa',
        publish_body(s,platform='wechat-oa'))
    s.db.commit()
    result=module().handle_publish(s.db,job)
    assert result['status']=='reconciliation_required'


def test_oversized_approved_title_rejected_before_queue_instead_of_silent_trimming(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    s.content.cover_title='江'*31
    change_channel(s,'抖音')
    with pytest.raises(HTTPException) as exc: queue(s)
    assert exc.value.status_code==409
    assert submitted==[] and s.db.scalars(select(Job)).all()==[]


def test_returned_admin_or_foreign_url_is_never_a_publication_receipt(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch,output={'status':'published',
        'url':'https://creator.douyin.com/creator-micro/content/manage?token=secret'})
    job=queue(s); s.db.commit(); result=module().handle_publish(s.db,job)
    assert 'url' not in result and 'secret' not in json.dumps(result)
    assert s.db.scalars(select(Publication)).all()==[]


def test_expected_release_rejects_confirmation_of_previous_preview(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    previous=s.release['id']
    s.content.cover_title='更新后的江岸空间'; change_channel(s,'抖音')
    with pytest.raises(HTTPException) as exc:
        module().enqueue_publish(s.db,s.ctx,'p','douyin',
            {**publish_body(s),'expected_release_id':previous})
    assert exc.value.status_code==409 and submitted==[]


def test_xhs_utf16_title_count_rejects_eleven_reviewed_emoji(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    s.content.cover_title='😀'*11; change_channel(s,'小红书')
    with pytest.raises(HTTPException) as exc:
        module().enqueue_publish(s.db,s.ctx,'p','xiaohongshu',
            publish_body(s,platform='xiaohongshu'))
    assert exc.value.status_code==409 and submitted==[]


def test_process_crash_after_attempt_marker_cannot_resubmit(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch,failure=SystemExit('process died'))
    job=queue(s); s.db.commit()
    with pytest.raises(SystemExit): module().handle_publish(s.db,job)
    s.db.rollback()
    result=module().handle_publish(s.db,job)
    assert result['status']=='reconciliation_required' and len(submitted)==1
    receipts=list((settings.storage_root/'platform-publish-ledger').rglob('*.json'))
    assert len(receipts)==1 and receipts[0].stat().st_mode & 0o777 == 0o600
    assert receipts[0].parent.stat().st_mode & 0o777 == 0o700


def test_publication_cover_rejects_output_outside_private_storage(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    from app.business import export_content
    package=export_content(s.db,s.ctx,'p','c')
    with pytest.raises(ValueError):
        module().publication_cover(s.db,s.ctx,'p',package,s.root/'outside')
    assert submitted==[] and not (s.root/'outside').exists()


def test_two_workers_with_independent_db_sessions_submit_once(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit(); jid=job.id
    external=subprocess.run
    def delayed(args,*pos,**kwargs):
        if 'whoami' not in args: time.sleep(.1)
        return external(args,*pos,**kwargs)
    monkeypatch.setattr(subprocess,'run',delayed)
    def worker(_):
        with Session(s.db.bind) as db:
            result=module().handle_publish(db,db.get(Job,jid))
            db.commit()
            return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,range(2)))
    assert [item['status'] for item in results]==['review_pending','review_pending']
    assert len(submitted)==1


def test_execution_account_verification_failure_never_creates_attempt(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit()
    submitted=install_runtime(s,monkeypatch,logged_in=False)
    with pytest.raises(HTTPException) as exc: module().handle_publish(s.db,job)
    assert exc.value.status_code==409 and submitted==[]
    assert not list((settings.storage_root/'platform-publish-ledger').rglob('*.json'))


@pytest.mark.parametrize('mode',['cancel','timeout'])
def test_interrupted_publish_terminates_owned_process_and_child(publish_scenario,monkeypatch,mode):
    import os
    import signal
    import sys
    s=publish_scenario
    marker=s.root/'processes.json'
    script='''import json,os,pathlib,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
time.sleep(30)
'''
    s.db.info['job_cancelled']=lambda:mode=='cancel' and marker.exists()
    if mode=='timeout': monkeypatch.setattr(module(),'PUBLISH_TIMEOUT_SECONDS',.2)
    spec={'command':[sys.executable,'-c',script,str(marker)],'cwd':str(s.root),'env':os.environ.copy()}
    try:
        with pytest.raises(module().PublishInterrupted):
            module()._run_publisher(s.db,spec)
        identities=json.loads(marker.read_text())
        for pid in identities.values():
            result=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True)
            assert not result.stdout.strip() or result.stdout.strip().startswith('Z')
    finally:
        if marker.exists():
            identities=json.loads(marker.read_text())
            try: os.killpg(identities['parent'],signal.SIGKILL)
            except ProcessLookupError: pass


def test_enqueue_rejects_busy_receipt_lock_without_waiting_while_holding_db_lock(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch)
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        with module()._receipt_lock('t','p','douyin',s.release['id']):
            future=pool.submit(queue,s)
            with pytest.raises(HTTPException) as exc:
                future.result(timeout=.3)
            assert exc.value.status_code==409
    finally:
        pool.shutdown(wait=True)


def test_confirmation_requires_the_prepared_account_session(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    with pytest.raises(HTTPException) as exc:
        module().enqueue_publish(s.db,s.ctx,'p','douyin',
            {'content_version_id':'c','confirmed':True,'request_key':'publish-1234'})
    assert exc.value.status_code==422 and submitted==[]
    assert s.db.scalars(select(Job)).all()==[]


def test_account_switch_after_preparation_requires_new_confirmation(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    body=publish_body(s)
    module().runtime.logout('t','p','douyin')
    with pytest.raises(HTTPException) as exc:
        module().enqueue_publish(s.db,s.ctx,'p','douyin',body)
    assert exc.value.status_code==409 and submitted==[]
    assert s.db.scalars(select(Job)).all()==[]


def test_account_switch_after_queue_never_submits_or_marks_attempt(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit()
    module().runtime.logout('t','p','douyin')
    with pytest.raises(HTTPException) as exc: module().handle_publish(s.db,job)
    assert exc.value.status_code==409 and submitted==[]
    assert not list((settings.storage_root/'platform-publish-ledger').rglob('*.json'))


def test_editing_confirmed_session_invalidates_human_authorization(publish_scenario,monkeypatch):
    s=publish_scenario; submitted=install_runtime(s,monkeypatch)
    job=queue(s); s.db.commit()
    job.payload={**job.payload,'expected_session_id':'f'*64}; s.db.commit()
    with pytest.raises(ValueError): module().handle_publish(s.db,job)
    assert submitted==[]


def test_receipt_records_the_session_that_was_confirmed(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch)
    session=publish_body(s)['expected_session_id']
    job=queue(s); s.db.commit(); module().handle_publish(s.db,job)
    records=[json.loads(path.read_text()) for path in (settings.storage_root/'platform-publish-ledger').rglob('*.json')]
    assert len(records)==1 and records[0].get('expected_session_id')==session


def test_request_key_cannot_be_reused_for_another_account_session(publish_scenario,monkeypatch):
    s=publish_scenario; install_runtime(s,monkeypatch)
    queue(s); s.db.commit()
    module().runtime.logout('t','p','douyin')
    with pytest.raises(HTTPException) as exc: queue(s)
    assert exc.value.status_code==409
