"""Native teaching cards must remain legible and retain exact source numbers."""
from pathlib import Path
import importlib.util

from PIL import Image


def module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "build_longform_story_cards.py"
    assert path.is_file(), "缺少逐镜教学图示生成脚本"
    spec = importlib.util.spec_from_file_location("longform_story_cards", path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def test_long_caption_is_fully_wrapped_inside_a_readable_portrait_card(tmp_path):
    card = module()
    caption = "先记录每位家人日常使用空间的需要，再把资料依据、待核对的问题与家庭优先级分别写清楚。" * 3
    shot = {"id": "long-caption", "chapter_id": "c1", "visual_type": "checklist", "caption": caption,
            "voiceover": "把家庭需求整理成可核对的问题，不把示意画面当成真实房源布局。", "fact_refs": []}
    result = card.render_card(shot, {"id": "c1", "title": "可执行的核对清单", "purpose": "从需求出发核对资料"},
                              tmp_path / "long-caption.png", size=(720, 1280))
    with Image.open(result["path"]) as image:
        assert image.size == (720, 1280)
    title = next(box for box in result["text_boxes"] if box["role"] == "caption")
    assert "".join(title["lines"]) == caption
    assert len(title["lines"]) >= 3
    assert title["font_size"] >= 22
    for box in result["text_boxes"]:
        x0, y0, x1, y1 = box["box"]
        for left, top, right, bottom in box["drawn_bounds"]:
            assert x0 <= left <= right <= x1
            assert y0 <= top <= bottom <= y1


def test_data_card_preserves_decimal_and_unit_strings_without_new_measurements(tmp_path):
    card = module()
    shot = {"id": "data", "chapter_id": "c1", "visual_type": "data",
            "caption": "329㎡建筑面积、8.2米开间、4房同层，分别该核对什么？",
            "voiceover": "建筑面积329㎡，客厅开间8.2米，资料描述4房同层；这些资料数字不能直接证明实际使用体验。",
            "fact_refs": [{"id": "fact-area", "revision": 3}]}
    result = card.render_card(shot, {"id": "c1", "title": "资料数字与家庭需求", "purpose": "记录数字来源与口径"},
                              tmp_path / "data.png", size=(720, 1280))
    assert result["source_numbers"] == ["329㎡", "8.2米", "4房"]
    drawn = "\n".join(line for box in result["text_boxes"] for line in box["lines"])
    for value in ("329㎡", "8.2米", "4房"):
        assert value in drawn
    assert "资料数字 · 口径需核对" in drawn
    assert "示例讲解 · 非房源实拍" in drawn
    assert "8." not in [line for box in result["text_boxes"] for line in box["lines"]]
