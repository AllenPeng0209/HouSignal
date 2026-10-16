"""Native PPU protocol uses real codecs and substitutes only remote HTTP."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.config import settings
from app.content_skills import media


@pytest.fixture
def ppu(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "native_video_backend", "lightx2v")
    monkeypatch.setitem(settings.__dict__, "native_video_base_url", "http://127.0.0.1:18879")
    monkeypatch.setitem(settings.__dict__, "native_video_model", "minimax-h3-dense8")
    monkeypatch.setitem(settings.__dict__, "native_video_timeout_seconds", 900)
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    monkeypatch.setattr(settings, "dashscope_video_model", "")


def _video(tmp_path, *, size="576x1024", audio=True):
    target = tmp_path / ("source-audio.mp4" if audio else "source-silent.mp4")
    command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=400:sample_rate=32000"]
    command += ["-frames:v", "56", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    subprocess.run(command + [str(target)], check=True)
    return target.read_bytes()


def test_ppu_video_native_generation_saves_fully_decoded_artifact_without_cloud_key(ppu, tmp_path):
    # Catches routing PPU through DashScope/Easel, losing real task identity,
    # changing the H3 frame grid, or declaring URL-only output as generated.
    video = _video(tmp_path)
    calls, receipts = [], []
    def transport(request):
        calls.append(request)
        if request.url.path == "/v1/service/metadata":
            return httpx.Response(200, json={"model_cls": "minimax_h3", "nproc_per_node": 4})
        if request.url.path == "/v1/tasks/queue/status":
            return httpx.Response(200, json={"is_processing": False, "active_count": 0, "pending_count": 0})
        if request.url.path == "/v1/tasks/video/":
            payload = json.loads(request.content)
            assert payload["task"] == "t2av" and payload["size"] == [1024, 576]
            assert payload["num_frames"] == 56 and payload["seed"] == 42
            assert payload["save_result_path"].startswith("./housignal-")
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"task_id": "native-test-01", "task_status": "pending", "save_result_path": payload["save_result_path"]})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"task_id": "native-test-01", "status": "completed"})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=video, headers={"content-type": "video/mp4"})
        pytest.fail("Unexpected native runtime request")
    result = media.execute("ai-video-gen", input_text="A moving red product in a studio, no dialogue.", assets=[],
                           parameters={"size": "576x1024", "duration": 2}, output_dir=tmp_path / "output",
                           context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport)), "on_usage": receipts.append})
    actual = next(path for path in result["artifact_paths"] if path.suffix == ".mp4")
    assert actual.read_bytes() == video
    receipt = json.loads(next(path for path in result["artifact_paths"] if path.name == "video-receipt.json").read_text())
    assert receipt["backend_task_id"] == "native-test-01"
    assert receipt["frames_decoded"] == 56 and receipt["has_native_audio"] is True
    assert receipt["all_video_and_audio_frames_decoded"] is True
    assert receipt["actual_duration_seconds"] == pytest.approx(56 / 24, abs=0.08)
    assert receipt["billing_kind"] == "existing_compute_no_per_request_cloud_api"
    assert receipts[0]["model"] == "minimax-h3-dense8" and receipts[0]["units"]["video_frames"] == 56
    assert all(request.url.port == 18879 for request in calls)


@pytest.mark.parametrize("negative", ["warped walls, invented logos, unwanted dialogue", ""])
def test_ppu_preserves_negative_constraints_using_only_h3_supported_request_fields(ppu, tmp_path, negative):
    # The live H3 runner rejects negative_prompt after accepting its HTTP schema.
    # Keep its real request-field whitelist while exercising the complete local
    # submission, polling, codec validation and receipt path without GPU calls.
    video = _video(tmp_path)
    original = "A slow architectural camera move through a quiet living room."
    submitted = []
    rejected = []
    allowed = {"return_result_tensor", "save_result_path", "seed", "task", "size", "num_frames", "prompt"}

    def transport(request):
        if request.url.path == "/v1/service/metadata":
            return httpx.Response(200, json={"model_cls": "minimax_h3", "nproc_per_node": 4})
        if request.url.path == "/v1/tasks/queue/status":
            return httpx.Response(200, json={"is_processing": False, "active_count": 0, "pending_count": 0})
        if request.url.path == "/v1/tasks/video/":
            payload = json.loads(request.content)
            submitted.append(payload)
            rejected.extend(sorted(set(payload) - allowed))
            return httpx.Response(200, json={"task_id": "native-constraints-01", "task_status": "pending",
                                             "save_result_path": payload["save_result_path"]})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "failed" if rejected else "completed",
                                             "error": "unsupported request fields" if rejected else None})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=video)
        pytest.fail("Unexpected native runtime request")

    result = media.execute("ai-video-gen", input_text=original, assets=[],
                           parameters={"size": "576x1024", "duration": 2, "negative_prompt": negative},
                           output_dir=tmp_path / "output",
                           context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport))})
    assert len(submitted) == 1
    actual = submitted[0]["prompt"]
    assert actual.startswith(original)
    if negative:
        assert negative in actual and actual != original
    else:
        assert actual == original
    assert next(path for path in result["artifact_paths"] if path.suffix == ".mp4").read_bytes() == video
    for filename in ("video-submission.json", "video-receipt.json"):
        record = json.loads((tmp_path / "output" / filename).read_text())
        assert record["original_prompt_sha256"] == hashlib.sha256(original.encode()).hexdigest()
        assert record["actual_prompt_sha256"] == hashlib.sha256(actual.encode()).hexdigest()
        assert record["prompt_sha256"] == record["actual_prompt_sha256"]
        assert bool(record["prompt_adaptation"]) is bool(negative)


def test_ppu_rejects_overlong_combined_prompt_without_dropping_constraints_or_submitting(ppu, tmp_path):
    # A full-length prompt plus exclusions must fail locally, rather than lose
    # user constraints or send an oversized adapted prompt to the resident model.
    requests = []
    with pytest.raises(ValueError, match="12000|缩短"):
        media.execute("ai-video-gen", input_text="A" * 12000, assets=[],
                      parameters={"size": "576x1024", "duration": 2, "negative_prompt": "warped walls"},
                      output_dir=tmp_path / "output",
                      context={"media_http_client": httpx.Client(transport=httpx.MockTransport(
                          lambda request: requests.append(request) or httpx.Response(500)))})
    assert requests == []


@pytest.mark.parametrize("url", ["http://169.254.169.254", "https://example.com", "http://localhost:18879?token=x", "http://127.0.0.1:8878/v1"])
def test_ppu_video_rejects_untrusted_or_legacy_gateway_before_request(ppu, tmp_path, monkeypatch, url):
    # Catches a user-controlled model endpoint/legacy gateway becoming an SSRF.
    monkeypatch.setitem(settings.__dict__, "native_video_base_url", url)
    calls = []
    with pytest.raises(ValueError, match="地址|原生|配置"):
        media.execute("ai-video-gen", input_text="A moving product", assets=[], parameters={"size": "576x1024", "duration": 2},
                      output_dir=tmp_path / "output", context={"before_model_call": lambda: calls.append(1)})
    assert calls == []


def test_ppu_video_rejects_silent_media_without_claiming_native_audio(ppu, tmp_path):
    # Catches accepting a static or silent substitute for this audio-video model.
    video = _video(tmp_path, audio=False)
    def transport(request):
        if request.url.path == "/v1/service/metadata":
            return httpx.Response(200, json={"model_cls": "minimax_h3", "nproc_per_node": 4})
        if request.url.path == "/v1/tasks/queue/status":
            return httpx.Response(200, json={"is_processing": False, "active_count": 0, "pending_count": 0})
        if request.url.path == "/v1/tasks/video/":
            return httpx.Response(200, json={"task_id": "native-test-02", "task_status": "pending", "save_result_path": "./source.mp4"})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(200, content=video)
    with pytest.raises(ValueError, match="音轨|音频"):
        media.execute("ai-video-gen", input_text="A moving product", assets=[], parameters={"size": "576x1024", "duration": 2},
                      output_dir=tmp_path / "output", context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport))})
    assert not (tmp_path / "output/generated.mp4").exists()
    assert not (tmp_path / "output/video-receipt.json").exists()


def test_ppu_video_schema_offers_only_verified_runtime_sizes(ppu):
    # Catches the page offering 720P or 1080P dimensions this deployment rejects.
    fields = {field["key"]: field for field in media.definition_for("ai-video-gen")["parameters"]}
    assert fields["size"]["default"] == "576x1024"
    assert {option["value"] for option in fields["size"]["options"]} == {"576x1024", "1024x576"}
    assert fields["duration"]["max"] == 14
    assert "resolution" not in fields
    assert media.readiness("ai-video-gen")["status"] == "ready"


def test_short_drama_accepts_native_ppu_image_to_video_protocol(ppu, monkeypatch):
    # Catches a working H3 first-frame protocol being gated on a Wan model name.
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_image_model", "qwen-image-2.1-pro")
    monkeypatch.setattr(settings, "dashscope_tts_model", "qwen3-tts-flash")
    from app.content_skills.tools import drama
    assert drama.readiness()["status"] == "ready"


def test_ppu_short_drama_generation_schema_uses_its_verified_dimensions(ppu):
    # Catches a ready native drama form sending the unsupported default 1280x720.
    fields = {field["key"]: field for field in media.definition_for("short-drama", {"op": "generate"})["parameters"]}
    assert fields["size"]["default"] == "576x1024"
    assert "resolution" not in fields


def test_busy_ppu_is_not_sent_another_generation(ppu, tmp_path):
    # Catches using an existing resource while its owner is already generating.
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path == "/v1/service/metadata":
            return httpx.Response(200, json={"model_cls": "minimax_h3", "nproc_per_node": 4})
        return httpx.Response(200, json={"is_processing": True, "active_count": 1, "pending_count": 0})
    with pytest.raises(ValueError, match="其他任务|稍后"):
        media.execute("ai-video-gen", input_text="Moving product", assets=[], parameters={"size": "576x1024", "duration": 2},
                      output_dir=tmp_path / "output", context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport))})
    assert all(request.method == "GET" for request in requests)
    assert not list((tmp_path / "output").glob("*"))


def test_ppu_failure_retains_actual_task_id_and_does_not_repeat_submission(ppu, tmp_path):
    # Catches retrying a remote task whose submission already had real effects.
    posts = []
    def transport(request):
        if request.url.path == "/v1/service/metadata":
            return httpx.Response(200, json={"model_cls": "minimax_h3", "nproc_per_node": 4})
        if request.url.path == "/v1/tasks/queue/status":
            return httpx.Response(200, json={"is_processing": False, "active_count": 0, "pending_count": 0})
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(200, json={"task_id": "native-failed-01", "task_status": "pending", "save_result_path": "./failed.mp4"})
        return httpx.Response(200, json={"status": "failed", "error": "private internal error"})
    context = {"media_http_client": httpx.Client(transport=httpx.MockTransport(transport))}
    for _ in range(2):
        with pytest.raises(ValueError):
            media.execute("ai-video-gen", input_text="Moving product", assets=[], parameters={"size": "576x1024", "duration": 2},
                          output_dir=tmp_path / "output", context=context)
    saved = json.loads((tmp_path / "output/video-submission.json").read_text())
    assert saved["backend_task_id"] == "native-failed-01"
    assert len(posts) == 1
    assert "private internal error" not in json.dumps(saved)
    assert not (tmp_path / "output/generated.mp4").exists()
