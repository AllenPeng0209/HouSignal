"""Interactive queue isolation and permission-fenced progress regressions.

The external handler boundary alone is replaced by a controlled slow handler.
Queue rows, leases, sessions, source permissions and transactions are real.
"""
from datetime import datetime, timedelta, timezone
import threading
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.job_models import Job
from app.security import context_for_user
from app.jobs import enqueue_job
from app import business_models, agent_models


def make_factory(tmp_path, *, autocommit=False):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}", connect_args={"check_same_thread": False},
                           **({"isolation_level": "AUTOCOMMIT"} if autocommit else {}))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(id="u", email="worker@example.com", password_hash="x", name="测试人"),
                    Tenant(id="t", name="合成测试组织")])
        db.flush()
        db.add(Membership(user_id="u", tenant_id="t", role="admin", active=True))
        db.add(Property(id="p", tenant_id="t", name="合成排队项目", city="上海", is_test=True))
        db.commit()
    return engine, factory


@pytest.fixture
def queue_factory(tmp_path):
    engine, factory = make_factory(tmp_path)
    yield factory
    engine.dispose()


def add_job(factory, kind="chat", **values):
    with factory() as db:
        job = enqueue_job(db, context_for_user(db, "u", "t"), "p", kind, {})
        for key, value in values.items():
            setattr(job, key, value)
        db.commit()
        return job.id


def test_chat_lane_does_not_wait_for_older_background_job(queue_factory):
    # A regression to the shared FIFO must select the old media job and fail.
    from app.worker import claim_job
    add_job(queue_factory, "extract_asset", created_at=datetime.now(timezone.utc)-timedelta(minutes=1))
    chat_id = add_job(queue_factory)
    with queue_factory() as db:
        claimed = claim_job(db, kinds=("chat",))
        assert claimed.id == chat_id
        assert claim_job(db, kinds=("chat",)) is None
        assert db.scalar(select(Job).where(Job.kind == "extract_asset")).status == "queued"


def test_job_attempt_timestamps_preserve_first_start_and_record_terminal(queue_factory):
    from app.worker import claim_job, complete_job
    jid = add_job(queue_factory)
    start = datetime.now(timezone.utc)
    with queue_factory() as db:
        first = claim_job(db, now=start, lease_seconds=1)
        assert first.started_at == start.replace(tzinfo=None)
        assert first.attempt_started_at == start.replace(tzinfo=None)
        second = claim_job(db, now=start+timedelta(seconds=2), lease_seconds=300)
        assert second.started_at == start.replace(tzinfo=None)
        assert second.attempt_started_at == (start+timedelta(seconds=2)).replace(tzinfo=None)
        assert complete_job(db, jid, second.lease_token, second.attempt, {"answer": "已完成"})
        assert db.get(Job, jid).completed_at is not None
        assert db.get(Job, jid).progress == 1


def test_partial_progress_is_visible_without_committing_handler_business_writes(queue_factory):
    from app.worker import claim_job, execute_job
    jid = add_job(queue_factory)
    with queue_factory() as db:
        job = claim_job(db)
        ticket = (jid, job.lease_token, job.attempt)
    observed = []

    def controlled_handler(db, job):
        db.add(Fact(id="uncommitted", tenant_id="t", property_id="p", semantic_key="area", label="面积", value="329", unit="㎡"))
        db.info["job_progress"](progress=0.55, stage="正在生成回答", preview_text="当前项目", access_refs=[])
        with queue_factory() as reader:
            running = reader.get(Job, jid)
            observed.append((running.status, running.result["preview_text"], running.first_token_at))
            assert reader.get(Fact, "uncommitted") is None
        # The fenced failure must roll the business record back and remove preview.
        raise ValueError("受控失败")

    assert not execute_job(*ticket, session_factory=queue_factory, handlers={"chat": controlled_handler})
    assert observed[0][0:2] == ("running", "当前项目")
    assert observed[0][2] is not None
    with queue_factory() as db:
        assert db.get(Fact, "uncommitted") is None
        assert db.get(Job, jid).result == {}
        assert db.get(Job, jid).completed_at is not None


