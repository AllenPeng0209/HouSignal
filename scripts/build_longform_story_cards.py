#!/usr/bin/env python3
"""Draw source-bound teaching cards; no model, network, API or service writes.

Inputs: a final content object, a storyboard JSON (+ optional --script), or a
private generation checkpoint for partial preview. Outputs are PNGs and an
explicit shot-id mapping. These diagrams are illustrative, never floor plans.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

BG = "#F3F6FC"
INK = "#18334E"
MUTED = "#536D88"
BLUE = "#386FE3"
TEAL = "#20878B"
PALE = "#E8EFFB"
NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:㎡|m²|m2|平方米|米|房|万元|元|%)")
TOKEN = re.compile(r"\d+(?:\.\d+)?(?:\s*(?:㎡|m²|m2|平方米|米|房|万元|元|%))?|[A-Za-z]+|\n|.")


def font(size):
    candidates = ("/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/STHeiti Light.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise ValueError("教学图示需要可读中文字体")


def wrapped(draw, text, face, width):
    lines, line = [], ""
    for token in TOKEN.findall(text):
        if token == "\n":
            lines.append(line); line = ""; continue
        if draw.textlength(token, font=face) > width:
            raise ValueError("原文数字或词组宽度超过文字框，请扩大图示尺寸")
        if line and draw.textlength(line + token, font=face) > width:
            lines.append(line); line = token
        else:
            line += token
    if line:
        lines.append(line)
    return lines or [""]


def text_box(draw, text, box, *, size, minimum=22, color=INK, role="body", records=None):
    """Fit the whole supplied text; no omitted lines or tiny hidden type."""
    x0, y0, x1, y1 = box
    for actual_size in range(size, minimum - 1, -1):
        face = font(actual_size)
        lines = wrapped(draw, text, face, x1 - x0)
        step = round(actual_size * 1.30)
        if len(lines) * step <= y1 - y0:
            break
    else:
        raise ValueError("图示文字过长，请精简观众字幕或使用更大画幅；不会截断原文")
    bounds, y = [], y0
    for line in lines:
        measured = draw.textbbox((0, 0), line, font=face)
        # Align actual glyph top to the declared box, including CJK bearings.
        position = (x0 - measured[0], y - measured[1])
        draw.text(position, line, font=face, fill=color)
        bounds.append(list(draw.textbbox(position, line, font=face)))
        y += step
    record = {"role": role, "text": text, "lines": lines, "font_size": actual_size,
              "box": list(box), "drawn_bounds": bounds}
    if records is not None:
        records.append(record)
    return record


def points(text):
    # Keep original decimal strings and questions; no inferred measurements.
    values = [part.strip() for part in re.split(r"(?<=[。！？?！；;])|\s*→\s*", text) if part.strip()]
    if len(values) <= 1:
        values = [part.strip() for part in re.split(r"[，、；;]", text) if part.strip()]
    return values


def _needs(text):
    clauses = [part.strip() for part in re.split(r"[，。；;！？]", text) if part.strip()]
    groups = ((r"孩子|儿童|学习", "desk"), (r"老人|长辈", "bed"),
              (r"伴侣|休息|睡眠", "bed"), (r"办公|工作", "desk"),
              (r"沙发|客厅|家人|活动|相聚", "sofa"))
    found = []
    for pattern, kind in groups:
        clause = next((part for part in clauses if re.search(pattern, part)), None)
        if clause and clause not in [row["text"] for row in found]:
            match = re.search(pattern, clause)
            found.append({"label": match.group(), "text": clause, "icon": kind})
    return found[:3]


def icon(draw, kind, box, color=BLUE):
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    line = max(3, round(width / 35))
    def rect(a, b, c, d, *, fill=None):
        draw.rounded_rectangle((x0 + width*a, y0 + height*b, x0 + width*c, y0 + height*d),
                               radius=max(4, width/30), outline=color, width=line, fill=fill)
    if kind == "desk":
        rect(.10, .43, .90, .56, fill="#D4E2FF")
        rect(.18, .58, .24, .93); rect(.76, .58, .82, .93)
        rect(.32, .08, .68, .37); rect(.48, .35, .52, .43)
    elif kind == "bed":
        rect(.10, .24, .90, .82, fill="#D4E2FF")
        rect(.16, .30, .45, .48); rect(.55, .30, .84, .48)
        draw.line((x0+width*.13, y0+height*.58, x0+width*.87, y0+height*.58), fill=color, width=line)
        rect(.17, .82, .23, .94); rect(.77, .82, .83, .94)
    else:
        rect(.13, .15, .87, .58, fill="#D4E2FF")
        rect(.08, .48, .92, .84, fill="#D4E2FF")
        rect(.06, .39, .19, .80); rect(.81, .39, .94, .80)
        draw.line((x0+width*.50, y0+height*.51, x0+width*.50, y0+height*.82), fill=color, width=line)


def render_card(shot, chapter, output, *, size=(720, 1280), fictional_numbers=False):
    if min(size) < 600 or max(size) > 1920:
        raise ValueError("教学图示请使用720×1280或1280×720等清晰画幅")
    for key in ("id", "caption", "voiceover"):
        if not isinstance(shot.get(key), str) or not shot[key].strip():
            raise ValueError("每镜需要明确编号、观众字幕与口播")
    if len(shot["caption"]) > 600 or len(shot["voiceover"]) > 2000:
        raise ValueError("逐镜文字超过本地图示上限")
    width, height = size; portrait = height > width
    margin = round(width * .065); inner = width - margin * 2
    image = Image.new("RGB", size, BG); draw = ImageDraw.Draw(image); records = []
    draw.rectangle((0, 0, width, 10), fill=BLUE)
    header_top = 76 if portrait else 66
    notice = "虚构示例 · 非房源实拍" if fictional_numbers else "示例讲解 · 非房源实拍"
    text_box(draw, notice, (margin, header_top, width-margin, header_top+33),
             size=23, minimum=20, color=TEAL, role="illustration_notice", records=records)
    chapter_text = str(chapter.get("title") or "空间核对")
    text_box(draw, chapter_text, (margin, header_top+46, width-margin, header_top+108),
             size=26, minimum=20, color=MUTED, role="chapter", records=records)
    title_top = header_top + 127
    title_height = 247 if portrait else 145
    title_record = text_box(draw, shot["caption"], (margin, title_top, width-margin, title_top+title_height),
                            size=46 if portrait else 43, minimum=22, role="caption", records=records)
    # Reserve the bottom for source boundaries; spoken subtitles may cover it
    # in the movie, while the top illustration notice always remains visible.
    body_top = title_top + len(title_record["lines"]) * round(title_record["font_size"] * 1.30) + 28
    body_bottom = height - (169 if portrait else 119)
    if body_bottom <= body_top + 130:
        raise ValueError("图示画幅不足以同时清晰显示字幕与教学步骤")
    source = shot["caption"] + "\n" + shot["voiceover"]
    numbers = list(dict.fromkeys(match.group() for match in NUMBER.finditer(source)))
    categories = shot.get("visual_type", "scene")
    layout = categories
    needs = _needs(shot["voiceover"])
    flow = [part.strip() for part in shot["caption"].split("→") if part.strip()]
    rows = []
    if len(flow) >= 2:
        layout = "walkthrough"; rows = flow[:5]
    elif categories == "data" or categories == "scene" and numbers:
        layout = "data"; rows = numbers[:4]
    elif categories in {"scene", "comparison"} and needs:
        layout = "needs"; rows = needs
    elif categories in {"walkthrough", "checklist", "comparison"}:
        rows = points(shot["caption"])
        if len(rows) < 2:
            rows = points(shot["voiceover"])
        rows = rows[:5] or [shot["caption"]]
    else:
        layout = "scene"; rows = points(shot["voiceover"])[:3] or [shot["caption"]]
    if layout == "needs":
        columns = len(rows) if not portrait else 1
        gap = 16; cell_width = (inner-gap*(columns-1))//columns
        cell_height = (body_bottom-body_top-gap*(len(rows)-1))//len(rows) if portrait else body_bottom-body_top
        for index, need in enumerate(rows):
            x = margin if portrait else margin + index*(cell_width+gap)
            y = body_top + index*(cell_height+gap) if portrait else body_top
            draw.rounded_rectangle((x,y,x+cell_width,y+cell_height), radius=20, fill="white")
            symbol = min(110, cell_height-30, cell_width//3)
            icon(draw, need["icon"], (x+18,y+18,x+18+symbol,y+18+symbol))
            text_x = x+symbol+36 if portrait else x+18
            text_y = y+25 if portrait else y+symbol+29
            text_box(draw, need["text"], (text_x,text_y,x+cell_width-18,y+cell_height-18),
                     size=30, minimum=22, role="need", records=records)
    else:
        gap = 18; row_height = (body_bottom-body_top-gap*(len(rows)-1))//len(rows)
        for index, row in enumerate(rows):
            y = body_top + index*(row_height+gap)
            row_pad = min(18, max(4, row_height//9))
            draw.rounded_rectangle((margin,y,width-margin,y+row_height), radius=20,
                                   fill=PALE if layout == "data" else "white")
            if layout == "data":
                text_box(draw, row, (margin+25,y+row_pad,width-margin-25,y+row_height-row_pad),
                         size=76 if portrait else 56, minimum=26, color=BLUE, role="source_number", records=records)
            else:
                draw.ellipse((margin+21,y+22,margin+48,y+49), fill=TEAL if layout == "walkthrough" else BLUE)
                text_box(draw, row, (margin+66,y+row_pad,width-margin-23,y+row_height-row_pad),
                         size=30, minimum=22, role="question_or_step", records=records)
                if layout == "walkthrough" and index < len(rows)-1:
                    center = width//2
                    draw.line((center,y+row_height+2,center,y+row_height+14), fill=TEAL, width=3)
                    draw.polygon(((center-6,y+row_height+9),(center+6,y+row_height+9),(center,y+row_height+17)), fill=TEAL)
    if layout == "data":
        footer = "虚构示例数字 · 不代表实际房源" if fictional_numbers else "资料数字 · 口径需核对"
    elif layout == "needs":
        footer = "生活需求示意 · 不代表房源布局"
    else:
        footer = "核对方法示意 · 以实际资料与确认为准"
    text_box(draw, footer, (margin,body_bottom+(24 if portrait else 12),width-margin,height-70), size=23, minimum=20,
             color=TEAL, role="source_boundary", records=records)
    text_box(draw, "资料依据  /  待核对问题  /  家庭优先级", (margin,height-48,width-margin,height-16),
             size=20, minimum=18, color=MUTED, role="recording_method", records=records)
    output = Path(output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return {"shot_id": shot["id"], "chapter_id": shot.get("chapter_id"), "path": str(output),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "width": width, "height": height,
            "presentation": "story_card", "is_illustration": True, "fictional_numbers": fictional_numbers,
            "visual_type": categories, "drawn_layout": layout,
            "source_numbers": numbers, "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "fact_refs": shot.get("fact_refs", []), "text_boxes": records}


def load_source(path, script_path=None):
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ValueError("分镜文件超过2MB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if "outline" in value and "chapters" in value and value.get("schema_version") == 1:
        board = {"chapters": value["outline"]["chapters"],
                 "shots": [shot for draft in value["chapters"] for shot in draft["shots"]]}
        partial = len(value["chapters"]) != len(board["chapters"])
    else:
        board = value.get("storyboard", value); partial = False
    if not isinstance(board, dict) or not isinstance(board.get("shots"), list) or not 1 <= len(board["shots"]) <= 120:
        raise ValueError("需要1至120镜的有效分镜或章节checkpoint")
    script = Path(script_path).read_text(encoding="utf-8") if script_path else value.get("script")
    if script is not None and script.rstrip() != "\n".join(shot["voiceover"] for shot in board["shots"]).rstrip():
        raise ValueError("脚本正文与逐镜口播不同，请提供同一内容版本")
    return board, partial, hashlib.sha256(path.read_bytes()).hexdigest()


def build_cards(input_path, output_dir, *, script_path=None, size=(720,1280)):
    board, partial, digest = load_source(input_path, script_path)
    chapters = {row["id"]: row for row in board["chapters"]}
    output_dir = Path(output_dir).resolve(); output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    fictional_numbers = any("虚构" in shot["voiceover"] for shot in board["shots"])
    for index, shot in enumerate(board["shots"]):
        if shot.get("chapter_id") not in chapters:
            raise ValueError("镜头无法对应本版本章节")
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", shot["id"])[:100] or hashlib.sha256(shot["id"].encode()).hexdigest()[:16]
        records.append(render_card(shot, chapters[shot["chapter_id"]], output_dir/f"{index+1:03d}-{safe_id}.png",
                                   size=size, fictional_numbers=fictional_numbers))
    manifest = {"schema_version":1,"source_json_sha256":digest,"partial_checkpoint":partial,
                "presentation":"story_card","is_illustration":True,"fictional_numbers":fictional_numbers,"shot_count":len(records),
                "mapping":{record["shot_id"]:record["path"] for record in records},"cards":records}
    manifest["shots"] = [{key:record[key] for key in ("shot_id","chapter_id","path","sha256","width","height","presentation","source_text_sha256","fact_refs")}
                         for record in records]
    (output_dir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    columns = 3; thumb_width = 240; thumb_height = round(thumb_width*size[1]/size[0])
    sheet = Image.new("RGB",(columns*thumb_width,((len(records)+columns-1)//columns)*(thumb_height+32)),BG)
    draw=ImageDraw.Draw(sheet)
    for index, record in enumerate(records):
        x,y=index%columns*thumb_width,index//columns*(thumb_height+32)
        with Image.open(record["path"]) as card:
            sheet.paste(card.resize((thumb_width,thumb_height)),(x,y))
        draw.text((x+8,y+thumb_height+6),record["shot_id"],font=font(14),fill=INK)
    sheet.save(output_dir/"contact-sheet.jpg",quality=92)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path,nargs="?")
    parser.add_argument("--storyboard",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--script",type=Path)
    parser.add_argument("--size",choices=("720x1280","1280x720"),default="720x1280")
    args=parser.parse_args();size=tuple(map(int,args.size.split("x")))
    if bool(args.input) == bool(args.storyboard):
        parser.error("请提供一个input或--storyboard分镜文件")
    manifest=build_cards(args.storyboard or args.input,args.output,script_path=args.script,size=size)
    print(json.dumps({"shot_count":manifest["shot_count"],"partial_checkpoint":manifest["partial_checkpoint"],
                      "manifest":str(args.output.resolve()/"manifest.json"),"mapping":manifest["mapping"]},ensure_ascii=False))


if __name__=="__main__":
    main()
