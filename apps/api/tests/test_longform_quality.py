"""Generated and edited longform drafts obey the same narrative graph rules."""
from copy import deepcopy
import json
import re

import pytest

from test_longform_generation import (
    ChapterModel,
    generation_module,
    longform_input,
    prohibit_unconfigured_model_calls,
    run_generation,
)


@pytest.fixture
def valid_longform(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider

    monkeypatch.setattr(provider, "chat", ChapterModel())
    return run_generation(generation_module, longform_input, tmp_path / "valid")


def test_manual_edit_cannot_remove_individual_shots_reference_mapping(generation_module, valid_longform):
    board = deepcopy(valid_longform["storyboard"])
    for chapter in board["chapters"]:
        shots = [shot for shot in board["shots"] if shot["chapter_id"] == chapter["id"]]
        for shot in shots[1:]:
            shot["reference_beat_ids"] = []

    # A union at chapter level still covers all six source beats, but thirty
    # actual shots no longer explain which reference structure they adapt.
    assert sum(not shot["reference_beat_ids"] for shot in board["shots"]) == 30
    with pytest.raises(ValueError, match="参考|映射"):
        generation_module.validate_storyboard(board, valid_longform["script"])


def test_manual_edit_cannot_concentrate_half_the_film_in_one_chapter(generation_module, valid_longform):
    board = deepcopy(valid_longform["storyboard"])
    original_shots = board["shots"]
    redistributed = []
    groups = ((0, 18), (18, 21), (21, 24), (24, 27), (27, 30), (30, 33))
    additional_observations = (
        "",
        "先明确家庭作息再核对资料。",
        "把观察记录与后续问题分开。",
        "区分休息与工作对边界的需要。",
        "每项取舍都写出依据和顾虑。",
        "离开之前复核仍需补充的问题。",
    )
    for chapter, (start, end), observation in zip(board["chapters"], groups, additional_observations):
        shots = original_shots[start:end]
        shots[0]["voiceover"] += observation
        chapter["target_seconds"] = 180 if start == 0 else 36
        chapter["shot_ids"] = [shot["id"] for shot in shots]
        for shot in shots:
            shot["chapter_id"] = chapter["id"]
            shot["plan_section"] = chapter["plan_sections"][0]
            shot["reference_beat_ids"] = deepcopy(chapter["reference_beat_ids"])
            shot["duration"] = 10 if start == 0 else 12
        redistributed.extend(shots)
    board["shots"] = redistributed
    script = "\n".join(shot["voiceover"] for shot in redistributed)

    # The total remains six minutes with distinct, complete narration. Only
    # the first chapter violates the chapter-sized production contract.
    assert [chapter["target_seconds"] for chapter in board["chapters"]] == [180, 36, 36, 36, 36, 36]
    assert sum(shot["duration"] for shot in redistributed) == 360
    counts = [sum(len(re.findall(r"[\u4e00-\u9fff]", shot["voiceover"])) for shot in redistributed
                  if shot["chapter_id"] == chapter["id"]) for chapter in board["chapters"]]
    assert 684 <= counts[0] <= 936
    assert all(137 <= count <= 187 for count in counts[1:])
    with pytest.raises(ValueError, match="章节|镜头"):
        generation_module.validate_storyboard(board, script)


def test_manual_edit_cannot_reduce_shots_by_stretching_static_frames(generation_module, valid_longform):
    board = deepcopy(valid_longform["storyboard"])
    reduced = []
    for chapter in board["chapters"]:
        shots = [shot for shot in board["shots"] if shot["chapter_id"] == chapter["id"]]
        # Keep every original narration word, so a narration-count rejection
        # cannot accidentally substitute for the missing shot-density check.
        shots[4]["voiceover"] += " " + shots[5]["voiceover"]
        shots = shots[:5]
        for shot in shots:
            shot["duration"] = 12
        chapter["shot_ids"] = [shot["id"] for shot in shots]
        reduced.extend(shots)
    board["shots"] = reduced
    script = "\n".join(shot["voiceover"] for shot in reduced)

    assert len(reduced) == 30
    assert sum(shot["duration"] for shot in reduced) == 360
    with pytest.raises(ValueError, match="章节|镜头"):
        generation_module.validate_storyboard(board, script)


REPEATED_SENTENCE = "看房要结合家庭需求分析空间安排。"
DISTINCT_SUFFIXES = "甲乙丙丁戊己庚辛壬癸"


def _pad_chapter(shots, chapter_index):
    for shot_index, shot in enumerate(shots):
        shot["voiceover"] = (REPEATED_SENTENCE * 3
                             + DISTINCT_SUFFIXES[chapter_index] + DISTINCT_SUFFIXES[shot_index])


def test_manual_edit_cannot_pad_narration_with_a_repeated_sentence(generation_module, valid_longform):
    board = deepcopy(valid_longform["storyboard"])
    for index, chapter in enumerate(board["chapters"]):
        _pad_chapter([shot for shot in board["shots"] if shot["chapter_id"] == chapter["id"]], index)
    script = "\n".join(shot["voiceover"] for shot in board["shots"])

    assert len(set(shot["voiceover"] for shot in board["shots"])) == 36
    assert script.count(REPEATED_SENTENCE) == 108
    with pytest.raises(ValueError, match="重复|凑"):
        generation_module.validate_storyboard(board, script)


def test_generation_rejects_sentence_padding_before_checkpointing_a_chapter(
    generation_module, longform_input, monkeypatch, tmp_path,
):
    from app import provider

    model = ChapterModel()

    def padded_response(messages, **kwargs):
        response = model(messages, **kwargs)
        request = json.loads(messages[-1]["content"])
        if request["stage"] == "chapter":
            content = json.loads(response["choices"][0]["message"]["content"])
            _pad_chapter(content["shots"], int(request["chapter"]["id"][1:]) - 1)
            response["choices"][0]["message"]["content"] = json.dumps(content, ensure_ascii=False)
        return response

    monkeypatch.setattr(provider, "chat", padded_response)
    directory = tmp_path / "padded"
    with pytest.raises((ValueError, provider.ProviderError), match="重复|凑"):
        run_generation(generation_module, longform_input, directory)
    saved = json.loads((directory / "draft.json").read_text(encoding="utf-8"))
    assert saved["chapters"] == []
