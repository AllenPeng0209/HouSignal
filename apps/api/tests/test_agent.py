"""Behavioral coverage for the Bailian boundary and project-bound assistant."""
import json
from types import SimpleNamespace

import httpx
import pytest


def _configure_provider(monkeypatch):
    from app import provider
    monkeypatch.setattr(provider, "settings", SimpleNamespace(
        dashscope_api_key="unit-test-provider-key", dashscope_model="qwen-plus",
        dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ))
    return provider



def _mock_provider_post(monkeypatch, reply):
    from app import provider
    monkeypatch.setattr(provider, "_get_client", lambda: SimpleNamespace(post=reply))


def test_provider_preserves_real_tool_calls_and_usage(monkeypatch):
    provider = _configure_provider(monkeypatch)
    requests = []
    def respond(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"model": "qwen-plus", "choices": [{"message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
            "function": {"name": "get_project_overview", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25}})
    _mock_provider_post(monkeypatch, respond)
    result = provider.chat([{"role": "user", "content": "汇总"}], tools=[{"type": "function"}])
    assert result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_project_overview"
    assert result["usage"]["prompt_tokens"] == 100
    assert requests[0][0] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert requests[0][1]["json"]["enable_thinking"] is False
    assert requests[0][1]["json"]["max_tokens"] <= 2400


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_failure_never_exposes_supplier_body(monkeypatch, status):
    provider = _configure_provider(monkeypatch)
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: httpx.Response(
        status, json={"secret": "unit-test-provider-key", "error": "raw supplier diagnostic"}))
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{"role": "user", "content": "hello"}])
    assert "unit-test-provider-key" not in str(error.value)
    assert "raw supplier diagnostic" not in str(error.value)


def test_provider_refuses_unapproved_endpoint_before_network(monkeypatch):
    provider = _configure_provider(monkeypatch)
    provider.settings.dashscope_base_url = "https://collector.invalid/v1"
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: pytest.fail("credential must not leave Bailian"))
    with pytest.raises(provider.ProviderError):
        provider.chat([{"role": "user", "content": "hello"}])


def test_provider_accepts_official_workspace_host_but_not_suffix_attack(monkeypatch):
    provider = _configure_provider(monkeypatch)
    provider.settings.dashscope_base_url = "https://llm-abc123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: httpx.Response(200, json={
        "choices": [{"message": {"content": "已连接"}}], "usage": {}}))
    assert provider.chat([{"role": "user", "content": "hello"}])["choices"][0]["message"]["content"] == "已连接"
    provider.settings.dashscope_base_url = "https://llm-abc123.cn-beijing.maas.aliyuncs.com.collector.invalid/compatible-mode/v1"
    with pytest.raises(provider.ProviderError):
        provider.chat([{"role": "user", "content": "hello"}])


def test_asr_payload_omits_text_reasoning_and_temperature(monkeypatch):
    provider = _configure_provider(monkeypatch)
    requests = []
    def reply(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "真实音频转写"}}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    provider.chat([{"role": "user", "content": [{"type": "input_audio", "input_audio": {
        "data": "data:audio/wav;base64,AA=="}}]}], model="qwen3-asr-flash")
    assert "enable_thinking" not in requests[0]
    assert "temperature" not in requests[0]


def test_model_reply_without_any_data_query_cannot_be_reported_as_project_fact(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: httpx.Response(200, json={
        "choices": [{"message": {"content": "该项目面积999㎡，客户张总本周成交。"}}], "usage": {}}))
    db, _ = agent_data
    # Ambiguous follow-ups still require the model to choose and execute a tool.
    job = _queued_chat(db, "刚才提到的那件事是什么情况？")
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, job)
    response = db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == "assistant"))
    assert "999" not in response.content


def test_withdrawn_customer_is_not_sent_back_in_conversation_history(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage
    from app.business_models import Inquiry, Lead
    provider = _configure_provider(monkeypatch)
    db, ctx = agent_data
    first = _queued_chat(db, "关注客户1")
    db.add(ChatMessage(tenant_id="t1", property_id="p1", conversation_id=first.payload["conversation_id"],
        role="assistant", content="客户1已经确认个人需求，周六私人看房。", status="completed",
        citations=[{"kind": "lead", "id": "l1", "label": "客户1"}]))
    db.get(Inquiry, "i1").withdrawn = True
    db.get(Inquiry, "i1").consent = False
    db.get(Lead, "l1").stage = "withdrawn"
    db.commit()
    next_job = _queued_chat(db, "项目缺什么？", first.payload["conversation_id"])
    outgoing = []
    def reply(url, **kwargs):
        outgoing.append(kwargs["json"])
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            message = {"role": "assistant", "content": "项目仍需完善营销范围。[S1]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "m1", "type": "function",
                "function": {"name": "get_missing_requirements", "arguments": "{}"}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    agent.handle_chat(db, next_job)
    assert "周六私人看房" not in json.dumps(outgoing, ensure_ascii=False)


def test_chat_http_enqueues_202_and_conversations_are_private(agent_data, monkeypatch):
    from app.agent import router
    from app.agent_models import ChatMessage
    from app.db import get_db
    from app.security import get_context, Context
    from app.job_models import Job
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    db, ctx = agent_data
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: ctx
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: pytest.fail("API must not synchronously invoke model"))
    with TestClient(app) as client:
        c = client.post("/api/projects/p1/conversations", json={"title": "测试"})
        assert c.status_code == 201
        cid = c.json()["id"]
        reply = client.post(f"/api/conversations/{cid}/messages", json={"text": "汇总当前项目"})
        assert reply.status_code == 202
        job = db.get(Job, reply.json()["job_id"])
        assert job.status == "queued"
        assert job.payload["conversation_id"] == cid
        assert db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == "assistant")).status == "queued"
        ctx = Context("u2", "t1", "admin", "销售二", "u2@example.test", None)
        assert client.get(f"/api/conversations/{cid}/messages").status_code == 404


def test_generate_content_requires_structured_actual_response(monkeypatch):
    provider = _configure_provider(monkeypatch)
    generated = {"topic": "客厅尺度", "script": "经核验面积420平方米。", "cover_title": "看空间尺度",
                 "storyboard": [{"caption": "420㎡", "duration": 5}], "fact_refs": ["f1"]}
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": json.dumps(generated, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 45, "completion_tokens": 35}}))
    result = provider.generate_content({"project": "澜庭", "facts": [{"id": "f1", "value": "420", "unit": "㎡"}]})
    assert result["script"] == "经核验面积420平方米。"
    assert result["fact_refs"] == ["f1"]
    assert result["_usage"]["completion_tokens"] == 35
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: httpx.Response(200, json={
        "choices": [{"message": {"content": "无法生成"}}], "usage": {}}))
    with pytest.raises(provider.ProviderError):
        provider.generate_content({"facts": []})


