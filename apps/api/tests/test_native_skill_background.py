"""Native segmentation is verified with a real tiny ONNX graph, not fake masks."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "app" / "content_skills" / "tools" / "background.py"
    assert path.is_file(), "Native configured ONNX background inference is not implemented"
    spec = importlib.util.spec_from_file_location("native_background_test_target",path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def varint(value):
    parts = []
    while value > 127:
        parts.append((value & 127)|128)
        value >>= 7
    return bytes([*parts,value])


def integer(field,value):
    return varint(field<<3)+varint(value)


def message(field,value):
    value = value.encode() if isinstance(value,str) else value
    return varint((field<<3)|2)+varint(len(value))+value


def tensor_info(name,dimensions):
    dims = b"".join(message(1,integer(1,dim) if isinstance(dim,int) else message(2,dim)) for dim in dimensions)
    tensor_type = integer(1,1)+message(2,dims)
    return message(1,name)+message(2,message(1,tensor_type))


def tiny_model(*, constant=None, dynamic=False, channel_count=1):
    """Serialize ONNX's public protobuf wire schema without an onnx test dependency."""
    shape = (1,3,"H","W") if dynamic else (1,3,4,4)
    out_shape = (1,channel_count,"H","W") if dynamic else (1,channel_count,4,4)
    if constant is not None:
        tensor = b"".join(integer(1,dim) for dim in (1,1,4,4))+integer(2,1)
        tensor += b"".join(varint((4<<3)|5)+struct.pack("<f",constant) for _ in range(16))
        attr = message(1,"value")+message(5,tensor)+integer(20,4)
        node = message(2,"mask")+message(4,"Constant")+message(5,attr)
    elif channel_count != 1:
        node = message(1,"input")+message(2,"mask")+message(4,"Identity")
    else:
        axes = message(1,"axes")+integer(8,1)+integer(20,7)
        keep = message(1,"keepdims")+integer(3,1)+integer(20,2)
        node = message(1,"input")+message(2,"mask")+message(4,"ReduceMean")+message(5,axes)+message(5,keep)
    graph = message(1,node)+message(2,"real-segmentation-fixture")+message(11,tensor_info("input",shape))+message(12,tensor_info("mask",out_shape))
    return integer(1,7)+message(2,"Louxiang controlled ONNX fixture")+message(7,graph)+message(8,integer(2,13))


@pytest.fixture
def fixture(tmp_path):
    storage = tmp_path / "project-storage"
    storage.mkdir()
    model = storage / "fixture.onnx"
    model.write_bytes(tiny_model())
    image = storage / "subject.png"
    source = Image.new("RGB",(16,8),"black")
    ImageDraw.Draw(source).rectangle((0,0,7,7),fill="white")
    source.save(image)
    bg = storage / "background.png"
    Image.new("RGB",(8,8),"blue").save(bg)
    settings = SimpleNamespace(storage_root=storage,native_background_model_path=model)
    return {"storage":storage,"model":model,"image":image,"background":bg,"settings":settings}


def run(fixture, tmp_path, parameters=None):
    output = tmp_path / "output"
    result = module().dispatch("remove-bg",input_text="",assets=[{"path":fixture["image"]},{"path":fixture["background"]}],parameters=parameters or {},output_dir=output,context={"settings":fixture["settings"]})
    assert result.get("status") != "setup",result
    assert result["artifact_paths"] and result["response"]
    path = result["artifact_paths"][0]
    assert isinstance(path,Path) and path.is_file()
    assert path.resolve().is_relative_to(output.resolve())
    return Image.open(path),result


def test_unconfigured_model_returns_setup_without_creating_artifacts(tmp_path):
    settings = SimpleNamespace(storage_root=tmp_path,native_background_model_path=None)
    target = module()
    assert target.readiness({"settings":settings})["status"] == "setup"
    result = target.dispatch("remove-bg",input_text="",assets=[],parameters={},output_dir=tmp_path / "output",context={"settings":settings})
    assert result["status"] == "setup" and result["artifact_paths"] == []
    assert not list((tmp_path/"output").glob("*.png"))


@pytest.mark.parametrize("name",["u2net","u2netp","u2net_human_seg","isnet","isnet-general-use","silueta"])
def test_real_onnx_inference_produces_spatial_alpha_at_source_size(tmp_path,fixture,name):
    image,result = run(fixture,tmp_path,{"model":name})
    assert image.mode == "RGBA" and image.size == (16,8)
    # Sample inside the two stable plateaus; Lanczos has border ringing.
    assert image.getpixel((3,4))[3] == 255
    assert image.getpixel((12,4))[3] == 0
    assert image.getpixel((0,4))[:3] == (255,255,255)


def test_configured_model_reports_ready_and_missing_model_reports_setup(tmp_path,fixture):
    target = module()
    assert target.readiness({"settings":fixture["settings"]})["status"] == "ready"
    fixture["model"].unlink()
    assert target.readiness({"settings":fixture["settings"]})["status"] == "setup"


def test_real_constant_mask_does_not_divide_by_zero(tmp_path,fixture):
    fixture["model"].write_bytes(tiny_model(constant=1.0))
    image,result = run(fixture,tmp_path)
    assert image.getchannel("A").getextrema() == (255,255)


def test_dynamic_input_shape_uses_family_resolution_and_resizes_real_mask(tmp_path,fixture):
    fixture["model"].write_bytes(tiny_model(dynamic=True))
    image,result = run(fixture,tmp_path)
    assert image.size == (16,8)
    assert image.getpixel((0,4))[3] > 250
    assert image.getpixel((15,4))[3] < 5


