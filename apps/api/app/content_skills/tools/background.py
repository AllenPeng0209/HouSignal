"""Project-owned ONNX image segmentation and background replacement.

Behavior is adapted from Easel's remove_bg.py (ZJU-REAL/Easel, Apache-2.0;
see the LICENSE beside this module). Modifications: replaced rembg's automatic
model download, cache discovery, proxy/environment mutation and CLI exits with
an explicitly configured project weight file and direct ONNX Runtime CPU
inference. RGB normalization and output-mask scaling follow the published
U2Net / ISNet session contracts. Pillow preserves source alpha and renders
transparent, solid-color or authorized-image backgrounds. No Easel, rembg,
home-directory cache, credentials or network access is used at runtime.
Optional alpha_matting uses PyMatting's closed-form alpha and multilevel
foreground estimators on the actual ONNX mask, with foreground/background
thresholds 240/10 and local Pillow erosion. Missing PyMatting is a dependency
gap; installing it activates real inference without changing this module.

The only model path is settings.native_background_model_path, confined to
settings.storage_root. A request's model name selects preprocessing, never a
path. The context may contain this project's settings object for injection.
U2Net families use mean=(.485,.456,.406), std=(.229,.224,.225), default 320x320;
ISNet uses mean=(.5,.5,.5), std=(1,1,1), default 1024x1024. Fixed ONNX input
dimensions take precedence. Outputs must be a finite, single-channel mask.

Source reference contracts:
https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2net.py
https://github.com/danielgatis/rembg/blob/main/rembg/sessions/dis_general_use.py
"""
from __future__ import annotations

from collections.abc import Mapping
import importlib.util
import io
import math
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageColor, ImageFilter, ImageOps


SKILL_NAMES = frozenset({"remove-bg"})
MODELS = frozenset({"u2net", "u2netp", "u2net_human_seg", "isnet", "isnet-general-use", "silueta"})
MAX_PIXELS = 30_000_000
_PARAMETERS = frozenset({"model", "bg_color", "background_asset_index", "alpha_matting", "format", "quality"})


def _settings(context=None):
    if isinstance(context,Mapping) and context.get("settings") is not None:
        return context["settings"]
    if context is not None and getattr(context,"settings",None) is not None:
        return context.settings
    if isinstance(context,Mapping) and "native_background_model_path" in context:
        return context
    # Read only this project's settings; no original skill configuration.
    from ...config import settings
    return settings


def _setting(settings,name,default=None):
    return settings.get(name,default) if isinstance(settings,Mapping) else getattr(settings,name,default)


def _model_path(settings):
    configured = _setting(settings,"native_background_model_path")
    if not configured:
        return None
    storage = _setting(settings,"storage_root")
    if storage is None:
        raise ValueError("去背景模型需配置本项目 storage_root")
    root = Path(storage).resolve()
    model = Path(configured)
    if not model.is_absolute():
        raise ValueError("native_background_model_path 必须是项目存储内的绝对路径")
    if model.is_symlink() or not model.resolve().is_relative_to(root):
        raise ValueError("去背景模型权重必须位于本项目 storage_root 内，且不可为符号链接")
    if not model.is_file():
        return None
    if model.suffix.lower() != ".onnx" or model.stat().st_size == 0:
        raise ValueError("去背景权重必须是非空 ONNX 文件")
    return model.resolve()


def readiness(context=None):
    """Check explicit project configuration; never download or discover weights."""
    model = _model_path(_settings(context))
    if model is None:
        return {"status":"setup","reason":"需在本项目 storage_root 内配置 native_background_model_path；不会下载或复用外部模型缓存"}
    missing = [name for name in ("numpy","onnxruntime","PIL") if importlib.util.find_spec(name) is None]
    if missing:
        return {"status":"setup","reason":"去背景需安装本项目依赖："+"、".join(missing)}
    return {"status":"ready","reason":"本项目已配置 ONNX 去背景权重与本地 CPU 推理依赖；执行时验证真实模型输出"}


def _image(asset):
    if not isinstance(asset,Mapping) or not asset.get("path"):
        raise ValueError("图片素材需有已授权的本地 path")
    path = Path(asset["path"])
    if path.is_symlink() or not path.is_file():
        raise ValueError("图片素材不是普通项目文件")
    try:
        with Image.open(path) as image:
            if image.width*image.height>MAX_PIXELS:
                raise ValueError("图片超过3000万像素处理上限")
            return ImageOps.exif_transpose(image).convert("RGBA")
    except (OSError,Image.DecompressionBombError):
        raise ValueError("素材不是可解码图片") from None


def _number(value,name,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f"{name} 数值范围无效")
    return value


