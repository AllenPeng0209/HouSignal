"""Real PostgreSQL publication races, using only a private throwaway cluster."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import getpass
import hashlib
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from threading import Barrier, Event, Lock

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.business import approve_content, content_snapshot_hash, create_publication, publication_for_url_locked, revise_content
from app.business_models import ContentVersion, Publication
from app.config import settings
from app.core_models import Asset, Property
from app.db import Base
from app.job_models import Job
from app.platform_publish import _final_result
from test_business import scenario, rendered


@pytest.fixture(scope='module')
def private_postgres():
    binary=Path('/opt/homebrew/opt/postgresql@17/bin')
    initdb=shutil.which('initdb') or str(binary/'initdb')
    pg_ctl=shutil.which('pg_ctl') or str(binary/'pg_ctl')
    if not Path(initdb).is_file() or not Path(pg_ctl).is_file():
        pytest.skip('PostgreSQL binaries unavailable; never connect to a development database')
    with tempfile.TemporaryDirectory(prefix='lx-test-pg-',dir='/tmp') as root:
        root=Path(root);data=root/'data';private_socket=root/'socket';private_socket.mkdir(mode=0o700)
        with socket.socket() as available:
            available.bind(('127.0.0.1',0));port=available.getsockname()[1]
        subprocess.run([initdb,'-D',str(data),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True,timeout=30)
        started=False;engine=None
        try:
            subprocess.run([pg_ctl,'-D',str(data),'-l',str(root/'server.log'),'-o',
                f"-k {private_socket} -p {port} -h ''",'-w','start'],check=True,capture_output=True,timeout=30)
            started=True
            engine=create_engine(URL.create('postgresql+psycopg',username=getpass.getuser(),database='postgres',
                query={'host':str(private_socket),'port':str(port)}))
            yield engine
        finally:
            if engine is not None: engine.dispose()
            if started:
                subprocess.run([pg_ctl,'-D',str(data),'-m','fast','-w','stop'],check=True,capture_output=True,timeout=30)


@pytest.fixture
def concurrent_publications(scenario,private_postgres):
    db,ctx,p,f,a,c=scenario;rendered(scenario)
    first=approve_content(db,ctx,'p','c',{'confirmed':True});db.commit()
    updated=revise_content(db,ctx,'p','c',{'script':c.script});c2=db.get(ContentVersion,updated['id'])
    payload=b'second real render fixture'
    (settings.storage_root/'render2.mp4').write_bytes(payload)
    db.add(Asset(id='render2',tenant_id=ctx.tenant_id,property_id='p',name='second.mp4',kind='video',
        storage_key='render2.mp4',mime_type='video/mp4',sha256=hashlib.sha256(payload).hexdigest(),byte_size=len(payload),
        rights='authorized',rights_evidence='摄制授权',origin_type='actual',metadata_json={
            'content_snapshot_sha256':content_snapshot_hash(db,c2),'content_version_id':c2.id,'renderer':'ffmpeg'}))
    c2.render_asset_id='render2';c2.status='rendered';db.commit()
    second=approve_content(db,ctx,'p',c2.id,{'confirmed':True});db.commit()
    db.add(Property(id='other-project',tenant_id=ctx.tenant_id,name='其他项目',city='上海'));db.commit()
    Base.metadata.create_all(private_postgres)
    with private_postgres.begin() as target:
        for table in Base.metadata.sorted_tables:
            rows=[dict(row) for row in db.execute(select(table)).mappings()]
            if rows: target.execute(table.insert(),rows)
    yield private_postgres,ctx,(first,second),(c.id,c2.id)
    Base.metadata.drop_all(private_postgres)


@contextmanager
def overlap_url_reads(engine):
    """Observe real SQL; an unprotected first read waits for the competing read."""
    second_read=Event();lock=Lock();reads=0
    def after_read(conn,cursor,statement,parameters,context,many):
        nonlocal reads
        if not (statement.lstrip().startswith('SELECT') and 'FROM publications' in statement and 'publications.url =' in statement): return
        with lock: reads+=1;first=reads==1
        if first: second_read.wait(2)
        else: second_read.set()
    event.listen(engine,'after_cursor_execute',after_read)
    try: yield
    finally: event.remove(engine,'after_cursor_execute',after_read)


@pytest.mark.parametrize('same_release',[False,True],ids=['different-release-conflict','same-release-idempotent'])
def test_postgres_serializes_web_url_binding(concurrent_publications,same_release):
    # Catches SELECT-before-INSERT races; both requests use real independent PostgreSQL transactions.
    engine,ctx,releases,_=concurrent_publications;start=Barrier(2)
    def publish(release):
        start.wait(timeout=10)
        with Session(engine) as db:
            try: return create_publication('p',{'release_id':release['id'],'url':'https://www.douyin.com/video/1234567890123456789'},db,ctx)
            except HTTPException as error: return {'error':error.status_code}
    pair=(releases[0],releases[0] if same_release else releases[1])
    with overlap_url_reads(engine),ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(publish,pair))
    with Session(engine) as db:
        assert len(db.scalars(select(Publication)).all())==1
    if same_release: assert outcomes[0]['id']==outcomes[1]['id']
    else: assert sum(item.get('error')==409 for item in outcomes)==1


def test_postgres_serializes_web_and_platform_worker_url_binding(concurrent_publications):
    # Catches the worker bypassing the same lock while a human records a public receipt.
    engine,ctx,releases,contents=concurrent_publications;start=Barrier(2)
    url='https://www.douyin.com/video/1234567890123456789'
    def publish(kind):
        start.wait(timeout=10)
        with Session(engine) as db:
            if kind=='web':
                try:return create_publication('p',{'release_id':releases[0]['id'],'url':url},db,ctx)
                except HTTPException as error:return {'error':error.status_code}
            result=_final_result(db,Job(id='worker',tenant_id=ctx.tenant_id,property_id='p',user_id=ctx.user_id,kind='platform_publish'),
                {'content':{'id':contents[1],'cover_title':'second','channel':'抖音'},'release':releases[1]},
                {'platform':'douyin','result':{'status':'submitted','url':url}})
            db.commit();return result
    with overlap_url_reads(engine),ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(publish,('web','worker')))
    with Session(engine) as db: assert len(db.scalars(select(Publication)).all())==1
    assert outcomes[0].get('error')==409 or outcomes[1]['status']=='reconciliation_required'
    if outcomes[1]['status']=='reconciliation_required': assert 'publication_id' not in outcomes[1]


def test_postgres_receipt_lock_leaves_other_projects_available(concurrent_publications):
    # Catches replacing the project row lock with global publication serialization.
    engine,ctx,_,_=concurrent_publications
    def other_project():
        with Session(engine) as db:
            result=publication_for_url_locked(db,ctx.tenant_id,'other-project','https://example.test/other','other-release')
            db.commit();return result
    with ThreadPoolExecutor(max_workers=1) as pool,Session(engine) as locked:
        locked.scalar(select(Property).where(Property.id=='p').with_for_update())
        try:
            assert pool.submit(other_project).result(timeout=5) is None
        finally:
            locked.rollback()