def test_first_preview_timestamp_stays_first_and_preview_never_completes_message(queue_factory):
    from app.worker import claim_job, publish_progress, complete_job
    from app.agent_models import ChatMessage, Conversation
    jid = add_job(queue_factory)
    with queue_factory() as db:
        db.add(Conversation(id="conversation", tenant_id="t", property_id="p", user_id="u", title="合成查询"))
        db.add(ChatMessage(id="pending", tenant_id="t", property_id="p", conversation_id="conversation",
                           role="assistant", content="", status="queued", job_id=jid, citations=[]))
        db.commit()
        ticket = claim_job(db)
        token, attempt = ticket.lease_token, ticket.attempt
    publish_progress(jid, token, attempt, session_factory=queue_factory, progress=.25, stage="正在查询资料")
    with queue_factory() as db:
        assert db.get(Job, jid).first_token_at is None
    publish_progress(jid, token, attempt, session_factory=queue_factory, progress=.55, stage="正在生成回答", preview_text="第一段", access_refs=[])
    with queue_factory() as db:
        first = db.get(Job, jid).first_token_at
        assert first is not None
    publish_progress(jid, token, attempt, session_factory=queue_factory, progress=.65, stage="正在生成回答", preview_text="第一段和第二段", access_refs=[])
    with queue_factory() as db:
        assert db.get(Job, jid).first_token_at == first
        assert db.get(Job, jid).result["preview_text"] == "第一段和第二段"
        assert db.get(ChatMessage, "pending").status == "queued"
        assert db.get(ChatMessage, "pending").content == ""
        assert complete_job(db, jid, token, attempt, {"message_id": "pending"})
        assert db.get(Job, jid).result == {"message_id": "pending"}


def test_old_attempt_cannot_publish_after_job_has_been_reclaimed(queue_factory):
    from app.worker import claim_job, publish_progress, LostLease
    jid = add_job(queue_factory)
    with queue_factory() as db:
        first = claim_job(db, lease_seconds=1)
        token, attempt = first.lease_token, first.attempt
        second = claim_job(db, now=datetime.now(timezone.utc)+timedelta(seconds=2))
        assert second.attempt == 2
    with pytest.raises(LostLease):
        publish_progress(jid, token, attempt, session_factory=queue_factory, progress=.5,
                         stage="正在生成回答", preview_text="失效结果", access_refs=[])
    with queue_factory() as db:
        assert db.get(Job, jid).result == {}
        assert db.get(Job, jid).first_token_at is None


def test_large_project_preview_checks_every_source_without_truncating(queue_factory):
    from fastapi import HTTPException
    from app.worker import claim_job, publish_progress
    jid = add_job(queue_factory)
    refs = [{"kind": "fact", "id": f"f{n}", "revision": 1, "status": "verified", "valid": True} for n in range(101)]
    with queue_factory() as db:
        db.add_all([Fact(id=f"f{n}", tenant_id="t", property_id="p", semantic_key=f"custom{n}",
                        label=f"合成事实{n}", value="已核验", source="合成测试来源", status="verified", revision=1)
                    for n in range(101)])
        db.commit()
        ticket = claim_job(db)
        token, attempt = ticket.lease_token, ticket.attempt
    publish_progress(jid, token, attempt, session_factory=queue_factory, progress=.5,
                     stage="正在生成回答", preview_text="合法项目汇总", access_refs=refs)
    with queue_factory() as db:
        assert len(db.get(Job, jid).result["access_refs"]) == 101
        db.get(Fact, "f100").revision = 2
        db.commit()
    with pytest.raises(HTTPException) as exc:
        publish_progress(jid, token, attempt, session_factory=queue_factory, progress=.6,
                         stage="正在生成回答", preview_text="失效资料", access_refs=refs)
    assert exc.value.status_code == 409


