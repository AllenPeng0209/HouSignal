"""Native, bounded Model Studio media adapters; no legacy service or env loader.

Reworked from Easel ai_image/ai_video/tts provider orchestration (Apache-2.0).
Uses only Louxiang's configured credentials/models and trusted worker callbacks.
HTTP contracts were checked against Alibaba Cloud's official Model Studio docs;
unsupported model protocols remain setup instead of manufacturing output.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.parse import urlsplit

import httpx
from PIL import Image

from ...config import settings


def _secret():
    value = settings.dashscope_api_key
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def configured(kind):
    if kind == "video" and getattr(settings, "native_video_backend", "dashscope") == "lightx2v":
        try:
            _ppu_base_url()
            return settings.native_video_model == "minimax-h3-dense8"
        except ValueError:
            return False
    return bool(_secret() and getattr(settings, f"dashscope_{kind}_model", ""))


def _ppu_base_url():
    """Only a project-configured private tunnel may reach the native runtime."""
    try:
        parsed = urlsplit(settings.native_video_base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.port is None or parsed.port == 8878 or parsed.path not in {"", "/"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError()
        return f"http://{parsed.hostname}:{parsed.port}"
    except ValueError:
        raise ValueError("请配置本项目原生PPU私有隧道地址，不使用技能服务或公网接口") from None


def _ppu_generate(*, input_text, assets, parameters, output_dir, context):
    """Submit once to resident H3, download and fully decode native video/audio."""
    base = _ppu_base_url()
    model = settings.native_video_model
    if model != "minimax-h3-dense8":
        raise ValueError("本项目原生PPU目前仅验收minimax-h3-dense8模型")
    size = _frame(parameters, image=False)
    if size not in {"576*1024", "1024*576"}:
        raise ValueError("已验收PPU画幅为576x1024或1024x576")
    duration = parameters.get("duration", 5)
    if type(duration) is not int or not 2 <= duration <= 14:
        raise ValueError("原生PPU视频时长需为2至14秒整数，实际时长按H3帧网格返回")
    if not isinstance(input_text, str) or not 1 <= len(input_text.strip()) <= 12000:
        raise ValueError("请填写有效视频生成提示词")
    seed = parameters.get("seed", 42)
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("seed需为0至2147483647的整数")
    if len(assets) > 1:
        raise ValueError("原生PPU图生视频仅接受一张已授权PNG/JPEG首帧")
    width, height = map(int, size.split("*"))
    frames = math.ceil(duration * 24) + 1
    frames += (5 - frames) % 17
    negative = parameters.get("negative_prompt", "")
    if not isinstance(negative, str) or len(negative) > 2000:
        raise ValueError("negative_prompt需为不超过2000字符的文字")
    actual_prompt = input_text
    if negative:
        # Native H3 rejects a negative_prompt request field. Preserve the user's
        # exclusions explicitly in its supported text prompt instead of silently
        # dropping them or claiming separate negative conditioning.
        actual_prompt += "\n\nConstraints: avoid the following unwanted content or artifacts:\n" + negative
    if len(actual_prompt) > 12000:
        raise ValueError("视频提示词与排除要求合计超过12000字符，请缩短后重试；不会截断要求")
    payload = {"task": "i2av" if assets else "t2av", "prompt": actual_prompt, "seed": seed,
               "num_frames": frames, "size": [height, width],
               "save_result_path": "./housignal-" + uuid.uuid4().hex + ".mp4"}
    if assets:
        data = _image_input(assets, max_count=1)[0]["image"]
        if not data.startswith(("data:image/png;", "data:image/jpeg;")):
            raise ValueError("原生PPU首帧需要PNG/JPEG格式")
        payload["image_path"] = data.split(",", 1)[1]
    client = context.get("media_http_client") or httpx.Client(timeout=90, follow_redirects=False, trust_env=False)
    owned = "media_http_client" not in context
    record_path = output_dir / "video-submission.json"
    actual_prompt_sha = hashlib.sha256(actual_prompt.encode()).hexdigest()
    record = {"backend": "lightx2v", "model": model, "prompt_sha256": actual_prompt_sha,
              "original_prompt_sha256": hashlib.sha256(input_text.encode()).hexdigest(),
              "actual_prompt_sha256": actual_prompt_sha,
              "prompt_adaptation": "negative_constraints_in_text" if negative else None,
              "requested_duration_seconds": duration, "expected_frames": frames, "width": width, "height": height,
              "seed": seed, "state": "preflight"}
    started = time.monotonic()
    partial, target = output_dir / "generated.partial.mp4", output_dir / "generated.mp4"
    try:
        metadata = _json(client, "GET", base + "/v1/service/metadata", context=context, headers={})
        if metadata.get("model_cls") != "minimax_h3":
            raise ValueError("原生PPU当前运行模型与本项目已验收H3模型不一致")
        queue = _json(client, "GET", base + "/v1/tasks/queue/status", context=context, headers={})
        if queue.get("is_processing") is not False or queue.get("active_count") != 0 or queue.get("pending_count") != 0:
            raise ValueError("既有PPU视频服务正在处理其他任务，请稍后重试")
        if record_path.exists():
            raise ValueError("此目录已有原生PPU提交记录，请查询原任务，不能自动重复生成")
        record["state"] = "submitting"
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        context = _usage_context(context, model)
        submitted = _json(client, "POST", base + "/v1/tasks/video/", context=context, headers={}, payload=payload)
        task = submitted.get("task_id")
        if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task):
            raise ValueError("原生PPU返回的任务标识无效；请保留提交记录核查")
        record.update(backend_task_id=task, state="submitted")
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        deadline = time.monotonic() + settings.native_video_timeout_seconds
        while time.monotonic() < deadline:
            state = _json(client, "GET", base + f"/v1/tasks/{task}/status", context=context, headers={})
            status = state.get("status")
            if status == "completed":
                break
            if status in {"failed", "cancelled", "canceled"}:
                raise ValueError("原生PPU视频生成失败，已保留真实任务标识")
            if status not in {"pending", "queued", "processing", "running", "in_progress"}:
                raise ValueError("原生PPU任务状态无效，已保留真实任务标识")
            check = context.get("check_cancelled")
            if callable(check):
                check()
            time.sleep(2)
        else:
            raise ValueError("原生PPU生成超时，原任务可能仍在运行，请查询提交记录而非重复生成")
        _before(context)
        count = 0
        try:
            with client.stream("GET", base + f"/v1/tasks/{task}/result", timeout=90, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError()
                with partial.open("wb") as output:
                    for chunk in response.iter_bytes():
                        count += len(chunk)
                        if count > 100 * 1024 * 1024:
                            raise ValueError()
                        output.write(chunk)
            if not count:
                raise ValueError()
        except (httpx.HTTPError, ValueError):
            raise ValueError("原生PPU产物下载失败或超过100MB，已保留真实任务标识") from None
        from .av import MEDIA_FORMATS
        probe = subprocess.run([context.get("ffprobe_binary", settings.ffprobe_binary), "-v", "error", "-format_whitelist", MEDIA_FORMATS,
                                "-protocol_whitelist", "file,pipe", "-count_frames", "-show_entries",
                                "stream=codec_type,width,height,nb_read_frames,r_frame_rate,duration:format=duration", "-of", "json", str(partial)],
                               capture_output=True, timeout=90)
        try:
            info = json.loads(probe.stdout)
            videos = [stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"]
            audios = [stream for stream in info.get("streams", []) if stream.get("codec_type") == "audio"]
            if not audios or not any(int(stream.get("nb_read_frames", 0)) > 0 for stream in audios):
                raise ValueError("原生H3产物没有实际音频轨道")
            if (probe.returncode or len(videos) != 1 or videos[0].get("width") != width or videos[0].get("height") != height
                    or int(videos[0].get("nb_read_frames", 0)) != frames or videos[0].get("r_frame_rate") != "24/1"):
                raise ValueError("原生PPU视频画幅、帧数或帧率与请求不一致")
            actual_duration = float(videos[0].get("duration") or info["format"]["duration"])
            if not math.isfinite(actual_duration) or abs(actual_duration - frames / 24) > 0.08:
                raise ValueError("原生PPU视频实际时长与模型帧网格不一致")
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("原生PPU产物媒体信息无效") from None
        decoded = subprocess.run([context.get("ffmpeg_binary", settings.ffmpeg_binary), "-v", "error", "-xerror", "-format_whitelist", MEDIA_FORMATS,
                                  "-protocol_whitelist", "file,pipe", "-i", str(partial), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                                 capture_output=True, timeout=90)
        if decoded.returncode:
            raise ValueError("原生PPU完整视频和音频解码未通过")
        partial.replace(target)
        receipt = {**record, "state": "generated", "backend_metadata": {key: metadata[key] for key in ("model_cls", "nproc_per_node") if key in metadata},
                   "actual_duration_seconds": actual_duration, "frames_decoded": frames, "fps": 24,
                   "has_native_audio": True, "all_video_and_audio_frames_decoded": True,
                   "elapsed_seconds": round(time.monotonic() - started, 3), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                   "billing_kind": "existing_compute_no_per_request_cloud_api",
                   "cost_note": "既有PPU实例；无按次供应商API调用，基础算力费用未验证为0"}
        receipt_path = output_dir / "video-receipt.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        usage = {"video_seconds": actual_duration, "video_frames": frames}
        _receipt(context, {"usage": usage, "request_id": task}, model)
        return {"response": "已生成并完整解码本项目原生PPU视频与音轨，请复核画面后使用。", "model": model,
                "artifact_paths": [target, record_path, receipt_path], "usage": usage}
    finally:
        partial.unlink(missing_ok=True)
        if owned:
            client.close()


def base_url():
    parsed = urlsplit(settings.dashscope_base_url)
    allowed = parsed.hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"} or bool(re.fullmatch(r"[a-z0-9-]+\.(cn-beijing|ap-southeast-1|us-east-1|eu-central-1|cn-hongkong)\.maas\.aliyuncs\.com", parsed.hostname or ""))
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError("本项目模型服务地址配置无效")
    return f"https://{parsed.hostname}/api/v1"


def _before(context):
    callback = context.get("before_model_call")
    if callable(callback):
        callback()


def _usage_context(context, model=None):
    """Bind one real provider action before it starts, without mutating callers."""
    scoped = dict(context)
    if isinstance(model, str) and model:
        scoped["usage_model"] = model
    begin = scoped.get("begin_usage_call")
    if callable(begin):
        token = begin()
        try:
            if not isinstance(token, str):
                raise ValueError()
            uuid.UUID(token)
            scoped["usage_call_id"] = token
        except (ValueError, TypeError, AttributeError):
            raise ValueError("媒体用量调用标识无效") from None
    return scoped


def _receipt(context, data, model):
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    safe = {key: value for key, value in usage.items() if type(value) in (int, float) and math.isfinite(value) and value >= 0}
    token_fields = {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens"}
    record = {"model": model, "input_tokens": int(safe.get("input_tokens") or safe.get("prompt_tokens") or 0),
              "output_tokens": int(safe.get("output_tokens") or safe.get("completion_tokens") or 0),
              "units": {key: value for key, value in safe.items() if key not in token_fields}}
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    call_id = output.get("task_id") or data.get("request_id")
    if isinstance(call_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call_id):
        record["call_id"] = call_id
    token = context.get("usage_call_id")
    if isinstance(token, str):
        record["usage_call_id"] = token
    callback = context.get("on_usage")
    if callable(callback):
        callback(record)
    return safe


def _reported_receipt(context, data, model=None):
    """Retain supplier-reported counters before a failure/cancel gate rejects it."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict) or not any(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in usage.values()):
        return
    model = model or context.get("usage_model")
    if isinstance(model, str) and model:
        _receipt(context, data, model)


