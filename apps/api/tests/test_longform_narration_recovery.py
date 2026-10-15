"""An undersized chapter repairs its narration without rewriting the source graph."""
from copy import deepcopy
import json

import pytest

from test_longform_generation import (
    ChapterModel, chapter_reply, generation_module, longform_input, model_response,
    prohibit_unconfigured_model_calls,
)


SHORT_VOICES = (
    "从入口开始观察，把随身物品的放置需求写在纸上。",
    "标出入口与常用房间的位置，检查可能绕行的路线。",
    "讨论家人同时回家时，哪些停留动作会占用通道。",
    "把物品放置与换鞋路径联系起来，列出需要核对的问题。",
    "观察去向与视线变化，资料未说明的内容单独记录。",
    "对不同路线分别写下使用理由，先确认家庭的真实需要。",
)


class RecoveringModel(ChapterModel):
    def __init__(self, mutation=None):
        super().__init__()
        self.repair_mutation = mutation
        self.repair_request = None
        self.message_chars = []

    def __call__(self, messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        self.message_chars.append(sum(len(row["content"]) for row in messages))
        if request.get("chapter", {}).get("id") != "c2":
            return super().__call__(messages, **kwargs)
        self.calls.append({"stage": request["stage"], "chapter_id": "c2", "request": request})
        if request.get("response_scope") != "voiceover_patch":
            draft = chapter_reply(1)
            for row, voice in zip(draft["shots"], SHORT_VOICES):
                row["voiceover"] = voice
            return model_response(draft)
        self.repair_request = deepcopy(request)
        patch = {"chapter_id": "c2", "shots": [
            {"id": row["id"], "voiceover": row["voiceover"], "caption": row["caption"]}
            for row in chapter_reply(1)["shots"]]}
        if self.repair_mutation == "foreign_id":
            patch["shots"][0]["id"] = "foreign-shot"
        elif self.repair_mutation == "graph_change":
            patch["shots"][0]["asset_id"] = "foreign-asset"
        elif self.repair_mutation == "still_short":
            for row, voice in zip(patch["shots"], SHORT_VOICES):
                row["voiceover"] = voice
        elif self.repair_mutation == "duplicate":
            patch["shots"][1]["voiceover"] = patch["shots"][0]["voiceover"]
        elif self.repair_mutation == "invented_fact":
            patch["shots"][0]["voiceover"] += "这个项目售价为888万元。"
        return model_response(patch)


def generate(module, data, path, review, resume_dir=None):
    return module.generate_longform(data, checkpoint_dir=path, request_id="recovery-test",
        validate=lambda row: row, before_call=lambda: None, on_usage=lambda row: None,
        checkpoint_guard=lambda: None, resume_dir=resume_dir, review_chapter=review)


def saved_first_chapter(module, data, monkeypatch, tmp_path):
    from app import provider
    first = tmp_path / "first"
    monkeypatch.setattr(provider, "chat", ChapterModel(interrupted_chapter="c2"))
    with pytest.raises(provider.ProviderError, match="测试中断"):
        generate(module, data, first, lambda *_: None)
    assert len(json.loads((first / "draft.json").read_text())["chapters"]) == 1
    return first


def test_short_chapter_resumes_with_counted_local_narration_patch(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = RecoveringModel()
    reviewed = []
    monkeypatch.setattr(provider, "chat", model)
    result = generate(generation_module, longform_input, tmp_path / "resumed",
        lambda draft, *_: reviewed.append(deepcopy(draft)), first)
    shots = [row for row in result["storyboard"]["shots"] if row["chapter_id"] == "c2"]
    assert shots == chapter_reply(1)["shots"]
    assert [row["chapter_id"] for row in reviewed] == ["c2", "c3", "c4", "c5", "c6"]
    assert not any(row["chapter_id"] == "c1" for row in model.calls)
    repair = model.repair_request
    assert repair["narration_repair"]["actual_chinese_chars"] == 129
    assert repair["narration_repair"]["min_chinese_chars"] == 228
    assert repair["narration_repair"]["max_chinese_chars"] == 312
    assert repair["narration_repair"]["missing_chinese_chars"] == 99
    assert [row["voiceover"] for row in repair["narration_repair"]["shots"]] == list(SHORT_VOICES)
    assert [row["target_chinese_chars"] for row in repair["narration_repair"]["shots"]] == [45] * 6
    assert all(set(row) == {"id", "voiceover", "actual_chinese_chars", "target_chinese_chars"}
               for row in repair["narration_repair"]["shots"])
    assert all(count <= 7000 for count in model.message_chars)


@pytest.mark.parametrize("mutation", ["foreign_id", "graph_change", "still_short", "duplicate", "invented_fact"])
def test_narration_patch_keeps_graph_quality_and_fact_gates(generation_module, longform_input, monkeypatch, tmp_path, mutation):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = RecoveringModel(mutation)
    monkeypatch.setattr(provider, "chat", model)
    resumed = tmp_path / "resumed"
    with pytest.raises((provider.ProviderError, ValueError)):
        generate(generation_module, longform_input, resumed, lambda *_: None, first)
    # The single repair is bounded, and never overwrites the verified prior chapter.
    assert len(model.calls) == 2
    assert len(json.loads((first / "draft.json").read_text())["chapters"]) == 1
    assert not (resumed / "draft.json").exists()
    failures = [json.loads(line) for line in (resumed / "stage-failures.jsonl").read_text().splitlines()]
    assert failures[0]["code"] == "narration_length"
    assert failures[0]["actual_chinese_chars"] == 129
    assert failures[0]["min_chinese_chars"] == 228
    assert failures[0]["max_chinese_chars"] == 312
    assert all(voice not in json.dumps(failures, ensure_ascii=False) for voice in SHORT_VOICES)


def test_repaired_narration_must_pass_source_review_before_checkpoint(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    monkeypatch.setattr(provider, "chat", RecoveringModel())
    resumed = tmp_path / "resumed"
    def reject(draft, chapter, data):
        raise generation_module._StageError("本章存在无来源事实断言")
    with pytest.raises(provider.ProviderError, match="无来源事实断言"):
        generate(generation_module, longform_input, resumed, reject, first)
    assert not (resumed / "draft.json").exists()

