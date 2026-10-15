"""Default-suite editorial flows exercise real PDF/image parsing and video codecs."""
import base64
from io import BytesIO
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET

import httpx
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.content_skills.tools import editorial
except ImportError:
    editorial = None


def note(cards=3, video=False):
    value = {'title': 'Read the source', 'body': 'A source helps us explain the observation.',
        'caption': 'Keep a clear source and explain what was observed. ' * 3,
        'hashtags': ['Source', 'Review', 'Writing', 'Learning', 'Tools']}
    if video:
        value['storyboard'] = [{'narration': 'Read the source.', 'on_screen_text': 'Source',
            'visual': 'A real document', 'material_ref': None, 'duration': 3} for _ in range(6)]
        value['cards'] = [{'title': 'Read the source', 'body': ['Trace the original evidence.']}]
    else:
        value['cards'] = [{'title': f'Point {i + 1}', 'body': ['Keep the source.', 'Explain the observation.']} for i in range(cards)]
    return value


def response(data):
    return {'choices': [{'message': {'content': json.dumps(data)}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 20}, 'model': 'fixture-chat'}


def run(tmp_path, name, text='', assets=(), parameters=None, context=None):
    assert editorial is not None, 'native editorial dispatch has not been implemented'
    return editorial.dispatch(name, input_text=text, assets=list(assets), parameters=parameters or {},
        output_dir=tmp_path / 'out', context=context or {})


def small_params(**extra):
    return {'width': 320, 'height': 480, 'scale': 1, **extra}


def test_reviewed_xhs_note_renders_each_real_card_and_metadata(tmp_path):
    result = run(tmp_path, 'xhs-note-creator', parameters=small_params(note=note()))
    pngs = [path for path in result['artifact_paths'] if path.suffix == '.png']
    assert len(pngs) == 3
    for path in pngs:
        with Image.open(path) as image:
            assert image.size == (320, 480)
    metadata = json.loads((tmp_path / 'out' / 'note.json').read_text())
    assert metadata['title'] == 'Read the source' and len(metadata['cards']) == 3
    assert 'publication_state' in metadata and metadata['publication_state'] == 'not_published'
    assert 'Explain the observation' in (tmp_path / 'out' / 'note.md').read_text()


def test_xhs_density_failures_remain_reviewable_drafts(tmp_path):
    result = run(tmp_path, 'xhs-note-creator', parameters=small_params(note=note()))
    audit = json.loads((tmp_path / 'out' / 'card-audit.json').read_text())
    assert len(audit) == 3
    assert any(not row['pass'] for row in audit)
    assert result['status'] == 'draft'
    assert json.loads((tmp_path / 'out' / 'note.json').read_text())['visual_review'] == 'requires_revision'


def test_complete_chinese_xhs_cards_include_navigation_and_pass_visual_density(tmp_path):
    value = note()
    value['cards'] = [{'title': '从来源开始', 'body': [
        '保存原始材料与出处。', '明确自制数据的用途。',
        '核对每个数字和结论。', '交付前保留可编辑文件。']} for _ in range(3)]
    result = run(tmp_path, 'xhs-note-creator', parameters={
        'note': value, 'width': 1080, 'height': 1440, 'scale': 1, 'style': 'editorial'})
    audit = json.loads((tmp_path / 'out' / 'card-audit.json').read_text())
    assert all(row['pass'] for row in audit), audit
    assert result['status'] == 'completed'
    svg = (tmp_path / 'out' / 'card_1.svg').read_text()
    assert '01 / 03' in svg


def test_xhs_topic_uses_project_chat_and_model_budget_receipts(tmp_path):
    observed, receipts, before = [], [], []
    def chat(messages, **kwargs):
        observed.append(messages)
        return response(note(5))
    result = run(tmp_path, 'xhs-note-creator', 'Teach clear source citations', parameters=small_params(),
        context={'chat': chat, 'before_model_call': lambda: before.append(True), 'on_usage': receipts.append,
            'verified_facts': [{'label': 'Project', 'value': 'Observed project'}]})
    assert len([path for path in result['artifact_paths'] if path.suffix == '.png']) == 5
    assert 'Observed project' in observed[0][-1]['content']
    # A supplied project chat owns its call receipt; avoid recording it twice.
    assert before == [True] and result['usage']['prompt_tokens'] == 10
    assert receipts == []


@pytest.mark.parametrize('bad_note', [
    {**note(), 'cards': [{'title': 'Too long', 'body': ['x' * 90]}] * 3},
    {**note(), 'hashtags': ['one']},
    note(2),
])
def test_invalid_xhs_note_never_creates_card_artifacts(tmp_path, bad_note):
    with pytest.raises(ValueError):
        run(tmp_path, 'xhs-note-creator', parameters=small_params(note=bad_note))
    assert not list(tmp_path.rglob('*.png'))


def test_xhs_video_without_actual_video_delivers_only_storyboard_and_cover(tmp_path):
    result = run(tmp_path, 'xhs-note-creator', parameters=small_params(note=note(video=True), format='video'))
    assert (tmp_path / 'out' / 'storyboard.json').is_file()
    assert len([path for path in result['artifact_paths'] if path.suffix == '.png']) == 1
    assert not list(tmp_path.rglob('*.mp4'))
    assert result['status'] == 'draft' and '分镜' in result['response']


def pdf_fixture(tmp_path):
    pytest.importorskip('reportlab')
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    path = tmp_path / 'paper.pdf'
    picture = Image.new('RGB', (120, 80), '#002FA7')
    doc = canvas.Canvas(str(path))
    doc.drawString(60, 790, 'Study of factual content: a controlled fixture.')
    doc.drawString(60, 760, 'Method: compare the source with each generated statement.')
    doc.drawImage(ImageReader(picture), 60, 620, 120, 80)
    doc.showPage()
    doc.drawString(60, 790, 'Result: the observed score was 7 in the evaluated sample.')
    doc.drawString(60, 760, 'Limitation: only the given sample was tested.')
    doc.save()
    return path


def paper_library():
    return {'title': 'Factual content study', 'one_liner': 'Check statements against their original source.',
        'problem': 'Generated statements need evidence.', 'prior_gap': 'A source alone does not prove every statement.',
        'contributions': ['Compare the source with generated statements.'],
        'method': {'summary': 'Compare statements against the source.', 'analogy': 'Like checking a receipt against a purchase.'},
        'results': ['The observed sample score was 7.'], 'limitations': ['Only the given sample was tested.'],
        'takeaway': 'Keep evidence with the observation.', 'terms': [{'term': 'evidence', 'plain': 'The original source supporting a statement.'}],
        'evidence': [{'id': 'e1', 'page': 1, 'quote': 'compare the source with each generated statement'},
            {'id': 'e2', 'page': 2, 'quote': 'the observed score was 7 in the evaluated sample'}],
        'slides': [{'title': 'The question', 'body': ['Statements need evidence.', 'Compare every statement with its source.'], 'narration': 'Statements need evidence.', 'evidence_ids': ['e1'], 'figure_id': 'figure_001'},
            {'title': 'The method', 'body': ['Keep the original source.', 'Compare the generated statement.'], 'narration': 'Compare a statement with its original source.', 'evidence_ids': ['e1']},
            {'title': 'The result', 'body': ['The observed score was 7.', 'The evidence is from the given sample.'], 'narration': 'The sample score was seven.', 'evidence_ids': ['e2']}]}


def test_paper_reads_real_pages_and_images_then_renders_article_slides(tmp_path):
    src = pdf_fixture(tmp_path)
    inputs = []
    def chat(messages, **kwargs):
        inputs.append(messages[-1]['content'])
        return response(paper_library())
    result = run(tmp_path, 'paper-explainer', assets=[{'path': src}],
        parameters={'format': 'article', 'width': 640, 'height': 400, 'scale': 1}, context={'chat': chat})
    assert 'observed score was 7' in inputs[0]
    parsed = json.loads((tmp_path / 'out' / 'paper_parsed.json').read_text())
    assert len(parsed['pages']) == 2 and parsed['figures'][0]['id'] == 'figure_001'
    assert (tmp_path / 'out' / 'paper_figure_001.png').is_file()
    assert len([path for path in result['artifact_paths'] if path.name.startswith('slide_') and path.suffix == '.png']) == 3
    assert (tmp_path / 'out' / 'slides-contact-sheet.jpg').is_file()
    assert (tmp_path / 'out' / 'asset-library.json').is_file()
    article = (tmp_path / 'out' / 'article.md').read_text()
    assert 'score was 7' in article and '第 2 页' in article
    svg = ET.fromstring((tmp_path / 'out' / 'slide_1.svg').read_text())
    assert 'compare the source with each generated statement' in ''.join(svg.itertext())
    assert not list(tmp_path.rglob('*.mp4'))


def test_paper_invalid_evidence_is_rejected_before_slides(tmp_path):
    src = pdf_fixture(tmp_path)
    library = paper_library()
    library['evidence'][0]['quote'] = 'An invented result is not in this document.'
    with pytest.raises(ValueError, match='证据|quote|原文'):
        run(tmp_path, 'paper-explainer', assets=[{'path': src}], parameters={'format': 'article'},
            context={'chat': lambda *args, **kwargs: response(library)})
    assert not list(tmp_path.rglob('slide_*.png'))


def test_paper_video_missing_tts_returns_setup_without_silent_film(tmp_path, monkeypatch):
    assert editorial is not None
    src = pdf_fixture(tmp_path)
    monkeypatch.setattr(editorial.models, 'configured', lambda kind: False)
    result = run(tmp_path, 'paper-explainer', assets=[{'path': src}],
        parameters={'format': 'video', 'width': 320, 'height': 200},
        context={'chat': lambda *args, **kwargs: response(paper_library())})
    assert result['status'] == 'setup' and '配音' in result['response']
    assert not list(tmp_path.rglob('*.mp4'))


def test_paper_video_requires_explicit_frame_before_model_call(tmp_path):
    src = pdf_fixture(tmp_path)
    with pytest.raises(ValueError, match='画幅|width|height'):
        run(tmp_path, 'paper-explainer', assets=[{'path': src}], parameters={'format': 'video'},
            context={'chat': lambda *args, **kwargs: response(paper_library())})


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='real codecs required')
def test_paper_video_uses_real_tts_audio_durations_and_static_slides(tmp_path, monkeypatch):
    assert editorial is not None
    src = pdf_fixture(tmp_path)
    monkeypatch.setattr(editorial.settings, 'dashscope_api_key', 'fixture-only')
    monkeypatch.setattr(editorial.settings, 'dashscope_tts_model', 'qwen3-tts-flash')
    monkeypatch.setattr(editorial.settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    durations = iter([.24, .36, .48])
    def transport(request):
        seconds = next(durations)
        buf = BytesIO()
        with wave.open(buf, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(b''.join(struct.pack('<h', round(math.sin(i / 24000 * 440 * math.tau) * 2000)) for i in range(round(24000 * seconds))))
        return httpx.Response(200, json={'output': {'audio': {'data': 'data:audio/wav;base64,' + base64.b64encode(buf.getvalue()).decode()}}, 'usage': {'characters': 12}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = run(tmp_path, 'paper-explainer', assets=[{'path': src}],
            parameters={'format': 'video', 'width': 640, 'height': 400, 'scale': 1},
            context={'chat': lambda *args, **kwargs: response(paper_library()), 'media_http_client': client,
                'ffmpeg_binary': shutil.which('ffmpeg'), 'ffprobe_binary': shutil.which('ffprobe')})
    video = next(path for path in result['artifact_paths'] if path.name == 'paper_video.mp4')
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)], capture_output=True, check=True).stdout)
    assert any(stream['codec_type'] == 'audio' for stream in info['streams'])
    assert float(info['format']['duration']) == pytest.approx(1.08, abs=.12)
    plan = json.loads((tmp_path / 'out' / 'slide-plan.json').read_text())
    assert [slide['duration'] for slide in plan['slides']] == pytest.approx([.24, .36, .48])
    assert plan['image_motion'] == 'static'
    subtitles = (tmp_path / 'out' / 'subtitles.srt').read_text()
    assert '00:00:00,240 --> 00:00:00,600' in subtitles
    assert result['status'] == 'completed'


