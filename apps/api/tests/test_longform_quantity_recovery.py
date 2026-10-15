"""Unknown values get one text correction, never invented citations or measurements."""
from copy import deepcopy
import json

import pytest

from test_longform_generation import (
    ChapterModel, chapter_reply, generation_module, longform_input, model_response,
    prohibit_unconfigured_model_calls,
)
from test_longform_narration_recovery import generate, saved_first_chapter, SHORT_VOICES


class QuantityRepairModel(ChapterModel):
    def __init__(self, field="voiceover", mutation=None):
        super().__init__()
        self.field, self.quantity_mutation = field, mutation
        self.repair_request = None
        self.message_chars = []

    def __call__(self, messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        self.message_chars.append(sum(len(row["content"]) for row in messages))
        if request.get("chapter", {}).get("id") != "c2":
            return super().__call__(messages, **kwargs)
        self.calls.append({"stage": request["stage"], "chapter_id": "c2", "request": request})
        if request.get("response_scope") != "text_patch":
            draft = chapter_reply(1)
            draft["shots"][2][self.field] += "假设套内面积247㎡，可以算出得房率。"
            if self.quantity_mutation == "short_with_unknowns":
                for row, voice in zip(draft["shots"], SHORT_VOICES):
                    row["voiceover"] = voice
                draft["shots"][2]["voiceover"] += "套内面积247㎡。"
                draft["shots"][3]["caption"] += "总价888万元"
                draft["shots"][4]["visual_direction"] += "展示9.6米开间"
            if self.quantity_mutation in ("foreign_ref", "stale_ref"):
                draft["shots"][5]["fact_refs"] = [{"id": "foreign-fact" if self.quantity_mutation == "foreign_ref" else "f",
                                                  "revision": 1 if self.quantity_mutation == "foreign_ref" else 99}]
            return model_response(draft)
        self.repair_request = deepcopy(request)
        patch = {"chapter_id": "c2", "shots": [
            {"id": row["id"], "voiceover": row["voiceover"], "caption": row["caption"],
             "visual_direction": row["visual_direction"]} for row in chapter_reply(1)["shots"]]}
        if self.quantity_mutation == "unknown_again":
            patch["shots"][2][self.field] += "这个项目的套内面积为247㎡。"
        elif self.quantity_mutation == "foreign_graph":
            patch["shots"][0]["fact_refs"] = [{"id": "foreign-fact", "revision": 1}]
        elif self.quantity_mutation == "short_repair":
            for row, voice in zip(patch["shots"], SHORT_VOICES):
                row["voiceover"] = voice
        elif self.quantity_mutation == "known_value_wrong_label":
            patch["shots"][2]["voiceover"] += "这个项目的套内面积为420㎡。"
        return model_response(patch)


@pytest.mark.parametrize("field", ["voiceover", "caption", "visual_direction"])
def test_unknown_numeric_copy_repairs_all_three_text_fields_with_source_whitelist(generation_module, longform_input, monkeypatch, tmp_path, field):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = QuantityRepairModel(field)
    monkeypatch.setattr(provider, "chat", model)
    reviewed = []
    result = generate(generation_module, longform_input, tmp_path / "resumed",
        lambda draft, *_: reviewed.append(deepcopy(draft)), first)
    shots = [row for row in result["storyboard"]["shots"] if row["chapter_id"] == "c2"]
    assert shots == chapter_reply(1)["shots"]
    assert [row["chapter_id"] for row in reviewed] == ["c2", "c3", "c4", "c5", "c6"]
    repair = model.repair_request
    assert repair["text_repair"]["unsupported_quantity"] == "247㎡"
    assert repair["text_repair"]["field"] == field
    assert repair["text_repair"]["shot_id"] == "c2-shot-3"
    assert repair["facts"] == [{"id": "f", "revision": 1, "label": "面积", "value": "420", "unit": "㎡", "source": "测绘文件第2页"}]
    assert len(repair["text_repair"]["shots"]) == 6
    assert "247㎡" in repair["text_repair"]["shots"][2][field]
    assert all(count <= 7000 for count in model.message_chars)


@pytest.mark.parametrize("mutation", ["unknown_again", "foreign_graph", "short_repair"])
def test_numeric_text_correction_shares_single_retry_and_cannot_pass_bad_content(generation_module, longform_input, monkeypatch, tmp_path, mutation):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = QuantityRepairModel(mutation=mutation)
    monkeypatch.setattr(provider, "chat", model)
    resumed = tmp_path / "resumed"
    with pytest.raises((provider.ProviderError, ValueError)):
        generate(generation_module, longform_input, resumed, lambda *_: None, first)
    assert len(model.calls) == 2
    assert not (resumed / "draft.json").exists()


@pytest.mark.parametrize("mutation", ["foreign_ref", "stale_ref"])
def test_invalid_fact_reference_stops_before_quantity_correction(generation_module, longform_input, monkeypatch, tmp_path, mutation):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = QuantityRepairModel(mutation=mutation)
    monkeypatch.setattr(provider, "chat", model)
    with pytest.raises(ValueError, match="项目事实"):
        generate(generation_module, longform_input, tmp_path / "resumed", lambda *_: None, first)
    assert len(model.calls) == 1
    assert model.repair_request is None


def test_known_number_cannot_be_relabelled_as_invented_internal_area(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = QuantityRepairModel(mutation="known_value_wrong_label")
    monkeypatch.setattr(provider, "chat", model)
    def critic(draft, chapter, data):
        if "套内面积" in "".join(row["voiceover"] for row in draft["shots"]):
            raise generation_module._StageError("套内面积的标签没有来源")
    with pytest.raises(provider.ProviderError, match="没有来源"):
        generate(generation_module, longform_input, tmp_path / "resumed", critic, first)
    assert len(model.calls) == 2


def test_short_draft_with_unknown_values_gets_one_combined_text_patch(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    first = saved_first_chapter(generation_module, longform_input, monkeypatch, tmp_path)
    model = QuantityRepairModel(mutation="short_with_unknowns")
    monkeypatch.setattr(provider, "chat", model)
    result = generate(generation_module, longform_input, tmp_path / "resumed", lambda *_: None, first)
    assert [row for row in result["storyboard"]["shots"] if row["chapter_id"] == "c2"] == chapter_reply(1)["shots"]
    assert sum(row["chapter_id"] == "c2" for row in model.calls) == 2
    correction = model.repair_request["text_repair"]
    assert correction["actual_chinese_chars"] == 133
    assert correction["min_chinese_chars"] == 228
    assert correction["max_chinese_chars"] == 312
    assert correction["unsupported_quantities"] == [
        {"shot_id": "c2-shot-3", "field": "voiceover", "quantity": "247㎡"},
        {"shot_id": "c2-shot-4", "field": "caption", "quantity": "888万元"},
        {"shot_id": "c2-shot-5", "field": "visual_direction", "quantity": "9.6米"},
    ]

