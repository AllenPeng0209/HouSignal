"""Wire-level Bailian streaming regression tests; no paid API requests."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from app import provider


def test_explicit_cloud_chat_keeps_vision_requests_out_of_local_text_backend(configured, monkeypatch):
    configured.settings.native_text_backend = 'mlx'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'model': 'qwen3-vl-plus', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': '{"observation":"客厅"}'}}],
            'usage': {'prompt_tokens': 23, 'completion_tokens': 7}}))) as client:
        monkeypatch.setattr(configured, '_get_client', lambda: client)
        result = configured.cloud_chat([{'role': 'user', 'content': [
            {'type': 'text', 'text': '描述这张实拍'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,AA=='}}]}],
            model='qwen3-vl-plus')
    assert result['model'] == 'qwen3-vl-plus'
    assert result['choices'][0]['message']['content'] == '{"observation":"客厅"}'
    assert result['usage'] == {'prompt_tokens': 23, 'completion_tokens': 7}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(provider, "settings", SimpleNamespace(
        dashscope_api_key="test-bailian-secret", dashscope_model="qwen-plus",
        dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ))
    return provider


def event(delta=None, *, finish=None, usage=None):
    data = {"id": "chat-test", "model": "qwen-plus", "choices": []}
    if delta is not None or finish is not None:
        data["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
    if usage is not None:
        data["usage"] = usage
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


class WireStream(httpx.SyncByteStream):
    def __init__(self, parts):
        self.parts = parts
        self.closed = False
        self.read_count = 0

    def __iter__(self):
        for part in self.parts:
            self.read_count += 1
            if isinstance(part, Exception):
                raise part
            yield part.encode("utf-8")

    def close(self):
        self.closed = True


def serve(monkeypatch, parts, *, status=200):
    wire = WireStream(parts)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, stream=wire, headers={"content-type": "text/event-stream"})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(provider, "_get_client", lambda: client, raising=False)
    return wire, requests, client


def stream_call(**kwargs):
    function = getattr(provider, "stream_chat", None)
    assert callable(function), "Bailian adapter must support incremental SSE responses"
    return function([{"role": "user", "content": "汇总项目"}], **kwargs)


def test_stream_emits_content_before_finishing_and_preserves_usage(configured, monkeypatch):
    wire, requests, client = serve(monkeypatch, [
        ": heartbeat\n\n", event({"role": "assistant", "content": "项目"}),
        event({"content": "资料已核验。[S1]"}), event(finish="stop"),
        event(usage={"prompt_tokens": 18, "completion_tokens": 9, "total_tokens": 27}),
        "data: [DONE]\n\n",
    ])
    deltas = []

    def receive(delta):
        assert not wire.closed
        assert wire.read_count < 6, "Content must be delivered before the terminal event"
        deltas.append(delta)

    with client:
        result = stream_call(on_delta=receive, max_tokens=600)
    assert deltas == ["项目", "资料已核验。[S1]"]
    assert result["choices"][0] == {"index": 0, "message": {
        "role": "assistant", "content": "项目资料已核验。[S1]"}, "finish_reason": "stop"}
    assert result["usage"] == {"prompt_tokens": 18, "completion_tokens": 9, "total_tokens": 27}
    assert result["model"] == "qwen-plus"
    assert 0 <= result["_timing"]["first_token_ms"] <= result["_timing"]["duration_ms"]
    payload = json.loads(requests[0].content)
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["max_tokens"] == 600
    assert "tools" not in payload


def test_stream_assembles_interleaved_tool_arguments_without_publishing_them(configured, monkeypatch):
    wire, _, client = serve(monkeypatch, [
        event({"tool_calls": [{"index": 0, "id": "call_", "type": "function",
            "function": {"name": "get_project_", "arguments": "{\"include_"}},
            {"index": 1, "id": "call_two", "type": "function",
             "function": {"name": "search_evidence", "arguments": "{\"query\":"}}]}),
        event({"tool_calls": [{"index": 1, "function": {"arguments": "\"客厅\"}"}},
            {"index": 0, "id": "one", "function": {"name": "overview", "arguments": "facts\":true}"}}]}),
        event(finish="tool_calls"), "data: [DONE]\n\n",
    ])
    deltas = []
    with client:
        result = stream_call(tools=[{"type": "function"}], on_delta=deltas.append)
    assert deltas == []
    assert result["choices"][0]["message"]["tool_calls"] == [
        {"id": "call_one", "type": "function", "function": {
            "name": "get_project_overview", "arguments": '{"include_facts":true}'}},
        {"id": "call_two", "type": "function", "function": {
            "name": "search_evidence", "arguments": '{"query":"客厅"}'}},
    ]
    assert result["_timing"]["first_token_ms"] is None
    assert wire.closed


@pytest.mark.parametrize("parts", [
    [event({"content": "部分回答"}), event(finish="length"), "data: [DONE]\n\n"],
    [event({"content": "部分回答"}), "data: [DONE]\n\n"],
    [event({"content": "部分回答"}), event(finish="stop")],
    ["data: not-json-test-bailian-secret\n\n"],
    ["data: {\"error\":{\"message\":\"test-bailian-secret raw diagnostic\"}}\n\n"],
    [event(finish="stop"), "data: [DONE]\n\n"],
    [event({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}),
     event(finish="tool_calls"), "data: [DONE]\n\n"],
])
def test_stream_rejects_incomplete_or_invalid_results_safely(configured, monkeypatch, parts):
    wire, _, client = serve(monkeypatch, parts)
    with client, pytest.raises(provider.ProviderError) as error:
        stream_call()
    assert "test-bailian-secret" not in str(error.value)
    assert "raw diagnostic" not in str(error.value)
    assert wire.closed


def test_callback_failure_closes_stream_without_reading_or_retrying(configured, monkeypatch):
    wire, requests, client = serve(monkeypatch, [
        event({"content": "真实增量"}), event({"content": "不应继续读取"}),
        event(finish="stop"), "data: [DONE]\n\n",
    ])

    class Cancelled(RuntimeError):
        pass

    def cancel(_):
        raise Cancelled("job cancelled")

    with client, pytest.raises(Cancelled, match="job cancelled"):
        stream_call(on_delta=cancel)
    assert wire.closed
    assert wire.read_count == 1
    assert len(requests) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_stream_http_errors_do_not_publish_or_follow_vendor_payload(configured, monkeypatch, status):
    wire, requests, client = serve(monkeypatch, ["test-bailian-secret raw diagnostic"], status=status)
    with client, pytest.raises(provider.ProviderError) as error:
        stream_call()
    assert "test-bailian-secret" not in str(error.value)
    assert wire.read_count == 0
    assert len(requests) == 1
    assert wire.closed


def test_stream_network_failure_is_safe_and_is_not_retried(configured, monkeypatch):
    wire, requests, client = serve(monkeypatch, [httpx.ReadTimeout("test-bailian-secret")])
    with client, pytest.raises(provider.ProviderError) as error:
        stream_call()
    assert "test-bailian-secret" not in str(error.value)
    assert len(requests) == 1
    assert wire.closed


def test_stream_bounds_idle_wait(configured, monkeypatch):
    wire, requests, client = serve(monkeypatch, [
        event({"content": "响应"}), event(finish="stop"), "data: [DONE]\n\n",
    ])
    with client:
        stream_call()
    assert requests[0].extensions["timeout"]["read"] <= 30
    assert wire.closed


def test_stream_refuses_tool_mutation_after_terminal_finish(configured, monkeypatch):
    wire, _, client = serve(monkeypatch, [
        event({"content": "回答"}), event(finish="stop"),
        event({"tool_calls": [{"index": 0, "id": "late", "type": "function",
            "function": {"name": "get_project_overview", "arguments": "{}"}}]}, finish="tool_calls"),
        "data: [DONE]\n\n",
    ])
    with client, pytest.raises(provider.ProviderError):
        stream_call()
    assert wire.closed


def test_regular_chat_reuses_client_with_fresh_credentials_and_explicit_tool_choice(configured, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"model": "qwen-plus", "choices": [{
            "message": {"role": "assistant", "content": "合法响应"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(provider, "_get_client", lambda: client, raising=False)
        monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: pytest.fail("Top-level HTTP creates a new connection"))
        first = provider.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}], tool_choice="none")
        provider.settings.dashscope_api_key = "rotated-test-secret"
        second = provider.chat([{"role": "user", "content": "hi"}])
        assert "Authorization" not in client.headers
    assert first["choices"][0]["message"]["content"] == "合法响应"
    assert second["usage"]["completion_tokens"] == 2
    assert requests[0].headers["Authorization"] == "Bearer test-bailian-secret"
    assert requests[1].headers["Authorization"] == "Bearer rotated-test-secret"
    assert json.loads(requests[0].content)["tool_choice"] == "none"
    timeouts = requests[0].extensions["timeout"]
    assert all(value is not None and 0 < value <= 90 for value in timeouts.values())
    assert first["_timing"]["duration_ms"] >= 0


def test_regular_chat_rejects_token_truncation(configured, monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "截断JSON{"}, "finish_reason": "length"}]}))) as client:
        monkeypatch.setattr(provider, "_get_client", lambda: client, raising=False)
        monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: pytest.fail("Top-level HTTP creates a new connection"))
        with pytest.raises(provider.ProviderError):
            provider.chat([{"role": "user", "content": "generate"}])


def test_connection_pool_initialization_is_thread_safe(configured, monkeypatch):
    function = getattr(provider, "_get_client", None)
    assert callable(function), "Concurrent provider calls must use a shared connection pool"
    # The production pool owns its lifecycle and closes on process shutdown.
    monkeypatch.setattr(provider, "_client", None)
    with ThreadPoolExecutor(max_workers=8) as executor:
        clients = list(executor.map(lambda _: function(), range(24)))
    try:
        assert len({id(client) for client in clients}) == 1
        assert "Authorization" not in clients[0].headers
    finally:
        clients[0].close()


@pytest.mark.parametrize("scene_count", [0, 2, 4, 5])
def test_auto_content_generation_bounds_scene_count_without_silent_truncation(configured, monkeypatch, scene_count):
    generated = {"topic": "空间尺度", "script": "从客厅的空间关系讲清居住体验。",
        "cover_title": "看懂空间", "storyboard": [
            {"asset_id": "a1", "caption": "客厅尺度", "duration": 10, "start": 0}
            for _ in range(scene_count)], "fact_refs": []}
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(200, json={"model": "qwen-plus", "choices": [{
            "message": {"role": "assistant", "content": json.dumps(generated, ensure_ascii=False)},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 80, "completion_tokens": 100}})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        if scene_count > 4:
            with pytest.raises(provider.ProviderError) as error:
                provider.generate_content({"facts": [], "assets": [{"id": "a1"}]})
            assert "test-bailian-secret" not in str(error.value)
        else:
            result = provider.generate_content({"facts": [], "assets": [{"id": "a1"}]})
            assert len(result["storyboard"]) == scene_count
            assert result["script"] == "从客厅的空间关系讲清居住体验。"
        # The actual outbound generation request must have a bounded cost budget.
        assert json.loads(requests[0].content)["max_tokens"] <= 1200


def test_auto_content_generation_rejects_truncated_json_even_if_parsable(configured, monkeypatch):
    generated = {"topic": "空间", "script": "已有部分文案", "cover_title": "看空间",
                 "storyboard": [], "fact_refs": []}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(generated, ensure_ascii=False)},
                     "finish_reason": "length"}]}))) as client:
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        with pytest.raises(provider.ProviderError):
            provider.generate_content({"facts": [], "assets": []})
