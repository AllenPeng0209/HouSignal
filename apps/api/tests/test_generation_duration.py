"""Duration recovery exercises the real generation handler and evidence checks.

Only the billed, external chat request is substituted; draft validation and
database writes run as in the worker. Fixtures contain fictional test material.
"""
from copy import deepcopy
import json

import pytest
import httpx
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app import provider
from app.agent_models import ModelUsage
from app.business import handle_generate
from app.business_models import ContentVersion
from app.job_models import Job
from test_business import scenario


@pytest.fixture(autouse=True)
def cloud_duration_backend(monkeypatch):
    # These tests replace DashScope replies/transport. A developer's local PPU
    # preference must not redirect quota checks or bypass the mocked boundary.
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')


def draft(duration=5):
    return {"topic": "空间尺度", "script": "从420平方米的实拍空间观察日常居住体验。",
            "cover_title": "看懂空间", "fact_refs": [{"id": "f", "revision": 1}],
            "storyboard": [{"asset_id": "a", "caption": "客厅空间", "duration": duration, "start": 0}]}


def model_replies(monkeypatch, *drafts):
    responses = iter(deepcopy(drafts))
    requests = []

    def chat(messages, **kwargs):
        requests.append({"messages": messages, **kwargs})
        return {"model": "duration-fixture", "choices": [{"message": {
                    "role": "assistant", "content": json.dumps(next(responses), ensure_ascii=False)},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 60}}

    monkeypatch.setattr(provider, "chat", chat)
    return requests


def generation_job():
    return Job(id="duration-generation", user_id="admin", tenant_id="tenant", property_id="p",
               kind="generate_content", payload={"topic_id": "space"})


def usage_rows(db):
    # A fresh session observes the durable accounting after a handler rollback.
    with Session(bind=db.get_bind()) as accounting:
        return [(row.model, row.input_tokens, row.output_tokens) for row in
                accounting.scalars(select(ModelUsage).where(ModelUsage.job_id == "duration-generation"))]


def test_short_model_storyboard_can_be_corrected_once_without_rewriting_evidence(scenario, monkeypatch):
    # Removing the bounded duration correction reproduces the observed failed job.
    db, *_ = scenario
    requests = model_replies(monkeypatch, draft(5), draft(30))
    result = handle_generate(db, generation_job())
    content = db.get(ContentVersion, result["content_version_id"])
    assert content.script == "从420平方米的实拍空间观察日常居住体验。"
    assert content.storyboard == [{"asset_id": "a", "caption": "客厅空间", "duration": 30, "start": 0}]
    assert content.fact_refs == [{"id": "f", "revision": 1}]
    assert len(requests) == 2
    assert usage_rows(db) == [("duration-fixture", 40, 60), ("duration-fixture", 40, 60)]


def test_valid_model_storyboard_is_saved_without_rewriting_or_second_request(scenario, monkeypatch):
    db, *_ = scenario
    requests = model_replies(monkeypatch, draft(18))
    result = handle_generate(db, generation_job())
    content = db.get(ContentVersion, result["content_version_id"])
    assert content.storyboard == [{"asset_id": "a", "caption": "客厅空间", "duration": 18, "start": 0}]
    assert len(requests) == 1
    assert usage_rows(db) == [("duration-fixture", 40, 60)]


@pytest.mark.parametrize("duration", [5, 14.9])
def test_second_wrong_total_fails_without_third_request_and_accounts_both_calls(scenario, monkeypatch, duration):
    db, *_ = scenario
    requests = model_replies(monkeypatch, draft(5), draft(duration))
    with pytest.raises(ValueError, match="模型分镜总时长需为15至60秒"):
        handle_generate(db, generation_job())
    db.rollback()
    assert len(requests) == 2
    assert usage_rows(db) == [("duration-fixture", 40, 60), ("duration-fixture", 40, 60)]
    assert db.scalar(select(ContentVersion).where(ContentVersion.series_id == "duration-generation")) is None


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, True])
def test_nonfinite_or_invalid_duration_is_rejected_without_correction(scenario, monkeypatch, duration):
    db, *_ = scenario
    requests = model_replies(monkeypatch, draft(duration))
    with pytest.raises(ValueError, match="模型分镜时长不正确"):
        handle_generate(db, generation_job())
    db.rollback()
    assert len(requests) == 1
    assert usage_rows(db) == [("duration-fixture", 40, 60)]


@pytest.mark.parametrize("field,value", [
    ("script", "这是999平方米的空间。"),
    ("cover_title", "新的承诺"),
    ("fact_refs", [{"id": "unprovided", "revision": 1}]),
    ("fact_refs", [{"id": "f", "revision": True}]),
    ("asset_id", "unprovided"),
    ("caption", "新的画面"),
    ("start", 2),
])
def test_duration_correction_cannot_rewrite_draft_or_references(scenario, monkeypatch, field, value):
    db, *_ = scenario
    altered = draft(30)
    if field in ("asset_id", "caption", "start"):
        altered["storyboard"][0][field] = value
    else:
        altered[field] = value
    requests = model_replies(monkeypatch, draft(5), altered)
    with pytest.raises(provider.ProviderError, match="仅可调整分镜时长"):
        handle_generate(db, generation_job())
    db.rollback()
    assert len(requests) == 2
    assert usage_rows(db) == [("duration-fixture", 40, 60), ("duration-fixture", 40, 60)]
    assert db.scalar(select(ContentVersion).where(ContentVersion.series_id == "duration-generation")) is None


