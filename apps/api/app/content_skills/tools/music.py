"""Native configured music protocols adapted from Easel ai_music.py (Apache-2.0).

DashScope asynchronous text2audio and Suno-compatible generate/feed protocols
are explicitly configured by the project. No legacy env or account is read.
The adapter submits once, polls the same task, downloads real audio and checks
it with FFprobe before transcoding. Source protocol availability is not assumed
from a model name; service permissions and responses are verified at execution.
"""
from __future__ import annotations

import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit

import httpx

from ...config import settings
from . import models


def _configuration():
    provider = getattr(settings, "native_music_provider", "dashscope")
    model = settings.dashscope_music_model
    if provider not in {"dashscope", "suno-compatible"}:
        raise ValueError("本项目音乐provider仅支持dashscope或suno-compatible")
    if not model:
        raise ValueError("请配置本项目dashscope_music_model")
    explicit_key = getattr(settings, "native_music_api_key", "")
    if hasattr(explicit_key, "get_secret_value"):
        explicit_key = explicit_key.get_secret_value()
    base = getattr(settings, "native_music_base_url", "").rstrip("/")
    if provider == "suno-compatible":
        if not explicit_key:
            raise ValueError("Suno兼容音乐需要本项目独立native_music_api_key凭据")
        if not base:
            raise ValueError("请配置本项目native_music_base_url音乐服务地址")
        key = explicit_key
    else:
        base = base or models.base_url()
        host = urlsplit(base).hostname or ""
        if not explicit_key and not host.endswith(".aliyuncs.com"):
            raise ValueError("自定义音乐端点需要独立音乐凭据，不能转发百炼主key")
        key = explicit_key or models._secret()
        if not key:
            raise ValueError("请配置本项目音乐模型服务凭据")
    parsed = urlsplit(base)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("本项目音乐服务地址配置无效")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("音乐服务地址不允许私网IP")
    except ValueError as error:
        if "私网" in str(error):
            raise
    return provider, model, base, key


def readiness():
    try:
        _configuration()
    except ValueError as error:
        return {"status": "setup", "reason": str(error)}
    if not all(shutil.which(binary) for binary in (settings.ffmpeg_binary, settings.ffprobe_binary)):
        return {"status": "setup", "reason": "本项目音乐产物需要FFmpeg/FFprobe校验"}
    return {"status": "ready", "reason": "本项目音乐协议及模型已配置；任务状态与实际音轨会在执行时核验"}


def _request(client, method, endpoint, *, headers, context, payload=None):
    models._before(context)
    try:
        response = client.request(method, endpoint, headers=headers, json=payload, timeout=90, follow_redirects=False)
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError("音乐模型调用失败，请检查本项目服务权限与参数")
        if response.status_code != 200:
            try:
                models._reported_receipt(context, response.json())
            except json.JSONDecodeError:
                pass
            raise ValueError("音乐模型调用失败，请检查本项目服务权限与参数")
        result = response.json()
        if not isinstance(result, (dict, list)) or isinstance(result, dict) and result.get("code"):
            models._reported_receipt(context, result)
            raise ValueError("音乐模型返回格式无效或服务错误")
        return result
    except (httpx.HTTPError, json.JSONDecodeError):
        raise ValueError("音乐模型连接失败或返回格式无效") from None


