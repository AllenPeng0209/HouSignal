"""Long-form rendering uses measured narration, changing shots and real audio.

External speech is replaced only at its provider boundary; FFmpeg decoding,
timing validation and rendered frame/audio checks run against actual files.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import wave

import pytest
from PIL import Image

from app.config import settings


def storyboard():
    return {"schema_version": 2, "target_seconds": 360,
            "chapters": [{"id": "opening", "title": "先看生活问题"},
                         {"id": "layout", "title": "再看空间组织"}],
            "shots": [{"id": f"shot-{i}", "chapter_id": "opening" if i < 10 else "layout",
                       "asset_id": "photo", "start": 0, "duration": 12,
                       "voiceover": f"第{i + 1}个生活场景，我们结合已核验资料分析空间使用方式。",
                       "caption": "结合资料理解空间", "visual_direction": "慢慢推进图片，突出当前问题"}
                      for i in range(30)]}


def test_longform_rejects_missing_narration_before_creating_media():
    from app.longform_media import normalize_storyboard
    board = storyboard()
    board["shots"][2]["voiceover"] = ""
    with pytest.raises(ValueError, match="口播"):
        normalize_storyboard(board)


def test_longform_preserves_thirty_shots_and_chapter_links():
    from app.longform_media import normalize_storyboard
    board = normalize_storyboard(storyboard())
    assert len(board["shots"]) == 30
    assert board["shots"][10]["chapter_id"] == "layout"
    assert board["voiceover"]["enabled"] is True


@pytest.mark.parametrize("mutation", ["twenty_shots", "single_long_hold", "unlinked_chapter", "ten_minutes_plus"])
def test_longform_blocks_short_or_padded_or_unlinked_plans(mutation):
    from app.longform_media import normalize_storyboard
    board = storyboard()
    if mutation == "twenty_shots":
        board["shots"] = board["shots"][:20]
    elif mutation == "single_long_hold":
        board["shots"][2]["duration"] = 60
    elif mutation == "unlinked_chapter":
        board["shots"][2]["chapter_id"] = "missing"
    else:
        board["target_seconds"] = 601
    with pytest.raises(ValueError):
        normalize_storyboard(board)


def test_narration_timing_rejects_padding_a_short_voice_to_five_minutes():
    from app.longform_media import validate_measured_duration
    with pytest.raises(ValueError, match="口播.*不足"):
        validate_measured_duration([2.0] * 30, 360)
    assert validate_measured_duration([12.0] * 30, 360) == 360


def test_sentence_subtitles_retain_actual_provider_boundary_times():
    from app.longform_media import sentence_cues
    cues = sentence_cues([
        {"start": 0.2, "end": 0.8, "text": "先看"},
        {"start": 0.8, "end": 1.9, "text": "生活场景。"},
        {"start": 2.1, "end": 2.8, "text": "再看空间。"},
    ], "先看生活场景。再看空间。", 3.2)
    assert cues == [{"start": 0.2, "end": 1.9, "text": "先看生活场景。"},
                    {"start": 2.1, "end": 2.8, "text": "再看空间。"}]


def test_subtitles_never_accept_a_boundary_starting_after_real_audio_end():
    from app.longform_media import sentence_cues
    with pytest.raises(ValueError, match="时间戳"):
        sentence_cues([{"start": 2.05, "end": 2.12, "text": "越界文字"}], "实际语音", 2.0)


def test_voice_configuration_is_validated_before_sending_narration():
    from app.longform_media import normalize_storyboard
    board = storyboard(); board["voiceover"] = {"voice": "not-a-standard-voice", "rate": "-100%"}
    with pytest.raises(ValueError, match="音色|语速"):
        normalize_storyboard(board)


def test_private_media_probe_rejects_playlist_indirection(tmp_path):
    from app.media import _probe
    segment = tmp_path / "segment.ts"
    subprocess.run([settings.ffmpeg_binary, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:r=25:d=1", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:v", "libx264", "-c:a", "aac", "-f", "mpegts", str(segment)], check=True)
    playlist = tmp_path / "input.m3u8"
    playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises(ValueError, match="解码|时长"):
        _probe(playlist)


def test_rendered_image_shot_moves_and_contains_actual_narration(tmp_path):
    from app.longform_media import render_shot
    # Checkerboard makes camera movement observable without relying on text.
    image = Image.new("RGB", (640, 360), "#25485c")
    for x in range(0, 640, 32):
        for y in range(0, 360, 32):
            if (x // 32 + y // 32) % 2:
                image.paste("#ddd7b9", (x, y, x + 32, y + 32))
    source = tmp_path / "source.jpg"
    image.save(source)
    voice = tmp_path / "voice.wav"
    subprocess.run([settings.ffmpeg_binary, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ac", "1", "-ar", "24000", str(voice)], check=True)
    output = tmp_path / "clip.mp4"
    shot = {"id": "s1", "caption": "空间的使用方式", "visual_direction": "轻推", "start": 0}
    render_shot(None, source, "image/jpeg", voice, 2.0,
                [{"start": 0.1, "end": 1.8, "text": "这是实际音频对应的字幕"}],
                shot, "生活场景", "合成验收", "空间示意", True, (320, 180), tmp_path, output)
    info = json.loads(subprocess.run([settings.ffprobe_binary, "-v", "error", "-show_entries",
                                     "format=duration:stream=codec_type", "-of", "json", str(output)],
                                    capture_output=True, text=True, check=True).stdout)
    assert 1.9 <= float(info["format"]["duration"]) <= 2.2
    assert any(stream["codec_type"] == "audio" for stream in info["streams"])
    decoded_audio = subprocess.run([settings.ffmpeg_binary, "-v", "error", "-i", str(output),
                                    "-vn", "-ac", "1", "-ar", "24000", "-f", "s16le", "-"],
                                   capture_output=True, check=True).stdout
    assert any(decoded_audio)
    frames = []
    for index, start in enumerate((0.04, 1.6)):
        frame = tmp_path / f"frame-{index}.png"
        subprocess.run([settings.ffmpeg_binary, "-v", "error", "-y", "-ss", str(start),
                        "-i", str(output), "-frames:v", "1", str(frame)], check=True)
        with Image.open(frame) as actual:
            frames.append(actual.crop((10, 55, 300, 105)).tobytes())
    assert hashlib.sha256(frames[0]).digest() != hashlib.sha256(frames[1]).digest()
    # The production watermark remains above the changing subtitle region.
    with Image.open(tmp_path / "frame-1.png") as actual:
        pixels = actual.crop((145, 0, 315, 35)).get_flattened_data()
        assert sum(1 for red, green, blue in pixels if red > 190 and green > 150 and blue < 185) >= 20


def test_silent_speech_response_cannot_become_a_successful_voiceover(tmp_path, monkeypatch):
    from app import longform_media
    source = tmp_path / "silent.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)
    monkeypatch.setattr(longform_media.speech, "_edge_audio", lambda *args, **kwargs: (source, []))
    with pytest.raises(ValueError, match="静音"):
        longform_media.synthesize_shot(None, None, "这段口播必须有真实声音。", {}, tmp_path)


def test_selected_ordinary_speech_rate_reaches_synthesis_without_cloud_fallback(tmp_path, monkeypatch):
    from app import longform_media
    voice = tmp_path / "returned.wav"
    subprocess.run([settings.ffmpeg_binary, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ac", "1", "-ar", "24000", str(voice)], check=True)
    def provider(text, parameters, directory, context):
        assert parameters == {"voice": "zh-CN-YunxiNeural", "rate": "-10%"}
        return voice, [{"start": .1, "end": 1.9, "text": text}]
    monkeypatch.setattr(longform_media.speech, "_edge_audio", provider)
    actual, duration, cues = longform_media.synthesize_shot(None, None, "用核验资料解释空间。",
                     {"voice": "zh-CN-YunxiNeural", "rate": "-10%"}, tmp_path)
    assert duration == 2.0
    assert actual.is_file() and cues[0]["end"] == 1.9


def test_visual_categories_have_different_evidence_layouts():
    from app.longform_media import _overlay
    base = {"caption": "核对客厅尺度；核对餐厨动线；核对卧室配置", "visual_type": "scene"}
    ordinary = _overlay((720, 1280), base, "空间核对", "合成验收", True)
    checklist = _overlay((720, 1280), {**base, "visual_type": "checklist"}, "空间核对", "合成验收", True)
    data = _overlay((720, 1280), {"caption": "建筑面积420㎡ · 对照核验资料", "visual_type": "data"}, "资料尺度", "合成验收", True)
    assert checklist.getpixel((30, 350))[3] > ordinary.getpixel((30, 350))[3]
    assert data.getpixel((30, 350))[3] > ordinary.getpixel((30, 350))[3]


def test_prepared_story_card_is_not_covered_by_a_second_caption_or_data_card():
    from app.longform_media import _overlay
    shot = {"caption": "建筑面积420㎡ · 口径需核对", "visual_type": "data"}
    ordinary = _overlay((720, 1280), shot, "资料数字", "合成验收", True)
    prepared = _overlay((720, 1280), shot, "资料数字", "合成验收", True, story_card=True)
    assert ordinary.getpixel((30, 350))[3] > 0
    assert prepared.getpixel((30, 350))[3] == 0
    assert ordinary.getpixel((30, 90))[3] > 0
    assert prepared.getpixel((30, 90))[3] == 0
    assert prepared.getpixel((30, 20))[3] > 0


def test_five_minute_render_binds_real_audio_and_chapter_evidence(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app import agent_models, business_models, longform_media
    from app.core_models import Asset, Fact, Membership, Property, Tenant, User
    from app.business_models import ContentVersion
    from app.jobs import enqueue_job
    from app.security import context_for_user
    from app.media import handle_render
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        db.add_all([User(id="u", email="longform@example.com", password_hash="x", name="合成验收"),
                    Tenant(id="t", name="合成验收")]); db.flush()
        db.add(Membership(user_id="u", tenant_id="t", role="admin", active=True))
        db.add(Property(id="p", tenant_id="t", name="合成示意项目", city="上海", is_test=True,
                        marketing_confirmed=True, marketing_evidence="合成验收授权"))
        photo = tmp_path / "photo.jpg"; Image.new("RGB", (320, 180), "#25485c").save(photo)
        raw = photo.read_bytes()
        db.add(Asset(id="photo", tenant_id="t", property_id="p", name="示意照片", kind="image", storage_key="photo.jpg",
                     mime_type="image/jpeg", sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), origin_type="test",
                     rights="authorized", rights_evidence="本项目合成技术验收"))
        db.add(Fact(id="area", tenant_id="t", property_id="p", semantic_key="area", label="面积", value="420", unit="㎡",
                    source="合成验收资料", status="verified", revision=1))
        from test_longform_generation import NARRATION, SECTIONS, TITLES
        board = {"schema_version": 2, "target_seconds": 300,
                 "voiceover": {"enabled": True, "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-10%"},
                 "planning_context": {"hook": "从生活问题出发", "outline": ["回家动线", "公共空间", "私密边界", "家庭取舍"],
                                      "cta": "带走看房清单", "reference": {}}, "chapters": [], "shots": []}
        for chapter_index in range(6):
            cid = f"c{chapter_index+1}"
            chapter_shots = [{"id": f"{cid}-s{index+1}", "chapter_id": cid, "plan_section": SECTIONS[chapter_index],
                "reference_beat_ids": [], "voiceover": text, "caption": TITLES[chapter_index],
                "visual_direction": "依据核验资料展示当前空间问题", "visual_type": "walkthrough",
                "fact_refs": [{"id": "area", "revision": 1}], "asset_id": "photo", "production_status": "ready", "duration": 10, "start": 0}
                for index, text in enumerate(NARRATION[chapter_index][:5])]
            board["chapters"].append({"id": cid, "title": TITLES[chapter_index], "purpose": TITLES[chapter_index],
                "plan_sections": [SECTIONS[chapter_index]], "reference_beat_ids": [], "target_seconds": 50,
                "shot_ids": [shot["id"] for shot in chapter_shots]})
            board["shots"].extend(chapter_shots)
        content = ContentVersion(id="c", tenant_id="t", property_id="p", series_id="series", created_by="u",
                    topic="空间示意分析", script="\n".join(shot["voiceover"] for shot in board["shots"]), cover_title="空间资料讲解",
                    fact_refs=[{"id": "area", "revision": 1}], storyboard=board, aspect_ratio="16:9")
        db.add(content); db.commit()
        voice = tmp_path / "external-speech-boundary.wav"
        subprocess.run([settings.ffmpeg_binary, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                        "-ac", "1", "-ar", "24000", str(voice)], check=True)
        events = []
        db.info["job_progress"] = lambda **event: events.append(event)
        def returned_voice(text, *args, **kwargs):
            # The real handler must advertise its long duration before speech
            # begins so browsers can extend the durable-job waiting deadline.
            longform_events = [event for event in events if "longform_progress" in event]
            assert longform_events and longform_events[0]["longform_progress"]["target_seconds"] == 300
            assert type(longform_events[0]["longform_progress"]["target_seconds"]) is int
            return voice, [{"start": .1, "end": 9.8, "text": text}]
        monkeypatch.setattr(longform_media.speech, "_edge_audio", returned_voice)
        job = enqueue_job(db, context_for_user(db, "u", "t"), "p", "render_content", {"content_version_id": "c"})
        db.commit()
        result = handle_render(db, job); db.commit()
        rendered = db.get(Asset, result["render_asset_id"])
        actual_voice = db.get(Asset, result["voiceover_asset_id"])
        metadata = rendered.metadata_json
        assert 300 <= result["duration"] <= 300.75
        assert result["shot_count"] == 30
        assert metadata["voiceover_audio_asset_id"] == actual_voice.id
        assert metadata["voiceover_audio_sha256"] == hashlib.sha256((tmp_path / actual_voice.storage_key).read_bytes()).hexdigest()
        assert metadata["voiceover_audio_rights_revision"] == actual_voice.rights_revision
        assert metadata["speech_coverage"] >= .99
        assert len(metadata["shot_audio_receipts"]) == 30
        assert metadata["chapters"][0]["end"] == 50.0
        assert metadata["chapters"][1]["start"] == 50.0
        assert metadata["chapters"][5]["end"] == 300.0
        counters = [event["longform_progress"] for event in events if "longform_progress" in event]
        assert counters[0] == {"completed_chapters": 0, "total_chapters": 6, "completed_shots": 0,
                               "total_shots": 30, "target_seconds": 300, "stage": "prepare"}
        assert counters[-1] == {"completed_chapters": 6, "total_chapters": 6, "completed_shots": 30,
                                "total_shots": 30, "target_seconds": 300, "stage": "save"}
        assert any(counter["completed_shots"] == 10 and counter["completed_chapters"] == 2 for counter in counters)
        assert [counter["completed_shots"] for counter in counters] == sorted(counter["completed_shots"] for counter in counters)
    engine.dispose()
