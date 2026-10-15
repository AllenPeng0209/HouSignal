"""Six-minute drafts must expand a chosen plan, without padding or stale reuse.

Only the external model boundary is replaced. The six chapters below are
hand-authored distinct explanations, rather than output assembled by the
implementation under test or repeated sentences used to reach a word count.
"""
from copy import deepcopy
import importlib
import importlib.util
import json
import re

import pytest

from test_business import scenario


SECTIONS = ("hook", "outline-1", "outline-2", "outline-3", "outline-4", "cta")
TITLES = ("先明确日常问题", "观察回家动线", "分析公共空间", "检查私密边界", "比较家庭取舍", "整理看房清单")
NARRATION = (
    (
        "资料中的面积为420㎡，看房时先别急着判断大小，想一想家人回家以后会做什么，把常见活动写在纸上再观察空间。",
        "同一张户型图对不同家庭可能意味着不同生活，工作节奏和相处习惯会改变大家对空间安排的期待。",
        "如果只是沿着房间走一圈，很容易记住装修的视觉感受，却忽略日常收纳和通行之间可能存在的冲突。",
        "我们从回家动线开始，再看公共区域和私密边界，最后把观察结果整理成能够带走的看房问题清单。",
        "这里的判断不是替家庭做决定，而是帮助你区分已知资料和个人偏好，让每一项取舍都有明确理由。",
        "接下来带着一个具体生活场景看空间，例如下班携带物品回家时，哪些地方需要停留又如何顺手安放。",
    ),
    (
        "回家动线可以从入口开始观察，把开门拿取物品换鞋和收纳连接起来，看看这些动作是否能够顺序完成。",
        "先在资料上标出入口与常用房间的位置，再沿着路径观察转折，避免只凭单张照片判断整体行走体验。",
        "如果家人经常同时回家，可以讨论入口处的停留方式，哪些物品临时放置又会不会影响其他人通行。",
        "收纳好不好用取决于使用位置和习惯，不仅是柜体看起来多不多，需要结合常携带物品逐项提出问题。",
        "从入口走向公共区域时留意视线变化，观察是否容易找到去向，并把需要补充的资料单独记录下来。",
        "当动线存在多种选择时，分别描述它们对应的生活方式，先确认家庭实际需求，再比较可能的便利程度。",
    ),
    (
        "公共空间承担相聚和独处的不同需求，可以先讨论家人通常在哪里交谈休息，再查看区域之间的联系。",
        "观察客厅时不要只盯着沙发位置，试着把阅读交流和通行放在同一张图上，寻找可能重叠的活动区域。",
        "餐饮区域的使用方式也因家庭而异，日常简餐与朋友聚会需要不同安排，资料没有说明的部分要继续核对。",
        "光线和视线会影响空间感受，但照片的拍摄条件未必代表全天情况，最好把观察与进一步确认的问题分开。",
        "如果公共区域互相连接，可以讨论活动是否容易相互打扰，也要考虑希望保留多少家人之间的交流机会。",
        "把公共空间的优点和顾虑并列写下来，避免被单个装修细节带走判断，真正重要的是它如何承接家庭日常。",
    ),
    (
        "私密边界不只是房间有没有门，还包括通行和视线如何经过，先看看休息区域与主要活动区域的关系。",
        "家人的作息可能不同，可以用早起和晚归的生活场景检查路径，讨论哪些活动需要安静又如何减少干扰。",
        "如果家里需要远程工作，先列出通话阅读和整理资料的需求，再确认候选区域是否容易与其他活动冲突。",
        "对声音的感受需要现场和资料共同确认，单凭户型图无法得出隔声结论，应把待核验问题明确留在清单里。",
        "客人到访时的行走路线也值得讨论，公共活动与家庭私密区域之间是否需要额外安排，取决于你的相处习惯。",
        "边界清晰和交流方便并不是统一答案，先说明家庭最在意的情境，再比较不同安排各自带来的实际取舍。",
    ),
    (
        "完成观察以后，把每个空间问题对应到家庭需求，区分必须满足的条件与能够协商的偏好，比较才有依据。",
        "可以让家人分别写下最常发生的生活场景，再一起核对彼此的优先顺序，避免只由单个人的印象代表大家。",
        "对于资料已经说明的情况标出依据，对于尚未确认的情况保留问题，不要把推测当成楼盘已经具备的事实。",
        "当不同需求发生冲突时，说明各个选择会改变哪些日常活动，比较具体影响比简单评价好坏更容易达成共识。",
        "空间取舍也可能随着家庭生活变化，讨论当前需求的同时可以留出调整余地，但不承诺未来使用效果一定如此。",
        "最终选择应结合核验资料和实际观察，这份分析提供的是比较方法，帮助你看清理由而不是代替你作决定。",
    ),
    (
        "离开看房现场前，把回家动线公共活动和私密边界的问题重新核对一遍，看看哪些已经回答哪些仍需补充。",
        "清单最好写成能够直接询问的问题，附上相关资料位置和自己的观察，后续确认时更容易找到对应的依据。",
        "遇到无法立即确认的信息，可以先记录需要哪类资料和负责沟通的人，等待补充后再更新家庭的比较结果。",
        "如果准备比较另一处空间，沿用相同的生活场景和问题顺序，尽量让不同选择在一致的需求下接受观察。",
        "和家人讨论时先分享具体发现，再说明自己的偏好与理由，也给不同生活习惯留出表达空间，逐项完成取舍。",
        "你最想解决哪一种日常空间问题，可以带着这个问题整理自己的看房清单，下一次观察就从熟悉的生活开始。",
    ),
)