def _parameters(parameters):
    if not isinstance(parameters,Mapping):
        raise ValueError("parameters 需为对象")
    parameters = {str(key).replace("-","_"):value for key,value in parameters.items()}
    if set(parameters)-_PARAMETERS:
        raise ValueError("不支持的去背景参数："+"、".join(sorted(set(parameters)-_PARAMETERS)))
    model = parameters.get("model","u2net")
    if model not in MODELS:
        raise ValueError("model 需为 "+"、".join(sorted(MODELS)))
    fmt = str(parameters.get("format","png")).lower().lstrip(".")
    if fmt not in {"png","jpg","jpeg","webp"}:
        raise ValueError("抠图输出格式仅支持 png/jpg/jpeg/webp")
    quality = _number(parameters.get("quality",90),"quality",1,100)
    if int(quality)!=quality:
        raise ValueError("quality 需为整数")
    index = parameters.get("background_asset_index")
    if index is not None and (type(index) is not int or index<0):
        raise ValueError("background_asset_index 需为非负整数")
    color = parameters.get("bg_color") or None
    if color and index is not None:
        raise ValueError("bg_color 与 background_asset_index 只能指定一个")
    if color:
        color = str(color)
        if color.startswith("0x"):
            color = "#"+color[2:]
        try:
            color = ImageColor.getcolor(color,"RGBA")
        except ValueError:
            raise ValueError("背景颜色格式无效") from None
    matting = parameters.get("alpha_matting",False)
    if type(matting) is not bool:
        raise ValueError("alpha_matting 需为布尔值")
    return {"model":model,"format":fmt,"quality":int(quality),"index":index,"color":color,"alpha_matting":matting}


def _alpha_matting(image, mask, estimators):
    """Build a genuine trimap, solve alpha/foreground, and retain source alpha."""
    import numpy as np
    if min(image.size)<3 or image.width*image.height>4_000_000:
        raise ValueError("alpha_matting 图片需至少3x3且不超过400万像素")
    mask_pixels = np.asarray(mask,dtype=np.uint8)
    if mask_pixels.min()>240:
        return image.copy()
    if mask_pixels.max()<10:
        empty = image.copy()
        empty.putalpha(Image.new("L",image.size,0))
        return empty
    # The original erosion square is 10 pixels; an odd Pillow kernel uses 11.
    # Foreground's outer border is unknown, background's outer border is known.
    radius,kernel = 5,11
    foreground = Image.fromarray(np.where(mask_pixels>240,255,0).astype(np.uint8))
    background = Image.fromarray(np.where(mask_pixels<10,255,0).astype(np.uint8))
    def erode(binary,border):
        padded = ImageOps.expand(binary,border=radius,fill=border)
        eroded = padded.filter(ImageFilter.MinFilter(kernel))
        return np.asarray(eroded.crop((radius,radius,radius+image.width,radius+image.height)))>0
    known_foreground,known_background = erode(foreground,0),erode(background,255)
    if not known_foreground.any() or not known_background.any():
        raise ValueError("alpha_matting 的真实掩码未提供足够的确定前景/背景；请使用基础去背景")
    trimap = np.full(mask_pixels.shape,128/255.0,dtype=np.float64)
    trimap[known_foreground],trimap[known_background] = 1.0,0.0
    pixels = np.asarray(image.convert("RGB"),dtype=np.float64)/255.0
    estimate_alpha,estimate_foreground = estimators
    try:
        alpha = np.asarray(estimate_alpha(pixels,trimap),dtype=np.float64)
        if alpha.shape!=trimap.shape or not np.isfinite(alpha).all():
            raise ValueError("alpha_matting 未返回有效 alpha")
        alpha = np.clip(alpha,0,1)
        colors = np.asarray(estimate_foreground(pixels,alpha),dtype=np.float64)
        if colors.shape!=pixels.shape or not np.isfinite(colors).all():
            raise ValueError("alpha_matting 未返回有效 foreground")
    except Exception as exc:
        raise ValueError("alpha_matting 真实求解失败或输出无效") from exc
    source_alpha = np.asarray(image.getchannel("A"),dtype=np.float64)/255.0
    rgba = np.dstack((np.clip(colors,0,1),alpha*source_alpha))
    return Image.fromarray(np.rint(np.clip(rgba,0,1)*255).astype(np.uint8))


