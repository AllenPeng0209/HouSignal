"""Bounded official arXiv PDF acquisition for the native paper pipeline.

Independent native implementation of Easel paper-explainer's fetch/parse
workflow. Identifier schemes and version semantics follow arXiv's official
documentation: https://info.arxiv.org/help/arxiv_identifier.html . Only arxiv.org
abs/pdf inputs are accepted, requests are fixed to official HTTPS PDF URLs,
and redirects must stay on the same host and the same paper/version. No model,
Easel runtime, arbitrary URL, user cookies or service credentials are used.
The actual selected PDF is parsed through the local editorial ingestion code.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urljoin, urlsplit

import httpx

from . import documents, editorial


MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
PARAMETER_KEYS = frozenset({'paper', 'op'})
_NEW_ID = re.compile(r'(?P<date>\d{4})\.(?P<sequence>\d{4,5})(?:v[1-9]\d{0,5})?')
_OLD_ID = re.compile(r'[A-Za-z][A-Za-z0-9.-]{0,40}/(?P<date>\d{4})(?P<sequence>\d{3})(?:v[1-9]\d{0,5})?')


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        raise ValueError('请提供纯 arXiv 编号或官方 abs/pdf 链接')
    value = value.strip()
    if any(char.isspace() for char in value) or '\\' in value:
        raise ValueError('论文来源只能是一个完整 arXiv 编号或链接')
    if '://' in value:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {'http', 'https'} or parsed.hostname != 'arxiv.org'
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.port not in (None, 443) or '%' in parsed.path):
                raise ValueError()
        except ValueError:
            raise ValueError('仅允许无凭据、无签名参数的 arxiv.org 官方论文链接') from None
        path = parsed.path
        if path.startswith('/abs/'):
            value = path[5:]
        elif path.startswith('/pdf/'):
            value = path[5:]
            if value.endswith('.pdf'):
                value = value[:-4]
        else:
            raise ValueError('arXiv 链接需要 /abs/ 或 /pdf/ 路径')
    match = _NEW_ID.fullmatch(value) or _OLD_ID.fullmatch(value)
    if match is None:
        raise ValueError('arXiv 编号格式无效')
    month = int(match['date'][2:])
    if not 1 <= month <= 12 or int(match['sequence']) == 0:
        raise ValueError('arXiv 编号月份或序号无效')
    if _NEW_ID.fullmatch(value):
        # The official new format uses four sequence digits before 2015 and
        # five thereafter. Refuse ambiguous forms instead of silently padding.
        date = int(match['date'])
        expected = 4 if 704 <= date <= 1412 else 5
        if date < 704 or len(match['sequence']) != expected:
            raise ValueError('arXiv 编号的年份与序号位数不符合官方格式')
    return value


def _redirect(current, location, identifier):
    if not isinstance(location, str) or not location or len(location) > 400:
        raise ValueError('arXiv 重定向地址无效')
    target = urljoin(current, location)
    try:
        parsed = urlsplit(target)
        if parsed.scheme != 'https' or parsed.hostname != 'arxiv.org' or not parsed.path.startswith('/pdf/'):
            raise ValueError()
        if _identifier(target) != identifier:
            raise ValueError()
    except ValueError:
        raise ValueError('论文重定向不能改变官方 HTTPS 来源、编号或版本，也不能带签名参数') from None
    return target


def _download(client, identifier, target, context):
    url = 'https://arxiv.org/pdf/' + identifier
    deadline = time.monotonic() + 120
    check = context.get('check_cancelled')
    for redirects in range(4):
        if callable(check):
            check()
        if time.monotonic() > deadline:
            raise ValueError('arXiv 论文获取超时')
        # A fresh Request bypasses injected client's default cookies/headers;
        # auth=None also disables client-level Basic/OAuth authorization.
        request = httpx.Request('GET', url, headers={
            'Accept': 'application/pdf', 'Accept-Encoding': 'identity',
            'User-Agent': 'Louxiang-PaperFetch/1.0 (single-paper acquisition)'},
            extensions={'timeout': {'connect': 10.0, 'read': 30.0, 'write': 20.0, 'pool': 10.0}})
        response = None
        try:
            response = client.send(request, stream=True, follow_redirects=False, auth=None)
            if response.status_code in {301, 302, 303, 307, 308}:
                if redirects == 3:
                    raise ValueError('arXiv 论文重定向超过 3 次上限')
                url = _redirect(url, response.headers.get('location'), identifier)
                continue
            if response.status_code != 200:
                raise ValueError(f'arXiv 未返回可下载论文（HTTP {response.status_code}）')
            content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            if content_type and content_type not in {'application/pdf', 'application/octet-stream', 'binary/octet-stream'}:
                raise ValueError('arXiv 返回的内容不是 PDF')
            declared = response.headers.get('content-length')
            if declared is not None:
                if not declared.isdigit() or int(declared) > MAX_DOWNLOAD_BYTES:
                    raise ValueError('论文下载大小超过 40 MiB 上限或大小声明无效')
            count, prefix, digest = 0, bytearray(), hashlib.sha256()
            with target.open('xb') as output:
                for block in response.iter_bytes(chunk_size=65536):
                    if callable(check):
                        check()
                    if time.monotonic() > deadline:
                        raise ValueError('arXiv 论文下载超时')
                    count += len(block)
                    if count > MAX_DOWNLOAD_BYTES:
                        raise ValueError('论文流式下载超过 40 MiB 上限')
                    if len(prefix) < 8:
                        prefix.extend(block[:8 - len(prefix)])
                    output.write(block)
                    digest.update(block)
            if not count or not bytes(prefix).startswith(b'%PDF-'):
                raise ValueError('下载文件没有有效 PDF 文件头')
            if declared is not None and not response.headers.get('content-encoding') and count != int(declared):
                raise ValueError('论文下载长度与实际内容不一致')
            return {'download_url': url, 'byte_size': count, 'sha256': digest.hexdigest()}
        except (httpx.HTTPError, OSError):
            raise ValueError('arXiv 论文连接或下载失败，请稍后重试') from None
        finally:
            if response is not None:
                response.close()
    raise ValueError('arXiv 论文获取未完成')


def fetch(*, input_text, parameters, output_dir, context):
    """Fetch and locally parse one official paper, without model authorization.

    ``paper`` may hold a pure ID or an official abs/pdf URL. Otherwise input_text
    must contain exactly that source. Only ``paper`` and ``op='fetch'`` apply to
    this operation; manuscript/slide parameters belong to the explain operation.
    ``paper_http_client`` is a trusted server/test-only httpx.Client injection.
    """
    if not isinstance(parameters, Mapping) or not all(isinstance(key, str) for key in parameters):
        raise ValueError('parameters 必须是对象')
    if set(parameters) - PARAMETER_KEYS or parameters.get('op', 'fetch') != 'fetch':
        raise ValueError('fetch 仅支持 paper 和 op=fetch 参数')
    if not isinstance(context, Mapping):
        raise ValueError('项目执行 context 无效')
    identifier = _identifier(parameters.get('paper') or input_text)
    client = context.get('paper_http_client')
    if client is not None and not isinstance(client, httpx.Client):
        raise ValueError('paper_http_client 必须是服务端受控 HTTP 客户端')
    owned = client is None
    if owned:
        client = httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
    try:
        root = documents._Output(output_dir)
        root.check(['paper.pdf', 'paper_parsed.json', 'paper.txt', 'paper_source.json'])
        with tempfile.TemporaryDirectory(prefix='.paper-fetch-', dir=root.path) as temporary:
            stage = documents._Output(Path(temporary))
            downloaded = stage.path / 'paper.pdf'
            receipt = _download(client, identifier, downloaded, context)
            parsed = editorial._paper_ingest({'path': downloaded}, stage, allow_short=True)
            ref = {'title': 'arXiv ' + identifier, 'url': 'https://arxiv.org/abs/' + identifier,
                'pdf_url': 'https://arxiv.org/pdf/' + identifier, 'arxiv_id': identifier,
                'version_state': 'explicit' if re.search(r'v[1-9]\d*$', identifier) else 'latest_unpinned',
                'review_state': 'not_reviewed', 'publication_state': 'not_published',
                'fetched_at': datetime.now(timezone.utc).isoformat(), **receipt}
            parsed['source_refs'] = [ref]
            stage.json('paper_parsed.json', parsed)
            text = '\n\n'.join(f'## 第 {page["page"]} 页\n\n{page["text"]}' for page in parsed['pages']) + '\n'
            stage.write('paper.txt', text)
            stage.json('paper_source.json', ref)
            paths = [downloaded, *dict.fromkeys(stage.paths)]
            root.check([path.name for path in paths])
            for path in paths:
                root.write(path.name, path.read_bytes())
        note = '；PDF 未提取到文字，需要 OCR，未生成解读。' if parsed['text_extraction_state'] == 'needs_ocr' else '；尚未进行模型解读或人工核验。'
        return {'status': 'completed', 'response': f'已从 arXiv 官方获取论文并实际提取 {parsed["page_count"]} 页、{len(parsed["figures"])} 张内嵌图像' + note,
            'artifact_paths': root.paths, 'source_refs': [ref]}
    finally:
        if owned:
            client.close()