@pytest.fixture
def generation_module():
    # A missing feature is an explicit test failure, never a collection error.
    assert importlib.util.find_spec("app.longform_generation") is not None, "缺少分章长视频生成入口"
    return importlib.import_module("app.longform_generation")


@pytest.fixture(autouse=True)
def prohibit_unconfigured_model_calls(monkeypatch):
    from app import provider
    def blocked(*_args, **_kwargs):
        pytest.fail("长视频单测必须提供明确的外部模型响应夹具")
    monkeypatch.setattr(provider, "chat", blocked)
    monkeypatch.setattr(provider, "cloud_chat", blocked)


@pytest.fixture
def longform_input():
    return {
        "project": {"id": "p", "name": "江岸", "city": "苏州", "is_test": False},
        "topic": {"id": "space", "title": "从生活理解空间", "fact_refs": [{"id": "f", "revision": 1}]},
        "channel": "抖音", "audience": "改善家庭", "target_duration_seconds": 360,
        "facts": [{"id": "f", "revision": 1, "label": "面积", "value": "420", "unit": "㎡", "source": "测绘文件第2页"}],
        "assets": [{"id": "a", "name": "客厅实拍", "kind": "image", "origin_type": "actual", "description": "授权空间照片"}],
        "material_basis": {"kind": "actual", "expression_requirements": ["仅根据已核验资料描述空间"]},
        "planning_context": {
            "plan_id": "plan", "title": "家庭的空间取舍", "angle": "先讲生活，再讲面积", "brief": "带走一份看房问题清单",
            "planning": {"content_type": "professional", "subject": "空间取舍", "target_seconds": 360,
                         "skill_ids": ["topic-evaluation", "titles-and-hooks"],
                         "hook": "看房先别急着判断面积，先想家人的日常", "outline": [
                             "从入口观察回家动线", "分析公共空间与活动关系", "检查休息和工作的私密边界", "用家庭需求比较空间取舍"],
                         "cta": "带着自己的日常问题整理看房清单"},
            "content_type": {"id": "professional", "purpose": "用已核验资料建立专业理解"},
            "methods": [{"id": "topic-evaluation", "instructions": "检查目标家庭的问题是否具体，指出需要补充的证据",
                         "output_requirements": ["每个判断说明依据"]},
                        {"id": "titles-and-hooks", "instructions": "用生活问题打开内容，再给出明确观看收益",
                         "output_requirements": ["开场服务于后续内容"]}],
            "reference": {"reference_id": "reference", "title": "空间观察参考", "evidence_basis": "transcript_and_frames",
                          "source_digest": "d" * 64, "beats": [
                              {"id": "beat-1", "start_seconds": 0, "end_seconds": 10, "purpose": "生活问题开场"},
                              {"id": "beat-2", "start_seconds": 10, "end_seconds": 20, "purpose": "顺序观察动线"},
                              {"id": "beat-3", "start_seconds": 20, "end_seconds": 30, "purpose": "公共区域关系"},
                              {"id": "beat-4", "start_seconds": 30, "end_seconds": 40, "purpose": "私密边界比较"},
                              {"id": "beat-5", "start_seconds": 40, "end_seconds": 50, "purpose": "需求与取舍对应"},
                              {"id": "beat-6", "start_seconds": 50, "end_seconds": 60, "purpose": "收束并提出行动"}]},
        },
    }