@pytest.fixture
def agent_data():
    # Independent real SQLite database: only the slow supplier HTTP is doubled.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db import Base
    from app.core_models import User, Tenant, Membership, Property, Fact, Asset
    from app.business_models import Inquiry, Lead, FollowUp
    from app import agent_models, job_models
    from app.security import Context
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([Tenant(id="t1", name="团队1"), Tenant(id="t2", name="其他组织"),
                    User(id="u1", email="u1@example.test", password_hash="test", name="销售一"),
                    User(id="u2", email="u2@example.test", password_hash="test", name="销售二")])
        db.flush()
        db.add(Membership(user_id="u1", tenant_id="t1", role="admin", project_ids=None))
        db.add_all([Property(id="p1", tenant_id="t1", name="澜庭", city="苏州", marketing_confirmed=False),
                    Property(id="p2", tenant_id="t1", name="另一个项目", city="杭州"),
                    Property(id="p3", tenant_id="t2", name="其他组织项目", city="北京")])
        db.flush()
        db.add_all([Fact(id="f1", tenant_id="t1", property_id="p1", semantic_key="area", label="面积",
                         value="420", unit="㎡", source="楼书第2页", status="verified", revision=3),
                    Fact(id="f2", tenant_id="t1", property_id="p2", semantic_key="area", label="他项目秘密",
                         value="999", unit="㎡", source="秘密资料", status="verified"),
                    Asset(id="a1", tenant_id="t1", property_id="p1", name="楼书.pdf", kind="document",
                          storage_key="private/a1", mime_type="application/pdf", sha256="a"*64, byte_size=30,
                          extracted_text="花园有3棵桂花树，联系13800138000", rights="authorized",
                          rights_evidence="项目方资料处理授权", metadata_json={"model_processing_authorized": True}),
                    Asset(id="a2", tenant_id="t1", property_id="p2", name="他项目.pdf", kind="document",
                          storage_key="private/a2", mime_type="application/pdf", sha256="b"*64, byte_size=30,
                          extracted_text="花园保密细节", rights="authorized")])
        for suffix, owner, stage, consent in [("1", "u1", "appointment", True), ("2", "u2", "visit_completed", True),
                                               ("3", "u1", "withdrawn", True), ("4", "u1", "qualified", False)]:
            db.add(Inquiry(id="i"+suffix, tenant_id="t1", property_id="p1", name="客户"+suffix,
                           need="花园改善，13800138000", owner_id=owner, contactable=True, consent=consent,
                           qualified=True, contact_cipher="never-send-this-cipher"))
            db.flush()
            db.add(Lead(id="l"+suffix, tenant_id="t1", property_id="p1", inquiry_id="i"+suffix,
                        name="客户"+suffix, need="花园改善，13800138000", owner_id=owner,
                        stage=stage, next_action="本周安排看房"))
        db.add(FollowUp(id="fu1", tenant_id="t1", property_id="p1", lead_id="l1",
                       note="已确认周六，电话13800138000", next_action="周五确认到访时间", created_by="u1"))
        db.commit()
        yield db, Context("u1", "t1", "admin", "销售一", "u1@example.test", None)
    engine.dispose()


def test_tools_bind_scope_and_redact_customer_and_document_phones(agent_data):
    from app.agent import execute_tool
    db, ctx = agent_data
    result = execute_tool(db, ctx, "p1", "search_evidence", {"query": "花园"})
    serialized = json.dumps(result, ensure_ascii=False)
    assert "3棵桂花树" in serialized
    assert "保密细节" not in serialized
    assert "13800138000" not in serialized
    assert result["sources"][0]["id"] == "a1"
    with pytest.raises(ValueError):
        execute_tool(db, ctx, "p1", "search_evidence", {"query": "花园", "property_id": "p2"})


def test_priority_leads_exclude_withdrawn_and_unconsented_and_other_sales(agent_data):
    from app.agent import execute_tool
    from app.security import Context
    db, ctx = agent_data
    result = execute_tool(db, ctx, "p1", "list_priority_leads", {})
    assert [row["id"] for row in result["data"]["leads"]] == ["l2", "l1"]
    sales = Context("u1", "t1", "sales", "销售一", "u1@example.test", None)
    result = execute_tool(db, sales, "p1", "list_priority_leads", {})
    assert [row["id"] for row in result["data"]["leads"]] == ["l1"]
    assert "13800138000" not in json.dumps(result)


def test_fact_sources_have_current_revision_and_no_cross_project_leaks(agent_data):
    from app.agent import execute_tool
    from app.core_models import Fact
    db, ctx = agent_data
    result = execute_tool(db, ctx, "p1", "get_project_overview", {})
    assert [(fact['id'], fact['value']) for fact in result['data']['facts']] == [('f1', '420')]
    assert 'f2' not in {source['id'] for source in result['sources']}
    source = next(s for s in result["sources"] if s["kind"] == "fact")
    assert source["id"] == "f1" and source["revision"] == 3
    db.get(Fact, "f1").value = "425"
    db.get(Fact, "f1").revision = 4
    db.commit()
    updated = execute_tool(db, ctx, "p1", "get_project_overview", {})
    assert next(f for f in updated['data']['facts'] if f['id']=='f1')['value']=='425'
    assert next(s for s in updated["sources"] if s["kind"] == "fact")["revision"] == 4


def test_tool_cannot_fetch_other_sales_customer_or_arbitrary_sql(agent_data):
    from app.agent import execute_tool
    from app.security import Context
    from fastapi import HTTPException
    db, _ = agent_data
    ctx = Context("u1", "t1", "sales", "销售一", "u1@example.test", None)
    with pytest.raises(HTTPException) as error:
        execute_tool(db, ctx, "p1", "get_customer", {"lead_id": "l2"})
    assert error.value.status_code == 404
    with pytest.raises(ValueError):
        execute_tool(db, ctx, "p1", "execute_sql", {"sql": "select * from inquiries"})


def _queued_chat(db, text="请汇总项目", conversation_id=None):
    from app.agent_models import Conversation, ChatMessage
    from app.job_models import Job
    if conversation_id is None:
        conversation = Conversation(tenant_id="t1", property_id="p1", user_id="u1", title="项目汇总")
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id
    message = ChatMessage(tenant_id="t1", property_id="p1", conversation_id=conversation_id,
                          role="user", content=text, citations=[], status="completed")
    db.add(message)
    db.flush()
    job = Job(tenant_id="t1", property_id="p1", user_id="u1", kind="chat", status="running", attempt=1,
              payload={"conversation_id": conversation_id, "user_message_id": message.id})
    db.add(job)
    db.flush()
    message.job_id = job.id
    db.commit()
    return job


def test_real_function_loop_persists_references_and_sanitized_multi_turn_history(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall, ModelUsage
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    calls = []
    def reply(url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        assert "13800138000" not in json.dumps(body)
        if body["messages"][-1]["role"] != "tool":
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}
        else:
            message = {"role": "assistant", "content": "客户1下一步是周五确认到访时间。[S1]"}
        return httpx.Response(200, json={"model": "qwen-plus", "choices": [{"message": message}],
                                       "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
    _mock_provider_post(monkeypatch, reply)
    job = _queued_chat(db, "客户1电话13800138000，下一步是什么？")
    result = agent.handle_chat(db, job)
    db.commit()
    message = db.get(ChatMessage, result["message_id"])
    assert message.status == "completed"
    assert any(s["kind"] == "lead" and s["id"] == "l1" for s in message.citations)
    assert db.scalars(select(ToolCall).where(ToolCall.job_id == job.id)).all()
    assert sum(u.input_tokens for u in db.scalars(select(ModelUsage).where(ModelUsage.job_id == job.id))) == 40
    next_job = _queued_chat(db, "他刚才说的下一步？", job.payload["conversation_id"])
    agent.handle_chat(db, next_job)
    assert any("周五确认到访时间" in str(m.get("content")) for m in calls[2]["messages"])


def test_failed_provider_creates_failed_message_and_raises_safe_error(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: httpx.Response(500, text="raw secret supplier error"))
    job = _queued_chat(db)
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, job)
    db.flush()
    assistant = db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == "assistant"))
    assert assistant.status == "failed"
    assert "raw secret" not in assistant.content


def test_invalid_function_does_not_count_as_a_database_read(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    def reply(url, **kwargs):
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            message = {"role": "assistant", "content": "数据库查询成功，客户秘密张总。[S999]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "bad", "type": "function",
                "function": {"name": "execute_sql", "arguments": '{"sql":"select * from inquiries"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    job = _queued_chat(db, "刚才提到的客户具体是什么情况？")
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, job)
    answer = db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == "assistant"))
    assert "张总" not in answer.content
    assert answer.citations == []