def test_progress_immediately_rejects_cancelled_lease(queue_factory):
    from app.worker import claim_job, execute_job, LostLease
    from app.jobs import cancel_job
    jid = add_job(queue_factory)
    with queue_factory() as db:
        job = claim_job(db)
        ticket = (jid, job.lease_token, job.attempt)
    rejected = []

    def controlled_handler(db, job):
        with queue_factory() as cancellation:
            cancel_job(jid, context_for_user(cancellation, "u", "t"), cancellation)
        with pytest.raises(LostLease):
            db.info["job_progress"](progress=0.6, stage="正在生成回答", preview_text="晚到回答", access_refs=[])
        rejected.append(True)
        return {"answer": "晚到结果"}

    assert not execute_job(*ticket, session_factory=queue_factory, handlers={"chat": controlled_handler})
    assert rejected == [True]
    with queue_factory() as db:
        assert db.get(Job, jid).status == "cancelled"
        assert not db.get(Job, jid).result.get("preview_text")


@pytest.mark.parametrize("revoke", ["membership", "fact_revision"])
def test_progress_rechecks_current_permission_and_fact_revision(queue_factory, revoke):
    from app.worker import claim_job, execute_job
    jid = add_job(queue_factory)
    with queue_factory() as db:
        db.add(Fact(id="source", tenant_id="t", property_id="p", semantic_key="area", label="面积", value="329", unit="㎡", source="合成测试已核验资料", status="verified", revision=1))
        db.commit()
        job = claim_job(db)
        ticket = (jid, job.lease_token, job.attempt)
    published = []

    def controlled_handler(db, job):
        db.info["job_progress"](progress=0.4, stage="正在生成回答", preview_text="329㎡", access_refs=[{"kind": "fact", "id": "source", "revision": 1, "status": "verified", "valid": True}])
        published.append("before_revocation")
        with queue_factory() as updater:
            if revoke == "membership":
                updater.scalar(select(Membership).where(Membership.user_id == "u")).active = False
            else:
                updater.get(Fact, "source").revision = 2
            updater.commit()
        db.info["job_progress"](progress=0.6, stage="正在生成回答", preview_text="不应显示的旧资料", access_refs=[{"kind": "fact", "id": "source", "revision": 1, "status": "verified", "valid": True}])
        published.append("after_revocation")
        return {}

    assert not execute_job(*ticket, session_factory=queue_factory, handlers={"chat": controlled_handler})
    assert published == ["before_revocation"]
    with queue_factory() as db:
        assert db.get(Job, jid).result == {}
        assert db.get(Job, jid).status == "failed"


def test_worker_reserves_two_interactive_slots_and_two_background_slots(tmp_path):
    # Independent autocommit reads avoid SQLite's writer-upgrade limitation in
    # this scheduling-only test. Transaction fencing is exercised above and by
    # the PostgreSQL acceptance flow, rather than inferred from SQLite threads.
    from app.worker import run_worker
    engine, factory = make_factory(tmp_path, autocommit=True)
    for _ in range(5):
        add_job(factory, "chat")
        add_job(factory, "extract_asset")
    stop = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = {"chat": 0, "extract_asset": 0}
    maximum = {"chat": 0, "extract_asset": 0}
    entered = threading.Event()

    def controlled_handler(db, job):
        with lock:
            active[job.kind] += 1
            maximum[job.kind] = max(maximum[job.kind], active[job.kind])
            if active == {"chat": 2, "extract_asset": 2}:
                entered.set()
        assert release.wait(3)
        with lock:
            active[job.kind] -= 1
        return {"done": True}

    thread = threading.Thread(target=run_worker, args=(stop,), kwargs={"session_factory": factory, "handlers": {"chat": controlled_handler, "extract_asset": controlled_handler}})
    thread.start()
    try:
        assert entered.wait(2), "后台任务阻塞了交互队列或没有独立执行槽"
        release.set()
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            with factory() as db:
                if all(j.status == "succeeded" for j in db.scalars(select(Job)).all()):
                    break
            time.sleep(.02)
        else:
            pytest.fail("受控任务未全部完成")
        assert maximum == {"chat": 2, "extract_asset": 2}
    finally:
        release.set()
        stop.set()
        thread.join(timeout=3)
        engine.dispose()
    assert not thread.is_alive()