def outline_reply():
    return {"topic": "从生活理解空间", "cover_title": "看房先看日常", "chapters": [
        {"id": f"c{i + 1}", "title": title, "purpose": title,
         "plan_sections": [SECTIONS[i]], "reference_beat_ids": [f"beat-{i + 1}"], "target_seconds": 60, "shot_count": 6}
        for i, title in enumerate(TITLES)]}


def chapter_reply(index):
    return {"chapter_id": f"c{index + 1}", "shots": [
        {"id": f"c{index + 1}-shot-{i + 1}", "chapter_id": f"c{index + 1}",
         "plan_section": SECTIONS[index], "reference_beat_ids": [f"beat-{index + 1}"],
         "voiceover": text, "visual_direction": f"结合授权空间资料展示{TITLES[index]}，逐项标出当前观察问题",
         "visual_type": "walkthrough", "caption": TITLES[index], "fact_refs": [{"id": "f", "revision": 1}],
         "asset_id": "a", "duration": 10, "start": 0, "production_status": "ready"}
        for i, text in enumerate(NARRATION[index])]}


def model_response(content):
    return {"model": "configured-test-model", "usage": {"prompt_tokens": 100, "completion_tokens": 200},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}]}


class ChapterModel:
    """The supplier accepts the documented stage, without generating our result."""
    def __init__(self, *, mutation=None, interrupted_chapter=None):
        self.calls = []
        self.mutation = mutation
        self.interrupted_chapter = interrupted_chapter

    def __call__(self, messages, **kwargs):
        from app.provider import ProviderError
        request = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
        stage = request["stage"]
        chapter = request.get("chapter", {})
        cid = chapter.get("id")
        self.calls.append({"stage": stage, "chapter_id": cid, "request": request, "kwargs": kwargs})
        if stage == "outline":
            content = outline_reply()
            if self.mutation == "duplicate_chapter":
                content["chapters"][1]["id"] = "c1"
            elif self.mutation == "missing_outline":
                content["chapters"][4]["plan_sections"] = ["outline-3"]
            elif self.mutation == "missing_reference":
                content["chapters"][4]["reference_beat_ids"] = ["beat-4"]
            elif self.mutation == "unknown_reference":
                content["chapters"][4]["reference_beat_ids"] = ["foreign-beat"]
        elif stage == "chapter":
            if cid == self.interrupted_chapter:
                raise ProviderError("测试中断", usage_receipt={"model": "configured-test-model", "usage": {"prompt_tokens": 100, "completion_tokens": 0}})
            index = int(cid.removeprefix("c")) - 1
            content = chapter_reply(index)
            if index == 0:
                if self.mutation == "foreign_asset":
                    content["shots"][0]["asset_id"] = "foreign-asset"
                elif self.mutation == "foreign_fact":
                    content["shots"][0]["fact_refs"] = [{"id": "foreign-fact", "revision": 1}]
                elif self.mutation == "stale_fact":
                    content["shots"][0]["fact_refs"] = [{"id": "f", "revision": 99}]
                elif self.mutation == "invented_quantity":
                    content["shots"][0]["voiceover"] = content["shots"][0]["voiceover"].replace("420㎡", "888㎡")
                elif self.mutation == "planned_shot":
                    content["shots"][0]["asset_id"] = None
                    content["shots"][0]["production_status"] = "planned"
                elif self.mutation == "short_narration":
                    for shot in content["shots"]:
                        shot["voiceover"] = "只看装修。"
                elif self.mutation == "overlong_narration":
                    content["shots"][0]["voiceover"] *= 5
                elif self.mutation == "repeated_narration":
                    content["shots"][1]["voiceover"] = content["shots"][0]["voiceover"]
                elif self.mutation == "missing_shot_section":
                    for shot in content["shots"]:
                        shot["plan_section"] = "outline-1"
            if index == 1 and self.mutation == "cross_chapter_repeated_narration":
                content["shots"][0]["voiceover"] = NARRATION[0][0]
        else:
            pytest.fail(f"未知模型阶段: {stage}")
        return model_response(content)


