"""Measured, narrated chapter videos built only from authorized project assets.

There is no estimated speech track or silent fallback. The ordinary Edge voice
adapter is reused and every subtitle is tied to an actual returned audio cue.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import wave

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy import select

from .config import settings
from .core_models import Asset
from .security import context_for_user, require_project
from .content_skills.tools import speech


def _number(value, label, lower, upper):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{label}需为{lower}至{upper}的有效数字")
    return float(value)


def normalize_storyboard(storyboard):
    if not isinstance(storyboard, dict) or storyboard.get("schema_version") != 2:
        raise ValueError("长视频需要章节分镜格式")
    target = _number(storyboard.get("target_seconds"), "长视频目标秒数", 300, 600)
    chapters = storyboard.get("chapters")
    if not isinstance(chapters, list) or not 2 <= len(chapters) <= 20:
        raise ValueError("长视频需要2至20个内容章节")
    chapter_ids = []
    for chapter in chapters:
        if not isinstance(chapter, dict) or not isinstance(chapter.get("id"), str) or not chapter["id"].strip():
            raise ValueError("章节缺少有效编号")
        if not isinstance(chapter.get("title"), str) or not 1 <= len(chapter["title"].strip()) <= 100:
            raise ValueError("章节缺少明确标题")
        chapter_ids.append(chapter["id"])
    if len(set(chapter_ids)) != len(chapter_ids):
        raise ValueError("章节编号不能重复")
    shots = storyboard.get("shots")
    if not isinstance(shots, list) or not 30 <= len(shots) <= 120:
        raise ValueError("5至10分钟长视频需要30至120个具体分镜")
    voice = storyboard.get("voiceover") or {}
    if not isinstance(voice, dict) or voice.get("enabled", True) is not True:
        raise ValueError("长视频需要启用普通配音，不能用无声静帧补足时长")
    if voice.get("engine", "edge") != "edge":
        raise ValueError("长视频普通配音目前支持Edge音色，请选择已配置引擎")
    voice = {"enabled": True, "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-10%", **voice}
    if not isinstance(voice["voice"], str) or not re.fullmatch(r"[a-z]{2}-[A-Za-z]{2,4}-[A-Za-z]+Neural", voice["voice"]):
        raise ValueError("长视频请使用明确的普通Edge音色")
    rate = voice["rate"]
    if not isinstance(rate, str) or not re.fullmatch(r"[+-]\d{1,2}%", rate) or not -20 <= int(rate[:-1]) <= 20:
        raise ValueError("长视频语速需为-20%至+20%")
    result, used, previous, ids = [], set(), -1, set()
    for shot in shots:
        if not isinstance(shot, dict) or not isinstance(shot.get("asset_id"), str) or not shot["asset_id"]:
            raise ValueError("每个长视频分镜需要当前项目素材")
        cid = shot.get("chapter_id")
        if cid not in chapter_ids or chapter_ids.index(cid) < previous:
            raise ValueError("分镜须按章节顺序关联有效章节")
        previous = chapter_ids.index(cid)
        used.add(cid)
        sid = shot.get("id")
        if not isinstance(sid, str) or not sid.strip() or sid in ids:
            raise ValueError("每个分镜需要独立编号")
        ids.add(sid)
        text = shot.get("voiceover")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 600:
            raise ValueError("每个分镜需有1至600字可实际讲解的口播")
        for key in ("caption", "visual_direction"):
            if not isinstance(shot.get(key), str) or not 1 <= len(shot[key].strip()) <= 350:
                raise ValueError("每个分镜需有明确字幕要点与画面说明")
        result.append({**shot, "duration": _number(shot.get("duration"), "分镜计划时长", 1, 30),
                       "start": _number(shot.get("start", 0), "素材截取起点", 0, 600),
                       "voiceover": text.strip()})
    if used != set(chapter_ids):
        raise ValueError("每个章节至少需要一个可拍摄分镜")
    planned = sum(shot["duration"] for shot in result)
    if not 300 <= planned <= 600 or abs(planned - target) > max(1, target * .03):
        raise ValueError("章节分镜总时长必须与5至10分钟目标一致")
    return {**storyboard, "target_seconds": target, "shots": result, "voiceover": voice}


def validate_measured_duration(durations, target_seconds):
    if not durations or any(not math.isfinite(value) or not 1 <= value <= 30 for value in durations):
        raise ValueError("单镜实际口播需为1至30秒，请拆分过长内容")
    total = sum(durations)
    if total < 300:
        raise ValueError(f"实际口播仅{round(total)}秒，不足5分钟；请补充具体分析与拍摄内容后重新生成")
    if total > 600:
        raise ValueError("实际口播超过10分钟，请精简或拆成多条内容")
    if abs(total - target_seconds) > target_seconds * .20:
        raise ValueError("实际口播与计划时长差距超过20%，请按目标丰富或精简讲解")
    return total


def sentence_cues(boundaries, text, duration):
    """Join actual word boundaries; never distribute text over guessed times."""
    if not boundaries:
        return [{"start": 0.0, "end": duration, "text": text}]
    cues, current, previous = [], None, 0.0
    for cue in boundaries:
        if not isinstance(cue, dict):
            raise ValueError("配音字幕时间戳无效")
        start, end, word = cue.get("start"), cue.get("end"), cue.get("text")
        if type(start) not in (int, float) or type(end) not in (int, float) or not math.isfinite(start) or not math.isfinite(end) or not previous <= start < min(end, duration) or end > duration + .15 or not isinstance(word, str) or not word:
            raise ValueError("配音字幕时间戳与实际音轨不一致")
        if current is None:
            current = {"start": start, "end": min(end, duration), "text": word}
        else:
            separator = " " if current["text"][-1:].isascii() and current["text"][-1:].isalnum() and word[:1].isascii() and word[:1].isalnum() else ""
            current["text"] += separator + word
            current["end"] = min(end, duration)
        previous = end
        if len(current["text"]) >= 24 or re.search(r"[。！？!?；;]$", current["text"]):
            cues.append(current); current = None
    if current is not None:
        cues.append(current)
    return cues


def _audio_info(path):
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24000):
            raise ValueError("普通配音归一化音轨无效")
        count = source.getnframes()
        if not 24000 <= count <= 30 * 24000:
            raise ValueError("单镜实际口播需为1至30秒，请调整或拆分")
        samples = array.array("h", source.readframes(count))
        if not samples or max(abs(value) for value in samples) < 64 or math.sqrt(sum(value * value for value in samples) / len(samples)) < 20:
            raise ValueError("普通配音返回静音，无法制作有口播的长视频")
        return count / 24000


def synthesize_shot(db, job, text, parameters, directory, guard=None):
    from . import media, model_usage_ledger
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cancelled = []

    class BoundedVoice:
        def __init__(self, *args, **kwargs):
            from edge_tts import Communicate
            self.communicator = Communicate(*args, **kwargs, connect_timeout=15, receive_timeout=30)

        async def stream(self):
            async with asyncio.timeout(90):
                async for chunk in self.communicator.stream():
                    if db is not None:
                        try:
                            media._check_cancel(db)
                        except BaseException as error:
                            cancelled.append(error)
                            raise
                    yield chunk

    context = {"edge_tts_factory": BoundedVoice, "before_model_call": guard or (lambda: None)}
    if db is not None and job is not None:
        context.update(begin_usage_call=lambda: model_usage_ledger.begin_call(db, job),
                       on_usage=lambda receipt: model_usage_ledger.record_receipt(db, job, receipt["usage_call_id"], receipt))
    try:
        selected = {"voice": parameters.get("voice", "zh-CN-XiaoxiaoNeural")}
        if "rate" in parameters:
            selected["rate"] = parameters["rate"]
        original, boundaries = speech._edge_audio(text, selected, directory, context)
    except Exception:
        if cancelled:
            raise cancelled[0]
        raise
    if guard:
        guard()
    normalized = directory / "narration.wav"
    media._run([settings.ffmpeg_binary, "-hide_banner", "-v", "error", "-y",
                "-protocol_whitelist", "file,pipe", "-i", str(original), "-vn", "-ar", "24000", "-ac", "1",
                "-c:a", "pcm_s16le", str(normalized)], db, timeout=90)
    duration = _audio_info(normalized)
    return normalized, duration, sentence_cues(boundaries, text, duration)


def _overlay(size, shot, chapter, project, is_test, *, story_card=False):
    from . import media
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(12, width // 25)
    font = media._font(max(14, width // 34))
    small = media._font(max(12, width // 48))
    header = max(42, width // 34 + width // 48 + 14)
    draw.rectangle((0, 0, width, header), fill=(15, 28, 43, 220))
    draw.text((pad, 5), project[:35], font=small, fill="white")
    draw.text((pad, 8 + max(16, width // 45)), chapter[:38], font=font, fill=(181, 213, 252, 255))
    if is_test:
        label = "空间示意 · 非楼盘实拍"
        watermark_font = media._font(max(11, width // 60))
        label_width = draw.textlength(label, font=watermark_font)
        left = max(pad, width - pad - label_width)
        draw.rectangle((left - 4, 3, width - pad + 4, max(24, width // 45)), fill=(15, 28, 43, 240))
        draw.text((left, 6), label, font=watermark_font, fill=(255, 220, 151, 255))
    if story_card:
        return image
    lines = media._wrap(draw, shot["caption"], font, width - pad * 2)[:2]
    top = header + pad
    line_height = max(18, width // 28)
    draw.rectangle((pad // 2, top - 5, width - pad // 2, top + len(lines) * line_height + 5), fill=(15, 28, 43, 185))
    for line in lines:
        draw.text((pad, top), line, font=font, fill="white"); top += line_height
    visual_type = shot.get("visual_type", "scene")
    # Category-specific evidence cards only rearrange the validated caption.
    # Production directions are not audience copy and never appear on screen.
    if visual_type in {"data", "comparison", "checklist", "walkthrough"}:
        card_top = max(top + pad, height // 4)
        card_font = media._font(max(17, width // 24))
        small_card = media._font(max(14, width // 36))
        parts = [part.strip() for part in re.split(r"[；;。\n]|\s+[·|]\s+", shot["caption"]) if part.strip()][:4]
        if visual_type == "data":
            values = re.findall(r"\d+(?:\.\d+)?\s*(?:㎡|m²|m2|平方米|米|%|万元|元)", shot["caption"])[:3]
            highlights = " · ".join(values) or shot["caption"]
            highlight_lines = media._wrap(draw, highlights, card_font, width - pad * 3)[:3]
            card_height = max(height // 8, (len(highlight_lines) + 1) * max(24, width // 20))
            draw.rounded_rectangle((pad, card_top, width - pad, card_top + card_height), radius=max(8, pad // 2), fill=(24, 51, 83, 226))
            y = card_top + pad // 2
            for line in highlight_lines:
                draw.text((pad * 2, y), line, font=card_font, fill=(197, 224, 255, 255)); y += max(24, width // 20)
            draw.text((pad * 2, card_top + card_height - max(24, width // 28)), "按核验资料理解尺度", font=small_card, fill="white")
        elif visual_type == "comparison" and len(parts) >= 2:
            card_height = max(height // 7, width // 5)
            gap = max(10, pad // 2)
            half = (width - pad * 2 - gap) // 2
            for index, part in enumerate(parts[:2]):
                left = pad + index * (half + gap)
                draw.rounded_rectangle((left, card_top, left + half, card_top + card_height), radius=max(8, pad // 2), fill=(21 + index * 9, 43, 65 + index * 20, 225))
                y = card_top + pad // 2
                for line in media._wrap(draw, part, small_card, half - pad)[:5]:
                    draw.text((left + pad // 2, y), line, font=small_card, fill="white"); y += max(20, width // 28)
        else:
            row_height = max(45, width // 9)
            card_height = len(parts) * row_height + pad
            draw.rounded_rectangle((pad, card_top, width - pad, card_top + card_height), radius=max(8, pad // 2), fill=(18, 38, 60, 220))
            for index, part in enumerate(parts):
                y = card_top + pad // 2 + index * row_height
                draw.ellipse((pad * 2, y + 3, pad * 2 + max(14, width // 35), y + 3 + max(14, width // 35)), fill=(86, 157, 244, 255))
                text_x = pad * 2 + max(23, width // 25)
                for line in media._wrap(draw, part, small_card, width - text_x - pad * 2)[:2]:
                    draw.text((text_x, y), line, font=small_card, fill="white"); y += max(20, width // 32)
    return image


def render_shot(db, source, mime, voice, duration, cues, shot, chapter, project, title, is_test, size, directory, output, *, story_card=False):
    from . import media
    from .content_skills.tools.av import MEDIA_FORMATS
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    width, height = size
    # PNG cue frames work with the bundled FFmpeg even without libass/drawtext.
    # They change at actual returned speech boundaries; model text stays pixels.
    overlay_base = _overlay(size, shot, chapter, project, is_test, story_card=story_card)
    points = sorted({0.0, duration, *[max(0.0, min(duration, value)) for cue in cues for value in (cue["start"], cue["end"])]})
    manifest, overlay_index = [], 0
    for begin, end in zip(points, points[1:]):
        if end - begin < .001:
            continue
        overlay_image = overlay_base.copy()
        midpoint = (begin + end) / 2
        cue = next((cue for cue in cues if cue["start"] <= midpoint < cue["end"]), None)
        if cue is not None:
            draw = ImageDraw.Draw(overlay_image)
            pad = max(12, width // 25)
            font = media._font(max(14, width // 29))
            lines = media._wrap(draw, cue["text"], font, width - pad * 2)
            line_height = max(20, width // 23)
            top = height - line_height * len(lines) - pad
            draw.rectangle((0, top - pad // 2, width, height), fill=(9, 16, 26, 215))
            for line in lines:
                draw.text((pad, top), line, font=font, fill="white"); top += line_height
        overlay = directory / f"cue-{overlay_index:03d}.png"
        overlay_image.save(overlay)
        manifest.extend([f"file '{overlay.name}'", f"duration {end - begin:.6f}"])
        overlay_index += 1
    manifest.append(f"file 'cue-{overlay_index-1:03d}.png'")
    overlay_manifest = directory / "overlays.txt"
    overlay_manifest.write_text("\n".join(manifest), encoding="utf-8")
    base = [settings.ffmpeg_binary, "-hide_banner", "-v", "error", "-y", "-threads", "2", "-filter_complex_threads", "1"]
    def input_file(path):
        return ["-format_whitelist", MEDIA_FORMATS, "-protocol_whitelist", "file,pipe", "-i", str(path)]
    if mime.startswith("image/"):
        try:
            with Image.open(source) as original:
                if original.width * original.height > media.MAX_IMAGE_PIXELS:
                    raise ValueError("素材照片分辨率过大")
                background = ImageOps.fit(ImageOps.exif_transpose(original).convert("RGB"), (width * 2, height * 2))
            frame = directory / "background.png"; background.save(frame)
        except (OSError, Image.DecompressionBombError):
            raise ValueError("分镜图片无法解码") from None
        frames = math.ceil(duration * 25)
        motion = (f"zoompan=z='1.02+0.055*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                  f"d={frames}:s={width}x{height}:fps=25,setsar=1")
        command = base + input_file(frame) + input_file(voice)
    elif mime.startswith("video/"):
        info, available = media._probe(source, db)
        if not any(stream.get("codec_type") == "video" for stream in info.get("streams", [])) or shot["start"] + duration > available + .05:
            raise ValueError("分镜实际口播时长超过授权视频可截取范围，请补充素材")
        motion = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
        command = base + ["-ss", str(shot["start"])] + input_file(source) + input_file(voice)
    else:
        raise ValueError("长视频分镜只支持授权照片或视频")
    command += ["-f", "concat", "-safe", "1", "-protocol_whitelist", "file,pipe", "-i", str(overlay_manifest)]
    filters = f"[0:v]{motion}[bg];[bg][2:v]overlay=0:0:eof_action=repeat[v];[1:a]apad[a]"
    command += ["-filter_complex", filters, "-map", "[v]", "-map", "[a]", "-t", str(math.ceil(duration * 25) / 25),
                "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)]
    media._run(command, db, timeout=180, cwd=directory)


def _render_progress(db, board, progress, label, stage, completed_shots=0):
    """Only safe counters leave the render transaction, never voice/script text."""
    from . import media
    media._check_cancel(db)
    callback = db.info.get("job_progress")
    completed_chapters = sum(
        all(index < completed_shots for index, shot in enumerate(board["shots"]) if shot["chapter_id"] == chapter["id"])
        for chapter in board["chapters"])
    counters = {"completed_chapters": completed_chapters, "total_chapters": len(board["chapters"]),
                "completed_shots": completed_shots, "total_shots": len(board["shots"]),
                "target_seconds": int(board["target_seconds"]), "stage": stage}
    if callback:
        callback(progress=progress, stage=label, longform_progress=counters)
    return counters


def handle_longform_render(db, job, content, project, ctx):
    from . import media
    from .business import content_snapshot_hash, validate_content_ready
    from .business_models import ContentVersion
    board = normalize_storyboard(content.storyboard)
    _render_progress(db, board, .06, "准备章节视频与普通配音", "prepare")
    validate_content_ready(db, ctx, content)
    frozen = content_snapshot_hash(db, content)
    def guard():
        media._check_cancel(db)
        db.expire_all()
        active = context_for_user(db, job.user_id, job.tenant_id)
        require_project(db, active, job.property_id, roles=["admin", "owner", "editor", "content"])
        current = db.get(ContentVersion, content.id)
        validate_content_ready(db, active, current)
        if content_snapshot_hash(db, current) != frozen:
            raise ValueError("资料、授权或内容已改变，请重新渲染当前版本")
    source_files = {}
    for shot in board["shots"]:
        material = media._asset(db, job, shot["asset_id"])
        if material.origin_type != "actual" and not project.is_test:
            raise ValueError("真实项目请使用授权楼盘实拍素材")
        if material.id not in source_files:
            source_files[material.id] = (media._verified_path(material), material.mime_type,
                                         (material.metadata_json or {}).get("presentation"))
    size = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720), "4:5": (720, 900)}.get(content.aspect_ratio)
    if size is None:
        raise ValueError("不支持的成片画幅")
    chapters = {chapter["id"]: chapter for chapter in board["chapters"]}
    with tempfile.TemporaryDirectory(prefix="hs-longform-") as temp:
        root = Path(temp); measured, voices, cues, receipts = [], [], [], []
        # Measure every narration before encoding any frame. No duration padding.
        for index, shot in enumerate(board["shots"]):
            _render_progress(db, board, .08 + .30 * index / len(board["shots"]), f"合成口播 {index + 1}/{len(board['shots'])}", "voiceover")
            directory = root / f"shot-{index:03d}"; directory.mkdir()
            voice, duration, boundaries = synthesize_shot(db, job, shot["voiceover"], board["voiceover"], directory, guard)
            voices.append(voice); measured.append(duration); cues.append(boundaries)
            receipts.append({"shot_id": shot["id"], "chapter_id": shot["chapter_id"],
                             "voiceover_text_sha256": hashlib.sha256(shot["voiceover"].encode()).hexdigest(),
                             "audio_sha256": hashlib.sha256(voice.read_bytes()).hexdigest(),
                             "decoded_audio_seconds": duration})
        speech_total = validate_measured_duration(measured, board["target_seconds"])
        aligned = [math.ceil(value * 25) / 25 for value in measured]
        total = sum(aligned)
        if total > 600:
            raise ValueError("真实音轨与帧对齐后超过10分钟，请精简口播")
        elapsed, srt, timeline, chapter_times = 0.0, [], [], {}
        narration = root / "narration.wav"
        with wave.open(str(narration), "wb") as combined:
            combined.setnchannels(1); combined.setsampwidth(2); combined.setframerate(24000)
            for index, (shot, voice, duration) in enumerate(zip(board["shots"], voices, measured)):
                guard()
                _render_progress(db, board, .40 + .38 * index / len(board["shots"]), f"制作镜头 {index + 1}/{len(board['shots'])}", "render", index)
                path, mime, presentation = source_files[shot["asset_id"]]
                # A replaced upload must not be reused after a long TTS phase.
                media._verified_path(media._asset(db, job, shot["asset_id"]))
                directory = root / f"shot-{index:03d}"
                render_shot(db, path, mime, voice, duration, cues[index], shot, chapters[shot["chapter_id"]]["title"],
                            project.name, content.cover_title, project.is_test, size, directory, root / f"clip-{index:03d}.mp4",
                            story_card=presentation == "story_card")
                for cue in cues[index]:
                    srt.append(f"{len(srt)+1}\n{media._timestamp(elapsed+cue['start'])} --> {media._timestamp(elapsed+cue['end'])}\n{cue['text']}\n")
                with wave.open(str(voice), "rb") as source:
                    frames = source.readframes(source.getnframes()); combined.writeframes(frames)
                    combined.writeframes(b"\0\0" * max(0, round((aligned[index] - duration) * 24000)))
                chapter_times.setdefault(shot["chapter_id"], {"id": shot["chapter_id"], "title": chapters[shot["chapter_id"]]["title"], "start": elapsed})["end"] = elapsed + aligned[index]
                timeline.append({**receipts[index], "start": elapsed, "end": elapsed + aligned[index]})
                elapsed += aligned[index]
                _render_progress(db, board, .40 + .38 * (index + 1) / len(board["shots"]), f"已制作镜头 {index + 1}/{len(board['shots'])}", "render", index + 1)
        manifest = root / "clips.txt"
        manifest.write_text("\n".join(f"file 'clip-{index:03d}.mp4'\nduration {aligned[index]:.6f}" for index in range(len(board["shots"]))), encoding="utf-8")
        video = root / "output.mp4"
        _render_progress(db, board, .81, "合并章节、真实口播与镜头", "merge", len(board["shots"]))
        media._run([settings.ffmpeg_binary, "-hide_banner", "-v", "error", "-y", "-threads", "2", "-f", "concat", "-safe", "1",
                    "-protocol_whitelist", "file,pipe", "-i", str(manifest), "-protocol_whitelist", "file,pipe", "-i", str(narration),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
                    "-t", str(total), "-movflags", "+faststart", str(video)], db, timeout=180, cwd=root)
        _render_progress(db, board, .86, "完整解码校验成片", "decode", len(board["shots"]))
        info, decoded = media._probe(video, db)
        if abs(decoded - total) > .75 or not 300 <= decoded <= 600 or not {"h264", "aac"} <= {stream.get("codec_name") for stream in info.get("streams", [])}:
            raise ValueError("长视频成片音视频或实际时长校验失败")
        media._run([settings.ffmpeg_binary, "-hide_banner", "-v", "error", "-xerror", "-threads", "2",
                    "-protocol_whitelist", "file,pipe", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], db, timeout=300)
        guard()
        metadata = {"renderer": "ffmpeg", "schema_version": 2, "content_version_id": content.id,
                    "content_snapshot_sha256": frozen, "duration": decoded, "target_seconds": board["target_seconds"],
                    "decoded_voice_seconds": speech_total, "speech_coverage": speech_total / decoded,
                    "voiceover_enabled": True, "voice_engine": "edge",
                    "voice": board["voiceover"]["voice"], "voice_timing_source": "actual_provider_boundaries_or_measured_shot_audio",
                    "width": size[0], "height": size[1], "shot_count": len(board["shots"]),
                    "chapters": list(chapter_times.values()), "shot_timeline": timeline, "shot_audio_receipts": receipts,
                    "narration_text_sha256": hashlib.sha256("\n".join(shot["voiceover"] for shot in board["shots"]).encode()).hexdigest(),
                    "is_test": project.is_test, "watermarked": bool(project.is_test), "complete_decode_verified": True}
        final_progress = _render_progress(db, board, .94, "保存长视频、口播、章节与字幕", "save", len(board["shots"]))
        voice_asset = media._artifact(db, job, narration, "audio", "audio/wav", {**metadata, "artifact_type": "voiceover"}, project, "普通配音 · " + content.topic[:80])
        metadata.update(voiceover_audio_asset_id=voice_asset.id, voiceover_audio_sha256=voice_asset.sha256,
                        voiceover_audio_rights_revision=voice_asset.rights_revision)
        subtitle = root / "captions.srt"; subtitle.write_text("\n".join(srt), encoding="utf-8")
        chapters_path = root / "chapters.json"; chapters_path.write_text(json.dumps({"schema_version": 2, "chapters": metadata["chapters"], "shots": timeline}, ensure_ascii=False), encoding="utf-8")
        cover = root / "cover.jpg"
        media._run([settings.ffmpeg_binary, "-hide_banner", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(video), "-frames:v", "1", str(cover)], db, timeout=90)
        rendered = media._artifact(db, job, video, "video", "video/mp4", metadata, project, "长视频 · " + content.topic[:80])
        subtitles = media._artifact(db, job, subtitle, "subtitle", "application/x-subrip", metadata, project, "字幕 · " + content.topic[:80])
        cover_asset = media._artifact(db, job, cover, "image", "image/jpeg", {**metadata, "artifact_type": "cover"}, project, "封面 · " + content.topic[:80])
        chapter_asset = media._artifact(db, job, chapters_path, "text", "application/json", {**metadata, "artifact_type": "chapters"}, project, "章节与实测分镜 · " + content.topic[:80])
        current = db.get(ContentVersion, content.id); current.render_asset_id = rendered.id; current.status = "rendered"; db.flush()
        return {"content_version_id": current.id, "render_asset_id": rendered.id, "subtitle_asset_id": subtitles.id,
                "cover_asset_id": cover_asset.id, "voiceover_asset_id": voice_asset.id, "chapters_asset_id": chapter_asset.id,
                "duration": decoded, "decoded_voice_seconds": speech_total, "shot_count": len(board["shots"]),
                "longform_progress": final_progress}
