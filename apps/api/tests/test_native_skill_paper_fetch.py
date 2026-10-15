"""Official arXiv fetching uses real HTTP PDF fixtures and local pypdf parsing."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys

import httpx
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.content_skills.tools import paper_fetch
except ImportError:
    paper_fetch = None


def pdf_bytes():
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    target = BytesIO()
    doc = canvas.Canvas(target)
    doc.drawString(50, 790, 'A real paper fixture describes an observed local method.')
    doc.drawString(50, 760, 'The source text and embedded image are parsed from the PDF.')
    doc.drawImage(ImageReader(Image.new('RGB', (80, 50), '#002FA7')), 50, 650, 80, 50)
    doc.showPage()
    doc.drawString(50, 790, 'The measured result was seven for this controlled sample.')
    doc.save()
    return target.getvalue()


def fetch(tmp_path, client, text='2401.12345', parameters=None):
    assert paper_fetch is not None, 'native arXiv fetching has not been implemented'
    return paper_fetch.fetch(input_text=text, parameters=parameters or {}, output_dir=tmp_path / 'out',
        context={'paper_http_client': client, 'chat': lambda *a, **kw: pytest.fail('fetch must not use a model')})


@pytest.mark.parametrize('source,canonical', [
    ('2401.12345', '2401.12345'),
    ('2401.12345v2', '2401.12345v2'),
    ('https://arxiv.org/abs/2401.12345v2', '2401.12345v2'),
    ('https://arxiv.org/pdf/2401.12345.pdf', '2401.12345'),
    ('http://arxiv.org/abs/2401.12345', '2401.12345'),
    ('hep-th/9901001', 'hep-th/9901001'),
    ('https://arxiv.org/pdf/math.GT/0309136v1.pdf', 'math.GT/0309136v1'),
    ('0706.0001', '0706.0001'),
])
def test_fetch_normalizes_id_and_parses_real_pdf_page_and_image(tmp_path, source, canonical):
    contents, requests = pdf_bytes(), []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={'Content-Type': 'application/pdf'}, content=contents)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch(tmp_path, client, source)
    assert str(requests[0].url) == 'https://arxiv.org/pdf/' + canonical
    assert (tmp_path / 'out' / 'paper.pdf').read_bytes() == contents
    parsed = json.loads((tmp_path / 'out' / 'paper_parsed.json').read_text())
    assert parsed['page_count'] == 2 and 'measured result was seven' in parsed['pages'][1]['text']
    assert (tmp_path / 'out' / 'paper_figure_001.png').is_file()
    assert 'measured result was seven' in (tmp_path / 'out' / 'paper.txt').read_text()
    assert result['source_refs'][0]['url'] == 'https://arxiv.org/abs/' + canonical
    assert result['source_refs'][0]['sha256'] == hashlib.sha256(contents).hexdigest()
    assert result['source_refs'][0]['review_state'] == 'not_reviewed'
    assert all(path.parent == (tmp_path / 'out').resolve() for path in result['artifact_paths'])


def test_explicit_paper_parameter_accepts_task_description_in_input(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=pdf_bytes()))) as client:
        result = fetch(tmp_path, client, '请获取我选中的论文', {'op': 'fetch', 'paper': '2401.12345v3'})
    assert result['source_refs'][0]['arxiv_id'] == '2401.12345v3'


@pytest.mark.parametrize('source', [
    'https://example.com/paper.pdf', 'https://arxiv.org.evil.example/pdf/2401.12345',
    'https://user:password@arxiv.org/abs/2401.12345',
    'https://arxiv.org/pdf/2401.12345?token=secret',
    'https://arxiv.org/pdf/2401.12345#fragment',
    'https://arxiv.org:8443/pdf/2401.12345',
    'https://arxiv.org/html/2401.12345',
    'https://arxiv.org/pdf/%2e%2e/2401.12345',
    'file:///tmp/paper.pdf', '/Users/example/paper.pdf',
    '帮我下载2401.12345', '2413.12345', '2401.00000', '2401.12345v0',
    '2401.12345\nhttps://evil.example/',
])
def test_unapproved_source_is_rejected_before_network_or_output(tmp_path, source):
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, content=pdf_bytes()))) as client:
        with pytest.raises(ValueError):
            fetch(tmp_path, client, source)
    assert requests == [] and not list(tmp_path.rglob('paper.pdf'))


def test_fetch_does_not_forward_injected_client_credentials_or_cookies(tmp_path):
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, content=pdf_bytes())),
        headers={'Authorization': 'Bearer fixture-secret', 'Proxy-Authorization': 'fixture-proxy'},
        cookies={'account_session': 'fixture-session'}, auth=('fixture-user', 'fixture-pass')) as client:
        fetch(tmp_path, client)
    assert all(key not in requests[0].headers for key in ['authorization', 'cookie', 'proxy-authorization'])


def test_official_same_paper_redirect_is_followed_with_fixed_https_requests(tmp_path):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(302, headers={'location': '/pdf/2401.12345.pdf'})
        return httpx.Response(200, content=pdf_bytes())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch(tmp_path, client)
    assert requests == ['https://arxiv.org/pdf/2401.12345', 'https://arxiv.org/pdf/2401.12345.pdf']
    assert result['source_refs'][0]['arxiv_id'] == '2401.12345'


@pytest.mark.parametrize('location', ['https://evil.example/file.pdf', 'https://arxiv.org/pdf/2402.12345',
    'https://arxiv.org/pdf/2401.12345?signature=secret', 'http://arxiv.org/pdf/2401.12345'])
def test_redirect_cannot_change_origin_paper_or_introduce_signed_query(tmp_path, location):
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(302, headers={'location': location}))) as client:
        with pytest.raises(ValueError, match='重定向|redirect'):
            fetch(tmp_path, client)
    assert len(requests) == 1 and not list(tmp_path.rglob('paper.pdf'))


@pytest.mark.parametrize('body,headers', [
    (b'<html>login or challenge</html>', {'content-type': 'text/html'}),
    (b'%PDF-1.7\nThis is not a real PDF', {'content-type': 'application/pdf'}),
    (b'not a PDF', {'content-type': 'application/pdf'}),
    (b'', {'content-type': 'application/pdf'}),
    (pdf_bytes(), {'content-type': 'application/pdf', 'content-length': str(40 * 1024 * 1024 + 1)}),
])
def test_invalid_or_declared_oversize_download_leaves_no_deliverable(tmp_path, body, headers):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body, headers=headers))) as client:
        with pytest.raises(ValueError):
            fetch(tmp_path, client)
    assert not list(tmp_path.rglob('paper.pdf')) and not list(tmp_path.rglob('paper_parsed.json'))


def test_stream_limit_stops_unknown_size_pdf_without_retaining_partial_file(tmp_path):
    class Oversize(httpx.SyncByteStream):
        def __iter__(self):
            yield b'%PDF-1.7\n'
            block = b'0' * 1024 * 1024
            for _ in range(41):
                yield block
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Oversize()))) as client:
        with pytest.raises(ValueError, match='40|大小|上限'):
            fetch(tmp_path, client)
    assert not list(tmp_path.rglob('paper.pdf'))


def test_pdf_magic_can_arrive_across_small_stream_chunks(tmp_path):
    contents = pdf_bytes()
    class SplitHeader(httpx.SyncByteStream):
        def __iter__(self):
            yield contents[:2]
            yield contents[2:5]
            yield contents[5:]
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SplitHeader()))) as client:
        fetch(tmp_path, client)
    assert (tmp_path / 'out' / 'paper.pdf').read_bytes() == contents


def test_existing_output_symlink_cannot_replace_unrelated_file(tmp_path):
    outside = tmp_path / 'outside.txt'; outside.write_text('keep')
    directory = tmp_path / 'out'; directory.mkdir()
    (directory / 'paper.pdf').symlink_to(outside)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=pdf_bytes()))) as client:
        with pytest.raises(ValueError, match='链接|symbolic'):
            fetch(tmp_path, client)
    assert outside.read_text() == 'keep'


def test_unknown_fetch_parameters_are_not_ignored(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('must reject parameters before HTTP'))) as client:
        with pytest.raises(ValueError):
            fetch(tmp_path, client, parameters={'paper': '2401.12345', 'output': '/tmp/escape.pdf'})