def test_permission_withdrawal_during_model_call_prevents_final_customer_reply(agent_data, monkeypatch):
    from app import agent
    from app.business_models import Inquiry, Lead
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    def reply(url, **kwargs):
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            db.get(Inquiry, "i1").withdrawn = True
            db.get(Inquiry, "i1").consent = False
            db.get(Lead, "l1").stage = "withdrawn"
            db.flush()
            message = {"role": "assistant", "content": "客户1的私人需求是花园改善。[S1]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "r1", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, _queued_chat(db))


def test_conversation_retrieval_hides_revoked_customer_answer(agent_data):
    from app.agent import list_messages
    from app.agent_models import ChatMessage
    from app.business_models import Inquiry
    db, ctx = agent_data
    job = _queued_chat(db)
    db.add(ChatMessage(tenant_id="t1", property_id="p1", conversation_id=job.payload["conversation_id"],
                       role="assistant", content="客户1私密需求内容", status="completed",
                       citations=[{"kind": "lead", "id": "l1", "label": "客户1"}]))
    db.commit()
    db.get(Inquiry, "i1").consent = False
    db.commit()
    records = list_messages(job.payload["conversation_id"], ctx, db)
    assert "私密需求内容" not in json.dumps(records, ensure_ascii=False)


def test_external_customer_withdrawal_refreshes_held_identity_before_final_answer(agent_data, monkeypatch):
    from app import agent
    from app.business_models import Inquiry
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    held_record = db.get(Inquiry, "i1")
    assert held_record.consent is True
    def reply(url, **kwargs):
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            with Session(db.bind) as external:
                external.execute(update(Inquiry).where(Inquiry.id == "i1").values(consent=False, withdrawn=True))
                external.commit()
            message = {"role": "assistant", "content": "客户1私人需求详情。[S1]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "w1", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, _queued_chat(db))


def _pending_inquiry(db):
    from app.business_models import Inquiry
    inquiry = Inquiry(id="pending1", tenant_id="t1", property_id="p1", name="待处理客户A",
                      need="需要一层独立茶室，私人需求内容", owner_id="u1", qualified=False,
                      consent=True, contactable=True, withdrawn=False)
    db.add(inquiry)
    db.commit()
    return inquiry


def test_overview_has_record_sources_for_every_returned_pending_inquiry(agent_data):
    from app.agent import execute_tool
    db, ctx = agent_data
    _pending_inquiry(db)
    result = execute_tool(db, ctx, "p1", "get_project_overview", {})
    inquiry_ids = {row["id"] for row in result["data"]["pending_inquiries"]}
    source_ids = {ref["id"] for ref in result["sources"] if ref["kind"] == "inquiry"}
    assert inquiry_ids == {"pending1"}
    assert inquiry_ids <= source_ids


def test_pending_customer_security_checks_all_tool_refs_even_when_model_cites_only_fact(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall
    from app.business_models import Inquiry
    from app.core_models import Membership
    from app.security import Context
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    _pending_inquiry(db)
    membership = db.scalar(select(Membership).where(Membership.user_id == "u1"))
    membership.role = "sales"
    db.commit()
    sales = Context("u1", "t1", "sales", "销售一", "u1@example.test", None)
    def reply(url, **kwargs):
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            message = {"role": "assistant", "content": "面积420㎡。待处理客户A需要一层独立茶室，私人需求内容。[S1]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "pending_summary", "type": "function",
                "function": {"name": "get_project_overview", "arguments": "{}"}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    job = _queued_chat(db)
    result = agent.handle_chat(db, job)
    db.commit()
    assistant = db.get(ChatMessage, result["message_id"])
    assert [ref["kind"] for ref in assistant.citations] == ["fact"]
    # Reassign only the pending record. Display citations remain the same, but
    # every queried CRM record must remain an authorization dependency.
    db.get(Inquiry, "pending1").owner_id = "u2"
    db.commit()
    assert agent.execute_tool(db, sales, "p1", "get_project_overview", {})["data"]["pending_inquiries"] == []
    records = agent.list_messages(job.payload["conversation_id"], sales, db)
    assert "私人需求内容" not in json.dumps(records, ensure_ascii=False)
    next_job = _queued_chat(db, "还有哪些资料缺口？", job.payload["conversation_id"])
    next_user = db.get(ChatMessage, next_job.payload["user_message_id"])
    conversation = db.get(agent.Conversation, job.payload["conversation_id"])
    history = agent._history(db, sales, conversation, next_user)
    assert "私人需求内容" not in json.dumps(history, ensure_ascii=False)
    tool = db.scalar(select(ToolCall).where(ToolCall.job_id == job.id))
    assert any(ref["kind"] == "inquiry" and ref["id"] == "pending1" for ref in tool.result_refs)


def test_uncited_tool_record_reassignment_during_call_blocks_final_answer(agent_data, monkeypatch):
    from app import agent
    from app.business_models import Inquiry
    from app.core_models import Membership
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    _pending_inquiry(db)
    db.scalar(select(Membership).where(Membership.user_id == "u1")).role = "sales"
    db.commit()
    def reply(url, **kwargs):
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            db.get(Inquiry, "pending1").owner_id = "u2"
            db.flush()
            message = {"role": "assistant", "content": "客户A私人需求内容。[S1]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "r_pending", "type": "function",
                "function": {"name": "get_project_overview", "arguments": "{}"}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, _queued_chat(db))


def test_legacy_overview_without_complete_access_provenance_is_not_replayed(agent_data):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall
    db, ctx = agent_data
    job = _queued_chat(db)
    incomplete_sources = [{"kind": "fact", "id": "f1", "label": "面积", "revision": 3},
                          {"kind": "page", "id": "p1", "label": "旧工作台", "page": "dashboard"}]
    db.add(ChatMessage(tenant_id="t1", property_id="p1", conversation_id=job.payload["conversation_id"],
                       role="assistant", content="修复前旧回答中的私人需求内容", status="completed",
                       job_id=job.id, citations=[incomplete_sources[0]]))
    db.add(ToolCall(tenant_id="t1", property_id="p1", conversation_id=job.payload["conversation_id"],
                    job_id=job.id, name="get_project_overview", arguments={}, result_refs=incomplete_sources))
    db.commit()
    records = agent.list_messages(job.payload["conversation_id"], ctx, db)
    assert "私人需求内容" not in json.dumps(records, ensure_ascii=False)
    next_job = _queued_chat(db, "继续看缺口", job.payload["conversation_id"])
    conversation = db.get(agent.Conversation, job.payload["conversation_id"])
    message = db.get(ChatMessage, next_job.payload["user_message_id"])
    assert "私人需求内容" not in json.dumps(agent._history(db, ctx, conversation, message), ensure_ascii=False)


@pytest.mark.parametrize("change", ["revision", "status", "validity"])
def test_changed_fact_history_is_not_replayed_and_old_answer_marks_update(agent_data, change):
    from app import agent
    from app.agent_models import ChatMessage
    from app.core_models import Fact
    from app.db import utcnow
    from datetime import timedelta
    db, ctx = agent_data
    job = _queued_chat(db)
    reference = {"kind": "fact", "id": "f1", "label": "面积", "revision": 3,
                 "status": "verified", "valid": True}
    old_answer = ChatMessage(tenant_id="t1", property_id="p1", conversation_id=job.payload["conversation_id"],
        role="assistant", content="历史回答：面积420㎡，已经核验。", status="completed", citations=[reference])
    db.add(old_answer)
    db.commit()
    fact = db.get(Fact, "f1")
    if change == "revision":
        fact.value = "425"
        fact.revision = 4
        fact.status = "unverified"
    elif change == "status":
        fact.status = "expired"
    else:
        fact.valid_until = utcnow() - timedelta(seconds=1)
    db.commit()
    next_job = _queued_chat(db, "现在面积多少？", job.payload["conversation_id"])
    conversation = db.get(agent.Conversation, job.payload["conversation_id"])
    question = db.get(ChatMessage, next_job.payload["user_message_id"])
    assert "历史回答：面积420" not in json.dumps(agent._history(db, ctx, conversation, question), ensure_ascii=False)
    historical = next(row for row in agent.list_messages(conversation.id, ctx, db) if row["id"] == old_answer.id)
    assert "历史回答：面积420" in historical["content"]
    assert historical["source_state"] == "updated"
    assert "重新查询" in historical["content"]
    if change == "revision":
        current = agent.execute_tool(db, ctx, "p1", "get_project_overview", {})
        assert current["data"]["facts"][0]["value"] == "425"
        assert current["data"]["facts"][0]["status"] == "unverified"


def test_raw_document_requires_rights_evidence_and_explicit_model_permission(agent_data):
    from app.agent import execute_tool
    from app.core_models import Asset
    db, ctx = agent_data
    asset = db.get(Asset, "a1")
    asset.metadata_json = {"model_processing_authorized": False}
    asset.rights = "authorized"
    asset.rights_evidence = "项目方资料处理授权"
    db.commit()
    assert execute_tool(db, ctx, "p1", "search_evidence", {"query": "花园"})["data"]["matches"] == []
    asset.metadata_json = {"model_processing_authorized": True}
    asset.rights = "pending"
    db.commit()
    assert execute_tool(db, ctx, "p1", "search_evidence", {"query": "花园"})["data"]["matches"] == []
    asset.rights = "authorized"
    asset.rights_evidence = "待确认"
    db.commit()
    assert execute_tool(db, ctx, "p1", "search_evidence", {"query": "花园"})["data"]["matches"] == []
    asset.rights_evidence = "项目方资料处理授权"
    db.commit()
    assert execute_tool(db, ctx, "p1", "search_evidence", {"query": "花园"})["data"]["matches"][0]["id"] == "a1"


def test_document_model_permission_false_true_revoked_never_leaks_to_vendor(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Asset
    provider = _configure_provider(monkeypatch)
    db, ctx = agent_data
    asset = db.get(Asset, "a1")
    asset.rights_evidence = "项目方资料处理授权"
    asset.metadata_json = {"model_processing_authorized": False}
    db.commit()
    outgoing = []
    def reply(url, **kwargs):
        outgoing.append(kwargs["json"])
        last = kwargs["json"]["messages"][-1]
        if last["role"] == "tool":
            matches = json.loads(last["content"])["data"]["matches"]
            message = {"role": "assistant", "content": "文档内容：花园有3棵桂花树。[S1]" if matches else "没有可用于AI查询的授权文档。"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "doc_search", "type": "function",
                "function": {"name": "search_evidence", "arguments": '{"query":"花园"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    first = _queued_chat(db, "查一下花园文档")
    agent.handle_chat(db, first)
    db.commit()
    assert "3棵桂花树" not in json.dumps(outgoing, ensure_ascii=False)
    outgoing.clear()
    asset.metadata_json = {"model_processing_authorized": True}
    db.commit()
    second = _queued_chat(db, "再查一下花园文档", first.payload["conversation_id"])
    agent.handle_chat(db, second)
    db.commit()
    assert "3棵桂花树" in json.dumps(outgoing, ensure_ascii=False)
    outgoing.clear()
    asset.metadata_json = {"model_processing_authorized": False}
    db.commit()
    historical = agent.list_messages(first.payload["conversation_id"], ctx, db)
    assert "3棵桂花树" not in json.dumps(historical, ensure_ascii=False)
    third = _queued_chat(db, "当前花园文档还有什么？", first.payload["conversation_id"])
    agent.handle_chat(db, third)
    db.commit()
    assert "3棵桂花树" not in json.dumps(outgoing, ensure_ascii=False)


def test_document_model_permission_withdrawn_during_call_blocks_final_excerpt(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Asset
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    asset = db.get(Asset, "a1")
    asset.rights_evidence = "项目方资料处理授权"
    asset.metadata_json = {"model_processing_authorized": True}
    db.commit()
    def reply(url, **kwargs):
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            asset.metadata_json = {"model_processing_authorized": False}
            db.flush()
            message = {"role": "assistant", "content": "花园有3棵桂花树。[S1]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "doc_revoke", "type": "function",
                "function": {"name": "search_evidence", "arguments": '{"query":"花园"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    with pytest.raises(provider.ProviderError):
        agent.handle_chat(db, _queued_chat(db, "查花园文档"))


def _draft_client(db, initial_ctx):
    from app.agent import router
    from app.db import get_db
    from app.security import get_context
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(router)
    context = {"current": initial_ctx}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: context["current"]
    return TestClient(app), context


def test_conversation_draft_persists_on_retrieval_and_each_project_has_its_own(agent_data):
    db, ctx = agent_data
    client, _ = _draft_client(db, ctx)
    with client:
        c1 = client.post("/api/projects/p1/conversations", json={"title": "项目一"}).json()
        c2 = client.post("/api/projects/p2/conversations", json={"title": "项目二"}).json()
        saved = client.patch(f"/api/conversations/{c1['id']}/draft", json={"draft": "汇总项目一的最新资料缺口"})
        assert saved.status_code == 200
        assert saved.json()["draft"] == "汇总项目一的最新资料缺口"
        saved2 = client.patch(f"/api/conversations/{c2['id']}/draft", json={"draft": "项目二该先跟进谁？"})
        assert saved2.status_code == 200
        # Re-query from the server as a fresh page would, rather than relying on
        # the saved response or browser-only state.
        db.expire_all()
        project1 = client.get("/api/projects/p1/conversations").json()
        project2 = client.get("/api/projects/p2/conversations").json()
        assert project1[0]["draft"] == "汇总项目一的最新资料缺口"
        assert project2[0]["draft"] == "项目二该先跟进谁？"


def test_draft_write_requires_conversation_owner_and_current_project_permission(agent_data):
    from app.security import Context
    db, ctx = agent_data
    client, holder = _draft_client(db, ctx)
    with client:
        conversation = client.post("/api/projects/p1/conversations", json={"title": "私有会话"}).json()
        path = f"/api/conversations/{conversation['id']}/draft"
        assert client.patch(path, json={"draft": "仅我可见的草稿"}).status_code == 200
        holder["current"] = Context("u2", "t1", "admin", "销售二", "u2@example.test", None)
        assert client.patch(path, json={"draft": "其他成员覆盖"}).status_code == 404
        assert client.get("/api/projects/p1/conversations").json() == []
        holder["current"] = Context("u1", "t1", "admin", "销售一", "u1@example.test", ["p2"])
        assert client.patch(path, json={"draft": "已撤销项目权限"}).status_code == 404
        holder["current"] = ctx
        assert client.get("/api/projects/p1/conversations").json()[0]["draft"] == "仅我可见的草稿"
        assert client.patch(path, json={"draft": "x" * 2001}).status_code == 422


def test_successful_message_enqueue_clears_only_its_conversation_draft(agent_data, monkeypatch):
    from app.agent_models import Conversation
    db, ctx = agent_data
    client, _ = _draft_client(db, ctx)
    _mock_provider_post(monkeypatch, lambda *args, **kwargs: pytest.fail("draft/send endpoint must only enqueue"))
    with client:
        c1 = client.post("/api/projects/p1/conversations", json={"title": "发送会话"}).json()
        c2 = client.post("/api/projects/p2/conversations", json={"title": "另一个项目"}).json()
        client.patch(f"/api/conversations/{c1['id']}/draft", json={"draft": "准备发出的项目问题"})
        client.patch(f"/api/conversations/{c2['id']}/draft", json={"draft": "另一个项目草稿"})
        response = client.post(f"/api/conversations/{c1['id']}/messages", json={"text": "准备发出的项目问题"})
        assert response.status_code == 202
        db.expire_all()
        assert db.get(Conversation, c1["id"]).draft == ""
        assert db.get(Conversation, c2["id"]).draft == "另一个项目草稿"


def test_unconsented_pending_inquiries_are_aggregate_only_and_do_not_break_summary(agent_data, monkeypatch):
    from app import agent
    from app.business_models import Inquiry
    provider = _configure_provider(monkeypatch)
    db, ctx = agent_data
    db.add(Inquiry(id="no_consent", tenant_id="t1", property_id="p1", name="未授权姓名不可发送",
                   need="私人学校需求不可发送", owner_id="u1", qualified=False, consent=False,
                   contactable=False, withdrawn=False))
    db.commit()
    result = agent.execute_tool(db, ctx, "p1", "get_project_overview", {})
    serialized = json.dumps(result, ensure_ascii=False)
    assert "未授权姓名不可发送" not in serialized
    assert "私人学校需求不可发送" not in serialized
    assert "no_consent" not in serialized
    assert result["data"]["pending_inquiries"] == []
    assert result["data"]["pending_inquiries_without_consent"] == 1
    outgoing = []
    def reply(url, **kwargs):
        outgoing.append(kwargs["json"])
        if kwargs["json"]["messages"][-1]["role"] == "tool":
            message = {"role": "assistant", "content": "有1条咨询尚待确认授权。[S2]"}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "anonymous_summary", "type": "function",
                "function": {"name": "get_project_overview", "arguments": "{}"}}]}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {}})
    _mock_provider_post(monkeypatch, reply)
    reply_result = agent.handle_chat(db, _queued_chat(db))
    assert reply_result["message_id"]
    vendor_data = json.dumps(outgoing, ensure_ascii=False)
    assert "未授权姓名不可发送" not in vendor_data and "私人学校需求不可发送" not in vendor_data
    assert "no_consent" not in vendor_data


def test_rejected_message_does_not_discard_saved_draft(agent_data):
    from app.agent_models import Conversation
    db, ctx = agent_data
    client, _ = _draft_client(db, ctx)
    with client:
        conversation = client.post("/api/projects/p1/conversations", json={"title": "进行中的会话"}).json()
        message_path = f"/api/conversations/{conversation['id']}/messages"
        draft_path = f"/api/conversations/{conversation['id']}/draft"
        assert client.post(message_path, json={"text": "第一轮问题"}).status_code == 202
        assert client.patch(draft_path, json={"draft": "等待发送的新问题"}).status_code == 200
        assert client.post(message_path, json={"text": "等待发送的新问题"}).status_code == 409
        db.expire_all()
        assert db.get(Conversation, conversation["id"]).draft == "等待发送的新问题"


@pytest.mark.parametrize(("question", "tools"), [
    ("当前项目面积是多少？", ["search_evidence"]),
    ("当前项目缺少哪些资料？", ["get_missing_requirements"]),
    ("当前项目关键客户是谁？", ["list_priority_leads"]),
    ("请完整汇总当前项目情况", ["get_project_overview", "get_missing_requirements", "list_priority_leads", "get_content_metrics"]),
])
def test_clear_requests_read_live_records_before_one_compact_model_generation(agent_data, monkeypatch, question, tools):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall
    from app.core_models import Property
    from sqlalchemy import select
    db, _ = agent_data
    # Verbose operational evidence is not needed to summarize the business state.
    db.get(Property, "p1").marketing_evidence = "冗长内部授权说明" * 1000
    db.commit()
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append((messages, kwargs))
        return {"choices": [{"message": {"role": "assistant", "content": "已依据当前项目记录回答。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    job = _queued_chat(db, question)
    result = agent.handle_chat(db, job)
    message = db.get(ChatMessage, result["message_id"])
    calls = db.scalars(select(ToolCall).where(ToolCall.job_id == job.id).order_by(ToolCall.created_at)).all()
    assert message.status == "completed"
    assert [call.name for call in calls] == tools
    assert len(outgoing) == 1
    assert not outgoing[0][1].get("tools")
    model_input = json.dumps(outgoing[0][0], ensure_ascii=False)
    assert "冗长内部授权说明" not in model_input
    assert "never-send-this-cipher" not in model_input
    assert len(model_input) < 12000
    if "search_evidence" in tools or "get_project_overview" in tools:
        assert "420" in model_input and "楼书第2页" in model_input
        assert any(ref.get("revision") == 3 and ref.get("valid") is True
                   for call in calls for ref in call.result_refs)
    if "list_priority_leads" in tools:
        assert "客户2" in model_input and "客户3" not in model_input and "客户4" not in model_input
    if "get_content_metrics" in tools:
        reports = json.loads(outgoing[0][0][-1]["content"])
        assert reports["data"]["metrics"]["views"] is None


def test_simple_fact_generation_cannot_use_stale_revision_from_previous_turn(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Fact
    from app.agent_models import ChatMessage
    db, _ = agent_data
    job = _queued_chat(db, "当前项目面积是多少？")
    db.get(Fact, "f1").value = "425"
    db.get(Fact, "f1").revision = 4
    db.get(Fact, "f1").status = "unverified"
    db.commit()
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append(json.dumps(messages, ensure_ascii=False))
        return {"choices": [{"message": {"content": "面积425㎡，待核验。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    result = agent.handle_chat(db, job)
    assert len(outgoing) == 1
    assert "425" in outgoing[0] and "待核验" in outgoing[0]
    assert any(ref["revision"] == 4 and ref["valid"] is False and ref["status"] == "unverified"
               for ref in db.get(ChatMessage, result["message_id"]).citations)


def test_grounded_stream_publishes_sanitized_partial_answer_before_completion(agent_data, monkeypatch):
    from app import agent
    db, _ = agent_data
    updates = []
    db.info["job_progress"] = lambda **update: updates.append(update)
    def stream(messages, on_delta, **kwargs):
        text = "面积420㎡，联系人13800138000。" + "当前资料尚需人工核验与补充。" * 20 + "[S1]"
        for start in range(0, len(text), 70):
            on_delta(text[start:start + 70])
        # This assertion runs while the external response is still open.
        assert any(update.get("preview_text") for update in updates)
        assert all("13800138000" not in update.get("preview_text", "") for update in updates)
        assert all(any(ref.get("revision") == 3 for ref in update.get("access_refs", []))
                   for update in updates if update.get("preview_text"))
        return {"choices": [{"message": {"content": text}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "stream_chat", stream, raising=False)
    monkeypatch.setattr(agent.provider, "chat", lambda *args, **kwargs: pytest.fail("worker should stream grounded answers"))
    result = agent.handle_chat(db, _queued_chat(db, "当前项目面积是多少？"))
    assert result["message_id"]


def test_fact_revision_change_mid_stream_blocks_new_preview_and_final_answer(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Fact
    from app.agent_models import ChatMessage
    from sqlalchemy import select
    db, _ = agent_data
    updates = []
    db.info["job_progress"] = lambda **update: updates.append(update)
    def stream(messages, on_delta, **kwargs):
        on_delta("经核验，面积420㎡。[S1]" * 8)
        before = len(updates)
        db.get(Fact, "f1").revision = 4
        db.flush()
        on_delta("发生变化后不应发布的文本" * 10)
        assert len(updates) == before
        pytest.fail("changed fact must terminate the stream")
    monkeypatch.setattr(agent.provider, "stream_chat", stream, raising=False)
    monkeypatch.setattr(agent.provider, "chat", lambda *args, **kwargs: pytest.fail("worker should stream"))
    job = _queued_chat(db, "当前项目面积是多少？")
    with pytest.raises(agent.provider.ProviderError):
        agent.handle_chat(db, job)
    answer = db.scalar(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.role == "assistant"))
    assert answer.status == "failed" and "发生变化后" not in answer.content


def test_stream_progress_cancellation_is_not_swallowed(agent_data, monkeypatch):
    from app import agent
    db, _ = agent_data
    def checkpoint(**update):
        if update.get("preview_text"):
            raise RuntimeError("lease cancelled")
    db.info["job_progress"] = checkpoint
    def stream(messages, on_delta, **kwargs):
        on_delta("面积420㎡。" * 20)
        pytest.fail("cancelled checkpoint must interrupt provider stream")
    monkeypatch.setattr(agent.provider, "stream_chat", stream, raising=False)
    monkeypatch.setattr(agent.provider, "chat", lambda *args, **kwargs: pytest.fail("worker should stream"))
    with pytest.raises(RuntimeError, match="lease cancelled"):
        agent.handle_chat(db, _queued_chat(db, "当前项目面积是多少？"))


def test_specific_customer_summary_prefetches_unique_customer_and_keeps_followups(agent_data, monkeypatch):
    from app import agent
    db, _ = agent_data
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append((list(messages), kwargs))
        if messages[-1]["role"] != "tool":
            return {"choices": [{"message": {"tool_calls": [{"id": "customer_followup", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}}], "usage": {}}
        assert "周五确认到访时间" in messages[-1]["content"]
        return {"choices": [{"message": {"content": "客户1的下一步是周五确认到访时间。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    result = agent.handle_chat(db, _queued_chat(db, "请汇总项目里客户1的具体情况和跟进记录"))
    assert result["message_id"]
    assert len(outgoing) == 1
    assert not outgoing[0][1].get("tools")
    assert outgoing[0][1].get("model") == agent.provider.settings.dashscope_model


def test_streamed_customer_withdrawal_interrupts_before_next_batch(agent_data, monkeypatch):
    from app import agent
    from app.business_models import Inquiry
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    db, _ = agent_data
    updates = []
    db.info["job_progress"] = lambda **update: updates.append(update)
    def stream(messages, on_delta, **kwargs):
        on_delta("客户2已到访，客户1已预约，均有已记录下一步。[S1]" * 4)
        prior = len(updates)
        with Session(db.bind) as external:
            external.execute(update(Inquiry).where(Inquiry.id == "i2").values(consent=False, withdrawn=True))
            external.commit()
        with pytest.raises(agent.provider.ProviderError):
            on_delta("撤回授权之后仍展示私人客户情况的文本" * 8)
        assert len(updates) == prior
        raise agent.provider.ProviderError("查询期间资料或客户权限发生变化，请重新提问。")
    monkeypatch.setattr(agent.provider, "stream_chat", stream)
    with pytest.raises(agent.provider.ProviderError):
        agent.handle_chat(db, _queued_chat(db, "当前项目关键客户是谁？"))


def test_stream_preview_never_releases_partial_phone_email_or_credential(agent_data, monkeypatch):
    from app import agent
    db, _ = agent_data
    updates = []
    db.info["job_progress"] = lambda **update: updates.append(update)
    private_email = "privateperson" + "x" * 80 + "@example.test"
    private_key = "sk-" + "x" * 60
    def stream(messages, on_delta, **kwargs):
        text = "核验说明。" * 18 + "电话13800138000，邮箱" + private_email + "，凭证" + private_key + "。" + "请继续核验面积。" * 15
        for char in text:
            on_delta(char)
        previews = [update["preview_text"] for update in updates if update.get("preview_text")]
        assert previews
        assert all("1380013" not in preview and "privateperson" not in preview and "sk-" not in preview for preview in previews)
        return {"choices": [{"message": {"content": text}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "stream_chat", stream)
    result = agent.handle_chat(db, _queued_chat(db, "当前项目面积是多少？"))
    assert result["message_id"]


def test_distinct_project_page_sources_keep_correct_ui_citation_targets(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall
    from sqlalchemy import select
    db, _ = agent_data
    monkeypatch.setattr(agent.provider, "chat", lambda *args, **kwargs: {
        "choices": [{"message": {"content": "概况[S2]，资料缺口[S3]，效果复盘[S6]。"}}], "usage": {}})
    job = _queued_chat(db, "请完整汇总当前项目情况")
    result = agent.handle_chat(db, job)
    message = db.get(ChatMessage, result["message_id"])
    assert {ref["ref"]: ref["page"] for ref in message.citations} == {
        "S2": "dashboard", "S3": "property", "S6": "reports"}
    queried_pages = [ref for call in db.scalars(select(ToolCall).where(ToolCall.job_id == job.id))
                     for ref in call.result_refs if ref["kind"] == "page"]
    assert len({ref["ref"] for ref in queried_pages}) == 3


def test_complex_grounded_rounds_stream_keep_additional_tools_and_clear_provisional_text(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall
    from sqlalchemy import select
    db, _ = agent_data
    updates, streamed = [], []
    db.info["job_progress"] = lambda **update: updates.append(update)
    def plan(messages, **kwargs):
        assert kwargs.get("tools")
        return {"choices": [{"message": {"tool_calls": [{"id": "list_first", "type": "function",
            "function": {"name": "list_priority_leads", "arguments": "{}"}}]}}], "usage": {}}
    def stream(messages, on_delta, **kwargs):
        streamed.append(list(messages))
        assert kwargs.get("tools")  # Additional details must remain queryable.
        if len(streamed) == 1:
            on_delta("目前已有预约客户记录，正在核对具体跟进情况。" * 6)
            return {"choices": [{"message": {"content": "需要读取跟进详情。", "tool_calls": [{"id": "get_more", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}}], "usage": {}}
        assert "周五确认到访时间" in messages[-1]["content"]
        text = "客户1的下一步是周五确认到访时间。[S2]"
        on_delta(text)
        return {"choices": [{"message": {"content": text}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", plan)
    monkeypatch.setattr(agent.provider, "stream_chat", stream)
    job = _queued_chat(db, "刚才的客户1要怎么继续跟进？")
    result = agent.handle_chat(db, job)
    assert len(streamed) == 2
    assert any(update.get("preview_text") == "" for update in updates)
    assert "周五确认到访时间" in db.get(ChatMessage, result["message_id"]).content
    calls = db.scalars(select(ToolCall).where(ToolCall.job_id == job.id).order_by(ToolCall.created_at)).all()
    assert [call.name for call in calls] == ["list_priority_leads", "get_customer"]


def test_chinese_first_text_is_visible_without_a_32_character_delay(agent_data, monkeypatch):
    from app import agent
    db, _ = agent_data
    updates, clock = [], [0.0]
    db.info["job_progress"] = lambda **update: updates.append(update)
    monkeypatch.setattr(agent.time, "monotonic", lambda: clock[0])
    def stream(messages, on_delta, **kwargs):
        clock[0] = 0.25
        on_delta("项目面积")
        assert any(update.get("preview_text") == "项目面积" for update in updates)
        on_delta("420㎡，已经核验。[S1]")
        return {"choices": [{"message": {"content": "项目面积420㎡，已经核验。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "stream_chat", stream)
    agent.handle_chat(db, _queued_chat(db, "当前项目面积是多少？"))


def test_requery_cannot_replace_an_earlier_same_revision_verification_dependency(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Fact
    db, _ = agent_data
    rounds = []
    def generate(messages, **kwargs):
        rounds.append(list(messages))
        if len(rounds) == 2:
            # Status can change without a new value revision (e.g. expiration).
            db.get(Fact, "f1").status = "expired"
            db.flush()
        if len(rounds) <= 2:
            return {"choices": [{"message": {"tool_calls": [{"id": f"r{len(rounds)}", "type": "function",
                "function": {"name": "search_evidence", "arguments": '{"query":"面积"}'}}]}}], "usage": {}}
        return {"choices": [{"message": {"content": "面积420㎡，已经核验。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    with pytest.raises(agent.provider.ProviderError):
        agent.handle_chat(db, _queued_chat(db, "为什么面积记录不一致，请比较上次记录？"))
    assert len(rounds) == 2


def _large_history(db, current_question="刚才客户1的下一步具体怎么安排？"):
    from app.agent_models import ChatMessage, Conversation
    from app.db import utcnow
    from datetime import timedelta
    conversation = Conversation(tenant_id="t1", property_id="p1", user_id="u1", title="长对话")
    db.add(conversation)
    db.flush()
    for index in range(14):
        role = "user" if index % 2 == 0 else "assistant"
        body = (f"第{index}轮讨论客户1的花园改善需求。" + "这是需从数据库重新查询的历史项目记录。" * 230 + "客户1下一步：周五确认到访时间。")
        db.add(ChatMessage(tenant_id="t1", property_id="p1", conversation_id=conversation.id, role=role,
            content=body, status="completed", created_at=utcnow() - timedelta(minutes=30-index),
            citations=[{"kind": "lead", "id": "l1", "label": "客户1"}] if role == "assistant" else []))
    db.commit()
    job = _queued_chat(db, current_question, conversation.id)
    return conversation, db.get(ChatMessage, job.payload["user_message_id"])


def test_long_conversation_has_a_total_budget_and_preserves_recent_customer_context(agent_data):
    from app import agent
    db, ctx = agent_data
    conversation, question = _large_history(db)
    history = agent._history(db, ctx, conversation, question)
    assert sum(len(item["content"]) for item in history) <= 6000
    assert 6 <= len(history) <= 8
    assert history[-1] == {"role": "user", "content": question.content}
    latest_answer = next(item["content"] for item in reversed(history) if item["role"] == "assistant")
    assert "客户1" in latest_answer and "周五确认到访时间" in latest_answer
    assert '"id":"l1"' in latest_answer or '"id": "l1"' in latest_answer
    assert any("历史片段" in item["content"] for item in history)


def test_max_length_current_question_stays_complete_inside_history_budget(agent_data):
    from app import agent
    db, ctx = agent_data
    current = "刚才客户1的需求请继续查询。" + "补充背景。" * 1000
    current = current[:5000]
    conversation, question = _large_history(db, current)
    history = agent._history(db, ctx, conversation, question)
    assert history[-1]["content"] == current
    assert sum(len(item["content"]) for item in history) <= 6000
    assert len(history) >= 6
    recent = next(item["content"] for item in reversed(history) if item["role"] == "assistant")
    assert "客户1" in recent and ('"id":"l1"' in recent or '"id": "l1"' in recent)


def test_trimmed_history_rechecks_uncited_tool_dependencies_before_reusing_text(agent_data):
    from app import agent
    from app.agent_models import ChatMessage, ToolCall
    db, ctx = agent_data
    inquiry = _pending_inquiry(db)
    conversation, question = _large_history(db)
    # A long recent answer displays only a fact citation but depended on CRM too.
    question.created_at = agent.utcnow()
    answer = ChatMessage(tenant_id="t1", property_id="p1", conversation_id=conversation.id, role="assistant",
        content="不可重用的私人客户需求。" * 500, status="completed", job_id="old_sensitive_job",
        created_at=question.created_at.replace(microsecond=max(0, question.created_at.microsecond-1)),
        citations=[{"kind": "fact", "id": "f1", "label": "面积", "revision": 3, "status": "verified", "valid": True}])
    db.add(answer)
    db.add(ToolCall(tenant_id="t1", property_id="p1", conversation_id=conversation.id, job_id="old_sensitive_job",
        name="get_project_overview", arguments={}, result_refs=[
            {"kind": "inquiry", "id": inquiry.id, "label": "待处理客户A"},
            {"kind": "page", "id": "p1", "label": "工作台", "page": "dashboard", "access_refs_version": 2}]))
    inquiry.consent = False
    db.commit()
    history = agent._history(db, ctx, conversation, question)
    assert sum(len(item["content"]) for item in history) <= 6000
    assert "不可重用的私人客户需求" not in json.dumps(history, ensure_ascii=False)


def test_short_history_keeps_full_recent_answer_and_question(agent_data):
    from app import agent
    from app.agent_models import ChatMessage
    db, ctx = agent_data
    first = _queued_chat(db, "客户1下一步是什么？")
    db.add(ChatMessage(tenant_id="t1", property_id="p1", conversation_id=first.payload["conversation_id"],
        role="assistant", content="客户1需要花园改善，下一步是周五确认到访时间。", status="completed",
        citations=[{"kind": "lead", "id": "l1", "label": "客户1"}]))
    db.commit()
    next_job = _queued_chat(db, "他刚才说的下一步？", first.payload["conversation_id"])
    question = db.get(ChatMessage, next_job.payload["user_message_id"])
    conversation = db.get(agent.Conversation, first.payload["conversation_id"])
    history = agent._history(db, ctx, conversation, question)
    assert len(history) == 3
    assert history[0]["content"] == "客户1下一步是什么？"
    assert history[1]["content"].startswith("客户1需要花园改善，下一步是周五确认到访时间。")
    assert "历史片段" not in history[1]["content"]
    assert history[2]["content"] == "他刚才说的下一步？"


@pytest.mark.parametrize(("question", "expected"), [
    ("当前项目面积是多少？", "qwen-flash"),
    ("刚才客户1的下一步要怎么安排？", "qwen-plus"),
])
def test_fast_query_model_only_routes_explicit_grounded_requests(agent_data, monkeypatch, question, expected):
    from app import agent
    from app.agent_models import ModelUsage
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    provider.settings.dashscope_chat_model = "qwen-flash"
    db, _ = agent_data
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append(kwargs)
        if messages[-1]["role"] != "tool":
            return {"choices": [{"message": {"tool_calls": [{"id": "customer", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}}], "usage": {}}
        return {"choices": [{"message": {"content": "依据实时项目记录回答。[S1]"}}], "usage": {"prompt_tokens": 20}}
    monkeypatch.setattr(provider, "chat", generate)
    job = _queued_chat(db, question)
    agent.handle_chat(db, job)
    assert all(call.get("model") == expected for call in outgoing)
    assert {usage.model for usage in db.scalars(select(ModelUsage).where(ModelUsage.job_id == job.id))} == {expected}


def test_example_summary_prompt_requires_example_label_and_metric_availability(agent_data, monkeypatch):
    from app import agent
    from app.core_models import Property
    db, _ = agent_data
    db.get(Property, "p1").is_test = True
    db.commit()
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.extend(messages)
        return {"choices": [{"message": {"content": "示例项目暂无已核验发布回执，传播指标不可用。[S2]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    agent.handle_chat(db, _queued_chat(db, "请完整汇总当前项目情况"))
    instruction = outgoing[0]["content"]
    assert "开头" in instruction and "示例项目" in instruction
    assert "必须交代" in instruction and "指标可用性" in instruction


@pytest.mark.parametrize(("question", "expected_tools"), [
    ("请完整汇总当前项目情况，包括资料、内容、客户与下一步。", ["get_project_overview", "get_missing_requirements", "list_priority_leads", "get_content_metrics"]),
    ("当前项目还缺什么资料？哪些问题会影响内容制作与发布？", ["get_missing_requirements"]),
    ("当前项目的关键客户是谁？请说明判断依据和下一步跟进动作。", ["list_priority_leads"]),
    ("根据当前项目记录，接下来最应该推进哪三件事？", ["get_project_overview", "get_missing_requirements", "list_priority_leads", "get_content_metrics"]),
    ("当前项目进展如何，下一步该怎么做？", ["get_project_overview", "get_missing_requirements", "list_priority_leads", "get_content_metrics"]),
])
def test_workspace_shortcuts_use_one_grounded_generation(agent_data, monkeypatch, question, expected_tools):
    from app import agent
    from app.agent_models import ToolCall
    from sqlalchemy import select
    db, _ = agent_data
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append(kwargs)
        return {"choices": [{"message": {"content": "依据已查询的项目记录说明下一步。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    job = _queued_chat(db, question)
    agent.handle_chat(db, job)
    assert len(outgoing) == 1
    assert [call.name for call in db.scalars(select(ToolCall).where(ToolCall.job_id == job.id).order_by(ToolCall.created_at))] == expected_tools


def test_named_customer_project_next_step_keeps_specific_query_tools(agent_data, monkeypatch):
    from app import agent
    db, _ = agent_data
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append(kwargs)
        if messages[-1]["role"] != "tool":
            return {"choices": [{"message": {"tool_calls": [{"id": "named_customer", "type": "function",
                "function": {"name": "list_priority_leads", "arguments": '{"query":"陈女士"}'}}]}}], "usage": {}}
        return {"choices": [{"message": {"content": "当前授权记录没有陈女士，需补充或确认该客户资料。"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    agent.handle_chat(db, _queued_chat(db, "陈女士在当前项目里，接下来应推进什么？"))
    assert len(outgoing) == 2 and outgoing[0].get("tools")


@pytest.mark.parametrize(("query", "semantic_key", "label", "value", "unit"), [
    ("户型", "layout", "卧室布局", "四室两厅", ""),
    ("价格", "price", "参考总价", "3980", "万元"),
])
def test_fixed_fact_queries_find_semantic_records_without_keyword_in_label_or_source(agent_data, query, semantic_key, label, value, unit):
    from app import agent
    from app.core_models import Fact
    db, ctx = agent_data
    db.add(Fact(id="fixed_alias", tenant_id="t1", property_id="p1", semantic_key=semantic_key,
                label=label, value=value, unit=unit, source="已核对资料", status="unverified", revision=4))
    db.commit()
    result = agent.execute_tool(db, ctx, "p1", "search_evidence", {"query": query})
    assert [match["id"] for match in result["data"]["matches"]] == ["fixed_alias"]
    assert result["sources"][0]["revision"] == 4 and result["sources"][0]["valid"] is False
    assert all(match.get('value')!='999' for match in result['data']['matches'])


@pytest.mark.parametrize("question", ["项目面积多少，面宽几米？", "当前项目面积多少，总价多少？"])
def test_multiple_fact_questions_keep_tool_planning_instead_of_dropping_second_fact(agent_data, monkeypatch, question):
    from app import agent
    db, _ = agent_data
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append(kwargs)
        if messages[-1]["role"] != "tool":
            return {"choices": [{"message": {"tool_calls": [{"id": "multiple_facts", "type": "function",
                "function": {"name": "get_project_overview", "arguments": "{}"}}]}}], "usage": {}}
        return {"choices": [{"message": {"content": "面积420㎡，其他事实当前未记录，需补充后查询。[S1]"}}], "usage": {}}
    monkeypatch.setattr(agent.provider, "chat", generate)
    agent.handle_chat(db, _queued_chat(db, question))
    assert len(outgoing) == 2 and outgoing[0].get("tools")


def test_model_projection_uses_chinese_business_states_without_mutating_guard_references(agent_data):
    from app import agent
    from app.core_models import Fact
    db, ctx = agent_data
    db.get(Fact, "f1").status = "unverified"
    db.commit()
    raw = agent.execute_tool(db, ctx, "p1", "get_project_overview", {})
    references = {}
    agent._register_sources(raw, references)
    projected = agent._model_tool_result("get_project_overview", raw, include_ids=False)
    assert projected["data"]["facts"][0]["status"] == "待核验"
    assert next(source for source in projected["sources"] if source["kind"] == "fact")["status"] == "待核验"
    assert raw["data"]["facts"][0]["status"] == "unverified"
    assert next(source for source in references.values() if source["kind"] == "fact")["status"] == "unverified"
    assert projected["data"]["facts"][0]["valid"] is False
    raw_leads = agent.execute_tool(db, ctx, "p1", "list_priority_leads", {})
    agent._register_sources(raw_leads, {})
    projected_leads = agent._model_tool_result("list_priority_leads", raw_leads, include_ids=False)
    assert [lead["stage"] for lead in projected_leads["data"]["leads"]] == ["已到访", "已预约"]
    assert all(lead["source_confidence"] == "未知" for lead in projected_leads["data"]["leads"])
    assert [lead["stage"] for lead in raw_leads["data"]["leads"]] == ["visit_completed", "appointment"]


def test_priority_customer_projection_marks_active_scope_and_keeps_unknown_metrics_null(agent_data):
    from app import agent
    from app.business_models import Lead
    db, ctx = agent_data
    db.get(Lead, "l2").stage = "won"
    db.commit()
    raw = agent.execute_tool(db, ctx, "p1", "list_priority_leads", {})
    agent._register_sources(raw, {})
    projected = agent._model_tool_result("list_priority_leads", raw, include_ids=False)
    assert [lead["name"] for lead in projected["data"]["leads"]] == ["客户1"]
    scope = projected["data"]["scope"]
    assert "活跃" in scope and "授权" in scope and "未列出" in scope and "不代表" in scope
    metrics = agent.execute_tool(db, ctx, "p1", "get_content_metrics", {})
    agent._register_sources(metrics, {})
    model_metrics = agent._model_tool_result("get_content_metrics", metrics, include_ids=False)
    assert model_metrics["data"]["metrics"]["views"] is None
    assert model_metrics["data"]["metrics"]["qualified"] is None


def test_unique_named_customer_followup_is_prefetched_once_and_keeps_plus_model(agent_data, monkeypatch):
    from app import agent
    from app.agent_models import ToolCall
    from app.business_models import Inquiry, Lead
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    provider.settings.dashscope_chat_model = "qwen-flash"
    db, _ = agent_data
    db.get(Lead, "l1").name = "陈女士"
    db.get(Inquiry, "i1").name = "陈女士"
    db.commit()
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append((list(messages), kwargs))
        if messages[-1]["role"] != "tool":
            return {"choices": [{"message": {"tool_calls": [{"id": "customer_read", "type": "function",
                "function": {"name": "get_customer", "arguments": '{"lead_id":"l1"}'}}]}}], "usage": {}}
        assert "周五确认到访时间" in messages[-1]["content"]
        return {"choices": [{"message": {"content": "陈女士需要明确花园改善安排，最新跟进是周五确认到访时间。[S1]"}}], "usage": {}}
    monkeypatch.setattr(provider, "chat", generate)
    job = _queued_chat(db, "陈女士现在最需要解决的具体问题是什么？请结合最近跟进记录说明并附来源。")
    agent.handle_chat(db, job)
    assert len(outgoing) == 1
    assert outgoing[0][1].get("model") == "qwen-plus" and not outgoing[0][1].get("tools")
    calls = db.scalars(select(ToolCall).where(ToolCall.job_id == job.id)).all()
    assert [call.name for call in calls] == ["get_customer"]
    assert calls[0].arguments == {"lead_id": "l1"}
    assert any(ref["kind"] == "lead" and ref["id"] == "l1" for ref in calls[0].result_refs)


@pytest.mark.parametrize("boundary", ["same_name", "no_consent", "other_sales", "other_project", "multiple_names"])
def test_named_customer_fast_query_does_not_resolve_ambiguous_or_unauthorized_scope(agent_data, monkeypatch, boundary):
    from app import agent
    from app.business_models import Inquiry, Lead
    from app.core_models import Membership
    from sqlalchemy import select
    provider = _configure_provider(monkeypatch)
    db, _ = agent_data
    customer = db.get(Lead, "l1")
    customer.name = "陈女士"
    customer.need = "只有授权身份才可读取的私人家庭资料"
    inquiry = db.get(Inquiry, "i1")
    inquiry.name = "陈女士"
    if boundary == "same_name":
        db.add(Inquiry(id="i_duplicate", tenant_id="t1", property_id="p1", name="陈女士",
                       owner_id="u1", contactable=True, consent=True, qualified=True))
        db.flush()
        db.add(Lead(id="l_duplicate", tenant_id="t1", property_id="p1", inquiry_id="i_duplicate",
                    name="陈女士", owner_id="u1", stage="qualified", need="另一位同名客户，不等于同一个人"))
    elif boundary == "no_consent":
        inquiry.consent = False
    elif boundary == "other_sales":
        db.scalar(select(Membership).where(Membership.user_id == "u1")).role = "sales"
        customer.owner_id = "u2"
        inquiry.owner_id = "u2"
    db.commit()
    question = "陈女士最近跟进记录和下一步是什么？"
    if boundary == "other_project":
        question = "请去另一个项目查询陈女士最近跟进记录和下一步。"
    elif boundary == "multiple_names":
        question = "陈女士和客户2最近跟进记录与下一步是什么？"
    outgoing = []
    def generate(messages, **kwargs):
        outgoing.append((list(messages), kwargs))
        if messages[-1]["role"] != "tool":
            return {"choices": [{"message": {"tool_calls": [{"id": "query_names", "type": "function",
                "function": {"name": "list_priority_leads", "arguments": '{"query":"陈女士"}'}}]}}], "usage": {}}
        return {"choices": [{"message": {"content": "请确认可查阅的项目与具体客户，不能自动合并同名客户。"}}], "usage": {}}
    monkeypatch.setattr(provider, "chat", generate)
    agent.handle_chat(db, _queued_chat(db, question))
    assert len(outgoing) == 2 and outgoing[0][1].get("tools")
    if boundary in {"no_consent", "other_sales"}:
        assert "只有授权身份才可读取的私人家庭资料" not in json.dumps(outgoing, ensure_ascii=False)
