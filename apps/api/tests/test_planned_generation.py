"""A chosen plan must affect generation and stay within its project."""
import pytest
from fastapi import HTTPException
from app import operations_models
from test_business import scenario


def test_generation_rejects_missing_plan_instead_of_silently_ignoring_it(scenario):
    from app.business import generate
    db, ctx, *_ = scenario
    with pytest.raises(HTTPException) as exc:
        generate('p', {'topic_id': 'space', 'plan_id': 'missing'}, db, ctx)
    assert exc.value.status_code == 404


def add_plan(scenario):
    from app.operations_models import ContentAccount, ContentPlan
    db, *_ = scenario
    account = ContentAccount(id='account', tenant_id='tenant', property_id='p', platform='小红书',
        name='空间观察', positioning='讲清空间尺度', audience='改善家庭', voice='自然，电话13900000000',
        red_lines='不承诺升值', status='managed')
    plan = ContentPlan(id='plan', tenant_id='tenant', property_id='p', title='家庭的空间取舍',
        angle='先讲生活，再讲面积', channel='小红书', account_id='account', topic_id='space',
        status='draft', brief='强调生活体验', source_snapshot={'title':'公开讨论', 'summary':'仅是选题背景'})
    db.add_all([account, plan]); db.commit()
    return plan, account


def test_plan_generation_uses_its_account_channel_and_keeps_the_draft_link(scenario, monkeypatch):
    from app.business import handle_generate
    from app.job_models import Job
    from app import provider
    plan, _ = add_plan(scenario)
    db, *_ = scenario
    seen = []
    def model(data, **callbacks):
        seen.append(data)
        return {'topic':'家庭的空间取舍','script':'420㎡的空间如何承接日常。', 'cover_title':'生活与空间',
            'fact_refs':[{'id':'f','revision':1}],
            'storyboard':[{'asset_id':'a','duration':15,'start':0,'caption':'实拍空间'}]}
    monkeypatch.setattr(provider, 'generate_content', model)
    job = Job(id='generation',user_id='admin',tenant_id='tenant',property_id='p',kind='generate_content',
        payload={'topic_id':'space','plan_id':'plan','channel':'抖音'})
    result = handle_generate(db, job)
    assert seen[0]['channel'] == '小红书'
    assert seen[0]['planning_context']['title'] == '家庭的空间取舍'
    assert seen[0]['planning_context']['angle'] == '先讲生活，再讲面积'
    assert seen[0]['account']['red_lines'] == '不承诺升值'
    assert '13900000000' not in str(seen[0])
    assert plan.content_version_id == result['content_version_id']
    assert plan.status == 'draft'


def test_paused_account_prevents_queued_generation_before_the_model_call(scenario, monkeypatch):
    from app.business import handle_generate
    from app.job_models import Job
    from app import provider
    _, account = add_plan(scenario)
    db, *_ = scenario
    account.status = 'paused'; db.commit()
    monkeypatch.setattr(provider, 'generate_content', lambda *_, **callbacks: pytest.fail('暂停账号不应调用模型'))
    with pytest.raises(HTTPException) as exc:
        handle_generate(db, Job(id='generation',user_id='admin',tenant_id='tenant',property_id='p',
            kind='generate_content',payload={'topic_id':'space','plan_id':'plan'}))
    assert exc.value.status_code == 409


def test_a_plan_cannot_override_the_verified_topic_basis(scenario):
    from app.business import generate
    add_plan(scenario)
    db, ctx, *_ = scenario
    with pytest.raises(HTTPException) as exc:
        generate('p', {'topic_id':'tour','plan_id':'plan'}, db, ctx)
    assert exc.value.status_code == 409


def test_a_plan_changed_during_generation_does_not_receive_an_outdated_draft(scenario, monkeypatch):
    from app.business import handle_generate
    from app.job_models import Job
    from app import provider
    from sqlalchemy import select
    from app.business_models import ContentVersion
    plan, _ = add_plan(scenario)
    db, *_ = scenario
    def model(_, **callbacks):
        plan.angle = '新的内容方向'; db.flush()
        return {'topic':'旧计划草稿', 'script':'420㎡空间', 'cover_title':'空间',
            'fact_refs':[{'id':'f','revision':1}],
            'storyboard':[{'asset_id':'a','duration':15,'start':0,'caption':'实拍空间'}]}
    monkeypatch.setattr(provider, 'generate_content', model)
    with pytest.raises(HTTPException) as exc:
        handle_generate(db, Job(id='generation',user_id='admin',tenant_id='tenant',property_id='p',
            kind='generate_content',payload={'topic_id':'space','plan_id':'plan'}))
    assert exc.value.status_code == 409
    assert plan.content_version_id is None
    assert db.scalar(select(ContentVersion).where(ContentVersion.series_id=='generation')) is None


def test_revising_a_planned_draft_keeps_the_plan_on_the_new_version(scenario):
    from app.business import revise_content
    db, ctx, *_, content = scenario
    plan, _ = add_plan(scenario)
    plan.content_version_id = content.id; db.commit()
    revised = revise_content(db, ctx, 'p', content.id, {'script':'新的420㎡空间表达'})
    assert plan.content_version_id == revised['id']
    assert plan.status == 'draft'


@pytest.mark.parametrize('invalid', [False, 0, [], {}])
def test_invalid_plan_identifiers_are_not_treated_as_no_plan(scenario, invalid):
    from app.business import generate
    db, ctx, *_ = scenario
    with pytest.raises(HTTPException) as exc:
        generate('p', {'topic_id':'space','plan_id':invalid}, db, ctx)
    assert exc.value.status_code == 422