def _inference(image, model_path, model_name, matting_estimators=None):
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise ValueError("已配置模型，但本项目推理依赖无法导入") from exc
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model_path),sess_options=options,providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise ValueError("配置的 ONNX 去背景模型无法加载") from exc
    inputs = session.get_inputs()
    if len(inputs)!=1 or len(inputs[0].shape)!=4:
        raise ValueError("去背景模型需有单个 NCHW RGB 输入")
    specification = inputs[0]
    shape = specification.shape
    if isinstance(shape[0],int) and shape[0]!=1 or isinstance(shape[1],int) and shape[1]!=3:
        raise ValueError("去背景模型输入必须为 batch=1、RGB=3 的 NCHW 张量")
    isnet = model_name in {"isnet","isnet-general-use"}
    default_size = 1024 if isnet else 320
    height = shape[2] if isinstance(shape[2],int) and shape[2]>0 else default_size
    width = shape[3] if isinstance(shape[3],int) and shape[3]>0 else default_size
    if width*height>16_000_000 or min(width,height)<1:
        raise ValueError("去背景模型输入尺寸超过处理上限")
    pixels = np.asarray(image.convert("RGB").resize((width,height),Image.Resampling.LANCZOS),dtype=np.float32)
    pixels = pixels / max(float(pixels.max()),1e-6)
    mean = np.array((0.5,0.5,0.5) if isnet else (0.485,0.456,0.406),dtype=np.float32)
    std = np.array((1.0,1.0,1.0) if isnet else (0.229,0.224,0.225),dtype=np.float32)
    tensor = np.expand_dims(((pixels-mean)/std).transpose(2,0,1),0)
    if specification.type=="tensor(float16)":
        tensor = tensor.astype(np.float16)
    elif specification.type=="tensor(float)":
        tensor = tensor.astype(np.float32)
    else:
        raise ValueError("去背景模型输入需为 float32 或 float16")
    try:
        outputs = session.run(None,{specification.name:np.ascontiguousarray(tensor)})
    except Exception as exc:
        raise ValueError("ONNX 去背景模型实际前向推理失败") from exc
    if not outputs:
        raise ValueError("ONNX 去背景模型未输出掩码")
    mask = np.asarray(outputs[0],dtype=np.float32)
    if mask.ndim==4 and mask.shape[:2]==(1,1):
        mask = mask[0,0]
    elif mask.ndim==3 and mask.shape[0]==1:
        mask = mask[0]
    if mask.ndim!=2 or mask.size==0 or not np.isfinite(mask).all():
        raise ValueError("ONNX 输出需为有限数值的单通道二维掩码")
    minimum,maximum = float(mask.min()),float(mask.max())
    if maximum-minimum>1e-8:
        mask = (mask-minimum)/(maximum-minimum)
    else:
        # A valid constant probability mask means all foreground/background.
        mask = np.clip(mask,0,1)
    mask = Image.fromarray(np.rint(np.clip(mask,0,1)*255).astype(np.uint8)).resize(image.size,Image.Resampling.LANCZOS)
    if matting_estimators is not None:
        return _alpha_matting(image,mask,matting_estimators)
    source_alpha = np.asarray(image.getchannel("A"),dtype=np.uint16)
    inferred_alpha = np.asarray(mask,dtype=np.uint16)
    alpha = Image.fromarray(((source_alpha*inferred_alpha+127)//255).astype(np.uint8))
    image = image.copy()
    image.putalpha(alpha)
    return image


def _save(image, root, fmt, quality):
    root = Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    path = root/("cutout."+fmt)
    if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_nlink>1):
        raise ValueError("输出不可为符号链接或硬链接")
    if path.resolve().parent!=root:
        raise ValueError("抠图输出不可逃逸 output_dir")
    if fmt in {"jpg","jpeg"}:
        background = Image.new("RGBA",image.size,"white")
        background.alpha_composite(image)
        image = background.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer,format={"png":"PNG","jpg":"JPEG","jpeg":"JPEG","webp":"WEBP"}[fmt],**({"optimize":True} if fmt=="png" else {"quality":quality}))
    # Exclusive temporary creation plus replace never follows an output link.
    descriptor,temporary = tempfile.mkstemp(prefix=".native-cutout-",dir=root)
    try:
        with os.fdopen(descriptor,"wb") as handle:
            handle.write(buffer.getvalue())
            handle.flush()
        if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_nlink>1):
            raise ValueError("输出不可为符号链接或硬链接")
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def dispatch(skill_name, *, input_text, assets, parameters, output_dir, context):
    if skill_name!="remove-bg":
        raise ValueError("未注册的去背景技能")
    params = _parameters(parameters)
    configuration = _settings(context)
    available = readiness({"settings":configuration})
    if available["status"]=="setup":
        return {"status":"setup","response":available["reason"],"artifact_paths":[]}
    matting_estimators = None
    if params["alpha_matting"]:
        try:
            from pymatting import estimate_alpha_cf, estimate_foreground_ml
        except ImportError:
            return {"status":"setup","response":"alpha_matting 需安装本项目可选依赖 pymatting（包含 scipy/numba）；基础 ONNX 去背景可直接执行","artifact_paths":[]}
        matting_estimators = (estimate_alpha_cf,estimate_foreground_ml)
    if not isinstance(assets,(list,tuple)) or not assets:
        raise ValueError("去背景需至少一张已授权图片")
    index = params["index"]
    if index is not None and (index>=len(assets) or index==0):
        raise ValueError("background_asset_index 需指向已选的另一张背景图片")
    source = _image(assets[0])
    background = _image(assets[index]) if index is not None else None
    cutout = _inference(source,_model_path(configuration),params["model"],matting_estimators)
    if params["color"]:
        background = Image.new("RGBA",cutout.size,params["color"])
    elif background is not None:
        background = ImageOps.fit(background,cutout.size,method=Image.Resampling.LANCZOS)
    if background is not None:
        background.alpha_composite(cutout)
        cutout = background
    output = _save(cutout,output_dir,params["format"],params["quality"])
    return {"status":"completed","response":f"本项目 ONNX CPU 去背景已完成（{params['model']}）：{output.name}","artifact_paths":[output]}
