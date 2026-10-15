"""Overlong spoken copy is edited by deleting whole trailing sentences only."""
from copy import deepcopy
import json
import re

import pytest

from test_longform_generation import (
    ChapterModel, chapter_reply, generation_module, longform_input, model_response,
    prohibit_unconfigured_model_calls,
)
from test_longform_narration_recovery import generate, saved_first_chapter, SHORT_VOICES


TAILS = (
    "也可以先把日常需要搬运的物品列出来，比较它们在不同时间是否会占用这条路线，再提出资料核对问题。",
    "资料能说明位置却未必能说明家具摆放后的通行感受，因此应该把可能影响走路的条件逐项记录下来继续核实。",
    "如果家庭成员的回家节奏不同，可以分别描述需要停留的位置和拿取物品的动作，讨论是否存在需要调整的安排。",
    "核对时还可以把常用物品与偶尔使用的物品分开记录，提出收纳位置如何配合动作的问题，而不是直接下结论。",
    "把这些待确认事项放进清单以后，再检查已有资料是否已经回答相关问题，未回答的内容需要继续索取可靠依据。",
    "这种讨论不保证某个安排一定适合所有家庭，而是让每个选择对应明确的需求和仍需核对的条件，便于进一步比较。",
)


def overlong_draft():
    draft = chapter_reply(1)
    for row, tail in zip(draft["shots"], TAILS):
        row["voiceover"] += tail
    return draft


class OverlongModel(ChapterModel):
    def __init__(self, after_short=False, mutation=None):
        super().__init__()
        self.after_short, self.edit_mutation = after_short, mutation

    def __call__(self, messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        if request.get("chapter", {}).get("id") != "c2":
            return super().__call__(messages, **kwargs)
        self.calls.append({"stage": request["stage"], "chapter_id": "c2", "request": request})
        draft = overlong_draft()
        if self.edit_mutation == "unknown_quantity":
            draft["shots"][0]["voiceover"] = draft["shots"][0]["voiceover"].replace("回家动线", "247㎡动线")
        elif self.edit_mutation == "duplicate":
            draft["shots"][1]["voiceover"] = draft["shots"][0]["voiceover"]
        elif self.edit_mutation == "unsplittable":
            for row in draft["shots"]:
                row["voiceover"] = row["voiceover"].replace("。", "，")
        if not self.after_short:
            return model_response(draft)
        if request.get("response_scope") != "voiceover_patch":
            for row, voice in zip(draft["shots"], SHORT_VOICES):
                row["voiceover"] = voice
            return model_response(draft)
        return model_response({"chapter_id": "c2", "shots": [
            {"id": row["id"], "voiceover": row["voiceover"], "caption": row["caption"]}
            for row in draft["shots"]]})


@pytest.mark.parametrize("after_short", [False, True])
def test_overlong_chapter_deletes_sentence_suffixes_without_extra_model_calls(generation_module, longform_input, monkeypatch, tmp_path, after_short):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = OverlongModel(after_short)
    reviewed = []
    monkeypatch.setattr(provider, "chat", model)
    result = generate(generation_module, longform_input, tmp_path / "resumed",
        lambda draft, *_: reviewed.append(deepcopy(draft)), first)
    shots = [row for row in result["storyboard"]["shots"] if row["chapter_id"] == "c2"]
    source = overlong_draft()["shots"]
    count = sum(len(re.findall(r"[\u4e00-\u9fff]", row["voiceover"])) for row in shots)
    assert 228 <= count <= 312
    assert abs(count - 270) <= 24
    assert any(row["voiceover"] != original["voiceover"] for row, original in zip(shots, source))
    for row, original in zip(shots, source):
        assert original["voiceover"].startswith(row["voiceover"])
        assert row["voiceover"].endswith("。")
        assert {key: value for key, value in row.items() if key != "voiceover"} == {
            key: value for key, value in original.items() if key != "voiceover"}
    assert sum(row["chapter_id"] == "c2" for row in model.calls) == (2 if after_short else 1)
    assert [row["chapter_id"] for row in reviewed] == ["c2", "c3", "c4", "c5", "c6"]


def test_sentence_prefixes_do_not_cut_quoted_question_or_decimal(generation_module):
    text = "先核对资料，追问：“8.2米是在什么位置测得的？”，把测量边界记下来再比较。接着讨论家具与通行的关系。"
    choices = generation_module._sentence_prefixes(text)
    assert choices == [
        "先核对资料，追问：“8.2米是在什么位置测得的？”，把测量边界记下来再比较。",
        text,
    ]


@pytest.mark.parametrize("mutation", ["unknown_quantity", "duplicate", "unsplittable"])
def test_sentence_deletion_keeps_quantity_repeat_and_complete_sentence_gates(generation_module, longform_input, monkeypatch, tmp_path, mutation):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = OverlongModel(mutation=mutation)
    monkeypatch.setattr(provider, "chat", model)
    with pytest.raises((ValueError, provider.ProviderError)):
        generate(generation_module, longform_input, tmp_path / "resumed", lambda *_: None, first)
    assert not (tmp_path / "resumed" / "draft.json").exists()