def _find(obj, keys, depth=0):
    if depth > 12:
        return None
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, (str, int)):
                return str(value)
        for value in obj.values():
            found = _find(value, keys, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj[:100]:
            found = _find(value, keys, depth + 1)
            if found is not None:
                return found
    return None


def _download(client, value, path, context, base):
    if not isinstance(value, str):
        raise ValueError("音乐任务没有返回实际音频文件")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    allow = {urlsplit(base).hostname, *getattr(settings, "native_music_download_hosts", [])}
    cloud = any(host.endswith(suffix) for suffix in (".aliyuncs.com", ".alicdn.com", ".aliyun.com"))
    if parsed.scheme not in {"http", "https"} or not (cloud or host in allow) or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError("音乐产物域名需要在本项目native_music_download_hosts明确允许")
    if parsed.scheme == "http":
        value = "https://" + value.removeprefix("http://")
    models._before(context)
    try:
        count = 0
        with client.stream("GET", value, timeout=90, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError()
            with path.open("wb") as output:
                for block in response.iter_bytes():
                    count += len(block)
                    if count > 100 * 1024 * 1024:
                        raise ValueError()
                    output.write(block)
        if not count:
            raise ValueError()
    except (ValueError, httpx.HTTPError):
        path.unlink(missing_ok=True)
        raise ValueError("音乐产物为空、下载失败或超过100MB") from None


def dispatch(*, input_text, assets, parameters, output_dir, context):
    provider, model, base, key = _configuration()
    if assets:
        raise ValueError("音乐生成只接收提示词与歌词，不上传未声明的素材")
    if not isinstance(input_text, str) or not input_text.strip() or len(input_text) > 5000:
        raise ValueError("音乐生成提示词需要1至5000字符")
    duration = parameters.get("duration")
    if duration is not None and (type(duration) is not int or not 1 <= duration <= 600):
        raise ValueError("音乐duration需为1至600秒整数")
    instrumental = parameters.get("instrumental", True)
    lyrics = parameters.get("lyrics")
    if type(instrumental) is not bool or lyrics is not None and (not isinstance(lyrics, str) or len(lyrics) > 10000):
        raise ValueError("音乐instrumental或lyrics参数无效")
    headers = {"Authorization": "Bearer " + key}
    if provider == "dashscope":
        headers["X-DashScope-Async"] = "enable"
        endpoint = base + "/services/aigc/text2audio/generation"
        inputs = {"prompt": input_text}
        if lyrics:
            inputs["lyrics"] = lyrics
        generation = {"instrumental": instrumental and not lyrics}
        if duration is not None:
            generation["duration"] = duration
        payload = {"model": model, "input": inputs, "parameters": generation}
    else:
        endpoint = base + "/generate"
        payload = {"model": model, "prompt": input_text, "make_instrumental": instrumental and not lyrics}
        if lyrics:
            payload["lyrics"] = lyrics
        if duration is not None:
            payload["duration"] = duration
    fmt = parameters.get("format", "mp3")
    if fmt not in {"mp3", "wav", "m4a"}:
        raise ValueError("音乐format仅支持mp3/wav/m4a")
    context = models._usage_context(context, model)
    client = context.get("media_http_client") or httpx.Client(timeout=90, follow_redirects=False)
    owned = "media_http_client" not in context
    raw = output_dir / "music-source.audio"
    target = output_dir / f"music.{fmt}"
    try:
        submitted = _request(client, "POST", endpoint, headers=headers, context=context, payload=payload)
        models._reported_receipt(context, submitted, model)
        task = _find(submitted, ("task_id", "id", "request_id"))
        if not task or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task):
            raise ValueError("音乐服务未返回有效任务标识")
        deadline = time.monotonic() + 300
        finished = None
        while time.monotonic() < deadline:
            result = _request(client, "GET", base + ("/tasks/" if provider == "dashscope" else "/feed/") + task, headers=headers, context=context)
            status = (_find(result, ("task_status", "status", "state")) or "").lower()
            if status in {"succeeded", "success", "complete", "completed"}:
                finished = result
                break
            models._reported_receipt(context, result, model)
            if status in {"failed", "error", "canceled", "cancelled", "unknown"}:
                raise ValueError("音乐生成任务失败")
            if status not in {"pending", "running", "processing", "queued", "submitted", ""}:
                raise ValueError("音乐任务状态无效")
            callback = context.get("check_cancelled")
            if callable(callback):
                callback()
            time.sleep(2)
        if finished is None:
            raise ValueError("音乐任务超时，请查询已提交的任务后再决定是否重试")
        receipt = finished if isinstance(finished, dict) else finished[0]
        receipt = {**receipt, "output": {**(receipt.get("output") or {}), "task_id": task}}
        usage = models._receipt(context, receipt, model)
        audio_url = _find(finished, ("audio_url", "audio_file", "song_url", "stream_audio_url", "mp3", "output_url", "file_url", "url"))
        _download(client, audio_url, raw, context, base)
        from .av import MEDIA_FORMATS
        probe = subprocess.run([context.get("ffprobe_binary", settings.ffprobe_binary), "-v", "error", "-format_whitelist", MEDIA_FORMATS, "-protocol_whitelist", "file,pipe", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(raw)], capture_output=True, timeout=30)
        try:
            info = json.loads(probe.stdout)
            seconds = float(info["format"]["duration"])
            if probe.returncode or not math.isfinite(seconds) or not 0 < seconds <= 600 or not any(stream.get("codec_type") == "audio" for stream in info.get("streams", [])):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError("音乐服务产物没有有效音轨或时长超过10分钟") from None
        encode = subprocess.run([context.get("ffmpeg_binary", settings.ffmpeg_binary), "-v", "error", "-y", "-format_whitelist", MEDIA_FORMATS, "-protocol_whitelist", "file,pipe", "-i", str(raw), "-vn", "-ar", "44100", "-ac", "2", str(target)], capture_output=True, timeout=180)
        if encode.returncode or not target.is_file() or not target.stat().st_size:
            target.unlink(missing_ok=True)
            raise ValueError("音乐产物无法转为目标音频格式")
        usage["generated_audio_seconds"] = seconds
        return {"response": "已保存服务实际生成的音乐，真实音轨和时长已核验。", "artifact_paths": [target], "usage": usage, "model": model}
    finally:
        raw.unlink(missing_ok=True)
        if owned:
            client.close()
