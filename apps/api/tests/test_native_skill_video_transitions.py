"""Actual frame/PCM regression fixtures for one caption layer per video frame."""
from __future__ import annotations

import importlib.util
from io import BytesIO
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import wave

from PIL import Image, ImageChops
import pytest


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='actual FFmpeg codecs required')
CAPTIONS = ['杯身细节 · 自制示意', '清晰排版 · 非实际商品承诺', '核对来源 · 再交付']
SIZE = (480, 640)


def av_module():
    path = Path(__file__).resolve().parents[1] / 'app/content_skills/tools/av.py'
    spec = importlib.util.spec_from_file_location('native_video_transition_target', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(*args):
    proc = subprocess.run(args, capture_output=True, timeout=90)
    assert proc.returncode == 0, proc.stderr.decode(errors='replace')
    return proc.stdout


def probe(path):
    return json.loads(command('ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)))


def make_fixture_media(root):
    root.mkdir(parents=True, exist_ok=True)
    images = []
    for index in range(3):
        path = root / f'black_{index}.png'
        Image.new('RGB', SIZE, 'black').save(path)
        images.append(path)
    tone = root / 'tone.wav'
    with wave.open(str(tone), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(44100)
        stream.writeframes(b''.join(struct.pack('<h', round(2400 * math.sin(2 * math.pi * 440 * i / 44100))) for i in range(44100 * 6)))
    return [{'path': path, 'kind': 'image'} for path in images] + [{'path': tone, 'kind': 'audio'}]


@pytest.fixture(scope='module')
def assets(tmp_path_factory):
    return make_fixture_media(tmp_path_factory.mktemp('caption-transition-source'))


def dispatch(root, skill, assets, parameters):
    root.mkdir(parents=True, exist_ok=True)
    result = av_module().dispatch(skill, input_text='', assets=assets, parameters={
        'width': SIZE[0], 'height': SIZE[1], 'kenburns': False, 'crf': 0, 'preset': 'ultrafast', **parameters},
        output_dir=root, context={})
    assert result.get('status') == 'completed', result
    assert result['artifact_paths']
    return result


def video(result):
    return next(Path(path) for path in result['artifact_paths'] if Path(path).suffix == '.mp4')


def frame(path, at):
    raw = command('ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path), '-frames:v', '1',
        '-f', 'image2pipe', '-vcodec', 'png', '-')
    return Image.open(BytesIO(raw)).convert('RGB')


def references(root, assets):
    """An unchanged direct segment path gives one literal caption reference."""
    root.mkdir(parents=True, exist_ok=True)
    av = av_module()
    results = []
    for index, caption in enumerate(CAPTIONS):
        directory = root / str(index); directory.mkdir()
        path = directory / 'single.mp4'
        av._segment(assets[0]['path'], .5, path, *SIZE,
            {'kenburns': False, 'crf': 0, 'preset': 'ultrafast'}, assets, directory, caption=caption)
        results.append(frame(path, .2))
    return results


def glyph_error(actual, expected, all_references):
    """Measure actual glyph pixels, excluding the vast unchanged background."""
    mask = Image.new('L', SIZE)
    for reference in all_references:
        mask = ImageChops.lighter(mask, reference.convert('L'))
    flags = mask.point(lambda value: 255 if value > 12 else 0).tobytes()
    diffs = ImageChops.difference(actual.convert('L'), expected.convert('L')).tobytes()
    selected = [difference for difference, flag in zip(diffs, flags) if flag]
    assert len(selected) > 100, 'the real caption fixture must contain visible glyphs'
    return sum(selected) / len(selected)


def test_chinese_crossfade_frames_match_one_full_caption_not_a_blended_pair(tmp_path, assets):
    result = dispatch(tmp_path / 'fade', 'auto-short-video', assets, {'transition': 'fade', 'trans_duration': .4,
        'storyboard': [{'asset_index': 0, 'duration': 2, 'caption': CAPTIONS[0]},
            {'asset_index': 1, 'duration': 2, 'caption': CAPTIONS[1]}]})
    refs = references(tmp_path / 'refs', assets)
    for at, index in [(.5, 0), (1.7, 0), (1.766667, 0), (1.8, 1), (1.9, 1), (2.3, 1)]:
        error = glyph_error(frame(video(result), at), refs[index], refs)
        assert error < 3, f'caption glyph residual {error:.2f} at {at}s proves overlap/fading instead of a single full caption'
    info = probe(video(result))
    assert float(info['format']['duration']) == pytest.approx(3.6, abs=.035)
    command('ffmpeg', '-v', 'error', '-i', str(video(result)), '-map', '0', '-f', 'null', '-')


def test_three_scene_midpoint_cues_use_actual_nonoverlapping_output_times(tmp_path, assets):
    result = dispatch(tmp_path / 'three', 'auto-short-video', assets, {'transition': 'fade', 'trans_duration': .2,
        'storyboard': [{'asset_index': i, 'duration': duration, 'caption': CAPTIONS[i]} for i, duration in enumerate([1.2, .8, 1.1])]})
    receipt = next(Path(path) for path in result['artifact_paths'] if Path(path).name == 'caption-cues.json')
    data = json.loads(receipt.read_text())
    assert data['duration'] == pytest.approx(2.7, abs=.035)
    assert [(cue['start'], cue['end']) for cue in data['cues']] == pytest.approx([(0, 1.1), (1.1, 1.7), (1.7, 2.7)], abs=.0001)
    assert [cue['text'] for cue in data['cues']] == CAPTIONS
    assert data['handoff'] == 'transition_midpoint' and data['interval'] == '[start,end)'
    refs = references(tmp_path / 'refs', assets)
    for at, index in [(1.066667, 0), (1.133334, 1), (1.666667, 1), (1.733334, 2)]:
        assert glyph_error(frame(video(result), at), refs[index], refs) < 3


def test_no_transition_empty_scene_has_no_caption_or_previous_text_leak(tmp_path, assets):
    result = dispatch(tmp_path / 'none', 'auto-short-video', assets, {'transition': 'none',
        'storyboard': [{'asset_index': 0, 'duration': 1, 'caption': CAPTIONS[0]},
            {'asset_index': 1, 'duration': .5}, {'asset_index': 2, 'duration': 1, 'caption': CAPTIONS[2]}]})
    refs = references(tmp_path / 'refs', assets)
    assert glyph_error(frame(video(result), .8), refs[0], refs) < 3
    assert frame(video(result), 1.2).convert('L').getextrema() == (0, 0)
    assert glyph_error(frame(video(result), 1.6), refs[2], refs) < 3
    data = json.loads((tmp_path / 'none' / 'caption-cues.json').read_text())
    assert [(cue['start'], cue['end']) for cue in data['cues']] == [(0, 1), (1.5, 2.5)]


def test_legacy_slideshow_caption_list_uses_same_single_layer_pipeline(tmp_path, assets):
    result = dispatch(tmp_path / 'slideshow', 'slideshow-video', assets[:2], {'per': 1.2, 'transition': 'fade',
        'trans_duration': .4, 'captions': '|'.join(CAPTIONS[:2])})
    refs = references(tmp_path / 'refs', assets)
    assert glyph_error(frame(video(result), .9), refs[0], refs) < 3
    assert glyph_error(frame(video(result), 1.1), refs[1], refs) < 3
    assert float(probe(video(result))['format']['duration']) == pytest.approx(2, abs=.035)


def test_single_scene_direct_segment_keeps_literal_chinese_caption(tmp_path, assets):
    refs = references(tmp_path / 'refs', assets)
    result = dispatch(tmp_path / 'single', 'auto-short-video', assets[:1], {'transition': 'fade',
        'trans_duration': .4, 'storyboard': [{'asset_index': 0, 'duration': 1, 'caption': CAPTIONS[0]}]})
    assert glyph_error(frame(video(result), .5), refs[0], refs) < 3
    assert float(probe(video(result))['format']['duration']) == pytest.approx(1, abs=.035)


def test_caption_composition_keeps_actual_audio_pcm_and_total_duration(tmp_path, assets):
    common = {'transition': 'fade', 'trans_duration': .4, 'bgm_index': 3, 'bgm_volume': .6,
        'storyboard': [{'asset_index': 0, 'duration': 2}, {'asset_index': 1, 'duration': 2}]}
    plain = dispatch(tmp_path / 'plain', 'auto-short-video', assets, common)
    captioned = dispatch(tmp_path / 'captioned', 'auto-short-video', assets, {**common,
        'storyboard': [{**scene, 'caption': CAPTIONS[i]} for i, scene in enumerate(common['storyboard'])]})
    def pcm(path):
        return command('ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-f', 's16le', '-acodec', 'pcm_s16le', '-')
    assert pcm(video(captioned)) == pcm(video(plain))
    assert float(probe(video(captioned))['format']['duration']) == float(probe(video(plain))['format']['duration'])
    assert any(stream['codec_type'] == 'audio' for stream in probe(video(captioned))['streams'])
