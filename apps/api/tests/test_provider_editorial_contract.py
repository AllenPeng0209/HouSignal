"""Generation requests describe captions as viewer text and preserve media origin."""
import json

import pytest

from app import provider


def generated_messages(monkeypatch, data):
    requests = []
    def local_response(messages, **kwargs):
        requests.append(messages)
        return {'model': 'editorial-test', 'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps({'topic': '空间连接',
                'script': '合成示意：观察客厅与餐厅的连接。', 'cover_title': '合成空间示意',
                'storyboard': [{'asset_id': 'a', 'caption': '合成示意：观察空间连接',
                                'duration': 30, 'start': 0}], 'fact_refs': []}, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}
    monkeypatch.setattr(provider, 'chat', local_response)
    provider.generate_content(data)
    return requests[0]


def test_caption_contract_sends_viewer_subtitles_and_prohibits_invented_shot_actions(monkeypatch):
    messages = generated_messages(monkeypatch, {'project': {'is_test': False},
        'assets': [{'id': 'a', 'origin_type': 'actual', 'description': '客厅静态照片'}]})
    system = messages[0]['content']
    assert '直接显示给观众的字幕' in system
    assert '不是拍摄或剪辑指令' in system
    assert '不得虚构素材中未被证据确认的人物、动作或镜头' in system
    assert '简短画面提示' not in system
    assert json.loads(messages[1]['content'])['assets'][0]['description'] == '客厅静态照片'


@pytest.mark.parametrize('origins', [['test'], ['template'], ['test', 'template']])
def test_synthetic_only_test_project_is_explicitly_labeled_for_all_viewer_copy(monkeypatch, origins):
    messages = generated_messages(monkeypatch, {'project': {'is_test': True},
        'assets': [{'id': 'a', 'origin_type': origin} for origin in origins]})
    system = messages[0]['content']
    assert '本次只有合成或示意素材' in system
    assert 'script、caption、cover_title均须使用合成或示意表达' in system
    assert '不得称为实拍或现场' in system


def test_actual_media_does_not_receive_a_false_synthetic_only_declaration(monkeypatch):
    messages = generated_messages(monkeypatch, {'project': {'is_test': True},
        'assets': [{'id': 'a', 'origin_type': 'actual'}, {'id': 'b', 'origin_type': 'test'}]})
    assert '本次只有合成或示意素材' not in messages[0]['content']
