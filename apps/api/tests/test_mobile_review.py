"""An explicit mobile review must approve exactly the rendered asset previewed."""
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.business import approve_content, content_snapshot, snapshot_sha256
from app.business_models import ContentVersion, Release
from app.config import settings
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.db import Base
from app.security import Context


@pytest.fixture
def review_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id='tenant', name='楼响测试团队'), User(id='reviewer', email='reviewer@example.test', name='审核员', password_hash='unused', active=True)])
    db.flush()
    db.add(Membership(id='member', user_id='reviewer', tenant_id='tenant', role='reviewer', project_ids=['project'], active=True))
    db.add(Property(id='project', tenant_id='tenant', name='江岸', city='苏州', marketing_confirmed=True, marketing_evidence='项目营销授权书第1页'))
    db.add(Fact(id='fact', tenant_id='tenant', property_id='project', semantic_key='area', label='建筑面积', value='420', unit='㎡', source='测绘报告第2页', status='verified', revision=1, required=True))
    db.add(Asset(id='source', tenant_id='tenant', property_id='project', name='客厅实拍', kind='image', storage_key='source.jpg', mime_type='image/jpeg', sha256='source-hash', byte_size=3, rights='authorized', rights_evidence='摄影授权书第3页', rights_revision=1, origin_type='actual', metadata_json={}))
    content = ContentVersion(id='content', tenant_id='tenant', property_id='project', series_id='series', version=1, topic='空间', script='420㎡实拍空间', fact_refs=[{'id': 'fact', 'revision': 1}], storyboard=[{'asset_id': 'source', 'duration': 30, 'caption': '420㎡空间'}], channel='抖音', cover_title='江岸', aspect_ratio='9:16', subtitle_style='minimal', status='rendered', created_by='reviewer')
    db.add(content)
    db.commit()
    ctx = Context(user_id='reviewer', tenant_id='tenant', role='reviewer', name='审核员', email='reviewer@example.test', project_ids=['project'])
    def render(identifier):
        payload = ('actual integrity-checked render fixture ' + identifier).encode()
        (tmp_path / (identifier + '.mp4')).write_bytes(payload)
        asset = Asset(id=identifier, tenant_id='tenant', property_id='project', name='江岸.mp4', kind='video', storage_key=identifier + '.mp4', mime_type='video/mp4', sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), rights='authorized', rights_evidence='项目授权素材渲染', rights_revision=1, origin_type='actual', metadata_json={'content_snapshot_sha256': snapshot_sha256(content_snapshot(db, ctx, content)), 'content_version_id': 'content', 'renderer': 'ffmpeg'})
        db.add(asset)
        content.render_asset_id = identifier
        db.commit()
        return asset
    render('render-first')
    yield db, ctx, content, render
    db.close()
    engine.dispose()


def test_mobile_review_rejects_a_new_render_created_after_the_preview(review_env):
    db, ctx, content, render = review_env
    previewed = content.render_asset_id
    render('render-replacement')
    with pytest.raises(HTTPException) as error:
        approve_content(db, ctx, 'project', 'content', {'confirmed': True, 'expected_render_asset_id': previewed})
    assert error.value.status_code == 409
    assert db.scalar(select(Release)) is None
    assert content.status == 'rendered'


def test_mobile_review_accepts_the_current_previewed_render(review_env):
    db, ctx, content, _ = review_env
    result = approve_content(db, ctx, 'project', 'content', {'confirmed': True, 'expected_render_asset_id': 'render-first', 'note': '已查看当前成片'})
    release = db.get(Release, result['id'])
    assert release.snapshot['render']['id'] == 'render-first'
    assert release.review_note == '已查看当前成片'
    assert content.status == 'approved'


def test_browser_review_without_an_expected_render_stays_compatible(review_env):
    db, ctx, _, render = review_env
    render('render-replacement')
    result = approve_content(db, ctx, 'project', 'content', {'confirmed': True})
    assert db.get(Release, result['id']).snapshot['render']['id'] == 'render-replacement'


@pytest.mark.parametrize('expected', [None, '', 123, [], 'a' * 101])
def test_present_but_malformed_render_expectations_are_not_silently_ignored(review_env, expected):
    db, ctx, _, _ = review_env
    with pytest.raises(HTTPException) as error:
        approve_content(db, ctx, 'project', 'content', {'confirmed': True, 'expected_render_asset_id': expected})
    assert error.value.status_code == 422
    assert db.scalar(select(Release)) is None


def test_approval_refreshes_a_stale_identity_map_before_checking_the_render(review_env):
    db, ctx, content, render = review_env
    render('render-replacement')
    # Simulate a cached ORM object while keeping the persisted worker replacement.
    from sqlalchemy.orm.attributes import set_committed_value
    set_committed_value(content, 'render_asset_id', 'render-first')
    with pytest.raises(HTTPException) as error:
        approve_content(db, ctx, 'project', 'content', {'confirmed': True, 'expected_render_asset_id': 'render-first'})
    assert error.value.status_code == 409
    assert db.scalar(select(Release)) is None