def _json(client, method, url, *, context, headers, payload=None):
    _before(context)
    try:
        response = client.request(method, url, headers=headers, json=payload, timeout=90, follow_redirects=False)
        if response.status_code != 200:
            try:
                _reported_receipt(context, response.json())
            except json.JSONDecodeError:
                pass
            raise ValueError("本项目媒体模型调用失败，请检查模型权限、额度与参数")
        data = response.json()
        if not isinstance(data, dict) or data.get("code"):
            _reported_receipt(context, data)
            raise ValueError("媒体模型返回错误，请检查模型配置")
        return data
    except (httpx.HTTPError, json.JSONDecodeError):
        raise ValueError("媒体模型连接失败或返回格式无效") from None


def _poll(client, data, *, model, context, headers, timeout=300):
    task = data.get("output", {}).get("task_id")
    if not task:
        return data
    if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task):
        raise ValueError("媒体任务标识无效")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _json(client, "GET", base_url() + "/tasks/" + task, context=context, headers=headers)
        status = result.get("output", {}).get("task_status")
        if status == "SUCCEEDED":
            return result
        _reported_receipt(context, result, model)
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            raise ValueError("媒体生成任务失败，请检查模型和输入参数")
        if status not in {"PENDING", "RUNNING", "SUSPENDED"}:
            raise ValueError("媒体任务状态无效")
        check = context.get("check_cancelled")
        if callable(check):
            check()
        time.sleep(2)
    raise ValueError("媒体生成超时；任务已提交，请保留当前任务记录后重试查询")


