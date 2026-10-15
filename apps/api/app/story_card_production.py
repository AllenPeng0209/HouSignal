"""Local, visibly fictional teaching diagrams for demonstration projects only."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from .business_models import ContentVersion
from .core import valid_fact
from .core_models import Asset, Fact, Property
from .operations_models import ContentPlan
from .security import context_for_user, require_project

ILLUSTRATE_ROLES = ('owner', 'admin', 'editor', 'content')


@lru_cache(maxsize=1)
def card_renderer():
    # This is a repository-owned local renderer, never user-supplied code.
    path = Path(__file__).resolve().parents[3] / 'scripts' / 'build_longform_story_cards.py'
    spec = importlib.util.spec_from_file_location('louxiang_story_card_renderer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data(content):
    return {'topic': content.topic, 'script': content.script, 'cover_title': content.cover_title,
            'fact_refs': content.fact_refs, 'storyboard': content.storyboard}


def illustration_snapshot(db, ctx, pid, cid, *, lock=False):
    """Freeze facts, media, narration, the current series and its linked plans."""
    from .business import _content, _scope, content_snapshot, snapshot_sha256, validate_generated_content, _check_generated_material_copy
    project = require_project(db, ctx, pid, ILLUSTRATE_ROLES)
    if lock:
        project = db.scalar(select(Property).where(Property.id == pid, Property.tenant_id == ctx.tenant_id)
                            .with_for_update().execution_options(populate_existing=True))
    content = _content(db, ctx, pid, cid, ILLUSTRATE_ROLES)
    if lock:
        content = db.scalar(_scope(db, ContentVersion, ctx, pid).where(ContentVersion.id == cid)
                            .with_for_update().execution_options(populate_existing=True))
    if not project.is_test:
        raise HTTPException(409, '教学图卡仅用于虚构示例项目；真实项目请准备实际授权素材')
    if not isinstance(content.storyboard, dict) or content.storyboard.get('schema_version') != 2:
        raise HTTPException(409, '教学图卡需要完整的长视频章节与分镜')
    versions_query = _scope(db, ContentVersion, ctx, pid).where(ContentVersion.series_id == content.series_id)
    versions = db.scalars(versions_query.with_for_update().execution_options(populate_existing=True) if lock else versions_query).all()
    # A locking statement may have waited for a concurrently completed edit.
    # Read a fresh statement snapshot after the lock, including a new version
    # committed while the original locking query was waiting.
    latest = db.scalar(select(func.max(ContentVersion.version)).where(ContentVersion.tenant_id == ctx.tenant_id,
        ContentVersion.property_id == pid, ContentVersion.series_id == content.series_id)) if lock else max(version.version for version in versions)
    if latest != content.version:
        raise HTTPException(409, '脚本已有新版本，请刷新后为最新版本生成图卡')
    plans_query = _scope(db, ContentPlan, ctx, pid).where(ContentPlan.content_version_id == cid).order_by(ContentPlan.id)
    plans = db.scalars(plans_query.with_for_update().execution_options(populate_existing=True) if lock else plans_query).all()
    context_plan_id = (content.storyboard.get('planning_context') or {}).get('plan_id')
    if context_plan_id:
        linked = db.scalar(_scope(db, ContentPlan, ctx, pid).where(ContentPlan.id == context_plan_id))
        if linked and linked.content_version_id != cid:
            raise HTTPException(409, '策划已调整，请刷新后使用对应的新脚本版本')
    required_query = _scope(db, Fact, ctx, pid).where(Fact.required.is_(True)).order_by(Fact.id)
    required = db.scalars(required_query.with_for_update().execution_options(populate_existing=True) if lock else required_query).all()
    if not required or any(not valid_fact(fact) for fact in required):
        raise HTTPException(409, '项目核心事实尚未全部核验，或已有事实过期')
    if lock:
        fact_ids = [row.get('id') for row in content.fact_refs if isinstance(row, dict)]
        asset_ids = [shot.get('asset_id') for shot in content.storyboard['shots'] if shot.get('asset_id')]
        db.scalars(_scope(db, Fact, ctx, pid).where(Fact.id.in_(fact_ids)).with_for_update().execution_options(populate_existing=True)).all()
        db.scalars(_scope(db, Asset, ctx, pid).where(Asset.id.in_(asset_ids)).with_for_update().execution_options(populate_existing=True)).all()
    try:
        validate_generated_content(db, ctx, pid, _data(content))
        # Substituting diagrams changes every shot's source basis. Reject a
        # real-capture promise up front, while preserving the original text.
        template = [SimpleNamespace(origin_type='template')]
        _check_generated_material_copy(_data(content), template)
        _check_generated_material_copy({'topic': '', 'cover_title': '', 'storyboard': [],
            'script': '\n'.join(shot['visual_direction'] for shot in content.storyboard['shots'])
                + '\n' + '\n'.join(chapter['title'] + '\n' + chapter['purpose'] for chapter in content.storyboard['chapters'])}, template)
        from .content_planning import validate_planning_reference
        for plan in plans:
            validate_planning_reference(db, ctx, pid, plan.source_snapshot or {})
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    snapshot = content_snapshot(db, ctx, content)
    snapshot['linked_plans'] = [{key: deepcopy(getattr(plan, key)) for key in
        ('id', 'content_version_id', 'title', 'angle', 'brief', 'channel', 'account_id', 'topic_id', 'source_snapshot')}
        for plan in plans]
    snapshot['required_facts'] = [{'id': fact.id, 'revision': fact.revision, 'required': fact.required,
        'status': fact.status, 'value': fact.value, 'unit': fact.unit, 'source': fact.source,
        'valid_until': fact.valid_until.isoformat() if fact.valid_until else None} for fact in required]
    return content, snapshot_sha256(snapshot)


def _register_card(db, job, path, receipt, source_digest):
    """Create an exclusive private artifact, fenced against symlink directories."""
    from .config import settings
    identifiers = (job.tenant_id, job.property_id)
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in identifiers):
        raise ValueError('图卡项目标识格式不正确')
    root = settings.storage_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = root
    name = f'{uuid4()}.png'
    try:
        for part in (*identifiers, 'generated'):
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child; directory = directory / part
        output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        final = directory / name
        db.info.setdefault('created_private_files', []).append(str(final))
        with os.fdopen(output, 'wb') as target, Path(path).open('rb') as source:
            shutil.copyfileobj(source, target)
            target.flush(); os.fsync(target.fileno())
    finally:
        os.close(descriptor)
    payload = final.read_bytes()
    metadata = {key: deepcopy(receipt[key]) for key in ('shot_id', 'chapter_id', 'width', 'height', 'visual_type',
        'drawn_layout', 'source_text_sha256', 'fact_refs', 'text_boxes', 'fictional_numbers')}
    metadata.update(presentation='story_card', is_test=True, is_illustration=True,
                    illustration_job_id=job.id, source_snapshot_sha256=source_digest)
    asset = Asset(tenant_id=job.tenant_id, property_id=job.property_id,
        name=f"虚构示例教学图卡 · {receipt['shot_id']}", kind='image',
        storage_key=f'{job.tenant_id}/{job.property_id}/generated/{name}', mime_type='image/png',
        sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), rights='authorized',
        rights_evidence='本地原创程序绘制的虚构示例教学图示，仅供当前示例项目演示，不代表实际房源或现场拍摄',
        rights_revision=1, origin_type='template', metadata_json=metadata, extracted_text='')
    db.add(asset); db.flush()
    return asset


def handle_illustrate(db, job):
    from .business import _content, _scope, revise_content
    ctx = context_for_user(db, job.user_id, job.tenant_id)
    project = require_project(db, ctx, job.property_id, ILLUSTRATE_ROLES)
    if not project.is_test:
        raise HTTPException(409, '教学图卡仅用于虚构示例项目')
    cid = (job.payload or {}).get('content_version_id')
    source = _content(db, ctx, job.property_id, cid, ILLUSTRATE_ROLES)
    for version in db.scalars(_scope(db, ContentVersion, ctx, job.property_id)
                              .where(ContentVersion.series_id == source.series_id)).all():
        receipt = (version.storyboard or {}).get('illustration') if isinstance(version.storyboard, dict) else None
        if receipt and receipt.get('job_id') == job.id and receipt.get('source_content_version_id') == cid:
            return {'content_version_id': version.id, 'source_content_version_id': cid, 'card_count': len(version.storyboard['shots'])}
    source, digest = illustration_snapshot(db, ctx, job.property_id, cid)
    if digest != (job.payload or {}).get('source_snapshot_sha256'):
        raise HTTPException(409, '脚本、策划或来源资料已改变，请刷新后重新生成图卡')
    board = deepcopy(source.storyboard)
    size = {'9:16': (720, 1280), '16:9': (1280, 720), '1:1': (720, 720), '4:5': (720, 900)}.get(source.aspect_ratio)
    if size is None:
        raise HTTPException(409, '图卡画面比例不支持，请先修订脚本画幅')
    chapters = {chapter['id']: chapter for chapter in board['chapters']}
    renderer = card_renderer()
    def guard(*, lock=False):
        cancelled = db.info.get('job_cancelled')
        if cancelled and cancelled():
            raise ValueError('图卡生成已取消')
        db.flush(); db.expire_all()
        current = context_for_user(db, job.user_id, job.tenant_id)
        _, current_digest = illustration_snapshot(db, current, job.property_id, cid, lock=lock)
        if current_digest != digest:
            raise HTTPException(409, '脚本、策划或来源资料已改变，请刷新后重新生成图卡')
        return current
    total = len(board['shots'])
    with TemporaryDirectory(prefix='louxiang-story-cards-') as directory:
        for index, shot in enumerate(board['shots']):
            guard()
            progress = db.info.get('job_progress')
            if progress:
                progress(progress=.05 + .85 * index / total, stage=f'绘制虚构示例图卡 {index + 1}/{total}')
            path = Path(directory) / f'{index + 1:03d}.png'
            receipt = renderer.render_card(shot, chapters[shot['chapter_id']], path,
                size=size, fictional_numbers=True)
            guard()
            asset = _register_card(db, job, path, receipt, digest)
            shot.update(asset_id=asset.id, production_status='ready', start=0)
    current = guard(lock=True)
    board['illustration'] = {'job_id': job.id, 'source_content_version_id': cid, 'source_snapshot_sha256': digest,
        'kind': 'fictional_teaching_diagrams', 'card_count': total}
    new = revise_content(db, current, job.property_id, cid, {'script': source.script, 'storyboard': board},
                         roles=ILLUSTRATE_ROLES)
    return {'content_version_id': new['id'], 'source_content_version_id': cid, 'card_count': total}


registered_handlers = {'illustrate_content': handle_illustrate}
