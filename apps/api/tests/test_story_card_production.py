"""Demonstration diagrams are explicit sources, never covert real-property media."""
from copy import deepcopy
import importlib
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import event, select, text

from app import business
from app.business_models import ContentVersion
from app.core_models import Asset, Membership
from app.job_models import Job
from app.operations_models import ContentPlan
from app.security import context_for_user
from test_business import scenario
from test_longform_generation import chapter_reply, outline_reply


@pytest.fixture
def example_draft(scenario):
    db, ctx, project, _, _, content = scenario
    project.is_test = True
    outline = outline_reply()
    shots = [shot for index in range(6) for shot in chapter_reply(index)['shots']]
    shots[0]['voiceover'] = '此为虚构示例。' + shots[0]['voiceover']
    for shot in shots:
        shot.update(asset_id=None, production_status='planned', start=0)
        shot['reference_beat_ids'] = []
    chapters = []
    for chapter in outline['chapters']:
        chapters.append({key: deepcopy(value) for key, value in chapter.items() if key != 'shot_count'}
                        | {'reference_beat_ids': [], 'shot_ids': [shot['id'] for shot in shots if shot['chapter_id'] == chapter['id']]})
    board = {'schema_version': 2, 'target_seconds': 360, 'chapters': chapters, 'shots': shots,
             'voiceover': {'enabled': True, 'engine': 'edge', 'voice': 'zh-CN-XiaoxiaoNeural', 'rate': '-10%'},
             'planning_context': {'plan_id': 'plan', 'hook': '先想家庭日常',
                'outline': ['回家动线', '公共空间', '私密边界', '家庭取舍'], 'cta': '整理看房清单', 'reference': {}}}
    content.storyboard = board
    content.script = '\n'.join(shot['voiceover'] for shot in shots)
    content.topic = '虚构示例空间讲解'
    content.cover_title = '空间示意方法'
    plan = ContentPlan(id='plan', tenant_id='tenant', property_id='p', title='家庭空间讲解',
        topic_id='space', channel='抖音', content_version_id='c',
        source_snapshot={'planning': {'target_seconds': 360, 'outline': ['回家动线']}})
    db.add(plan); db.commit()
    return content


@pytest.fixture
def cards_module():
    assert importlib.util.find_spec('app.story_card_production') is not None, '缺少示例图卡生产 handler'
    return importlib.import_module('app.story_card_production')


def request(scenario):
    assert hasattr(business, 'illustrate'), '缺少示例图卡产品入口'
    db, ctx, *_ = scenario
    response = business.illustrate('p', 'c', db, ctx)
    return db.get(Job, response['job_id'])


def test_product_action_binds_private_fictional_cards_and_preserves_narration(scenario, example_draft, cards_module):
    db, ctx, project, _, _, original = scenario
    before_script = original.script
    before_voices = [shot['voiceover'] for shot in original.storyboard['shots']]
    job = request(scenario)
    result = cards_module.handle_illustrate(db, job)
    new = db.get(ContentVersion, result['content_version_id'])
    assert new.id != original.id and new.version == 2 and new.status == 'draft'
    assert new.render_asset_id is None and new.script == before_script
    assert [shot['voiceover'] for shot in new.storyboard['shots']] == before_voices
    assert all(shot['production_status'] == 'ready' and shot['start'] == 0 for shot in new.storyboard['shots'])
    assert len({shot['asset_id'] for shot in new.storyboard['shots']}) == 36
    assert db.get(ContentPlan, 'plan').content_version_id == new.id
    assert all(shot['asset_id'] is None for shot in original.storyboard['shots'])
    from app.config import settings
    for shot in new.storyboard['shots']:
        card = db.get(Asset, shot['asset_id'])
        assert card.origin_type == 'template' and card.rights == 'authorized'
        assert '本地原创' in card.rights_evidence and '虚构示例' in card.rights_evidence
        assert card.metadata_json['presentation'] == 'story_card' and card.metadata_json['is_test'] is True
        assert card.metadata_json['fictional_numbers'] is True
        assert not {'renderer', 'content_version_id'} & card.metadata_json.keys()
        path = settings.storage_root / card.storage_key
        assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
        with Image.open(path) as image:
            assert image.size == (720, 1280)
        assert str(path) in db.info['created_private_files']
    assert not {shot['asset_id'] for shot in new.storyboard['shots']} & {asset.id for asset in business.source_assets(db, ctx, project)}
    assert cards_module.handle_illustrate(db, job)['content_version_id'] == new.id
    assert len(db.scalars(select(ContentVersion).where(ContentVersion.series_id == original.series_id)).all()) == 2


def test_real_project_cannot_request_fictional_source_cards(scenario, example_draft):
    scenario[2].is_test = False; scenario[0].commit()
    with pytest.raises(HTTPException) as error:
        request(scenario)
    assert error.value.status_code == 409
    assert scenario[0].scalar(select(Job)) is None


def test_short_storyboard_cannot_request_longform_cards(scenario):
    scenario[2].is_test = True; scenario[0].commit()
    with pytest.raises(HTTPException) as error:
        request(scenario)
    assert error.value.status_code == 409


def test_editor_can_enqueue_without_expanding_public_revision_roles(scenario, example_draft):
    db, *_ = scenario
    db.get(Membership, 'm1').role = 'editor'; db.commit()
    editor = context_for_user(db, 'admin', 'tenant')
    assert hasattr(business, 'illustrate'), '缺少示例图卡产品入口'
    response = business.illustrate('p', 'c', db, editor)
    assert db.get(Job, response['job_id']).kind == 'illustrate_content'
    with pytest.raises(HTTPException) as error:
        business.revise_content(db, editor, 'p', 'c', {'script': example_draft.script})
    assert error.value.status_code == 403