@pytest.mark.parametrize("parameters,want_pixel",[({"bg_color":"red"},(255,0,0)),({"background_asset_index":1},(0,0,255)),({"format":"jpg"},(255,255,255))])
def test_background_replacement_changes_only_predicted_background(tmp_path,fixture,parameters,want_pixel):
    image,result = run(fixture,tmp_path,parameters)
    pixel = image.convert("RGB").getpixel((12,4))
    assert all(abs(a-b)<8 for a,b in zip(pixel,want_pixel))
    foreground = image.convert("RGB").getpixel((0,4))
    assert all(value>245 for value in foreground)


@pytest.mark.parametrize("parameters",[{"model_path":"/tmp/other.onnx"},{"model":"not-a-model"},{"bg_color":"red","background_asset_index":1},{"background_asset_index":3},{"quality":999},{"format":"../../escape"}])
def test_invalid_parameters_raise_without_output(tmp_path,fixture,parameters):
    with pytest.raises(ValueError):
        module().dispatch("remove-bg",input_text="",assets=[{"path":fixture["image"]},{"path":fixture["background"]}],parameters=parameters,output_dir=tmp_path/"output",context={"settings":fixture["settings"]})
    assert not list((tmp_path/"output").glob("*.png"))


def test_model_must_stay_in_project_storage(tmp_path,fixture):
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(tiny_model())
    fixture["settings"].native_background_model_path = outside
    with pytest.raises(ValueError):
        run(fixture,tmp_path)


@pytest.mark.parametrize("link",["symbolic","hard"])
def test_outputs_cannot_modify_existing_link_targets(tmp_path,fixture,link):
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"preserve-original")
    path = output/"cutout.png"
    if link=="symbolic":
        path.symlink_to(outside)
    else:
        import os
        os.link(outside,path)
    with pytest.raises(ValueError):
        run(fixture,tmp_path)
    assert outside.read_bytes() == b"preserve-original"


def test_invalid_onnx_graph_is_an_error_and_does_not_return_setup(tmp_path,fixture):
    fixture["model"].write_bytes(b"not-an-onnx-model")
    with pytest.raises(ValueError):
        run(fixture,tmp_path)


def test_non_finite_model_mask_is_rejected(tmp_path,fixture):
    fixture["model"].write_bytes(tiny_model(constant=float("nan")))
    with pytest.raises(ValueError):
        run(fixture,tmp_path)


def test_source_transparency_is_preserved(tmp_path,fixture):
    fixture["model"].write_bytes(tiny_model(constant=1.0))
    source = Image.open(fixture["image"]).convert("RGBA")
    source.putpixel((0,0),(255,255,255,0))
    source.save(fixture["image"])
    image,result = run(fixture,tmp_path)
    assert image.getpixel((0,0))[3] == 0
    assert image.getpixel((1,1))[3] == 255


def matting_image(fixture):
    source = Image.new("RGBA",(64,48),"black")
    ImageDraw.Draw(source).rectangle((0,0,31,47),fill="white")
    source.save(fixture["image"])


@pytest.mark.skipif(importlib.util.find_spec("pymatting") is None,reason="Optional PyMatting dependency is not installed")
def test_alpha_matting_executes_real_closed_form_and_foreground_solver(tmp_path,fixture):
    matting_image(fixture)
    image,result = run(fixture,tmp_path,{"alpha_matting":True})
    assert image.mode == "RGBA" and image.size == (64,48)
    assert image.getpixel((15,24))[3] > 250
    assert image.getpixel((50,24))[3] < 5
    # The real RGB edge refines the resized low-resolution ONNX mask.
    assert image.getpixel((30,24))[3] > 230
    assert image.getpixel((33,24))[3] < 25


def test_missing_optional_matting_dependency_returns_specific_setup(tmp_path,fixture,monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,"pymatting",None)
    target = module()
    result = target.dispatch("remove-bg",input_text="",assets=[{"path":fixture["image"]}],parameters={"alpha_matting":True},output_dir=tmp_path/"output",context={"settings":fixture["settings"]})
    assert result["status"]=="setup" and "pymatting" in result["response"]
    assert result["artifact_paths"]==[]


@pytest.mark.skipif(importlib.util.find_spec("pymatting") is None,reason="Optional PyMatting dependency is not installed")
def test_matting_preserves_original_transparent_pixels(tmp_path,fixture):
    matting_image(fixture)
    source = Image.open(fixture["image"])
    source.putpixel((15,24),(255,255,255,0))
    source.save(fixture["image"])
    image,result = run(fixture,tmp_path,{"alpha_matting":True})
    assert image.getpixel((15,24))[3]==0
    assert image.getpixel((16,24))[3]>250


def test_matting_rejects_non_finite_solver_output_without_saving(tmp_path,fixture,monkeypatch):
    import sys
    import numpy as np
    matting_image(fixture)
    backend = SimpleNamespace(estimate_alpha_cf=lambda image,trimap:np.full(trimap.shape,np.nan),estimate_foreground_ml=lambda image,alpha:image)
    monkeypatch.setitem(sys.modules,"pymatting",backend)
    with pytest.raises(ValueError,match="matting"):
        run(fixture,tmp_path,{"alpha_matting":True})
    assert not list((tmp_path/"output").glob("*.png"))