def run_generation(module, data, path, *, validate=None, resume_dir=None, **callbacks):
    return module.generate_longform(data, checkpoint_dir=path, request_id="test-request",
                                   validate=validate or (lambda result: result),
                                   before_call=callbacks.get("before_call", lambda: None),
                                   on_usage=callbacks.get("on_usage", lambda _response: None),
                                   on_progress=callbacks.get("on_progress"), resume_dir=resume_dir)


def chinese_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def test_six_minute_draft_expands_every_plan_section_and_reference_beat(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    model = ChapterModel()
    monkeypatch.setattr(provider, "chat", model)
    usages, calls, validations = [], [], []
    def validate(result):
        validations.append(deepcopy(result))
        return result
    result = run_generation(generation_module, longform_input, tmp_path / "run", validate=validate,
                            before_call=lambda: calls.append("checked"), on_usage=usages.append)
    board = result["storyboard"]
    assert board["schema_version"] == 2
    assert board["target_seconds"] == 360
    assert len(board["chapters"]) == 6
    assert len(board["shots"]) == 36
    assert sum(shot["duration"] for shot in board["shots"]) == 360
    assert {shot["plan_section"] for shot in board["shots"]} == set(SECTIONS)
    assert {beat for shot in board["shots"] for beat in shot["reference_beat_ids"]} == {f"beat-{i}" for i in range(1, 7)}
    for chapter in board["chapters"]:
        shots = [shot for shot in board["shots"] if shot["chapter_id"] == chapter["id"]]
        assert len(shots) == 6
        assert chapter["shot_ids"] == [shot["id"] for shot in shots]
        assert sum(shot["duration"] for shot in shots) == 60
        assert 228 <= sum(chinese_count(shot["voiceover"]) for shot in shots) <= 312
    assert result["script"] == "\n".join(shot["voiceover"] for shot in board["shots"])
    assert 1368 <= chinese_count(result["script"]) <= 1872
    assert len(set(shot["voiceover"] for shot in board["shots"])) == 36
    assert result["fact_refs"] == [{"id": "f", "revision": 1}]
    assert board["voiceover"] == {"enabled": True, "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-10%"}
    assert board["planning_context"]["plan_id"] == "plan"
    assert board["planning_context"]["hook"] == longform_input["planning_context"]["planning"]["hook"]
    assert board["planning_context"]["outline"] == longform_input["planning_context"]["planning"]["outline"]
    assert board["planning_context"]["cta"] == "带着自己的日常问题整理看房清单"
    assert board["planning_context"]["reference"]["reference_id"] == "reference"
    assert board["planning_context"]["reference"]["source_digest"] == "d" * 64
    assert validations and validations[-1]["script"] == result["script"]
    # These are budget/evidence checks and supplier receipts, not estimates.
    assert len(calls) >= 7
    assert len(usages) == 7
    assert all(receipt["usage"]["completion_tokens"] == 200 for receipt in usages)
    assert [(call["stage"], call["chapter_id"]) for call in model.calls] == [("outline", None)] + [("chapter", f"c{i}") for i in range(1, 7)]
    assert model.calls[0]["kwargs"]["max_tokens"] >= 3000
    assert all(call["kwargs"]["max_tokens"] >= 4000 for call in model.calls[1:])
    for call in model.calls:
        sent = json.dumps(call["request"], ensure_ascii=False)
        assert "检查目标家庭的问题是否具体" in sent
        if call["stage"] == "outline":
            for section in longform_input["planning_context"]["planning"]["outline"]:
                assert section in sent
            for beat in longform_input["planning_context"]["reference"]["beats"]:
                assert beat["purpose"] in sent
        else:
            index = int(call["chapter_id"].removeprefix("c")) - 1
            planning = longform_input["planning_context"]["planning"]
            sections = [planning["hook"], *planning["outline"], planning["cta"]]
            assert sections[index] in sent
            assert longform_input["planning_context"]["reference"]["beats"][index]["purpose"] in sent
            # Keep the global narrative visible, with only this chapter's
            # source evidence expanded in the resident model's small context.
            assert all(title in sent for title in TITLES)
        assert call["kwargs"].get("model") in (None, "")


@pytest.mark.parametrize("mutation", ["duplicate_chapter", "missing_outline", "missing_reference", "unknown_reference",
                                      "short_narration", "overlong_narration", "repeated_narration",
                                      "cross_chapter_repeated_narration", "missing_shot_section"])
def test_invalid_or_padded_chapter_model_output_is_rejected(generation_module, longform_input, monkeypatch, tmp_path, mutation):
    from app import provider
    monkeypatch.setattr(provider, "chat", ChapterModel(mutation=mutation))
    with pytest.raises((ValueError, provider.ProviderError)):
        run_generation(generation_module, longform_input, tmp_path / "run")


@pytest.mark.parametrize("mutation", ["foreign_asset", "foreign_fact", "stale_fact", "invented_quantity"])
def test_longform_rejects_foreign_project_evidence(generation_module, longform_input, scenario, monkeypatch, tmp_path, mutation):
    from app import provider
    from app.business import validate_generated_content
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider, "chat", ChapterModel(mutation=mutation))
    with pytest.raises((ValueError, provider.ProviderError)):
        run_generation(generation_module, longform_input, tmp_path / "run",
                       validate=lambda result: validate_generated_content(db, ctx, "p", result))