def _result_media(data, kind):
    output = data.get("output", {})
    if kind == "audio" and isinstance(output.get("audio"), dict):
        return output["audio"].get("url") or output["audio"].get("data")
    if kind == "video":
        return output.get("video_url")
    if kind == "image":
        results = output.get("results", [])
        if results and isinstance(results[0], dict):
            return results[0].get("url")
        for choice in output.get("choices", []):
            for part in choice.get("message", {}).get("content", []):
                if isinstance(part, dict) and part.get("image"):
                    return part["image"]
    return None


def _image_results(data):
    output = data.get("output", {})
    values = [result["url"] for result in output.get("results", []) if isinstance(result, dict) and result.get("url")]
    for choice in output.get("choices", []):
        for part in choice.get("message", {}).get("content", []):
            if isinstance(part, dict) and part.get("image"):
                values.append(part["image"])
    return values


def _download(client, value, path, *, context, kind):
    if not isinstance(value, str) or not value:
        raise ValueError("媒体模型没有返回实际产物")
    if value.startswith("data:"):
        try:
            header, encoded = value.split(",", 1)
            if ";base64" not in header or not header.startswith("data:" + kind + "/"):
                raise ValueError()
            contents = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ValueError("媒体模型返回的内联文件无效") from None
        if not contents or len(contents) > 256 * 1024 * 1024:
            raise ValueError("媒体产物为空或超过256MB")
        path.write_bytes(contents)
        return
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not any(host.endswith(suffix) for suffix in (".aliyuncs.com", ".alicdn.com", ".aliyun.com")) or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError("模型产物地址不在允许的云存储域名内")
    # Official examples may return HTTP OSS URLs; download through HTTPS.
    if parsed.scheme == "http":
        value = "https://" + value.removeprefix("http://")
    _before(context)
    count = 0
    try:
        with client.stream("GET", value, timeout=90, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError("媒体产物下载失败")
            with path.open("wb") as output:
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > 256 * 1024 * 1024:
                        raise ValueError("媒体产物超过256MB")
                    output.write(chunk)
        if not count:
            raise ValueError("媒体产物为空")
    except (httpx.HTTPError, ValueError):
        path.unlink(missing_ok=True)
        raise ValueError("媒体产物下载失败或超过处理上限") from None


def _frame(parameters, *, image):
    size = parameters.get("size")
    if not isinstance(size, str) or not re.fullmatch(r"\d{2,4}[x*]\d{2,4}", size):
        raise ValueError("请明确画幅尺寸size，例如1024x1024或1280x720")
    width, height = [int(part) for part in re.split("[x*]", size)]
    if min(width, height) < (256 if image else 240) or max(width, height) > 4096 or width * height > 16_777_216:
        raise ValueError("画幅尺寸超过模型处理范围")
    return f"{width}*{height}"


def _image_input(assets, *, max_count=3):
    if len(assets) > max_count:
        raise ValueError(f"此模型最多接收{max_count}张参考图片，请明确选择需要的角色或素材")
    parts = []
    for asset in assets:
        path = Path(asset["path"])
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("模型参考图片超过10MB")
        try:
            with Image.open(path) as image:
                if max(image.size) > 8000 or image.width * image.height > 30_000_000:
                    raise ValueError("模型参考图分辨率过大")
                image.verify()
        except OSError:
            raise ValueError("参考素材不是有效图片") from None
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/" + path.suffix.lower().lstrip(".")
        parts.append({"image": "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode()})
    return parts


def generate(kind, *, input_text, assets, parameters, output_dir, context):
    if kind == "video" and settings.native_video_backend == "lightx2v":
        return _ppu_generate(input_text=input_text, assets=assets, parameters=parameters, output_dir=output_dir, context=context)
    model = getattr(settings, f"dashscope_{kind}_model", "")
    if not configured(kind):
        raise ValueError(f"请配置本项目百炼{kind}模型与服务凭据")
    if kind == "image" and parameters.get("op") in {"img2img", "variations"} and not assets:
        raise ValueError("图生图或变体需要选择本项目参考图片")
    if kind == "image" and parameters.get("op") == "variations" and not input_text.strip():
        input_text = "Generate a visual variation of the supplied reference image, preserving the subject."
    if not input_text.strip() or len(input_text) > 12000:
        raise ValueError("请填写有效媒体生成提示词")
    headers = {"Authorization": "Bearer " + _secret()}
    if kind == "image":
        size = _frame(parameters, image=True)
        count = parameters.get("n", 1)
        if type(count) is not int or not 1 <= count <= 4:
            raise ValueError("图片数量n需为1至4的整数")
        if model.startswith(("qwen-image-2.1", "qwen-image-3.0", "wan2.6-image", "wan2.6-t2i", "wan2.7-image")):
            if model.startswith("wan2.6-t2i") and assets:
                raise ValueError("wan2.6-t2i仅支持文生图；参考图编辑请选择对应图片编辑型号")
            path = "/services/aigc/multimodal-generation/generation"
            maximum = 10 if model.startswith("qwen-image-2.1") else 9 if model.startswith("wan2.7-image") else 4 if model.startswith("wan2.6-image") else 3
            content = _image_input(assets, max_count=maximum) + [{"text": input_text}]
            payload = {"model": model, "input": {"messages": [{"role": "user", "content": content}]}, "parameters": {"size": size, "n": count, "watermark": False}}
            if model.startswith("wan2.6-image"):
                payload["parameters"]["enable_interleave"] = not bool(assets)
                if not assets:
                    payload["parameters"]["max_images"] = payload["parameters"].pop("n")
        elif model.startswith(("wanx", "wan2.1-t2i", "wan2.2-t2i", "wan2.5-t2i", "qwen-image")) and not assets:
            path = "/services/aigc/image-generation/generation" if model.startswith(("wan2.1-t2i", "wan2.2-t2i", "wan2.5-t2i")) else "/services/aigc/text2image/image-synthesis"
            payload = {"model": model, "input": {"prompt": input_text}, "parameters": {"size": size, "n": count, "watermark": False}}
            headers["X-DashScope-Async"] = "enable"
        else:
            raise ValueError("所配置图片模型协议暂不支持，请配置受支持的百炼图片模型")
        extension, mime_kind = "png", "image"
    elif kind == "video":
        size = _frame(parameters, image=False)
        duration = parameters.get("duration", 5)
        if type(duration) is not int or not 2 <= duration <= 15:
            raise ValueError("视频duration需为2至15秒整数")
        if not model.startswith("wan"):
            raise ValueError("所配置视频模型协议暂不支持，请配置万相视频模型")
        source = {"prompt": input_text}
        params = {"duration": duration, "watermark": False, "prompt_extend": False}
        if assets:
            if not "i2v" in model:
                raise ValueError("参考图生成视频需要配置i2v模型")
            reference = _image_input(assets[:1])[0]["image"]
            if model.startswith("wan2.7"):
                source["media"] = [{"type": "first_frame", "url": reference}]
            else:
                source["img_url"] = reference
            params["resolution"] = parameters.get("resolution", "720P")
        else:
            if not "t2v" in model:
                raise ValueError("文生视频需要配置t2v模型")
            if model.startswith("wan2.7"):
                width, height = [int(part) for part in size.split("*")]
                ratios = {(1280, 720): "16:9", (720, 1280): "9:16", (960, 960): "1:1", (1920, 1080): "16:9", (1080, 1920): "9:16", (1440, 1440): "1:1"}
                if (width, height) not in ratios:
                    raise ValueError("该万相模型需要明确支持的720P/1080P画幅")
                params.update({"ratio": ratios[(width, height)], "resolution": "1080P" if max(width, height) > 1440 else "720P"})
            else:
                params["size"] = size
        path = "/services/aigc/video-generation/video-synthesis"
        payload = {"model": model, "input": source, "parameters": params}
        headers["X-DashScope-Async"] = "enable"
        extension, mime_kind = "mp4", "video"
    elif kind == "tts":
        if not model.startswith(("qwen3-tts", "qwen-audio-3")) or "realtime" in model:
            raise ValueError("所配置配音模型需支持HTTP语音合成")
        if len(input_text) > 600:
            raise ValueError("单句配音最多600字符，请分句合成")
        voice = parameters.get("voice") or parameters.get("voice_id") or getattr(settings, "dashscope_tts_voice", "") or "Cherry"
        if not isinstance(voice, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", voice):
            raise ValueError("音色voice无效")
        public_voice = bool(re.fullmatch(r"[A-Za-z]{1,80}", voice)) or bool(re.fullmatch(r"(?:long|loong)[a-z]+(?:_v[23])?", voice))
        if "-vc" in model or not public_voice:
            allowed = context.get("allowed_voice_ids")
            if not isinstance(allowed, (list, tuple, set, frozenset)) or voice not in allowed:
                raise ValueError("复刻音色不属于当前项目或其源素材授权已失效")
        path = "/services/aigc/multimodal-generation/generation"
        payload = {"model": model, "input": {"text": input_text, "voice": voice, "language_type": parameters.get("language", "Auto")}}
        extension, mime_kind = "wav", "audio"
    else:
        raise ValueError("请配置并接入本项目受支持的音乐生成模型适配器")
    if parameters.get("seed") is not None:
        if type(parameters["seed"]) is not int or not 0 <= parameters["seed"] <= 2147483647:
            raise ValueError("seed需为0至2147483647的整数")
        payload.setdefault("parameters", {})["seed"] = parameters["seed"]
    if parameters.get("negative_prompt"):
        if not isinstance(parameters["negative_prompt"], str) or len(parameters["negative_prompt"]) > 2000:
            raise ValueError("negative_prompt需为不超过2000字符的文字")
        if kind == "image" and model.startswith("qwen-image-2.1"):
            payload["input"]["messages"][0]["content"][-1]["text"] += "\nAvoid: " + parameters["negative_prompt"]
        elif kind == "video":
            payload["input"]["negative_prompt"] = parameters["negative_prompt"]
        else:
            payload.setdefault("parameters", {})["negative_prompt"] = parameters["negative_prompt"]
    endpoint = base_url() + path
    target = output_dir / f"generated.{extension}"
    context = _usage_context(context, model)
    client = context.get("media_http_client") or httpx.Client(timeout=90, follow_redirects=False)
    owned = "media_http_client" not in context
    try:
        submitted = _json(client, "POST", endpoint, context=context, headers=headers, payload=payload)
        usage = _receipt(context, submitted, model)
        finished = _poll(client, submitted, model=model, context=context, headers=headers)
        if finished is not submitted and finished.get("usage"):
            usage = _receipt(context, finished, model)
        artifact_paths = []
        if kind == "image":
            values = _image_results(finished)
            if len(values) != count:
                raise ValueError("模型实际返回的图片数量与请求不一致")
            for index, value in enumerate(values):
                target = output_dir / (f"generated-{index + 1:04d}.png" if count > 1 else "generated.png")
                try:
                    _download(client, value, target, context=context, kind=mime_kind)
                    with Image.open(target) as image:
                        image.verify()
                except (OSError, ValueError):
                    target.unlink(missing_ok=True)
                    for previous in artifact_paths:
                        previous.unlink(missing_ok=True)
                    raise ValueError("模型产物不是有效图片") from None
                artifact_paths.append(target)
        else:
            _download(client, _result_media(finished, mime_kind), target, context=context, kind=mime_kind)
            from .av import MEDIA_FORMATS
            probe = subprocess.run([context.get("ffprobe_binary", settings.ffprobe_binary), "-v", "error", "-format_whitelist", MEDIA_FORMATS, "-protocol_whitelist", "file,pipe", "-show_entries", "stream=codec_type", "-of", "json", str(target)], capture_output=True, timeout=30)
            try:
                info = json.loads(probe.stdout)
                required = "audio" if kind == "tts" else "video"
                if probe.returncode or not any(stream.get("codec_type") == required for stream in info.get("streams", [])):
                    raise ValueError()
            except (ValueError, TypeError):
                target.unlink(missing_ok=True)
                raise ValueError("模型产物没有有效的媒体轨道") from None
            artifact_paths.append(target)
        return {"response": "已保存模型实际生成的媒体文件，请核对产物后使用。", "artifact_paths": artifact_paths, "usage": usage}
    finally:
        if owned:
            client.close()
