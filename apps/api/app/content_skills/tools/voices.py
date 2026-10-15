"""Project voice enrollment and cloning adapted from voice_clone.py (Apache-2.0).

Authorized samples come from project assets. Qwen-TTS voice enrollment accepts
an inline normalized sample, while older Qwen-Audio/CosyVoice uses a trusted
project storage URL adapter. Voice IDs and actual returned state are saved in
the current job; credentials, signed sample URLs and supplier payloads are not.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from ...config import settings
from . import models, speech


def readiness():
    model = settings.dashscope_tts_model
    if not models.configured("tts") or not (model.startswith("qwen3-tts-vc") or model.startswith("qwen-audio-3") or model.startswith("cosyvoice")):
        return {"status": "setup", "reason": "声音复刻需配置本项目真实复刻模型与凭据，普通TTS不能代替克隆"}
    if "realtime" in model:
        return {"status": "setup", "reason": "此原生任务使用HTTP声音复刻，请配置HTTP复刻型号"}
    return {"status": "ready", "reason": "本项目复刻模型已配置；可登记授权样本并保存实际voice_id，再生成语音"}


def dispatch(*, input_text, assets, parameters, output_dir, context):
    available = readiness()
    if available["status"] != "ready":
        raise ValueError(available["reason"])
    operation = parameters.get("op", "clone")
    if operation == "clone":
        voice = parameters.get("voice_id")
        if not isinstance(voice, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", voice):
            raise ValueError("声音克隆需选择本项目实际登记的voice_id")
        allowed = context.get("allowed_voice_ids")
        if not isinstance(allowed, (list, tuple, set, frozenset)) or voice not in allowed:
            raise ValueError("此复刻音色不属于当前项目")
        return models.generate("tts", input_text=input_text, assets=[], parameters={"voice_id": voice, "language": parameters.get("language", "Auto")}, output_dir=output_dir, context=context)
    if operation != "enroll":
        raise ValueError("声音复刻op仅支持enroll或clone")
    if len(assets) != 1:
        raise ValueError("声音登记需选择一份当前项目明确授权的语音样本")
    source = Path(assets[0]["path"])
    if source.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("声音登记样本超过10MB")
    duration = speech._probe(source, context)
    if not 3 <= duration <= 60:
        raise ValueError("声音登记样本需为3至60秒；建议清晰连续讲话10至20秒")
    name = parameters.get("name", "project")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9]{1,10}", name):
        raise ValueError("音色name需为1至10个字母或数字")
    model = settings.dashscope_tts_model
    proxy = output_dir / "voice-sample-proxy.wav"
    client = context.get("media_http_client") or httpx.Client(timeout=90, follow_redirects=False)
    owned = "media_http_client" not in context
    try:
        if model.startswith("qwen3-tts-vc"):
            speech._run([context.get("ffmpeg_binary", settings.ffmpeg_binary), "-v", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(proxy)])
            inline = "data:audio/wav;base64," + base64.b64encode(proxy.read_bytes()).decode()
            payload = {"model": "qwen-voice-enrollment", "input": {"action": "create", "target_model": model, "preferred_name": name, "audio": {"data": inline}}}
        else:
            locator = context.get("voice_sample_url")
            if not callable(locator):
                raise ValueError("此型号需要配置本项目授权样本的云存储URL适配器，或使用支持内联样本的Qwen-TTS复刻型号")
            value = locator(assets[0])
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".aliyuncs.com") or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError("复刻样本URL必须来自本项目授权云存储")
            payload = {"model": "voice-enrollment", "input": {"action": "create_voice", "target_model": model, "prefix": name, "url": value}}
        context = models._usage_context(context, model)
        response = models._json(client, "POST", models.base_url() + "/services/audio/tts/customization", context=context,
                                headers={"Authorization": "Bearer " + models._secret()}, payload=payload)
        usage = models._receipt(context, response, model)
        output = response.get("output") or {}
        voice_id = output.get("voice") or output.get("voice_id")
        if not isinstance(voice_id, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", voice_id):
            raise ValueError("复刻服务未返回实际voice_id，不能伪造音色登记")
        status = output.get("status") if isinstance(output.get("status"), str) else "created"
        record = {"voice_id": voice_id, "model": model, "name": name, "status": status,
                  "sample_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "sample_duration_seconds": duration,
                  "origin": "actual_project_voice_enrollment", "publication_state": "not_published"}
        target = output_dir / "voice-enrollment.json"
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"response": "已保存复刻服务实际返回的音色登记；请用该voice_id合成并人工听审。", "artifact_paths": [target], "voice_id": voice_id, "usage": usage, "model": model}
    finally:
        proxy.unlink(missing_ok=True)
        if owned:
            client.close()
