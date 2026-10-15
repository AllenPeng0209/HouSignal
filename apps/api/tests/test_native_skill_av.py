"""Native AV regression tests: real, tiny FFmpeg media; no Easel runtime."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import wave
import math
import struct

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")


def command(*args):
    proc = subprocess.run(args, capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    return proc.stdout


def probe(path):
    return json.loads(command("ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)))


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-av-fixtures")
    video = root / "video.mp4"
    command("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=96x64:r=30:d=2", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=2", "-vf", "drawbox=x=32:y=20:w=32:h=24:color=red:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video))
    silent = root / "silent.mp4"
    command("ffmpeg", "-y", "-v", "error", "-i", str(video), "-an", "-c:v", "copy", str(silent))
    audios = []
    for index, hz in enumerate((440, 880, 1320)):
        path = root / f"tone-{index}.wav"
        command("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=44100:d=2", str(path))
        audios.append(path)
    images = []
    for index, color in enumerate(("blue", "yellow")):
        path = root / f"image-{index}.png"
        command("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=96x64", "-frames:v", "1", str(path))
        images.append(path)
    srt = root / "captions.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:00,900\nHello\n\n2\n00:00:01,000 --> 00:00:01,800\nWorld\n", encoding="utf-8")
    spoken = root / "speech-with-silence.mp4"
    command("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=96x64:r=30:d=4", "-f", "lavfi", "-i", r"aevalsrc=0.15*sin(2*PI*440*t)*(lt(t\,1)+gte(t\,3)):d=4:s=44100", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(spoken))
    return {"video": video, "silent": silent, "audios": audios, "images": images, "srt": srt,"spoken":spoken}


def av_module():
    path = Path(__file__).resolve().parents[1] / "app" / "content_skills" / "tools" / "av.py"
    assert path.exists(), "Native AV dispatch is not implemented"
    spec = importlib.util.spec_from_file_location("native_av_test_target", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chinese_text_layers_render_distinct_real_glyphs(tmp_path):
    from PIL import Image
    av = av_module()
    av._text_filter('中', [], {}, tmp_path, 0, fontsize=48)
    av._text_filter('文', [], {}, tmp_path, 1, fontsize=48)
    with Image.open(tmp_path / 'text-000.png') as first, Image.open(tmp_path / 'text-001.png') as second:
        assert first.tobytes() != second.tobytes(), 'Different Chinese characters must not render the same missing-glyph square'


def test_portrait_intro_wraps_long_chinese_title_and_keeps_subtitle_separate(tmp_path):
    import numpy as np
    path = run(tmp_path, 'video-intro-outro', [], {'operation': 'card',
        'title': '内容交付，从清晰开始', 'subtitle': '原创验收示意', 'cta': '核对后再发布',
        'size': '720x1280', 'duration': .7, 'title_color': 'white', 'subtitle_color': 'yellow'})[0]
    frame = np.frombuffer(command('ffmpeg', '-v', 'error', '-ss', '0.35', '-i', str(path),
        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'), dtype=np.uint8).reshape(1280,720,3)
    neutral = frame.astype(np.int16)
    white = (frame[:,:,0]>180)&(frame[:,:,1]>180)&(frame[:,:,2]>180)&(abs(neutral[:,:,0]-neutral[:,:,2])<15)&(abs(neutral[:,:,1]-neutral[:,:,2])<15)
    yellow = (frame[:,:,0]>180)&(frame[:,:,1]>180)&(frame[:,:,2]<100)
    wy,wx=np.where(white); yy,yx=np.where(yellow & (np.indices(yellow.shape)[0]<1000))
    assert len(wx) and len(yx)
    assert wx.min()>=24 and wx.max()<696, 'Title must be within horizontal safe margins'
    assert wy.max()+15 < yy.min(), 'Actual title/subtitle glyph bands must not overlap'


def run(tmp_path, skill, paths, params=None, context=None, text=""):
    result = av_module().dispatch(skill, input_text=text, assets=[{"path": str(p)} for p in paths], parameters=params or {}, output_dir=tmp_path, context=context or {})
    assert result.get("status") != "setup", result
    assert result["response"]
    assert result["artifact_paths"], result
    for path in result["artifact_paths"]:
        assert isinstance(path, Path)
        assert path.resolve().is_relative_to(tmp_path.resolve())
        assert path.is_file() and path.stat().st_size > 0
    return result["artifact_paths"]


@pytest.mark.parametrize("operation,extra,want_duration,want_kind", [
    ("trim", {"start": 0.4, "end": 1.3}, 0.9, "video"),
    ("compress", {"crf": 32}, 2.0, "video"),
    ("convert", {"format": "mkv"}, 2.0, "video"),
    ("speed", {"factor": 2}, 1.0, "video"),
    ("extract-audio", {"format": "wav"}, 2.0, "audio"),
    ("thumbnail", {"time": 0.7}, None, "image"),
    ("extract-frames", {"fps": 1}, None, "frames"),
])
def test_video_operations_make_real_media(tmp_path, media, operation, extra, want_duration, want_kind):
    paths = run(tmp_path, "video-editing", [media["video"]], {"operation": operation, **extra})
    data = probe(paths[0])
    if want_duration is not None:
        assert float(data["format"]["duration"]) == pytest.approx(want_duration, abs=0.12)
    assert any(s["codec_type"] == ("audio" if want_kind == "audio" else "video") for s in data["streams"])
    if want_kind == "frames":
        assert len(paths) == 2


def test_video_concat_handles_silent_input_without_dropping_other_audio(tmp_path, media):
    path = run(tmp_path, "video-editing", [media["video"], media["silent"]], {"operation": "concat", "size": "96x64"})[0]
    data = probe(path)
    assert float(data["format"]["duration"]) == pytest.approx(4, abs=0.15)
    assert any(s["codec_type"] == "audio" for s in data["streams"])


@pytest.mark.parametrize("operation", ["subtitles", "watermark"])
def test_video_overlay_preserves_audio(tmp_path, media, operation):
    extra = media["srt"] if operation == "subtitles" else media["images"][0]
    path = run(tmp_path, "video-editing", [media["video"], extra], {"operation": operation, "soft": True, "width": 24})[0]
    data = probe(path)
    assert any(s["codec_type"] == "audio" for s in data["streams"])
    if operation == "subtitles":
        assert any(s["codec_type"] == "subtitle" for s in data["streams"])


def test_hard_subtitles_are_burned_at_actual_cue_times(tmp_path, media):
    path = run(tmp_path, "video-editing", [media["video"],media["srt"]], {"operation":"subtitles", "soft":False})[0]
    frame = command("ffmpeg", "-v", "error", "-i", str(path), "-ss", "0.5", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
    original = command("ffmpeg", "-v", "error", "-i", str(media["video"]), "-ss", "0.5", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
    assert sum(abs(a-b) for a,b in zip(frame,original)) > 20000


def test_text_overlay_handles_literal_filter_metacharacters(tmp_path, media):
    path = run(tmp_path,"video-editing",[media["video"]],{"operation":"text","text":"Hi: 50% ' [x]; 爱","fontsize":12})[0]
    assert float(probe(path)["format"]["duration"]) == pytest.approx(2,abs=0.1)


@pytest.mark.parametrize("operation,params,want", [
    ("trim", {"start": "00:00:00.5", "duration": 0.7}, 0.7),
    ("concat", {}, 4.0),
    ("convert", {"format": "mp3", "sample_rate": 22050, "channels": 1}, 2.0),
    ("volume", {"volume": 0.5}, 2.0),
    ("denoise", {"tier": 2}, 2.0),
    ("normalize", {}, 2.0),
    ("fade", {"fade_in": 0.1, "fade_out": 0.2}, 2.0),
    ("speed", {"factor": 4}, 0.5),
])
def test_audio_operations_preserve_intended_duration(tmp_path, media, operation, params, want):
    paths = media["audios"][:2] if operation == "concat" else media["audios"][:1]
    result = run(tmp_path, "audio-editing", paths, {"operation": operation, **params})
    data = probe(result[0])
    assert float(data["format"]["duration"]) == pytest.approx(want, abs=0.13)
    assert all(s["codec_type"] == "audio" for s in data["streams"])
    if operation == "convert":
        assert int(data["streams"][0]["sample_rate"]) == 22050
    if operation == "volume":
        with wave.open(str(result[0])) as handle:
            values = struct.unpack("<" + "h" * handle.getnframes(), handle.readframes(handle.getnframes()))
        rms = math.sqrt(sum(v*v for v in values)/len(values))
        assert rms == pytest.approx(1448, abs=20)


def test_audio_mix_combines_frequencies_and_delayed_effect(tmp_path, media):
    path = run(tmp_path, "audio-mix", media["audios"], {"duration": 2, "no_duck": True, "sfx_at": [0.5]})[0]
    assert float(probe(path)["format"]["duration"]) == pytest.approx(2, abs=0.06)
    raw = command("ffmpeg", "-v", "error", "-i", str(path), "-t", "0.3", "-f", "s16le", "-ac", "1", "-ar", "44100", "pipe:1")
    samples = struct.unpack("<" + "h" * (len(raw)//2), raw)
    powers = [abs(sum(v*math.sin(2*math.pi*hz*i/44100) for i,v in enumerate(samples))) for hz in (440,880,1320)]
    assert powers[0] > 1e6 and powers[1] > 1e5
    assert powers[2] < powers[1] * 0.1  # the effect has not started yet


@pytest.mark.parametrize("mode", ["crop", "blur", "pad"])
def test_video_reframe_changes_geometry_and_preserves_audio(tmp_path, media, mode):
    path = run(tmp_path, "video-reframe", [media["video"]], {"ratio": "9:16", "size": "54x96", "mode": mode})[0]
    streams = probe(path)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (54,96)
    assert any(s["codec_type"] == "audio" for s in streams)


@pytest.mark.parametrize("transition,want", [("none", 1.6), ("fade",1.4)])
def test_slideshow_renders_images_with_optional_transition(tmp_path, media, transition, want):
    path = run(tmp_path, "slideshow-video", media["images"], {"size": "96x64", "per": 0.8, "transition": transition, "trans_duration": 0.2, "kenburns": False})[0]
    assert float(probe(path)["format"]["duration"]) == pytest.approx(want, abs=0.12)
    assert any(s["codec_type"] == "audio" for s in probe(path)["streams"])


def test_intro_card_and_attach_render_real_clips(tmp_path, media):
    card_dir = tmp_path / "card"
    card = run(card_dir, "video-intro-outro", [], {"operation": "card", "title": "Intro", "duration": 0.6, "size": "96x64"})[0]
    output = run(tmp_path / "joined", "video-intro-outro", [media["video"], card], {"operation": "attach"})[0]
    assert float(probe(output)["format"]["duration"]) == pytest.approx(2.6, abs=0.13)


def test_green_screen_replaces_background_pixels(tmp_path, media):
    path = run(tmp_path, "green-screen", [media["video"], media["images"][0]], {"color": "green", "similarity": 0.3})[0]
    raw = command("ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
    red, green, blue = raw[:3]
    assert blue > 140 and blue > green * 2


@pytest.mark.parametrize("mode", ["waves", "bars", "spectrum", "cqt"])
def test_audio_visualizer_has_video_audio_and_correct_duration(tmp_path, media, mode):
    path = run(tmp_path, "audio-visualizer", media["audios"][:1], {"mode": mode, "size": "160x120"})[0]
    data = probe(path)
    assert float(data["format"]["duration"]) == pytest.approx(2, abs=0.1)
    assert {s["codec_type"] for s in data["streams"]} == {"video", "audio"}


def test_beat_sync_uses_explicit_beats_and_outputs_timing_manifest(tmp_path, media):
    paths = run(tmp_path, "beat-sync-video", [*media["images"], media["audios"][0]], {"size": "96x64", "beats": [0, 0.5, 1, 1.5, 2], "every": 1, "effect": "flash"})
    assert float(probe(paths[0])["format"]["duration"]) == pytest.approx(2, abs=0.1)
    manifest = json.loads(paths[1].read_text())
    assert manifest["segments"] == [{"start": 0.0, "end": 0.5}, {"start": 0.5, "end": 1.0}, {"start": 1.0, "end": 1.5}, {"start": 1.5, "end": 2.0}]


def test_highlights_cut_requested_segments_and_keep_source_timing(tmp_path, media):
    paths = run(tmp_path, "video-highlights", [media["video"]], {"segments": [{"start":0.3,"end":0.9,"label":"A"},{"start":1.1,"end":1.7}], "pad":0})
    assert len(paths) == 3
    assert float(probe(paths[0])["format"]["duration"]) == pytest.approx(0.6, abs=0.1)
    assert json.loads(paths[-1].read_text())["clips"][0]["start"] == 0.3


def test_clipify_outputs_requested_vertical_clips(tmp_path, media):
    paths = run(tmp_path, "clipify", [media["video"]], {"segments":[{"start":0.2,"end":1.0}],"pad":0,"size":"54x96"})
    stream = next(s for s in probe(paths[0])["streams"] if s["codec_type"] == "video")
    assert (stream["width"],stream["height"]) == (54,96)


def test_video_production_normalizes_and_creates_actual_quality_receipt(tmp_path, media):
    paths = run(tmp_path, "video-production", [media["video"], media["silent"], media["srt"]], {"size":"96x64","soft_subtitles":True,"cover":True})
    data = probe(paths[0])
    assert float(data["format"]["duration"]) == pytest.approx(4,abs=0.15)
    receipt = json.loads(paths[-1].read_text())
    assert receipt["duration_seconds"] == pytest.approx(4,abs=0.15)
    assert receipt["width"] == 96 and receipt["height"] == 64
    assert receipt["has_audio"] is True
    assert any(p.suffix == ".jpg" for p in paths)


def test_auto_short_video_honors_storyboard_duration_and_audio(tmp_path, media):
    paths = run(tmp_path, "auto-short-video", [*media["images"], media["silent"], media["audios"][0]], {"size":"96x64","storyboard":[{"asset_index":0,"duration":0.4},{"asset_index":2,"duration":0.6}],"kenburns":False})
    data = probe(paths[0])
    assert float(data["format"]["duration"]) == pytest.approx(1,abs=0.12)
    assert any(s["codec_type"]=="audio" for s in data["streams"])


def test_subtitle_translation_preserves_cues_and_uses_configured_chat(tmp_path, media):
    def chat(messages, *, max_tokens):
        assert "Hello" in messages[-1]["content"]
        return {"choices":[{"message":{"content":"[\"你好\", \"世界\"]"}}]}
    paths = run(tmp_path, "subtitle-translate", [media["srt"]], {"target_language":"简体中文"}, {"chat":chat})
    translated = paths[0].read_text()
    assert "00:00:00,200 --> 00:00:00,900" in translated
    assert "Hello\n你好" in translated and "World\n世界" in translated


def test_subtitle_translation_requires_chat_or_explicit_translations(tmp_path, media):
    result = av_module().dispatch("subtitle-translate", input_text="", assets=[{"path": str(media["srt"])}], parameters={}, output_dir=tmp_path, context={})
    assert result["status"] == "setup" and not result["artifact_paths"]


@pytest.mark.parametrize("parameters", [{"operation":"trim","start":1,"end":0.5}, {"operation":"speed","factor":0}, {"operation":"convert","format":"../../evil"}, {"operation":"thumbnail","time":"NaN"}])
def test_invalid_video_parameters_raise_without_artifacts(tmp_path, media, parameters):
    with pytest.raises(ValueError):
        av_module().dispatch("video-editing", input_text="", assets=[{"path": str(media["video"])}], parameters=parameters, output_dir=tmp_path, context={})
    assert not list(tmp_path.glob("*.mp4"))


def test_refuses_output_symlink_escape(tmp_path, media):
    outside = tmp_path.parent / "outside-native-av.wav"
    (tmp_path / "audio.wav").symlink_to(outside)
    with pytest.raises(ValueError):
        run(tmp_path, "audio-editing", media["audios"][:1], {"operation":"convert"})
    assert not outside.exists()


def test_frame_extraction_refuses_individual_output_symlink(tmp_path, media):
    outside = tmp_path.parent / "outside-frame.jpg"
    (tmp_path / "frame-0001.jpg").symlink_to(outside)
    with pytest.raises(ValueError):
        run(tmp_path,"video-editing",[media["video"]],{"operation":"extract-frames"})
    assert not outside.exists()


def test_configured_binary_paths_work_without_path_and_do_not_leak_context(tmp_path, media, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    monkeypatch.setenv("PATH", "")
    module = av_module()
    result = module.dispatch("audio-editing",input_text="",assets=[{"path":str(media["audios"][0])}],parameters={"operation":"trim","duration":0.5},output_dir=tmp_path,context={"ffmpeg_binary":ffmpeg,"ffprobe_binary":ffprobe})
    assert result.get("status")!="setup" and result["artifact_paths"][0].is_file()
    assert module.readiness("audio-editing")["status"] == "setup"


def test_audio_denoise_video_keeps_picture_by_default(tmp_path, media):
    path = run(tmp_path,"audio-denoise",[media["video"]],{"tier":1})[0]
    data = probe(path)
    assert {s["codec_type"] for s in data["streams"]} == {"audio","video"}
    assert float(data["format"]["duration"]) == pytest.approx(2,abs=0.1)


@pytest.mark.parametrize("operation",["remove-silence","jumpcut","silence-cut","mute-cut"])
def test_silence_jumpcut_removes_detected_silence_with_true_timing_receipt(tmp_path,media,operation):
    paths = run(tmp_path,"video-editing",[media["spoken"]],{"operation":operation,"noise":-40,"min_silence":0.3,"pad":0})
    assert len(paths)==2
    data = probe(paths[0])
    assert float(data["format"]["duration"]) == pytest.approx(2,abs=0.15)
    assert {stream["codec_type"] for stream in data["streams"]} == {"video","audio"}
    audio = next(s for s in data["streams"] if s["codec_type"]=="audio")
    video = next(s for s in data["streams"] if s["codec_type"]=="video")
    assert float(audio["duration"]) == pytest.approx(float(video["duration"]),abs=0.09)
    receipt = json.loads(paths[1].read_text())
    assert len(receipt["detected_silences"])==1 and len(receipt["kept_segments"])==2
    assert receipt["detected_silences"][0]["start"] == pytest.approx(1,abs=0.08)
    assert receipt["detected_silences"][0]["end"] == pytest.approx(3,abs=0.08)
    assert receipt["removed_seconds"] == pytest.approx(2,abs=0.15)
    assert receipt["output_duration_seconds"] == pytest.approx(float(data["format"]["duration"]),abs=0.001)


def test_silence_jumpcut_without_silence_keeps_entire_video(tmp_path,media):
    paths = run(tmp_path,"video-editing",[media["video"]],{"operation":"remove-silence"})
    assert float(probe(paths[0])["format"]["duration"]) == pytest.approx(2,abs=0.05)
    receipt = json.loads(paths[1].read_text())
    assert receipt["detected_silences"]==[] and receipt["removed_seconds"]==0


def test_palette_gif_is_animated_and_obeys_range_fps_and_size(tmp_path,media):
    from PIL import Image
    path = run(tmp_path,"video-editing",[media["spoken"]],{"operation":"gif","start":1,"duration":1,"fps":10,"width":48})[0]
    with Image.open(path) as gif:
        assert gif.format=="GIF" and gif.size==(48,32)
        assert gif.n_frames==10
        total = 0
        first = gif.convert("RGB").tobytes()
        for frame in range(gif.n_frames):
            gif.seek(frame)
            total += gif.info.get("duration",0)
        assert total == pytest.approx(1000,abs=50)
        assert gif.convert("RGB").tobytes()!=first


@pytest.mark.parametrize("replace",[False,True])
def test_video_bgm_preserves_duration_and_obeys_mix_or_replace(tmp_path,media,replace):
    path = run(tmp_path,"video-editing",[media["spoken"],media["audios"][1]],{"operation":"bgm","audio_index":1,"music_volume":0.5,"replace_audio":replace})[0]
    data = probe(path)
    assert float(data["format"]["duration"]) == pytest.approx(4,abs=0.04)
    video = next(s for s in data["streams"] if s["codec_type"]=="video")
    audio = next(s for s in data["streams"] if s["codec_type"]=="audio")
    assert float(audio["duration"]) == pytest.approx(float(video["duration"]),abs=0.04)
    def powers(start):
        raw = command("ffmpeg","-v","error","-i",str(path),"-ss",str(start),"-t","0.3","-f","s16le","-ac","1","-ar","44100","pipe:1")
        samples = struct.unpack("<"+"h"*(len(raw)//2),raw)
        return [abs(sum(value*math.sin(2*math.pi*hz*i/44100) for i,value in enumerate(samples))) for hz in (440,880)]
    voice,music = powers(0.2)
    assert music>1e6
    assert voice<music*0.05 if replace else voice>1e6
    # Music is looped beyond its original two seconds, during source silence.
    assert powers(2.2)[1]>1e6


def test_video_bgm_adds_sound_to_silent_video_using_selected_music(tmp_path,media):
    path = run(tmp_path,"video-editing",[media["silent"],media["audios"][0]],{"operation":"bgm","music_index":1,"voice_volume":0.2,"bgm_volume":0.3})[0]
    data = probe(path)
    assert {s["codec_type"] for s in data["streams"]} == {"audio","video"}
    assert float(data["format"]["duration"]) == pytest.approx(2,abs=0.04)


def test_bgm_cannot_read_an_audio_path_that_was_not_selected(tmp_path,media):
    with pytest.raises(ValueError,match="已提供"):
        run(tmp_path,"video-editing",[media["video"]],{"operation":"bgm","music":str(media["audios"][0])})
    assert not list(tmp_path.glob("*.mp4"))


def test_video_bgm_mix_keeps_source_equal_track_normalization(tmp_path,media):
    path = run(tmp_path,"video-editing",[media["video"],media["audios"][1]],{"operation":"bgm","audio_index":1,"voice_volume":0,"music_volume":0.5})[0]
    raw = command("ffmpeg","-v","error","-i",str(path),"-ss","0.2","-t","0.5","-f","s16le","-ac","1","-ar","44100","pipe:1")
    samples = struct.unpack("<"+"h"*(len(raw)//2),raw)
    rms = math.sqrt(sum(value*value for value in samples)/len(samples))
    # 4096 sine peak * .5 music gain / 2 equal amix tracks / sqrt(2).
    assert rms == pytest.approx(724,abs=40)
