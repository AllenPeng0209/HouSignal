#!/usr/bin/env python3
"""Project-owned offline Qwen text runtime. No gateway or paid API dependency."""
from __future__ import annotations
import argparse
from dataclasses import dataclass, field
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import re
import stat
import select
import socket
import threading
import time
import uuid

MODEL_ALIAS = "housignal-qwen3-vl-32b-text"
MAX_INPUT_TOKENS = 8192
MAX_OUTPUT_TOKENS = 6000
MAX_BODY_BYTES = 128 * 1024
AUTH_ROOT = Path("/mnt/data/workdir/yanlun/housignal/native_qwen_runtime")

class RequestError(Exception):
    def __init__(self, code, message, status=400, *, usage=None, usage_status=None):
        super().__init__(message)
        self.code, self.status = code, status
        if usage is not None:
            if set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"} or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in usage.values()) or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
                raise ValueError("Known usage requires complete actual nonnegative token counts")
            self.usage_status = "known"
        else:
            if usage_status not in {None, "unknown"}:
                raise ValueError("Unavailable actual generation counts must be unknown")
            self.usage_status = usage_status
        self.usage = usage

@dataclass(frozen=True)
class ChatRequest:
    messages: list
    max_tokens: int
    temperature: float
    request_id: str

@dataclass(frozen=True)
class Generation:
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str

@dataclass
class GenerationControl:
    request_id: str
    deadline: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    reason: str | None = None
    def should_stop(self):
        if self.cancelled.is_set():
            self.reason = self.reason or "cancelled"
        elif time.monotonic() >= self.deadline:
            self.reason = "timeout"
        return self.reason is not None


def validate_request(body, alias):
    if not isinstance(body, dict):
        raise RequestError("invalid_request", "JSON object required")
    allowed = {"model", "messages", "max_tokens", "max_completion_tokens", "temperature", "stream", "n", "request_id"}
    if set(body) - allowed:
        raise RequestError("unsupported_parameter", "Only plain text chat parameters are supported")
    if body.get("model") != alias:
        raise RequestError("model_not_allowed", "The configured model is fixed")
    if body.get("stream", False) is not False or body.get("n", 1) != 1:
        raise RequestError("unsupported_parameter", "Only one non-streaming completion is supported")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise RequestError("invalid_messages", "One to 64 text messages required")
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise RequestError("invalid_message", "Messages require only role and string content")
        if not isinstance(message["role"], str) or message["role"] not in {"system", "user", "assistant"} or not isinstance(message["content"], str) or not message["content"].strip():
            raise RequestError("invalid_message", "Only nonempty string content and system/user/assistant roles are supported")
        if len(message["content"]) > 32768:
            raise RequestError("input_too_large", "Message exceeds text limit")
    if "max_tokens" in body and "max_completion_tokens" in body:
        raise RequestError("invalid_max_tokens", "Choose one token limit field")
    tokens = body.get("max_tokens", body.get("max_completion_tokens", 768))
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= MAX_OUTPUT_TOKENS:
        raise RequestError("invalid_max_tokens", "Token limit must be an integer from 1 to 6000")
    temperature = body.get("temperature", 0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 1:
        raise RequestError("invalid_temperature", "Temperature must be finite and between 0 and 1")
    request_id = body.get("request_id", uuid.uuid4().hex)
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in request_id):
        raise RequestError("invalid_request_id", "Request id must contain 1 to 80 letters, digits, underscores or hyphens")
    return ChatRequest(messages, tokens, float(temperature), request_id)


def completion_result(text, prompt_tokens, completion_tokens, control, max_tokens, *, eos_reached=False):
    """Keep actual generated-token counts even when the output cannot succeed."""
    usage = {"prompt_tokens":int(prompt_tokens), "completion_tokens":int(completion_tokens), "total_tokens":int(prompt_tokens) + int(completion_tokens)}
    if control.should_stop():
        raise RequestError("generation_" + control.reason, "Generation " + control.reason, 504 if control.reason == "timeout" else 499, usage=usage)
    if not text.strip():
        raise RequestError("empty_generation", "Model returned empty text", 503, usage=usage)
    return Generation(text, int(prompt_tokens), int(completion_tokens), "length" if completion_tokens >= max_tokens and not eos_reached else "stop")


