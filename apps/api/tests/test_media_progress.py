"""Real handler checkpoints must describe work and fence sensitive next steps.

Only the external model is substituted; database validation, file decoding,
audio chunking and MP4 rendering execute as in the application.
"""
import hashlib
import io
import json
import subprocess
import wave

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import agent_models, business_models
from app.business import handle_generate
from app.business_models import ContentVersion
from app.config import settings
from app.core_models import Asset, Fact, Membership, Property, Tenant, User
from app.db import Base
from app.jobs import enqueue_job
from app.media import handle_extract, handle_render
from app.security import context_for_user


@pytest.fixture
def progress_db(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add_all([User(id="u", email="progress@example.com", password_hash="x", name="合成验收"),
                Tenant(id="t", name="合成验收组织")])
    db.flush()
    db.add(Membership(user_id="u", tenant_id="t", role="admin", active=True))
    db.add(Property(id="p", tenant_id="t", name="合成验收项目", city="上海", is_test=True,
                    marketing_confirmed=True, marketing_evidence="仅供技术验收"))
    db.add(Fact(id="f", tenant_id="t", property_id="p", semantic_key="area", label="建筑面积",
                value="420", unit="㎡", source="合成验收文件第1页", status="verified", revision=1))
    db.commit()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    yield db
    db.close()
    engine.dispose()


def put_asset(db, root, *, mime="image/png", kind="image", authorized=True):
    if kind == "audio":
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(16000)
            sound.writeframes(b"\0\0" * (16000 * 2))
        data, name = buffer.getvalue(), "synthetic.wav"
    else:
        buffer = io.BytesIO()
        Image.new("RGB", (160, 100), "#94aabc").save(buffer, format="PNG")
        data, name = buffer.getvalue(), "synthetic.png"
    (root / name).write_bytes(data)
    asset = Asset(id="a", tenant_id="t", property_id="p", name=name, kind=kind,
                  storage_key=name, mime_type=mime, sha256=hashlib.sha256(data).hexdigest(),
                  byte_size=len(data), rights="authorized", rights_evidence="合成验收素材授权",
                  origin_type="test", metadata_json={"model_processing_authorized": authorized})
    db.add(asset)
    db.commit()
    return asset


def job_for(db, kind, payload):
    job = enqueue_job(db, context_for_user(db, "u", "t"), "p", kind, payload)
    db.commit()
    return job


def script_result():
    return {"topic": "空间尺度", "script": "这是420平方米的合成验收空间。",
            "cover_title": "空间尺度", "fact_refs": [{"id": "f", "revision": 1}],
            "storyboard": [{"asset_id": "a", "duration": 15, "start": 0, "caption": "420㎡空间"}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 80}, "model": "qwen-plus"}


def model_response(text):
    return {"model": "model-fixture", "choices": [{"message": {"role": "assistant", "content": text},
                                                    "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10}}


def assert_checkpoints(events, fragments):
    stages = [event["stage"] for event in events]
    for fragment in fragments:
        assert any(fragment in stage for stage in stages), (fragment, stages)
    progress = [event["progress"] for event in events]
    assert progress == sorted(progress)
    assert all(0 <= value < 1 for value in progress)
    assert all("preview_text" not in event for event in events)


def test_generate_reports_validated_phases_without_exposing_json(progress_db, tmp_path, monkeypatch):
    # Removing post-model evidence verification/progress hides the important validation phase.
    import app.provider as provider
    put_asset(progress_db, tmp_path)
    events = []
    progress_db.info["job_progress"] = lambda **event: events.append(event)
    monkeypatch.setattr(provider, "generate_content", lambda data, **callbacks: script_result())
    result = handle_generate(progress_db, job_for(progress_db, "generate_content", {"topic_id": "space"}))
    assert progress_db.get(ContentVersion, result["content_version_id"]).script == "这是420平方米的合成验收空间。"
    assert_checkpoints(events, ["核对事实", "生成脚本", "检查生成依据", "保存草稿"])


def test_generate_checkpoint_cancellation_stops_before_model(progress_db, tmp_path, monkeypatch):
    import app.provider as provider
    put_asset(progress_db, tmp_path)
    class StopWork(RuntimeError):
        pass
    def checkpoint(**event):
        if "生成脚本" in event["stage"]:
            raise StopWork("任务已取消")
    progress_db.info["job_progress"] = checkpoint
    monkeypatch.setattr(provider, "generate_content", lambda data, **callbacks: pytest.fail("取消后不得发起模型调用"))
    with pytest.raises(StopWork):
        handle_generate(progress_db, job_for(progress_db, "generate_content", {"topic_id": "space"}))
    assert progress_db.scalar(select(ContentVersion)) is None


def test_ocr_reports_recognition_and_draft_validation(progress_db, tmp_path, monkeypatch):
    import app.provider as provider
    asset = put_asset(progress_db, tmp_path)
    events = []
    progress_db.info["job_progress"] = lambda **event: events.append(event)
    monkeypatch.setattr(provider, "cloud_chat", lambda *args, **kwargs: model_response("建筑面积420平方米"))
    result = handle_extract(progress_db, job_for(progress_db, "extract_asset", {"asset_id": asset.id}))
    assert result["status"] == "extracted"
    assert asset.metadata_json["extraction_verified"] is False
    assert_checkpoints(events, ["检查素材授权", "识别图片文字", "整理待核验资料"])


def test_asr_reports_each_real_chunk_without_reordering(progress_db, tmp_path, monkeypatch):
    import app.media as media
    import app.provider as provider
    asset = put_asset(progress_db, tmp_path, mime="audio/wav", kind="audio")
    events = []
    progress_db.info["job_progress"] = lambda **event: events.append(event)
    monkeypatch.setattr(media, "ASR_CHUNK_SECONDS", 1)
    monkeypatch.setattr(provider, "cloud_chat", lambda *args, **kwargs: model_response("合成语音验收。"))
    handle_extract(progress_db, job_for(progress_db, "extract_asset", {"asset_id": asset.id}))
    assert [page["start_second"] for page in asset.metadata_json["text_pages"]] == [0, 1]
    assert_checkpoints(events, ["转写第1/2段", "转写第2/2段", "整理待核验资料"])


def test_asr_authorization_withdrawal_stops_before_next_chunk(progress_db, tmp_path, monkeypatch):
    import app.media as media
    import app.provider as provider
    asset = put_asset(progress_db, tmp_path, mime="audio/wav", kind="audio")
    monkeypatch.setattr(media, "ASR_CHUNK_SECONDS", 1)
    def checkpoint(**event):
        if "转写第2/2段" in event["stage"]:
            asset.rights = "revoked"
            progress_db.flush()
    progress_db.info["job_progress"] = checkpoint
    def remote(messages, **kwargs):
        if asset.rights == "revoked":
            pytest.fail("撤销授权后不得发送下一段音频")
        return model_response("合成语音验收。")
    monkeypatch.setattr(provider, "cloud_chat", remote)
    with pytest.raises(ValueError, match="授权"):
        handle_extract(progress_db, job_for(progress_db, "extract_asset", {"asset_id": asset.id}))
    assert asset.extracted_text == ""


def test_render_reports_shots_and_verification_for_decodable_mp4(progress_db, tmp_path):
    put_asset(progress_db, tmp_path)
    content = ContentVersion(id="c", tenant_id="t", property_id="p", series_id="s", version=1,
                             topic="合成成片验收", script="420平方米的合成空间。",
                             fact_refs=[{"id": "f", "revision": 1}],
                             storyboard=[{"asset_id": "a", "duration": 7.5, "caption": "420㎡空间"},
                                         {"asset_id": "a", "duration": 7.5, "caption": "合成素材验收"}],
                             created_by="u", aspect_ratio="16:9")
    progress_db.add(content)
    progress_db.commit()
    events = []
    progress_db.info["job_progress"] = lambda **event: events.append(event)
    result = handle_render(progress_db, job_for(progress_db, "render_content", {"content_version_id": "c"}))
    rendered = progress_db.get(Asset, result["render_asset_id"])
    info = json.loads(subprocess.run([settings.ffprobe_binary, "-v", "error", "-show_entries",
                                    "stream=codec_name:format=duration", "-of", "json",
                                    str(tmp_path / rendered.storage_key)],
                                    capture_output=True, text=True, check=True).stdout)
    assert {stream["codec_name"] for stream in info["streams"]} == {"h264", "aac"}
    assert 14.9 < float(info["format"]["duration"]) < 15.2
    assert_checkpoints(events, ["核对事实", "制作第1/2镜头", "制作第2/2镜头", "校验成片", "保存成片"])