def test_longform_preserves_v2_after_real_project_evidence_validation(generation_module, longform_input, scenario, monkeypatch, tmp_path):
    from app import provider
    from app.business import validate_generated_content
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider, "chat", ChapterModel())
    result = run_generation(generation_module, longform_input, tmp_path / "run",
                            validate=lambda result: validate_generated_content(db, ctx, "p", result))
    assert result["storyboard"]["schema_version"] == 2
    assert len(result["storyboard"]["shots"]) == 36
    assert result["fact_refs"] == [{"id": "f", "revision": 1}]


def test_planned_shot_keeps_material_gap_explicit(generation_module, longform_input, scenario, monkeypatch, tmp_path):
    from app import provider
    from app.business import validate_generated_content
    db, ctx, *_ = scenario
    monkeypatch.setattr(provider, "chat", ChapterModel(mutation="planned_shot"))
    result = run_generation(generation_module, longform_input, tmp_path / "run",
                            validate=lambda result: validate_generated_content(db, ctx, "p", result))
    shot = result["storyboard"]["shots"][0]
    assert shot["asset_id"] is None
    assert shot["production_status"] == "planned"
    assert shot["visual_direction"]
    assert len(result["storyboard"]["shots"]) == 36


def test_interrupted_generation_reuses_only_completed_validated_chapters(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    first = ChapterModel(interrupted_chapter="c2")
    monkeypatch.setattr(provider, "chat", first)
    checkpoint = tmp_path / "interrupted"
    usages = []
    with pytest.raises(provider.ProviderError, match="测试中断"):
        run_generation(generation_module, longform_input, checkpoint, on_usage=usages.append)
    saved = [path.read_text(encoding="utf-8") for path in checkpoint.rglob("*.json")]
    assert saved and any(NARRATION[0][0] in text for text in saved)
    assert not any(NARRATION[1][0] in text for text in saved)
    assert len(usages) == 3  # The failed supplier attempt still has a receipt.
    resumed = ChapterModel()
    monkeypatch.setattr(provider, "chat", resumed)
    result = run_generation(generation_module, deepcopy(longform_input), tmp_path / "resumed", resume_dir=checkpoint)
    assert len(result["storyboard"]["shots"]) == 36
    assert [(call["stage"], call["chapter_id"]) for call in resumed.calls] == [("chapter", f"c{i}") for i in range(2, 7)]
    assert result["storyboard"]["shots"][0]["voiceover"] == NARRATION[0][0]


def test_completed_checkpoint_still_revalidates_current_project_facts(generation_module, longform_input, scenario, monkeypatch, tmp_path):
    from app import provider
    from app.business import validate_generated_content
    db, ctx, _project, fact, *_ = scenario
    validate = lambda result: validate_generated_content(db, ctx, "p", result)
    checkpoint = tmp_path / "completed"
    monkeypatch.setattr(provider, "chat", ChapterModel())
    run_generation(generation_module, longform_input, checkpoint, validate=validate)
    fact.revision = 2
    db.commit()
    def no_regeneration(*_args, **_kwargs):
        pytest.fail("完整检查点应先校验已有稿件，不重新消费模型调用")
    monkeypatch.setattr(provider, "chat", no_regeneration)
    with pytest.raises(ValueError, match="未经核验或过期"):
        run_generation(generation_module, deepcopy(longform_input), tmp_path / "rechecked",
                       validate=validate, resume_dir=checkpoint)


def test_permission_cancelled_after_supplier_response_does_not_checkpoint_that_chapter(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    model = ChapterModel()
    state = {"cancelled": False}
    def response_then_cancel(messages, **kwargs):
        response = model(messages, **kwargs)
        if model.calls[-1]["chapter_id"] == "c2":
            state["cancelled"] = True
        return response
    def permission_check():
        if state["cancelled"]:
            raise ValueError("任务已取消")
    monkeypatch.setattr(provider, "chat", response_then_cancel)
    checkpoint = tmp_path / "cancelled"
    usages = []
    with pytest.raises(ValueError, match="任务已取消"):
        run_generation(generation_module, longform_input, checkpoint, before_call=permission_check, on_usage=usages.append)
    saved = [path.read_text(encoding="utf-8") for path in checkpoint.rglob("*.json")]
    assert any(NARRATION[0][0] in text for text in saved)
    assert not any(NARRATION[1][0] in text for text in saved)
    assert all(call["chapter_id"] != "c3" for call in model.calls)
    assert len(usages) == 3


@pytest.mark.parametrize("change", ["hook", "reference_digest", "method_instructions"])
def test_changed_input_digest_never_reuses_old_chapters(generation_module, longform_input, monkeypatch, tmp_path, change):
    from app import provider
    checkpoint = tmp_path / "first-plan"
    monkeypatch.setattr(provider, "chat", ChapterModel(interrupted_chapter="c2"))
    with pytest.raises(provider.ProviderError):
        run_generation(generation_module, longform_input, checkpoint)
    updated = deepcopy(longform_input)
    if change == "hook":
        marker = "先讨论家庭的工作和休息习惯再看空间"
        updated["planning_context"]["planning"]["hook"] = marker
    elif change == "reference_digest":
        marker = "e" * 64
        updated["planning_context"]["reference"]["source_digest"] = marker
    else:
        marker = "重新逐项比较家庭需求和需要补充的证据"
        updated["planning_context"]["methods"][0]["instructions"] = marker
    model = ChapterModel()
    monkeypatch.setattr(provider, "chat", model)
    result = run_generation(generation_module, updated, tmp_path / "new-plan", resume_dir=checkpoint)
    assert [(call["stage"], call["chapter_id"]) for call in model.calls] == [("outline", None)] + [("chapter", f"c{i}") for i in range(1, 7)]
    assert any(marker in json.dumps(call["request"], ensure_ascii=False) for call in model.calls)
    if change == "hook":
        assert result["storyboard"]["planning_context"]["hook"] == marker
    elif change == "reference_digest":
        assert result["storyboard"]["planning_context"]["reference"]["source_digest"] == marker


def test_changed_trusted_director_method_invalidates_cached_chapters(generation_module, longform_input, monkeypatch, tmp_path):
    from app import provider
    from app.content_skills import catalog
    checkpoint = tmp_path / "old-director"
    monkeypatch.setattr(provider, "chat", ChapterModel(interrupted_chapter="c2"))
    with pytest.raises(provider.ProviderError, match="测试中断"):
        run_generation(generation_module, longform_input, checkpoint)
    original_definition = catalog.definition
    marker = "编导新要求：每章先指出观察问题，再说明证据边界和家庭取舍"
    def revised_definition(name):
        result = deepcopy(original_definition(name))
        if name == "video-script":
            result["instructions"].append(marker)
        return result
    monkeypatch.setattr(catalog, "definition", revised_definition)
    model = ChapterModel()
    monkeypatch.setattr(provider, "chat", model)
    result = run_generation(generation_module, deepcopy(longform_input), tmp_path / "new-director", resume_dir=checkpoint)
    assert len(result["storyboard"]["shots"]) == 36
    assert [(call["stage"], call["chapter_id"]) for call in model.calls] == [("outline", None)] + [("chapter", f"c{i}") for i in range(1, 7)]
    assert all(marker in json.dumps(call["request"], ensure_ascii=False) for call in model.calls)


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("relative", ["job", "group/project/job"])
def test_checkpoint_rejects_symlink_in_any_parent(generation_module, longform_input, monkeypatch, tmp_path, operation, relative):
    from app import provider
    external = tmp_path / "external"
    target = external / relative
    if operation == "read":
        monkeypatch.setattr(provider, "chat", ChapterModel())
        run_generation(generation_module, longform_input, target)
        def no_supplier_call(*_args, **_kwargs):
            pytest.fail("读取含链接的恢复目录前必须拒绝请求")
        monkeypatch.setattr(provider, "chat", no_supplier_call)
    else:
        target.mkdir(parents=True)
        (target / "draft.json").write_text('{"sentinel":"outside draft must remain unchanged"}', encoding="utf-8")
        monkeypatch.setattr(provider, "chat", ChapterModel())
    draft = target / "draft.json"
    original_bytes = draft.read_bytes()
    link = tmp_path / "linked-parent"
    link.symlink_to(external, target_is_directory=True)
    unsafe_checkpoint = link / relative
    assert not unsafe_checkpoint.is_symlink()  # The link is an ancestor.
    with pytest.raises(ValueError, match="链接|symlink"):
        run_generation(generation_module, deepcopy(longform_input), unsafe_checkpoint)
    assert draft.read_bytes() == original_bytes
    assert sorted(path.name for path in target.iterdir()) == ["draft.json"]