def create_server(backend, token, alias, manifest_id, *, port=8891, timeout_seconds=120, hard_exit=None):
    """Expose authenticated private loopback routes, serializing actual computation."""
    if not token or len(token) < 16:
        raise ValueError("A private bearer token of at least 16 characters is required")
    if not 0 < timeout_seconds <= 300:
        raise ValueError("Generation timeout must be finite and at most 300 seconds")
    active_lock = threading.Lock()
    controls = {}
    controls_lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass  # Never log bearer credentials or submitted text.
        def respond(self, status, value):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers(); self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def authorized(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.respond(401, {"error":{"code":"unauthorized", "message":"Bearer authentication required"}})
                return False
            return True
        def do_GET(self):
            if not self.authorized(): return
            if self.path == "/health":
                with controls_lock: ids = list(controls)
                self.respond(200, {"status":"ok", "model":alias, "manifest_id":manifest_id, "text_only":True, "concurrency":1, "busy":bool(ids), "active_request_ids":ids, "timeout_seconds":timeout_seconds,"soft_stop_seconds":timeout_seconds - min(15.0,timeout_seconds * 0.1),"failure_usage_receipts":True,"private_auth_policy":"owned-fixed-directory-no-follow-regular-0600-single-link","decoder_layers":getattr(backend,"decoder_layers",None),"parameter_count":getattr(backend,"parameter_count",None),"identity_scope":getattr(backend,"identity_scope",None),"max_input_tokens":MAX_INPUT_TOKENS,"max_output_tokens":MAX_OUTPUT_TOKENS})
            elif self.path == "/v1/models":
                self.respond(200, {"object":"list", "data":[{"id":alias, "object":"model", "owned_by":"housignal-native-runtime", "revision":manifest_id}]})
            else: self.respond(404, {"error":{"code":"not_found"}})
        def read_body(self):
            try: length = int(self.headers.get("Content-Length", "0"))
            except ValueError: raise RequestError("invalid_body", "Valid Content-Length required")
            if not 1 <= length <= MAX_BODY_BYTES:
                raise RequestError("invalid_body", "Request size must be from 1 to 131072 bytes", 413)
            self.connection.settimeout(10)
            try: return json.loads(self.rfile.read(length))
            except (ValueError, socket.timeout): raise RequestError("invalid_json", "Valid JSON required")
        def do_POST(self):
            if not self.authorized(): return
            try:
                body = self.read_body()
                if self.path == "/v1/cancel":
                    if not isinstance(body, dict) or set(body) != {"request_id"} or not isinstance(body["request_id"], str):
                        raise RequestError("invalid_cancel", "Request id required")
                    with controls_lock:
                        control = controls.get(body["request_id"])
                        if control: control.cancelled.set()
                    self.respond(200, {"request_id":body["request_id"], "cancelled":control is not None}); return
                if self.path != "/v1/chat/completions":
                    self.respond(404, {"error":{"code":"not_found"}}); return
                request = validate_request(body, alias)
                if not active_lock.acquire(blocking=False):
                    raise RequestError("runtime_busy", "One generation is already running", 429)
                hard_deadline = time.monotonic() + timeout_seconds
                stop_grace_seconds = min(15.0, timeout_seconds * 0.1)
                control = GenerationControl(request.request_id, hard_deadline - stop_grace_seconds)
                with controls_lock: controls[control.request_id] = control
                result_queue = queue.Queue(maxsize=1)
                finished = threading.Event()
                def compute():
                    try: result_queue.put((True, backend.generate(request, control)))
                    except Exception as error: result_queue.put((False, error))
                    finally:
                        with controls_lock: controls.pop(control.request_id, None)
                        finished.set(); active_lock.release()
                threading.Thread(target=compute, daemon=True).start()
                if hard_exit:
                    def watchdog():
                        if not finished.wait(timeout_seconds):
                            hard_exit(75)  # Only this project-owned model process is terminated.
                    threading.Thread(target=watchdog, daemon=True).start()
                stop_seen_at = None
                while True:
                    try: ok, result = result_queue.get(timeout=0.005); break
                    except queue.Empty:
                        if control.should_stop():
                            stop_seen_at = stop_seen_at or time.monotonic()
                            if time.monotonic() >= min(hard_deadline, stop_seen_at + stop_grace_seconds):
                                raise RequestError("generation_" + control.reason, "Generation " + control.reason, 504 if control.reason == "timeout" else 499, usage_status="unknown")
                        readable, _, _ = select.select([self.connection], [], [], 0)
                        if readable:
                            try: disconnected = self.connection.recv(1, socket.MSG_PEEK) == b""
                            except OSError: disconnected = True
                            if disconnected:
                                control.cancelled.set(); return
                if not ok:
                    if isinstance(result, RequestError): raise result
                    raise RequestError("model_inference_failed", "Model inference failed", 503, usage_status="unknown")
                self.respond(200, {"id":"chatcmpl-" + control.request_id, "object":"chat.completion", "created":int(time.time()), "model":alias, "system_fingerprint":manifest_id, "choices":[{"index":0,"message":{"role":"assistant","content":result.text},"finish_reason":result.finish_reason}], "usage":{"prompt_tokens":result.prompt_tokens,"completion_tokens":result.completion_tokens,"total_tokens":result.prompt_tokens + result.completion_tokens}})
            except RequestError as error:
                payload = {"model":alias, "system_fingerprint":manifest_id, "error":{"code":error.code,"message":str(error)}}
                if error.usage_status is not None:
                    payload["usage_status"] = error.usage_status
                    payload["usage"] = error.usage
                self.respond(error.status, payload)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def checkpoint_manifest(model_path):
    """Pin local identity using config/tokenizer and shard header hashes plus stats.

    Header hashes cover every tensor's name, shape, dtype and byte offsets. This is
    explicitly a metadata identity, not a full content checksum or upstream commit.
    """
    root = Path(model_path).resolve()
    files = {}
    for name in ["config.json", "model.safetensors.index.json", "tokenizer.json", "tokenizer_config.json", "chat_template.json", "vocab.json", "merges.txt"]:
        file = root / name
        files[name] = {"sha256":hashlib.sha256(file.read_bytes()).hexdigest(), "bytes":file.stat().st_size}
    index = json.loads((root / "model.safetensors.index.json").read_text())
    for name in sorted(set(index["weight_map"].values())):
        if Path(name).name != name: raise ValueError("Invalid shard basename")
        file = root / name; stat = file.stat()
        with file.open("rb") as stream:
            prefix = stream.read(8)
            header_size = int.from_bytes(prefix, "little")
            if not 0 < header_size < 8 * 1024 * 1024: raise ValueError("Invalid safetensor header")
            header = stream.read(header_size)
        files[name] = {"bytes":stat.st_size,"mtime_ns":stat.st_mtime_ns,"header_sha256":hashlib.sha256(prefix + header).hexdigest()}
    manifest = {"schema_version":1,"model_alias":MODEL_ALIAS,"identity_scope":"local config/tokenizer content, safetensor headers/sizes/mtime; not full tensor checksum or upstream revision", "files":files,"tensor_keys":len(index["weight_map"])}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest["manifest_id"] = "local-meta-sha256:" + digest
    return manifest

class TransformersBackend:
    def __init__(self, model_path, expected_manifest):
        import torch
        from transformers import AutoTokenizer, Qwen3VLForConditionalGeneration
        if checkpoint_manifest(model_path) != expected_manifest:
            raise RuntimeError("Checkpoint metadata changed before loading")
        torch.set_num_threads(8); torch.set_num_interop_threads(4)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
        if not self.tokenizer.chat_template:
            self.tokenizer.chat_template = json.loads((Path(model_path) / "chat_template.json").read_text())["chat_template"]
        started = time.monotonic()
        self.model, info = Qwen3VLForConditionalGeneration.from_pretrained(model_path, dtype=torch.bfloat16, device_map={"":"cuda:0"}, attn_implementation="sdpa", local_files_only=True, trust_remote_code=False, output_loading_info=True)
        if any(info.get(k) for k in ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"]):
            raise RuntimeError("Checkpoint did not load without missing/unexpected tensors")
        self.model.eval()
        if len(self.model.model.language_model.layers) != 64 or self.model.lm_head.weight.device.type != "cuda":
            raise RuntimeError("Full 64-layer decoder and LM head residency required")
        if checkpoint_manifest(model_path) != expected_manifest:
            raise RuntimeError("Checkpoint metadata changed while loading")
        self.load_seconds = time.monotonic() - started
        self.parameter_count = sum(p.numel() for p in self.model.parameters())
        self.decoder_layers = len(self.model.model.language_model.layers)
        self.identity_scope = expected_manifest["identity_scope"]
    def generate(self, request, control):
        from transformers import StoppingCriteria, StoppingCriteriaList
        torch = self.torch
        inputs = self.tokenizer.apply_chat_template(request.messages, tokenize=True, add_generation_prompt=True, return_tensors="pt", return_dict=True)
        inputs.pop("token_type_ids", None)
        prompt_tokens = inputs["input_ids"].shape[-1]
        if prompt_tokens > MAX_INPUT_TOKENS:
            raise RequestError("input_too_large", "Tokenized input exceeds 8192 tokens", 413)
        class Stop(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs): return control.should_stop()
        inputs = inputs.to("cuda:0")
        eos_token_id = self.tokenizer.eos_token_id
        eos_ids = list(eos_token_id) if isinstance(eos_token_id, (list, tuple)) else [eos_token_id]
        kwargs = {"max_new_tokens":request.max_tokens,"do_sample":request.temperature > 0,"stopping_criteria":StoppingCriteriaList([Stop()]), "pad_token_id":eos_ids[0], "eos_token_id":eos_token_id, "use_cache":True}
        if request.temperature > 0: kwargs["temperature"] = request.temperature
        with torch.inference_mode():
            output = self.model.generate(**inputs, **kwargs)
        generated = output[0, prompt_tokens:]
        token_count = int(generated.shape[-1])
        text = self.tokenizer.decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        eos_reached = token_count > 0 and int(generated[-1]) in eos_ids
        return completion_result(text, int(prompt_tokens), token_count, control, request.max_tokens, eos_reached=eos_reached)


def read_private_auth(auth_path, *, expected_root=AUTH_ROOT):
    """Read only the pinned owned regular token file using directory-relative fds."""
    root = Path(expected_root).absolute()
    path = Path(auth_path).absolute()
    if root != root.resolve() or path != root / "native-qwen.auth":
        raise ValueError("Auth must use the fixed private runtime directory and filename")
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 0o700:
            raise ValueError("Auth directory must be owned and exactly 0700")
        path_directory = os.stat(root, follow_symlinks=False)
        if (directory.st_dev, directory.st_ino) != (path_directory.st_dev, path_directory.st_ino):
            raise ValueError("Auth directory identity changed")
        fd = os.open("native-qwen.auth", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1 or metadata.st_uid != os.geteuid():
                raise ValueError("Auth must be owned, regular, single-link and exactly 0600")
            if not 24 <= metadata.st_size <= 512:
                raise ValueError("Private bearer-token file size is invalid")
            data = os.read(fd, 513)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)
    try:
        token = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise ValueError("Private bearer-token format is invalid") from None
    if re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token) is None:
        raise ValueError("Private bearer-token format is invalid")
    return token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--timeout-seconds", type=int, default=280)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "0": raise RuntimeError("This deployment is pinned to proven idle physical PPU 0")
    os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["TRANSFORMERS_OFFLINE"] = "1"
    token = read_private_auth(args.auth_file)
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text())
    backend = TransformersBackend(args.model_path, manifest)
    print(json.dumps({"event":"model_ready","model":MODEL_ALIAS,"manifest_id":manifest["manifest_id"],"load_seconds":backend.load_seconds,"parameter_count":backend.parameter_count,"decoder_layers":64,"physical_ppu":0}), flush=True)
    server = create_server(backend, token, MODEL_ALIAS, manifest["manifest_id"], port=args.port, timeout_seconds=args.timeout_seconds, hard_exit=os._exit)
    print(json.dumps({"event":"listening","host":"127.0.0.1","port":args.port}), flush=True)
    server.serve_forever()

if __name__ == "__main__": main()