@pytest.mark.parametrize("field,value", [
    ("fact_refs", [{"id": "unprovided", "revision": 1}]),
    ("fact_refs", [{"id": "f", "revision": 2}]),
    ("asset_id", "unprovided"),
])
def test_invalid_references_are_rejected_before_any_duration_correction(scenario, monkeypatch, field, value):
    db, *_ = scenario
    invalid = draft(5)
    if field == "asset_id":
        invalid["storyboard"][0][field] = value
    else:
        invalid[field] = value
    requests = model_replies(monkeypatch, invalid)
    with pytest.raises(ValueError):
        handle_generate(db, generation_job())
    db.rollback()
    assert len(requests) == 1
    assert usage_rows(db) == [("duration-fixture", 40, 60)]


def test_quota_is_checked_before_first_billed_request(scenario, monkeypatch):
    db, *_ = scenario
    monkeypatch.setattr(provider.settings, "app_env", "development")
    monkeypatch.setattr(provider.settings, "development_token_budget", 1000)
    db.add(ModelUsage(tenant_id="tenant", property_id="p", job_id="earlier", model="fixture",
                      input_tokens=1000, output_tokens=0))
    db.commit()
    requests = model_replies(monkeypatch, draft(30))
    with pytest.raises(HTTPException) as error:
        handle_generate(db, generation_job())
    assert error.value.status_code == 429
    assert requests == []


def test_quota_is_checked_again_before_correction(scenario, monkeypatch):
    db, *_ = scenario
    monkeypatch.setattr(provider.settings, "app_env", "development")
    monkeypatch.setattr(provider.settings, "development_token_budget", 1000)
    db.add(ModelUsage(tenant_id="tenant", property_id="p", job_id="earlier", model="fixture",
                      input_tokens=950, output_tokens=0))
    db.commit()
    requests = model_replies(monkeypatch, draft(5), draft(30))
    with pytest.raises(HTTPException) as error:
        handle_generate(db, generation_job())
    assert error.value.status_code == 429
    db.rollback()
    assert len(requests) == 1
    assert usage_rows(db) == [("duration-fixture", 40, 60)]


def test_malformed_model_output_still_records_real_usage(scenario, monkeypatch):
    db, *_ = scenario
    requests = model_replies(monkeypatch, {"script": "不完整响应"})
    with pytest.raises(provider.ProviderError, match="格式无效"):
        handle_generate(db, generation_job())
    db.rollback()
    assert len(requests) == 1
    assert usage_rows(db) == [("duration-fixture", 40, 60)]


def test_truncated_supplier_response_records_usage_even_when_transport_parser_rejects_it(scenario, monkeypatch):
    # HTTP200 + finish_reason=length has billed usage but is never a usable draft.
    db, *_ = scenario
    monkeypatch.setattr(provider.settings, "dashscope_api_key", "synthetic-duration-key")
    response = {"model": "duration-fixture", "choices": [{"message": {
        "role": "assistant", "content": json.dumps(draft(30), ensure_ascii=False)},
        "finish_reason": "length"}], "usage": {"prompt_tokens": 40, "completion_tokens": 60}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as client:
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        with pytest.raises(provider.ProviderError, match="格式无效"):
            handle_generate(db, generation_job())
    db.rollback()
    assert usage_rows(db) == [("duration-fixture", 40, 60)]


def test_billing_transaction_has_owning_tenant_context_for_postgresql_rls(scenario, monkeypatch):
    db, *_ = scenario
    model_replies(monkeypatch, draft(30))

    def check_tenant(session, flush_context, instances):
        if any(isinstance(row, ModelUsage) and row.job_id == "duration-generation" for row in session.new):
            assert session.info.get("tenant_id") == "tenant", "Billing writes need PostgreSQL tenant context"
            assert session.info.get("user_id") == "admin"

    event.listen(Session, "before_flush", check_tenant)
    try:
        result = handle_generate(db, generation_job())
        assert db.get(ContentVersion, result["content_version_id"]).status == "draft"
    finally:
        event.remove(Session, "before_flush", check_tenant)


def test_above_maximum_total_is_corrected_only_by_changing_durations(scenario, monkeypatch):
    db, *_ = scenario
    long = draft(40)
    long["storyboard"].append({"asset_id": "a", "caption": "空间关系", "duration": 40, "start": 0})
    corrected = deepcopy(long)
    corrected["storyboard"][0]["duration"] = 15
    corrected["storyboard"][1]["duration"] = 15
    requests = model_replies(monkeypatch, long, corrected)
    result = handle_generate(db, generation_job())
    assert db.get(ContentVersion, result["content_version_id"]).storyboard == corrected["storyboard"]
    assert len(requests) == 2


def test_worker_failure_preserves_both_billed_calls_without_saving_draft(scenario, monkeypatch):
    from app.worker import claim_job, execute_job
    db, *_ = scenario
    db.add(generation_job())
    db.commit()
    claimed = claim_job(db)
    ticket = (claimed.id, claimed.lease_token, claimed.attempt)
    model_replies(monkeypatch, draft(5), draft(5))
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    assert execute_job(*ticket, session_factory=factory, handlers={"generate_content": handle_generate}) is False
    db.expire_all()
    job = db.get(Job, "duration-generation")
    assert job.status == "failed"
    assert job.error == "模型分镜总时长需为15至60秒"
    assert usage_rows(db) == [("duration-fixture", 40, 60), ("duration-fixture", 40, 60)]
    assert db.scalar(select(ContentVersion).where(ContentVersion.series_id == "duration-generation")) is None

