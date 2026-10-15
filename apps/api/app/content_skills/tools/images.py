"""Project image functions adapted from Easel shared image_ops/img_enhance/meme_ops.

Original project: ZJU-REAL/Easel, Apache-2.0 (see this directory's LICENSE).
Modified for Louxiang: argparse/sys.exit/env discovery removed; authorized
assets are supplied by the project worker, paths are server owned, resource
bounds and explicit errors replace silent fallbacks. No Easel runtime imports.
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


SKILLS = frozenset({"image-editing", "image-enhance", "meme-generator"})
MAX_PIXELS = 30_000_000
FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}


def _number(parameters, name, default=None, *, minimum=0, maximum=4096, integer=False):
    value = parameters.get(name, default)
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name}参数范围无效")
    if integer and int(value) != value:
        raise ValueError(f"{name}需要整数")
    return int(value) if integer else value


def _image(asset):
    try:
        with Image.open(Path(asset["path"])) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("图片超过3000万像素处理上限")
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, Image.DecompressionBombError):
        raise ValueError("素材不是可解码图片") from None


def _size(width, height):
    width, height = int(width), int(height)
    if min(width, height) < 1 or width * height > MAX_PIXELS:
        raise ValueError("输出图片尺寸无效或超过3000万像素")
    return width, height


def _font(size):
    for filename in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc", "/Library/Fonts/Arial Unicode.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(filename).is_file():
            return ImageFont.truetype(filename, size)
    return ImageFont.load_default(size=size)


def _save(image, output_dir, fmt="png", quality=90):
    if fmt not in FORMATS:
        raise ValueError("图片格式仅支持png/jpg/webp")
    path = output_dir / f"image.{fmt}"
    if path.is_symlink():
        raise ValueError("输出目录包含非法链接")
    if fmt in {"jpg", "jpeg"}:
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image.convert("RGBA"))
        image = background.convert("RGB")
    options = {"quality": quality} if fmt != "png" else {"optimize": True}
    image.save(path, format=FORMATS[fmt], **options)
    return path


def _ratio(value):
    try:
        if isinstance(value, str) and ":" in value:
            numerator, denominator = value.split(":")
            result = float(numerator) / float(denominator)
        else:
            result = float(value)
        if not math.isfinite(result) or not 0.1 <= result <= 10:
            raise ValueError()
        return result
    except (ValueError, TypeError, ZeroDivisionError):
        raise ValueError("ratio需要有效宽高比，例如9:16") from None


def _collage(images, parameters):
    if not 2 <= len(images) <= 16:
        raise ValueError("拼图需要2至16张已选图片")
    cell_width = _number(parameters, "cell_width", images[0].width, minimum=1, integer=True)
    cell_height = _number(parameters, "cell_height", images[0].height, minimum=1, integer=True)
    gap = _number(parameters, "gap", 10, maximum=200, integer=True)
    grid = parameters.get("grid")
    if grid:
        try:
            cols, rows = [int(part) for part in str(grid).lower().split("x")]
        except ValueError:
            raise ValueError("grid格式需为列x行，例如3x2") from None
        if not 1 <= cols <= 16 or not 1 <= rows <= 16 or cols * rows < len(images):
            raise ValueError("拼图网格不足以容纳图片")
    elif parameters.get("mode", "horizontal") == "vertical":
        cols, rows = 1, len(images)
    elif parameters.get("mode", "horizontal") == "horizontal":
        cols, rows = len(images), 1
    else:
        raise ValueError("mode仅支持horizontal/vertical")
    canvas = Image.new("RGBA", _size(cols * cell_width + gap * (cols - 1), rows * cell_height + gap * (rows - 1)), parameters.get("background", "white"))
    for index, image in enumerate(images):
        tile = ImageOps.fit(image, (cell_width, cell_height), method=Image.Resampling.LANCZOS)
        canvas.alpha_composite(tile, ((index % cols) * (cell_width + gap), (index // cols) * (cell_height + gap)))
    return canvas


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for character in str(text)[:2000]:
        if character == "\n" or draw.textlength(line + character, font=font) > width:
            lines.append(line)
            line = "" if character == "\n" else character
        else:
            line += character
    if line:
        lines.append(line)
    return lines


def _meme(image, text, parameters):
    layout = parameters.get("layout", "overlay")
    if layout not in {"overlay", "top-bar", "bottom-bar"}:
        raise ValueError("layout仅支持overlay/top-bar/bottom-bar")
    font_size = max(12, min(72, round(image.width * 0.075)))
    font = _font(font_size)
    if layout != "overlay":
        caption = parameters.get("caption") or text
        if not caption.strip():
            raise ValueError("请填写表情包文字")
        lines = _wrap(ImageDraw.Draw(image), caption, font, max(1, image.width - 16))
        bar_height = max(font_size + 20, len(lines) * (font_size + 6) + 20)
        canvas = Image.new("RGBA", _size(image.width, image.height + bar_height), parameters.get("bar_color", "white"))
        canvas.alpha_composite(image, (0, bar_height if layout == "top-bar" else 0))
        draw = ImageDraw.Draw(canvas)
        start = 10 if layout == "top-bar" else image.height + 10
        for index, line in enumerate(lines):
            draw.text(((image.width - draw.textlength(line, font=font)) / 2, start + index * (font_size + 6)), line, font=font, fill=parameters.get("text_color", "black"))
        return canvas
    draw = ImageDraw.Draw(image)
    top = parameters.get("top") or text
    bottom = parameters.get("bottom", "")
    if not str(top).strip() and not str(bottom).strip():
        raise ValueError("请填写表情包文字")
    for body, position in ((top, 6), (bottom, max(0, image.height - font_size - 12))):
        if body:
            draw.text(((image.width - draw.textlength(str(body), font=font)) / 2, position), str(body)[:300], font=font, fill="white", stroke_width=max(1, font_size // 12), stroke_fill="black")
    return image


def _color_preserving_autocontrast(image):
    """Conservative luminance gain; never stretch three RGB channels apart."""
    import numpy as np
    luminance = image.convert("L")
    original = np.asarray(luminance, dtype=np.float32)
    leveled = np.asarray(ImageOps.autocontrast(luminance, cutoff=1), dtype=np.float32)
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    # Full histogram stretching can map a flat colored product to black.
    # Limit automatic exposure changes, preserve its channel ratios, and stay
    # inside the RGB gamut instead of clipping channels independently.
    gain = np.clip(leveled / np.maximum(original, 1), .9, 1.1)
    gain = np.minimum(gain, 255 / np.maximum(rgb.max(axis=2), 1))
    adjusted = np.rint(rgb * gain[:, :, None]).astype(np.uint8)
    return Image.fromarray(adjusted)


def dispatch(skill_name, *, input_text, assets, parameters, output_dir, context):
    if not assets:
        raise ValueError("请先选择要处理的图片素材")
    images = [_image(asset) for asset in assets]
    image = images[0]
    fmt = str(parameters.get("format", "png")).lower()
    quality = _number(parameters, "quality", 90, minimum=1, maximum=100, integer=True)
    operation = parameters.get("op", "info")
    if skill_name == "image-enhance":
        if parameters.get("denoise"):
            try:
                import cv2
                import numpy as np
            except ImportError:
                raise ValueError("去噪需要配置本项目OpenCV与NumPy依赖") from None
            array = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
            image = Image.fromarray(cv2.cvtColor(cv2.fastNlMeansDenoisingColored(array, None, 5, 5, 7, 21), cv2.COLOR_BGR2RGB))
        scale = _number(parameters, "scale", 2, minimum=0.1, maximum=8)
        image = image.convert("RGB").resize(_size(round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
        automatic = parameters.get("auto", False)
        if automatic or parameters.get("autocontrast"):
            image = _color_preserving_autocontrast(image)
        sharpen = _number(parameters, "sharpen", 1 if automatic else 0, maximum=5)
        if sharpen:
            image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=round(80 * sharpen), threshold=2))
        image = ImageEnhance.Contrast(image).enhance(_number(parameters, "contrast", 1.05 if automatic else 1, maximum=5))
        image = ImageEnhance.Color(image).enhance(_number(parameters, "saturation", 1.08 if automatic else 1, maximum=5))
    elif skill_name == "meme-generator":
        image = _meme(image, input_text, parameters)
    elif operation == "info":
        path = output_dir / "image-info.json"
        path.write_text(json.dumps({"width": image.width, "height": image.height, "mode": image.mode, "byte_size": Path(assets[0]["path"]).stat().st_size}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"response": f"图片尺寸{image.width}×{image.height}，已生成真实素材信息。", "artifact_paths": [path]}
    elif operation == "resize":
        width = _number(parameters, "width", minimum=1, integer=True)
        height = _number(parameters, "height", minimum=1, integer=True)
        percent = _number(parameters, "percent", minimum=0.1, maximum=800)
        if percent is not None:
            size = _size(round(image.width * percent / 100), round(image.height * percent / 100))
        elif width and height:
            ratio = min(width / image.width, height / image.height)
            size = _size(round(image.width * ratio), round(image.height * ratio)) if parameters.get("keep_ratio") else _size(width, height)
        elif width:
            size = _size(width, round(image.height * width / image.width))
        elif height:
            size = _size(round(image.width * height / image.height), height)
        else:
            raise ValueError("缩放需要width、height或percent")
        image = image.resize(size, Image.Resampling.LANCZOS)
    elif operation == "crop":
        box = parameters.get("box")
        if box is not None:
            try:
                box = [int(part) for part in box.split(",")] if isinstance(box, str) else list(box)
            except (ValueError, TypeError):
                raise ValueError("裁剪box需要四个整数") from None
            if len(box) != 4 or any(type(part) is not int for part in box) or not 0 <= box[0] < box[2] <= image.width or not 0 <= box[1] < box[3] <= image.height:
                raise ValueError("裁剪框超出图片范围")
        else:
            width = _number(parameters, "width", minimum=1, integer=True)
            height = _number(parameters, "height", minimum=1, integer=True)
            if not width or not height or width > image.width or height > image.height:
                raise ValueError("居中裁剪需要不超过原图的width/height")
            left, top = (image.width - width) // 2, (image.height - height) // 2
            box = (left, top, left + width, top + height)
        image = image.crop(box)
    elif operation == "pad":
        ratio = _ratio(parameters.get("ratio", "1:1"))
        size = _size(image.width, round(image.width / ratio)) if image.width / image.height > ratio else _size(round(image.height * ratio), image.height)
        canvas = Image.new("RGBA", size, parameters.get("background", "white"))
        canvas.alpha_composite(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
        image = canvas
    elif operation in {"convert", "compress"}:
        if operation == "compress" and fmt == "png":
            fmt = "jpg"
    elif operation == "thumbnail":
        size = _number(parameters, "size", 256, minimum=1, integer=True)
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
    elif operation == "round":
        radius = _number(parameters, "radius", 40, maximum=min(image.size), integer=True)
        percent = _number(parameters, "radius_percent", maximum=50)
        if percent is not None:
            radius = round(min(image.size) * percent / 100)
        mask = Image.new("L", image.size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
        image.putalpha(mask)
        fmt = "png"
    elif operation == "collage":
        image = _collage(images, parameters)
    elif operation == "watermark":
        opacity = _number(parameters, "opacity", 0.5, maximum=1)
        margin = _number(parameters, "margin", 24, maximum=500, integer=True)
        layer = Image.new("RGBA", image.size)
        text = parameters.get("text") or input_text
        if parameters.get("watermark_asset_index") is not None:
            index = parameters["watermark_asset_index"]
            if type(index) is not int or not 0 <= index < len(images):
                raise ValueError("水印素材选择无效")
            mark = images[index].copy()
            mark.thumbnail((max(1, image.width // 3), max(1, image.height // 3)))
            mark.putalpha(mark.getchannel("A").point(lambda value: round(value * opacity)))
            layer.alpha_composite(mark, (max(0, image.width - mark.width - margin), max(0, image.height - mark.height - margin)))
        elif text:
            font = _font(_number(parameters, "size", 36, minimum=8, maximum=160, integer=True))
            draw = ImageDraw.Draw(layer)
            bounds = draw.textbbox((0, 0), str(text)[:200], font=font)
            width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            position = parameters.get("position", "bottom-right")
            if position not in {"top-left", "top-right", "bottom-left", "bottom-right", "center"}:
                raise ValueError("水印position无效")
            x = margin if position.endswith("left") else image.width - width - margin
            y = margin if position.startswith("top") else image.height - height - margin
            if position == "center":
                x, y = (image.width - width) / 2, (image.height - height) / 2
            color = ImageColor.getrgb(parameters.get("color", "white"))
            draw.text((x, y - bounds[1]), str(text)[:200], font=font, fill=(*color[:3], round(opacity * 255)))
        else:
            raise ValueError("请提供水印文字或选择水印素材")
        image = Image.alpha_composite(image, layer)
    else:
        raise ValueError("不支持的图像处理op")
    path = _save(image, output_dir, fmt, quality)
    limit = _number(parameters, "max_kb", minimum=1, maximum=100000)
    if operation == "compress" and limit is not None:
        while path.stat().st_size > limit * 1024 and quality > 15:
            quality = max(15, quality - 5)
            path = _save(image, output_dir, fmt, quality)
        if path.stat().st_size > limit * 1024:
            path.unlink()
            raise ValueError("指定大小无法在当前分辨率达到，请降低尺寸")
    return {"response": f"已生成{image.width}×{image.height}真实图片，可下载查看。", "artifact_paths": [path]}
