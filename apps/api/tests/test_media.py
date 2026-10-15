"""Durable execution, scoped retrieval, and actual media processing tests.

These tests catch lease races, unauthorized model processing, and fake render
success; external speech/OCR responses alone are replaced at their boundary.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from app.db import Base
from app.core_models import User, Tenant, Membership, Property, Asset, Fact
from app.job_models import Job
from app.security import context_for_user
from app.jobs import enqueue_job, scoped_job
from app.config import settings
from pathlib import Path
import hashlib
import json
import subprocess
from PIL import Image


def put_asset(db, root, name, data, mime="text/plain", kind="text", authorized=False):
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    target.write_bytes(data)
    asset = Asset(tenant_id="t", property_id="p", name=name, kind=kind,
                  storage_key=name, mime_type=mime, sha256=hashlib.sha256(data).hexdigest(),
                  byte_size=len(data), rights="authorized", rights_evidence="仅供合成媒体验收",
                  origin_type="test", metadata_json={"model_processing_authorized": authorized})
    db.add(asset)
    db.commit()
    return asset


def extraction_job(db, asset):
    job = enqueue_job(db, context_for_user(db, "u", "t"), "p", "extract_asset", {"asset_id":asset.id})
    db.commit()
    return job


def test_text_extraction_records_candidates_without_verifying(media_db, tmp_path, monkeypatch):
    from app.media import handle_extract
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    media_db.add(Fact(id="area", tenant_id="t", property_id="p", semantic_key="area", label="面积",
                      value="", unit="㎡", status="unverified"))
    media_db.commit()
    asset = put_asset(media_db, tmp_path, "facts.txt", "建筑面积：420㎡\n客厅面宽8.6米".encode())
    result = handle_extract(media_db, extraction_job(media_db, asset))
    media_db.commit()
    assert "420" in media_db.get(Asset, asset.id).extracted_text
    candidates = media_db.get(Asset, asset.id).metadata_json["fact_candidates"]
    assert any(c["value"] == "420" for c in candidates)
    assert media_db.get(Fact, "area").status == "unverified"
    assert result["status"] == "extracted"


def test_pdf_extraction_keeps_page_source(media_db, tmp_path, monkeypatch):
    from app.media import handle_extract
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    import io
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject("/Type"):NameObject("/Font"), NameObject("/Subtype"):NameObject("/Type1"), NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 100 Td (Area: 420 sqm) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    asset = put_asset(media_db, tmp_path, "facts.pdf", out.getvalue(), "application/pdf", "document")
    handle_extract(media_db, extraction_job(media_db, asset))
    media_db.commit()
    assert "420" in asset.extracted_text
    assert asset.metadata_json["text_pages"][0]["page"] == 1
    assert asset.metadata_json["fact_candidates"][0]["page"] == 1


def test_image_model_processing_requires_explicit_authorization(media_db, tmp_path, monkeypatch):
    from app.media import handle_extract
    import app.provider as provider
    import io
    out = io.BytesIO()
    Image.new("RGB", (64,64), "white").save(out, format="PNG")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(provider, "cloud_chat", lambda *a, **k: pytest.fail("未授权照片不能发送外部模型"))
    asset = put_asset(media_db, tmp_path, "room.png", out.getvalue(), "image/png", "image")
    result = handle_extract(media_db, extraction_job(media_db, asset))
    assert result["status"] == "waiting_authorization"
    assert asset.extracted_text == ""


def test_path_traversal_asset_never_opens_outside_storage(media_db, tmp_path, monkeypatch):
    from app.media import handle_extract
    monkeypatch.setattr(settings, "storage_root", tmp_path / "private")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    asset = put_asset(media_db, tmp_path / "private", "safe.txt", b"safe")
    asset.storage_key = "../secret.txt"
    media_db.commit()
    with pytest.raises(ValueError):
        handle_extract(media_db, extraction_job(media_db, asset))


def test_changed_private_bytes_are_rejected_before_extraction(media_db,tmp_path,monkeypatch):
    from app.media import handle_extract
    monkeypatch.setattr(settings,"storage_root",tmp_path)
    asset=put_asset(media_db,tmp_path,"original.txt",b"original area")
    (tmp_path/asset.storage_key).write_bytes(b"replaced data")
    with pytest.raises(ValueError,match="校验"):
        handle_extract(media_db,extraction_job(media_db,asset))
    assert asset.extracted_text==""


def test_render_creates_decodable_mp4_and_frozen_snapshot(media_db, tmp_path, monkeypatch):
    from app.media import handle_render, validate_render_snapshot
    from app.business_models import ContentVersion
    import io
    out = io.BytesIO()
    Image.new("RGB", (320,180), "#99b8d1").save(out, format="JPEG")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    asset = put_asset(media_db, tmp_path, "synthetic.jpg", out.getvalue(), "image/jpeg", "image")
    fact = Fact(id="verified-area", tenant_id="t", property_id="p", semantic_key="area",
                label="面积", value="420", unit="㎡", source="合成验收资料第1页", status="verified", revision=1)
    media_db.add(fact)
    content = ContentVersion(id="video", tenant_id="t", property_id="p", series_id="series", version=1,
             topic="合成媒体技术验收", script="本视频仅供测试，面积420平方米。", fact_refs=[{"id":fact.id,"revision":1}],
             storyboard=[{"asset_id":asset.id,"duration":15,"caption":"合成媒体测试 · 420㎡"}],
             channel="抖音", cover_title="合成媒体验收", aspect_ratio="16:9", subtitle_style="minimal", created_by="u")
    media_db.add(content)
    media_db.commit()
    job = enqueue_job(media_db, context_for_user(media_db, "u", "t"), "p", "render_content", {"content_version_id":"video"})
    media_db.commit()
    result = handle_render(media_db, job)
    media_db.commit()
    rendered = media_db.get(Asset, result["render_asset_id"])
    path = tmp_path / rendered.storage_key
    probe = subprocess.run([settings.ffprobe_binary,"-v","error","-show_entries","format=duration:stream=codec_name","-of","json",str(path)],capture_output=True,text=True,check=True)
    info = json.loads(probe.stdout)
    assert 14.9 <= float(info["format"]["duration"]) <= 15.2
    assert any(s["codec_name"] == "h264" for s in info["streams"])
    assert validate_render_snapshot(media_db, content, media_db.get(Property,"p"))
    assert result["subtitle_asset_id"] and result["cover_asset_id"]
    fact.revision = 2
    media_db.commit()
    assert not validate_render_snapshot(media_db, content, media_db.get(Property,"p"))


def test_render_rejects_template_on_real_property(media_db, tmp_path, monkeypatch):
    from app.media import handle_render
    from app.business_models import ContentVersion
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    asset = put_asset(media_db, tmp_path, "fake.jpg", b"not-a-photo", "image/jpeg", "image")
    asset.origin_type = "template"
    project = media_db.get(Property,"p")
    project.is_test = False
    fact = Fact(id="vf", tenant_id="t", property_id="p", semantic_key="area", label="面积",value="420",unit="㎡",source="面积文件第1页",status="verified",revision=1)
    media_db.add(fact)
    content = ContentVersion(id="invalid", tenant_id="t", property_id="p", series_id="s", version=1,
        topic="测试",script="420平方米",fact_refs=[{"id":"vf","revision":1}],
        storyboard=[{"asset_id":asset.id,"duration":15,"caption":"测试"}],created_by="u")
    media_db.add(content)
    media_db.commit()
    job = enqueue_job(media_db,context_for_user(media_db,"u","t"),"p","render_content",{"content_version_id":"invalid"})
    media_db.commit()
    with pytest.raises(ValueError, match="真实"):
        handle_render(media_db,job)


def test_authorized_ocr_records_real_response_as_unverified_draft(media_db,tmp_path,monkeypatch):
    from app.media import handle_extract
    from app.agent_models import ModelUsage
    import app.provider as provider
    import io
    out=io.BytesIO()
    Image.new("RGB",(1800,100),"white").save(out,format="PNG")
    monkeypatch.setattr(settings,"storage_root",tmp_path)
    def external_ocr(messages,**kwargs):
        assert kwargs["model"]==settings.dashscope_vision_model
        url=messages[1]["content"][1]["image_url"]["url"]
        image=Image.open(io.BytesIO(__import__('base64').b64decode(url.split(',')[1])))
        assert image.width<=1600
        return {"model":"qwen-vl-plus","choices":[{"message":{"role":"assistant","content":"建筑面积420平方米"}}],"usage":{"prompt_tokens":12,"completion_tokens":6}}
    monkeypatch.setattr(provider,"cloud_chat",external_ocr)
    asset=put_asset(media_db,tmp_path,"floorplan.png",out.getvalue(),"image/png","image",True)
    handle_extract(media_db,extraction_job(media_db,asset))
    media_db.commit()
    assert asset.extracted_text=="建筑面积420平方米"
    assert asset.metadata_json["fact_candidates"][0]["status"]=="unverified"
    assert media_db.scalar(select(ModelUsage)).input_tokens==12


def test_asr_sends_mono16k_data_url_and_persists_transcript(media_db,tmp_path,monkeypatch):
    from app.media import handle_extract
    import app.provider as provider
    import io, wave, struct, math, base64
    buffer=io.BytesIO()
    with wave.open(buffer,"wb") as sound:
        sound.setnchannels(2)
        sound.setsampwidth(2)
        sound.setframerate(44100)
        frames=b"".join(struct.pack("<hh",int(2000*math.sin(i*0.05)),int(2000*math.sin(i*0.05))) for i in range(44100))
        sound.writeframes(frames)
    monkeypatch.setattr(settings,"storage_root",tmp_path)
    def external_asr(messages,**kwargs):
        assert kwargs["model"]==settings.dashscope_asr_model
        audio=messages[0]["content"][0]
        assert audio["type"]=="input_audio"
        uri=audio["input_audio"]["data"]
        assert uri.startswith("data:audio/wav;base64,")
        with wave.open(io.BytesIO(base64.b64decode(uri.split(',')[1]))) as proxy:
            assert proxy.getnchannels()==1 and proxy.getframerate()==16000
        return {"model":"qwen3-asr-flash","choices":[{"message":{"role":"assistant","content":"这是合成音频技术测试。"}}],"usage":{"prompt_tokens":25,"completion_tokens":10}}
    monkeypatch.setattr(provider,"cloud_chat",external_asr)
    asset=put_asset(media_db,tmp_path,"synthetic.wav",buffer.getvalue(),"audio/wav","audio",True)
    result=handle_extract(media_db,extraction_job(media_db,asset))
    media_db.commit()
    assert result["status"]=="extracted"
    assert asset.extracted_text=="这是合成音频技术测试。"
    assert asset.metadata_json["text_pages"][0]["start_second"]==0


def test_real_video_trim_renders_silent_source_with_subtitles(media_db,tmp_path,monkeypatch):
    from app.media import handle_render
    from app.business_models import ContentVersion
    monkeypatch.setattr(settings,"storage_root",tmp_path)
    source=tmp_path/"source.mp4"
    subprocess.run([settings.ffmpeg_binary,"-hide_banner","-loglevel","error","-y","-f","lavfi","-i","color=c=blue:s=320x180:r=25",
                    "-t","20","-c:v","libx264","-pix_fmt","yuv420p",str(source)],check=True)
    asset=put_asset(media_db,tmp_path,"video.mp4",source.read_bytes(),"video/mp4","video")
    media_db.add(Fact(id="video-fact",tenant_id="t",property_id="p",semantic_key="area",label="面积",value="420",unit="㎡",source="合成测试资料第1页",status="verified",revision=1))
    content=ContentVersion(id="trim",tenant_id="t",property_id="p",series_id="s",version=1,
        topic="合成视频剪辑验收",script="合成媒体测试。",fact_refs=[{"id":"video-fact","revision":1}],
        storyboard=[{"asset_id":asset.id,"start":2,"duration":15,"caption":"仅供技术验收"}],
        aspect_ratio="16:9",created_by="u")
    media_db.add(content)
    media_db.commit()
    job=enqueue_job(media_db,context_for_user(media_db,"u","t"),"p","render_content",{"content_version_id":"trim"})
    media_db.commit()
    result=handle_render(media_db,job)
    media_db.commit()
    rendered=media_db.get(Asset,result["render_asset_id"])
    info=json.loads(subprocess.run([settings.ffprobe_binary,"-v","error","-show_entries","stream=codec_name:format=duration","-of","json",str(tmp_path/rendered.storage_key)],capture_output=True,text=True,check=True).stdout)
    assert {s["codec_name"] for s in info["streams"]}=={"h264","aac"}
    assert 14.9<float(info["format"]["duration"])<15.2
    assert rendered.metadata_json["watermarked"] is True


@pytest.fixture
def media_db(tmp_path):
    from app import business_models, agent_models
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add_all([User(id="u", email="media@example.com", password_hash="x", name="测试人"),
                Tenant(id="t", name="测试组织"), Tenant(id="other", name="另一组织")])
    db.flush()
    db.add(Membership(user_id="u", tenant_id="t", role="admin", active=True))
    db.add(Property(id="p", tenant_id="t", name="合成媒体验收项目", city="上海", is_test=True,
                    marketing_confirmed=True, marketing_evidence="测试用途授权"))
    db.add(Property(id="other-p", tenant_id="other", name="隔离项目", city="北京"))
    db.commit()
    yield db
    db.close()
    engine.dispose()


def test_enqueue_is_atomic_with_callers_transaction(media_db):
    ctx = context_for_user(media_db, "u", "t")
    job = enqueue_job(media_db, ctx, "p", "extract_asset", {"asset_id":"a"})
    assert job.id
    media_db.rollback()
    assert media_db.scalar(select(Job)) is None


def test_job_retrieval_rejects_other_tenant(media_db):
    media_db.add(Job(id="foreign", tenant_id="other", property_id="other-p", user_id="u", kind="chat"))
    media_db.commit()
    ctx = context_for_user(media_db, "u", "t")
    with pytest.raises(HTTPException) as exc:
        scoped_job(media_db, ctx, "foreign")
    assert exc.value.status_code == 404


def test_expired_attempt_cannot_complete_or_persist_results(media_db):
    from app.worker import claim_job, complete_job
    ctx = context_for_user(media_db, "u", "t")
    job = enqueue_job(media_db, ctx, "p", "extract_asset", {})
    media_db.commit()
    first = claim_job(media_db, lease_seconds=1)
    token, attempt = first.lease_token, first.attempt
    second = claim_job(media_db, now=datetime.now(timezone.utc) + timedelta(seconds=2))
    assert second.attempt == 2 and second.lease_token != token
    media_db.add(Asset(id="late", tenant_id="t", property_id="p", name="late", kind="text",
                       storage_key="late.txt", mime_type="text/plain", sha256="0"*64, byte_size=1))
    assert complete_job(media_db, job.id, token, attempt, {"bad":"late"}) is False
    assert media_db.get(Asset, "late") is None
    assert media_db.get(Job, job.id).status == "running"


def test_cancelled_running_job_cannot_succeed(media_db):
    from app.worker import claim_job, complete_job
    ctx = context_for_user(media_db, "u", "t")
    j = enqueue_job(media_db, ctx, "p", "extract_asset", {})
    media_db.commit()
    claimed = claim_job(media_db)
    token, attempt = claimed.lease_token, claimed.attempt
    claimed.status = "cancelled"
    claimed.lease_token = None
    media_db.commit()
    assert not complete_job(media_db, j.id, token, attempt, {"success":True})
    assert media_db.get(Job, j.id).status == "cancelled"


def test_lease_expiry_stops_after_bounded_attempts(media_db):
    from app.worker import claim_job
    ctx = context_for_user(media_db, "u", "t")
    job = enqueue_job(media_db, ctx, "p", "extract_asset", {})
    media_db.commit()
    now = datetime.now(timezone.utc)
    for offset in (0, 2, 4):
        claimed = claim_job(media_db, now=now+timedelta(seconds=offset), lease_seconds=1)
        assert claimed
    assert claim_job(media_db, now=now+timedelta(seconds=6), lease_seconds=1) is None
    assert media_db.get(Job, job.id).status == "failed"


def test_worker_checks_revoked_membership_before_calling_handler(media_db):
    from app.worker import claim_job, execute_job
    ctx=context_for_user(media_db,"u","t")
    job=enqueue_job(media_db,ctx,"p","extract_asset",{})
    media_db.commit()
    ticket=claim_job(media_db)
    media_db.scalar(select(Membership).where(Membership.user_id=="u")).active=False
    media_db.commit()
    def must_not_run(*args):
        pytest.fail("权限被撤销的任务不能读取项目或调用模型")
    execute_job(ticket.id,ticket.lease_token,ticket.attempt,
                session_factory=sessionmaker(bind=media_db.bind,expire_on_commit=False),
                handlers={"extract_asset":must_not_run})
    media_db.expire_all()
    assert media_db.get(Job,job.id).status=="failed"
    assert "权限" in media_db.get(Job,job.id).error


def test_retryable_failure_is_delayed_and_bounded(media_db):
    from app.worker import claim_job, fail_job
    ctx=context_for_user(media_db,"u","t")
    job=enqueue_job(media_db,ctx,"p","extract_asset",{})
    media_db.commit()
    now=datetime.now(timezone.utc)
    for attempt in range(1,4):
        ticket=claim_job(media_db,now=now+timedelta(seconds=60*attempt))
        assert ticket.attempt==attempt
        fail_job(media_db,ticket.id,ticket.lease_token,ticket.attempt,"模型服务暂时不可用",retry=True)
        if attempt<3:
            assert claim_job(media_db) is None
    media_db.expire_all()
    assert media_db.get(Job,job.id).status=="failed"


def test_cancel_marks_pending_chat_response_and_fences_completion(media_db):
    from app.agent_models import Conversation, ChatMessage
    from app.jobs import cancel_job
    from app.worker import claim_job, complete_job
    ctx=context_for_user(media_db,"u","t")
    conversation=Conversation(id="chat",tenant_id="t",property_id="p",user_id="u",title="测试")
    media_db.add(conversation)
    job=enqueue_job(media_db,ctx,"p","chat",{"conversation_id":"chat"})
    media_db.add(ChatMessage(id="response",tenant_id="t",property_id="p",conversation_id="chat",
                            role="assistant",content="",citations=[],status="queued",job_id=job.id))
    media_db.commit()
    ticket=claim_job(media_db)
    token,attempt=ticket.lease_token,ticket.attempt
    cancel_job(job.id,ctx,media_db)
    assert media_db.get(ChatMessage,"response").status=="cancelled"
    assert not complete_job(media_db,job.id,token,attempt,{"text":"late"})


def test_cancel_cannot_overwrite_concurrently_completed_job(media_db):
    from app.jobs import cancel_job
    ctx=context_for_user(media_db,"u","t")
    job=enqueue_job(media_db,ctx,"p","extract_asset",{})
    media_db.commit()
    # Simulate a worker completion after this API Session loaded queued state.
    media_db.execute(update(Job).where(Job.id==job.id).values(status="succeeded",result={"asset_id":"finished"})
                     .execution_options(synchronize_session=False))
    media_db.commit()
    assert job.status=="queued"
    result=cancel_job(job.id,ctx,media_db)
    media_db.expire_all()
    assert media_db.get(Job,job.id).status=="succeeded"
    assert result["status"]=="succeeded"


def test_expired_worker_cannot_mark_job_failed_without_lease(media_db):
    from app.worker import claim_job, fail_job
    ctx=context_for_user(media_db,"u","t")
    job=enqueue_job(media_db,ctx,"p","extract_asset",{})
    media_db.commit()
    ticket=claim_job(media_db)
    ticket.lease_until=datetime.now(timezone.utc)-timedelta(seconds=1)
    media_db.commit()
    assert not fail_job(media_db,ticket.id,ticket.lease_token,ticket.attempt,"late failure")
    assert media_db.get(Job,job.id).status=="running"


def test_exhausted_chat_lease_marks_persisted_response_failed(media_db):
    from app.agent_models import Conversation, ChatMessage
    from app.worker import claim_job
    ctx=context_for_user(media_db,"u","t")
    media_db.add(Conversation(id="abandoned",tenant_id="t",property_id="p",user_id="u",title="中断测试"))
    job=enqueue_job(media_db,ctx,"p","chat",{})
    job.status="running"
    job.attempt=3
    job.lease_token="expired"
    job.lease_until=datetime.now(timezone.utc)-timedelta(seconds=1)
    media_db.add(ChatMessage(id="abandoned-message",tenant_id="t",property_id="p",conversation_id="abandoned",
            role="assistant",content="",citations=[],status="running",job_id=job.id))
    media_db.commit()
    assert claim_job(media_db) is None
    assert media_db.get(ChatMessage,"abandoned-message").status=="failed"


def test_durable_worker_modules_are_available():
    # Missing worker means accepted jobs can never complete after API restart.
    assert importlib.util.find_spec("app.job_models") is not None
    assert importlib.util.find_spec("app.jobs") is not None
    assert importlib.util.find_spec("app.worker") is not None