def test_changed_fact_rejects_queued_illustration_before_any_file(scenario, example_draft, cards_module):
    db, _, _, fact, *_ = scenario
    job = request(scenario)
    fact.revision += 1; db.commit()
    with pytest.raises(HTTPException) as error:
        cards_module.handle_illustrate(db, job)
    assert error.value.status_code == 409
    assert not db.info.get('created_private_files')


def test_plan_change_during_drawing_rejects_new_version_and_tracks_cleanup(scenario, example_draft, cards_module, monkeypatch):
    db, *_ = scenario
    job = request(scenario)
    renderer = cards_module.card_renderer()
    real = renderer.render_card
    def changed(*args, **kwargs):
        result = real(*args, **kwargs)
        plan = db.get(ContentPlan, 'plan')
        plan.source_snapshot = {'planning': {'target_seconds': 360, 'outline': ['新的取舍']}}
        db.flush()
        return result
    monkeypatch.setattr(renderer, 'render_card', changed)
    with pytest.raises(HTTPException) as error:
        cards_module.handle_illustrate(db, job)
    assert error.value.status_code == 409
    assert len(db.scalars(select(ContentVersion)).all()) == 1
    # The temporary drawing is removed even before registration. Registered
    # files, if present, remain owned by the worker's rollback cleanup fence.
    from app.worker import _cleanup_outputs
    created = [Path(path) for path in db.info.get('created_private_files', [])]
    _cleanup_outputs(db)
    assert all(not path.exists() for path in created)


def test_capture_promise_is_not_silently_rewritten_to_fit_diagram_source(scenario, example_draft):
    db, *_ = scenario
    example_draft.topic = '真实样板间实拍'; db.commit()
    original_script = example_draft.script
    with pytest.raises(HTTPException) as error:
        request(scenario)
    assert error.value.status_code == 409
    assert db.get(ContentVersion, 'c').topic == '真实样板间实拍'
    assert db.get(ContentVersion, 'c').script == original_script


def test_duplicate_click_reuses_request_and_viewer_cannot_create_one(scenario, example_draft):
    db, *_ = scenario
    first = request(scenario)
    assert request(scenario).id == first.id
    db.get(Membership, 'm1').role = 'viewer'; db.commit()
    viewer = context_for_user(db, 'admin', 'tenant')
    with pytest.raises(HTTPException) as error:
        business.illustrate('p', 'c', db, viewer)
    assert error.value.status_code == 403
    assert len(db.scalars(select(Job)).all()) == 1


def test_cancel_after_first_card_leaves_no_new_version_and_outputs_are_cleanable(scenario, example_draft, cards_module, monkeypatch):
    db, *_ = scenario
    job = request(scenario)
    real = cards_module._register_card
    def registered(*args, **kwargs):
        asset = real(*args, **kwargs)
        db.info['job_cancelled'] = lambda: True
        return asset
    monkeypatch.setattr(cards_module, '_register_card', registered)
    with pytest.raises(ValueError, match='取消'):
        cards_module.handle_illustrate(db, job)
    created = [Path(path) for path in db.info['created_private_files']]
    assert len(created) == 1 and created[0].is_file()
    assert len(db.scalars(select(ContentVersion)).all()) == 1
    db.rollback()
    from app.worker import _cleanup_outputs
    _cleanup_outputs(db)
    assert all(not path.exists() for path in created)


def test_symlink_output_directory_cannot_write_outside_private_storage(scenario, example_draft, cards_module, tmp_path):
    db, *_ = scenario
    job = request(scenario)
    from app.config import settings
    outside = tmp_path.parent / f'{tmp_path.name}-outside'; outside.mkdir()
    directory = settings.storage_root / 'tenant' / 'p'
    directory.mkdir(parents=True)
    (directory / 'generated').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        cards_module.handle_illustrate(db, job)
    assert not list(outside.iterdir())
    assert len(db.scalars(select(ContentVersion)).all()) == 1


def test_project_locked_snapshot_refreshes_a_concurrent_test_flag_change(scenario, example_draft):
    db, *_ = scenario
    reads = 0
    def changed(connection, cursor, statement, parameters, context, executemany):
        nonlocal reads
        if statement.startswith('SELECT') and 'FROM properties' in statement:
            reads += 1
            if reads == 2:
                connection.execute(text("UPDATE properties SET is_test = 0 WHERE id = 'p'"))
    event.listen(db.bind, 'before_cursor_execute', changed)
    try:
        with pytest.raises(HTTPException) as error:
            request(scenario)
        assert error.value.status_code == 409
        assert db.scalar(select(Job)) is None
    finally:
        event.remove(db.bind, 'before_cursor_execute', changed)


@pytest.mark.parametrize(('ratio', 'size'), [('1:1', (720, 720)), ('4:5', (720, 900))])
def test_cards_use_the_content_frame_ratio(scenario, example_draft, cards_module, ratio, size):
    db, *_ = scenario
    example_draft.aspect_ratio = ratio; db.commit()
    result = cards_module.handle_illustrate(db, request(scenario))
    content = db.get(ContentVersion, result['content_version_id'])
    from app.config import settings
    for shot in content.storyboard['shots']:
        asset = db.get(Asset, shot['asset_id'])
        with Image.open(settings.storage_root / asset.storage_key) as image:
            assert image.size == size