def test_raw_paper_extraction_keeps_short_text_without_model_threshold(tmp_path):
    from reportlab.pdfgen import canvas
    src = tmp_path / 'short.pdf'
    pdf = canvas.Canvas(str(src)); pdf.drawString(50, 790, 'Short source text.'); pdf.save()
    root = editorial.documents._Output(tmp_path / 'extract')
    parsed = editorial._paper_ingest({'path': src}, root, allow_short=True)
    assert parsed['pages'][0]['text'].strip() == 'Short source text.'
    assert parsed['text_extraction_state'] == 'short'
    with pytest.raises(ValueError, match='文字不足|OCR'):
        editorial._paper_ingest({'path': src}, editorial.documents._Output(tmp_path / 'explain'))


def test_raw_paper_extraction_identifies_real_scan_requiring_ocr(tmp_path):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    src = tmp_path / 'scan.pdf'
    pdf = canvas.Canvas(str(src))
    pdf.drawImage(ImageReader(Image.new('RGB', (120, 80), '#002FA7')), 50, 650, 120, 80)
    pdf.save()
    parsed = editorial._paper_ingest({'path': src}, editorial.documents._Output(tmp_path / 'scan-extract'), allow_short=True)
    assert parsed['pages'][0]['text'] == '' and len(parsed['figures']) == 1
    assert parsed['text_extraction_state'] == 'needs_ocr'
