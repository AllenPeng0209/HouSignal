"""Outcome reads follow actual version-series receipts without inventing attribution."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util

import pytest

from test_operations import operations_client

ROOT = '/api/projects/p/operations/research/references/ref/outcomes'


@pytest.fixture
def outcomes_client(operations_client):
    client, db, current, ctx = operations_client
    if importlib.util.find_spec('app.research_outcomes'):
        from app.research_outcomes import router
        client.app.include_router(router)
    from app.operations_models import ContentOpportunity
    db.add_all([ContentOpportunity(id=identity, tenant_id=tenant, property_id=project, title=identity,
        kind='trend', platform='抖音', source_url='https://www.douyin.com/video/7390000000000000001')
        for identity, tenant, project in [('ref', 't', 'p'), ('ref2', 't', 'p'), ('foreign-ref', 'other', 'foreign')]])
    db.commit()
    return client, db, current, ctx


def content(db, identity, series='series', version=1, tenant='t', project='p'):
    from app.business_models import ContentVersion
    row = ContentVersion(id=identity, tenant_id=tenant, property_id=project, series_id=series,
        version=version, topic='住宅', script='真实项目脚本', created_by='u')
    db.add(row)
    db.flush()
    return row


def plan(db, identity, cid=None, reference='ref', tenant='t', project='p', legacy=False, template='question', created=None):
    from app.operations_models import ContentPlan
    row = ContentPlan(id=identity, tenant_id=tenant, property_id=project, title=identity, channel='抖音',
        topic_id='tour', content_version_id=cid, opportunity_id=reference if legacy else None,
        reference_id=None if legacy else reference,
        source_snapshot={'reference_analysis': {'reuse_template': {'id': template}}})
    if created is not None:
        row.created_at = created
    db.add(row)
    db.flush()
    return row


def receipt(db, identity, cid, state='live', status='verified', metrics=None, tenant='t', project='p',
            release_tenant=None, release_project=None):
    from app.business_models import Release, Publication
    release = Release(id='release-' + identity, tenant_id=release_tenant or tenant, property_id=release_project or project,
        content_version_id=cid, status='approved', snapshot={}, snapshot_sha256='a'*64, reviewer_id='u')
    db.add(release)
    db.flush()
    row = Publication(id=identity, tenant_id=tenant, property_id=project, release_id=release.id,
        title=identity, url='https://www.douyin.com/video/7390000000000000001', channel='抖音',
        platform_state=state, receipt_status=status, metrics=metrics if metrics is not None else {'D7': None, 'D30': None})
    db.add(row)
    db.flush()
    return row


def generation(db, identity, plan_id, *, tenant='t', project='p', status='succeeded', kind='generate_content'):
    from app.job_models import Job
    row = Job(id=identity, tenant_id=tenant, property_id=project, user_id='u', kind=kind,
              status=status, payload={'plan_id': plan_id}, result={})
    db.add(row)
    db.flush()
    return row


def test_outcomes_empty_reference_is_truthful_and_readable_by_viewer(outcomes_client):
    client, _, current, ctx = outcomes_client
    current['context'] = replace(ctx, role='viewer')
    response = client.get(ROOT)
    assert response.status_code == 200, response.text
    assert response.json()['plans'] == response.json()['publications'] == []
    assert response.json()['verified_live_count'] == 0
    assert response.json()['coverage_note']


def test_old_published_version_remains_visible_after_plan_points_to_new_revision(outcomes_client):
    client, db, _, _ = outcomes_client
    old = content(db, 'old', version=1)
    current = content(db, 'current', version=2)
    plan(db, 'adaptation', current.id)
    receipt(db, 'old-publication', old.id, metrics={'D7': {'views': 0, 'likes': None}, 'D30': None})
    db.commit()
    response = client.get(ROOT)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['plans'][0]['content_id'] == 'current'
    assert result['plans'][0]['stage'] == 'published'
    row = result['publications'][0]
    assert (row['id'], row['content_version_id'], row['content_version'], row['plan_id'], row['template_id']) == (
        'old-publication', 'old', 1, 'adaptation', 'question')
    assert row['metrics'] == {'D7': {'views': 0, 'likes': None}, 'D30': None}
    assert result['verified_live_count'] == 1


def test_editing_plan_keeps_publications_from_its_completed_generation(outcomes_client):
    from app import operations
    client, db, _, ctx = outcomes_client
    old = content(db, 'old-script', series='old-generation')
    adaptation = plan(db, 'adaptation', old.id)
    generation(db, 'old-generation', adaptation.id)
    receipt(db, 'old-publication', old.id, metrics={'D7': {'views': 18}, 'D30': None})
    db.commit()

    operations.update_plan(db, ctx, 'p', adaptation.id, {'angle': '新的表达角度'})
    db.commit()
    result = client.get(ROOT).json()

    assert result['plans'][0]['content_id'] is None
    assert result['plans'][0]['stage'] == 'published'
    assert [row['id'] for row in result['publications']] == ['old-publication']
    assert result['publications'][0]['plan_ids'] == ['adaptation']
    assert result['publications'][0]['metrics']['D7']['views'] == 18
    assert result['verified_live_count'] == 1


def test_regenerating_plan_preserves_publications_from_each_generation(outcomes_client):
    client, db, _, _ = outcomes_client
    old = content(db, 'old-script', series='old-generation')
    current = content(db, 'new-script', series='new-generation')
    adaptation = plan(db, 'adaptation', current.id)
    generation(db, 'old-generation', adaptation.id)
    generation(db, 'new-generation', adaptation.id)
    receipt(db, 'old-publication', old.id)
    receipt(db, 'new-publication', current.id, status='unverified')
    db.commit()

    result = client.get(ROOT).json()

    assert {row['id'] for row in result['publications']} == {'old-publication', 'new-publication'}
    assert all(row['plan_ids'] == ['adaptation'] for row in result['publications'])
    assert result['plans'][0]['content_id'] == 'new-script'
    assert result['plans'][0]['stage'] == 'published'
    assert result['verified_live_count'] == 1


def test_generation_history_requires_successful_jobs_owned_by_the_same_project_and_plan(outcomes_client):
    client, db, _, _ = outcomes_client
    adaptation = plan(db, 'adaptation')
    other = plan(db, 'other-adaptation', reference='ref2')
    for identity, owner, tenant, project, status, kind in [
        ('owned-generation', adaptation.id, 't', 'p', 'succeeded', 'generate_content'),
        ('other-plan-generation', other.id, 't', 'p', 'succeeded', 'generate_content'),
        ('other-project-generation', adaptation.id, 't', 'p2', 'succeeded', 'generate_content'),
        ('other-tenant-generation', adaptation.id, 'other', 'foreign', 'succeeded', 'generate_content'),
        ('failed-generation', adaptation.id, 't', 'p', 'failed', 'generate_content'),
        ('running-generation', adaptation.id, 't', 'p', 'running', 'generate_content'),
        ('other-kind-generation', adaptation.id, 't', 'p', 'succeeded', 'content_skill'),
    ]:
        generation(db, identity, owner, tenant=tenant, project=project, status=status, kind=kind)
        # Deliberately inconsistent local series values must not make a foreign
        # or unsuccessful job's publication belong to this reference's plan.
        version = content(db, identity + '-script', series=identity)
        receipt(db, identity + '-publication', version.id)
    db.commit()

    result = client.get(ROOT).json()

    assert [row['id'] for row in result['publications']] == ['owned-generation-publication']
    assert result['publications'][0]['plan_ids'] == ['adaptation']
    assert result['verified_live_count'] == 1


def test_unverified_and_removed_receipts_do_not_inflate_verified_live_count(outcomes_client):
    client, db, _, _ = outcomes_client
    row = content(db, 'current')
    plan(db, 'legacy-plan', row.id, legacy=True, template='invented-technique')
    receipt(db, 'pending', row.id, status='unverified')
    receipt(db, 'removed', row.id, state='removed')
    db.commit()
    result = client.get(ROOT).json()
    assert {row['id'] for row in result['publications']} == {'pending', 'removed'}
    assert all(row['metrics'] == {'D7': None, 'D30': None} for row in result['publications'])
    assert all(row['template_id'] is None for row in result['publications'])
    assert result['plans'][0]['stage'] == 'publication_recorded'
    assert result['verified_live_count'] == 0


def test_every_join_is_scoped_and_other_reference_receipts_stay_out(outcomes_client):
    client, db, current, ctx = outcomes_client
    local = content(db, 'local')
    other_reference = content(db, 'other-reference-content', series='other-series')
    wrong_project = content(db, 'wrong-project', version=3, project='p2')
    wrong_tenant = content(db, 'wrong-tenant', version=4, tenant='other', project='foreign')
    plan(db, 'local-plan', local.id)
    plan(db, 'other-reference-plan', other_reference.id, reference='ref2')
    plan(db, 'foreign-plan', local.id, tenant='other', project='foreign')
    plan(db, 'wrong-pointer-plan', wrong_project.id)
    receipt(db, 'visible', local.id)
    receipt(db, 'other-reference-publication', other_reference.id)
    receipt(db, 'bad-version-project', wrong_project.id)
    receipt(db, 'bad-version-tenant', wrong_tenant.id)
    receipt(db, 'bad-release-project', local.id, release_project='p2')
    receipt(db, 'bad-release-tenant', local.id, release_tenant='other', release_project='foreign')
    receipt(db, 'bad-publication-project', local.id, project='p2')
    receipt(db, 'bad-publication-tenant', local.id, tenant='other', project='foreign')
    db.commit()
    result = client.get(ROOT).json()
    assert {row['id'] for row in result['publications']} == {'visible'}
    assert {row['id'] for row in result['plans']} == {'local-plan', 'wrong-pointer-plan'}
    bad_pointer = next(row for row in result['plans'] if row['id'] == 'wrong-pointer-plan')
    assert bad_pointer['content_id'] is None
    current['context'] = replace(ctx, project_ids=['p2'])
    assert client.get(ROOT).status_code == 404
    current['context'] = replace(ctx, tenant_id='other')
    assert client.get(ROOT).status_code == 404


def test_shared_series_receipt_is_counted_once_without_guessing_a_unique_plan(outcomes_client):
    client, db, _, _ = outcomes_client
    row = content(db, 'current')
    plan(db, 'adapt-a', row.id)
    plan(db, 'adapt-b', row.id)
    receipt(db, 'one-receipt', row.id)
    db.commit()
    result = client.get(ROOT).json()
    assert len(result['publications']) == result['verified_live_count'] == 1
    assert result['publications'][0]['plan_id'] is None
    assert set(result['publications'][0]['plan_ids']) == {'adapt-a', 'adapt-b'}
    assert all(row['stage'] == 'published' for row in result['plans'])


def test_read_budget_caps_plans_and_publications_without_unbounded_version_ids(outcomes_client):
    client, db, _, _ = outcomes_client
    row = content(db, 'current')
    now = datetime.now(timezone.utc)
    for index in range(101):
        plan(db, f'plan-{index}', row.id, created=now-timedelta(minutes=index))
    for index in range(201):
        receipt(db, f'publication-{index}', row.id)
    db.commit()
    result = client.get(ROOT).json()
    assert len(result['plans']) == 100
    assert len(result['publications']) == 200
    assert result['verified_live_count'] == 200
    assert result['coverage_note']


def test_completed_generation_history_respects_the_recent_plan_budget(outcomes_client):
    client, db, _, _ = outcomes_client
    now = datetime.now(timezone.utc)
    for index in range(101):
        adaptation = plan(db, f'plan-{index}', created=now-timedelta(minutes=index))
        generation(db, f'generation-{index}', adaptation.id)
        version = content(db, f'script-{index}', series=f'generation-{index}')
        receipt(db, f'publication-{index}', version.id)
    db.commit()

    result = client.get(ROOT).json()

    assert len(result['plans']) == 100
    assert len(result['publications']) == 100
    assert 'plan-100' not in {row['id'] for row in result['plans']}
    assert 'publication-100' not in {row['id'] for row in result['publications']}
    assert all(row['plan_ids'][0] != 'plan-100' for row in result['publications'])


def test_completed_generation_history_keeps_the_publication_read_budget(outcomes_client):
    client, db, _, _ = outcomes_client
    adaptation = plan(db, 'adaptation')
    for index in range(201):
        generation(db, f'generation-{index}', adaptation.id)
        version = content(db, f'script-{index}', series=f'generation-{index}')
        receipt(db, f'publication-{index}', version.id)
    db.commit()

    result = client.get(ROOT).json()

    assert len(result['publications']) == 200
    assert result['verified_live_count'] == 200
    assert all(row['plan_ids'] == ['adaptation'] for row in result['publications'])
