"""Long native writing tasks retain their explicit, bounded provider budget."""
import json
import httpx
from app import provider


def test_long_native_chapter_budget_reaches_the_model_without_silent_clipping(monkeypatch):
    requests = []
    def boundary(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'project-model', 'choices': [
            {'message': {'role': 'assistant', 'content': '完整章节草稿。'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 15, 'completion_tokens': 20}})
    monkeypatch.setattr(provider.settings, 'dashscope_api_key', 'synthetic-native-key')
    monkeypatch.setattr(provider.settings, 'native_text_backend', 'dashscope')
    with httpx.Client(transport=httpx.MockTransport(boundary)) as client:
        monkeypatch.setattr(provider, '_get_client', lambda: client)
        provider.chat([{'role': 'user', 'content': '写一个完整的虚构章节。'}], max_tokens=6000)
    assert requests[0]['max_tokens'] == 6000
