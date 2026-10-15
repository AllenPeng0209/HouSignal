"""Real file and codec regressions for the project's native media skills."""
from pathlib import Path
import importlib
import json
import sys
import subprocess
import wave
import base64
import io
from types import SimpleNamespace
from uuid import UUID

from PIL import Image
import pytest


@pytest.mark.parametrize("skill,parameters", [
    ("auto-subtitle", {"format": "txt", "engine": "local"}),
    ("video-to-article", {"op": "transcribe", "engine": "local"}),
])
def test_local_plain_asr_never_calls_project_cloud(media, tmp_path, monkeypatch, skill, parameters):
    from app.content_skills.tools import speech
    monkeypatch.setattr(speech, "local_asr_ready", lambda: True)
    schema = media.definition_for(skill, parameters)
    assert "engine" in {field["key"] for field in schema["parameters"]}
    assert media.readiness_for(skill, parameters)["status"] == "ready"
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    def forbidden(*_a, **_k):
        pytest.fail("Local ASR must not call a metered chat provider")
    result = media.execute(skill, input_text="", assets=[{"path": path, "kind": "audio"}],
        parameters=parameters, output_dir=tmp_path / "out", context={"chat": forbidden, "cloud_chat": forbidden,
        "transcribe_segments": lambda *_a, **_k: [{"start": 0, "end": .8, "text": "A genuine local transcript."}]})
    assert result["artifact_paths"][0].read_text().strip() == "A genuine local transcript."
    assert not result.get("usage")


def test_local_plain_asr_without_weights_reports_setup_before_execution(media, monkeypatch):
    from app.content_skills.tools import speech
    monkeypatch.setattr(speech, "local_asr_ready", lambda: False)
    assert media.readiness_for("auto-subtitle", {"format": "txt", "engine": "local"})["status"] == "setup"


def test_article_keeps_exact_actual_transcript_separate_from_rewritten_draft(media, tmp_path):
    source = 'This is a self-created demo. Keep the source and review every file before publishing.'
    path = tmp_path / 'speech.wav'
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b'\x00\x00' * 16000)
    seen = []
    def rewrite(messages, **kwargs):
        seen.append(messages)
        return {'choices': [{'message': {'content': '# 自制演示\n\n文章草稿，转录待核验。'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 8}, 'model': 'ppu:controlled-fixture'}
    result = media.execute('video-to-article', input_text='整理原文，不增加事实。',
        assets=[{'path': path, 'kind': 'audio'}], parameters={'op': 'article', 'engine': 'local'},
        output_dir=tmp_path / 'out', context={'chat': rewrite, 'chat_manages_lifecycle': True,
            'transcribe_segments': lambda *_a, **_k: [{'start': 0, 'end': .8, 'text': source}]})
    outputs = {p.name: p for p in result['artifact_paths']}
    assert outputs['transcript.txt'].read_text() == source + '\n'
    assert outputs['article.md'].read_text() == '# 自制演示\n\n文章草稿，转录待核验。\n'
    assert source in seen[0][1]['content']


def test_audio_denoise_level_is_a_bounded_integer_in_the_actual_api_contract(media):
    from app.native_skills import _parameters
    schema = media.definition_for("audio-denoise", {"tier": 2, "mix": .8, "format": "wav"})
    tier = next(field for field in schema["parameters"] if field["key"] == "tier")
    assert tier["type"] == "number" and tier["integer"] is True
    assert (tier["min"], tier["max"]) == (1, 3)
    assert _parameters(SimpleNamespace(parameters={"tier": 2, "mix": .8, "format": "wav"}, account_id=None), schema)["tier"] == 2
    from fastapi import HTTPException
    for value in (True, 2.5, 0, 4, "two"):
        with pytest.raises(HTTPException):
            _parameters(SimpleNamespace(parameters={"tier": value}, account_id=None), schema)


def test_every_media_live_case_accepts_its_declared_value_types_in_the_api_contract(media):
    from app.native_skills import _parameters
    path = API_ROOT.parents[1] / "scripts/skill_acceptance/media_cases.json"
    cases = json.loads(path.read_text())["cases"]
    for case in cases:
        p = case["parameters"]
        schema = media.definition_for(case["skill_name"], p)
        parsed = _parameters(SimpleNamespace(parameters=p, account_id=None), schema)
        assert all(parsed[key] == value for key, value in p.items()), case["skill_name"]


def test_edge_word_boundaries_keep_adjacent_integer_ticks_without_float_overlap(media, tmp_path):
    # Observed from the real Chinese Edge stream: computing start + duration
    # separately introduces a tiny false overlap at exactly 0.3625 seconds.
    target = tmp_path / "sample.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(target)], check=True)
    audio = target.read_bytes()
    class Communicator:
        def __init__(self, *_a, **_k): pass
        async def stream(self):
            yield {"type": "audio", "data": audio}
            yield {"type": "WordBoundary", "offset": 1000000, "duration": 2625000, "text": "先"}
            yield {"type": "WordBoundary", "offset": 3625000, "duration": 3125000, "text": "保留"}
    result = media.execute("tts-voiceover", input_text="先保留", assets=[], parameters={"engine": "edge"},
        output_dir=tmp_path / "result", context={"edge_tts_factory": Communicator})
    srt = next(path for path in result["artifact_paths"] if path.suffix == ".srt").read_text()
    assert "先" in srt and "保留" in srt
    assert "00:00:00,100 --> 00:00:00,362" in srt and "00:00:00,362 --> 00:00:00,675" in srt


def test_media_receipt_keeps_precall_uuid_after_cancel_before_artifact_download(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_image_model", "qwen-image-2.1")
    monkeypatch.setattr(settings, "dashscope_api_key", "fixture-key")
    token = str(UUID(int=1)); events=[]; receipts=[]; cancelled=False
    def begin():
        events.append('begin');return token
    def before():
        if cancelled:raise RuntimeError('cancelled after real response')
    def transport(_request):
        nonlocal cancelled
        assert events == ['begin'], 'Metering ID must precede actual provider request'
        cancelled=True
        return httpx.Response(200,json={'request_id':'supplier-request',
            'output':{'results':[{'url':'https://assets.aliyuncs.com/generated.png'}]},'usage':{'image_count':1}})
    context={'begin_usage_call':begin,'before_model_call':before,'on_usage':receipts.append,
        'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))}
    with pytest.raises(RuntimeError,match='cancelled'):
        media.execute('ai-image-gen',input_text='Original product image',assets=[],parameters={'size':'1024x1024'},output_dir=tmp_path/'out',context=context)
    assert receipts[0]['usage_call_id'] == token and receipts[0]['units']['image_count']==1
    assert receipts[0]['call_id']=='supplier-request'
    assert 'usage_call_id' not in context


def test_async_media_submit_and_final_usage_share_one_precall_uuid(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_image_model','wanx2.1-t2i-turbo')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    token=str(UUID(int=2));begins=[];receipts=[]
    stream=io.BytesIO();Image.new('RGB',(16,16),'blue').save(stream,format='PNG')
    url='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
    def begin():begins.append(token);return token
    def transport(request):
        assert begins==[token]
        if request.method=='POST':return httpx.Response(200,json={'output':{'task_id':'fixture-task','task_status':'PENDING'},'usage':{'image_count':0}})
        return httpx.Response(200,json={'output':{'task_id':'fixture-task','task_status':'SUCCEEDED','results':[{'url':url}]},'usage':{'image_count':1}})
    context={'begin_usage_call':begin,'on_usage':receipts.append,'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))}
    media.execute('ai-image-gen',input_text='Original image',assets=[],parameters={'size':'1024x1024'},output_dir=tmp_path/'out',context=context)
    assert begins==[token] and len(receipts)==2
    assert {record['usage_call_id'] for record in receipts}=={token}
    assert [record['units']['image_count'] for record in receipts]==[0,1]
    assert 'usage_call_id' not in context


def test_each_edge_audio_call_mints_its_own_uuid_before_streaming(media, tmp_path):
    from app.content_skills.tools import speech
    source=tmp_path/'source.mp3'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=duration=0.3',str(source)],check=True)
    audio=source.read_bytes();begins=[];receipts=[]
    def begin():
        token=str(UUID(int=len(begins)+10));begins.append(token);return token
    class Communicator:
        def __init__(self,*_a,**_k):assert len(begins)>len(receipts)
        async def stream(self):yield {'type':'audio','data':audio}
    context={'begin_usage_call':begin,'on_usage':receipts.append,'edge_tts_factory':Communicator}
    for index in range(2):
        folder=tmp_path/f'line-{index}';folder.mkdir()
        speech._edge_audio('Original synthetic words.',{},folder,context)
    assert len(set(begins))==2
    assert [r['usage_call_id'] for r in receipts]==begins
    assert 'usage_call_id' not in context


def test_voice_enrollment_allocates_usage_id_before_its_provider_action(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_tts_model','qwen3-tts-vc-2026-01-22')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    sample=tmp_path/'sample.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=duration=6',str(sample)],check=True)
    token=str(UUID(int=3));begins=[];receipts=[]
    def begin():begins.append(token);return token
    def transport(_request):
        assert begins==[token]
        return httpx.Response(200,json={'output':{'voice_id':'fixture-voice-id'},'usage':{'audio_seconds':6}})
    context={'begin_usage_call':begin,'on_usage':receipts.append,'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))}
    media.execute('voice-clone',input_text='',assets=[{'path':sample,'kind':'audio'}],parameters={'op':'enroll','name':'fixture'},output_dir=tmp_path/'out',context=context)
    assert begins==[token] and receipts[0]['usage_call_id']==token
    assert 'usage_call_id' not in context


def test_managed_project_chat_does_not_mint_a_second_usage_id(media):
    from app.content_skills.tools import speech
    response={'choices':[{'message':{'content':'Complete actual text'},'finish_reason':'stop'}],'usage':{'input_tokens':10,'output_tokens':5}}
    context={'chat':lambda *_a,**_k:response,'begin_usage_call':lambda:pytest.fail('Managed chat owns its own precall UUID'),
        'on_usage':lambda _receipt:pytest.fail('Managed chat already durably records its response')}
    assert speech._chat([{'role':'user','content':'Original request'}],context)==response


def test_music_submit_and_poll_share_a_precall_uuid(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_music_model','configured-music-model')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    monkeypatch.setattr(settings,'native_music_provider','dashscope')
    monkeypatch.setattr(settings,'native_music_download_hosts',['cdn.example.test'])
    sample=tmp_path/'song.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=duration=0.3',str(sample)],check=True)
    token=str(UUID(int=4));begins=[];receipts=[]
    def begin():begins.append(token);return token
    def transport(request):
        assert begins==[token]
        if request.method=='POST':return httpx.Response(200,json={'output':{'task_id':'fixture-song','task_status':'PENDING'}})
        if request.url.host=='cdn.example.test':return httpx.Response(200,content=sample.read_bytes())
        return httpx.Response(200,json={'output':{'task_id':'fixture-song','task_status':'SUCCEEDED','audio_url':'https://cdn.example.test/song.wav'},'usage':{'audio_seconds':.3}})
    context={'begin_usage_call':begin,'on_usage':receipts.append,'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))}
    media.execute('ai-music',input_text='Original instrumental',assets=[],parameters={'duration':5,'instrumental':True},output_dir=tmp_path/'out',context=context)
    assert begins==[token] and receipts[0]['usage_call_id']==token
    assert 'usage_call_id' not in context


@pytest.mark.parametrize('status',['FAILED','CANCELED'])
def test_failed_async_media_preserves_reported_usage_before_rejecting_status(media, tmp_path, monkeypatch, status):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_image_model','wanx2.1-t2i-turbo')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    token=str(UUID(int=5));receipts=[];begins=[]
    def begin():begins.append(token);return token
    def transport(request):
        if request.method=='POST':return httpx.Response(200,json={'output':{'task_id':'fixture-task','task_status':'PENDING'}})
        return httpx.Response(200,json={'output':{'task_id':'fixture-task','task_status':status},'usage':{'image_count':1}})
    context={'begin_usage_call':begin,'on_usage':receipts.append,'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))}
    with pytest.raises(ValueError,match='任务失败'):
        media.execute('ai-image-gen',input_text='Original image',assets=[],parameters={'size':'1024x1024'},output_dir=tmp_path/'out',context=context)
    assert begins==[token]
    assert any(r.get('usage_call_id')==token and r['units'].get('image_count')==1 for r in receipts)
    assert not list((tmp_path/'out').iterdir())


@pytest.mark.parametrize('http_status,code',[(429,None),(200,'ProviderRejected')])
def test_media_http_failure_keeps_only_actually_reported_usage(media, tmp_path, monkeypatch, http_status, code):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_image_model','qwen-image-2.1')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    token=str(UUID(int=6));receipts=[]
    payload={'usage':{'image_count':1}}
    if code:payload['code']=code
    context={'begin_usage_call':lambda:token,'on_usage':receipts.append,
        'media_http_client':httpx.Client(transport=httpx.MockTransport(lambda _r:httpx.Response(http_status,json=payload)))}
    with pytest.raises(ValueError):
        media.execute('ai-image-gen',input_text='Original image',assets=[],parameters={'size':'1024x1024'},output_dir=tmp_path/'out',context=context)
    assert len(receipts)==1 and receipts[0]['usage_call_id']==token and receipts[0]['units']=={'image_count':1}


@pytest.mark.parametrize('provider_status',['failed','canceled'])
def test_music_failure_preserves_reported_audio_seconds(media, tmp_path, monkeypatch, provider_status):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_music_model','configured-music-model')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    monkeypatch.setattr(settings,'native_music_provider','dashscope')
    token=str(UUID(int=7));receipts=[]
    def transport(request):
        if request.method=='POST':return httpx.Response(200,json={'output':{'task_id':'fixture-song','task_status':'PENDING'}})
        return httpx.Response(200,json={'output':{'task_id':'fixture-song','task_status':provider_status},'usage':{'audio_seconds':.3}})
    context={'begin_usage_call':lambda:token,'on_usage':receipts.append,'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))}
    with pytest.raises(ValueError,match='任务失败'):
        media.execute('ai-music',input_text='Original instrumental',assets=[],parameters={'duration':5},output_dir=tmp_path/'out',context=context)
    assert receipts[0]['usage_call_id']==token and receipts[0]['units']=={'audio_seconds':.3}


def test_error_response_without_usage_does_not_fabricate_a_receipt(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_image_model','qwen-image-2.1')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    receipts=[]
    context={'begin_usage_call':lambda:str(UUID(int=8)),'on_usage':receipts.append,
        'media_http_client':httpx.Client(transport=httpx.MockTransport(lambda _r:httpx.Response(400,json={'code':'Failed'})))}
    with pytest.raises(ValueError):
        media.execute('ai-image-gen',input_text='Original image',assets=[],parameters={'size':'1024x1024'},output_dir=tmp_path/'out',context=context)
    assert receipts==[]


@pytest.mark.parametrize('vendor_output',[None,[], 'invalid-output'])
def test_bad_optional_vendor_metadata_cannot_discard_valid_error_usage(media, tmp_path, monkeypatch, vendor_output):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings,'dashscope_image_model','qwen-image-2.1')
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-key')
    token=str(UUID(int=9));receipts=[]
    context={'begin_usage_call':lambda:token,'on_usage':receipts.append,
        'media_http_client':httpx.Client(transport=httpx.MockTransport(lambda _r:httpx.Response(429,json={'output':vendor_output,'usage':{'image_count':1}})))}
    with pytest.raises(ValueError):
        media.execute('ai-image-gen',input_text='Original image',assets=[],parameters={'size':'1024x1024'},output_dir=tmp_path/'out',context=context)
    assert receipts[0]['usage_call_id']==token and receipts[0]['units']=={'image_count':1}


API_ROOT = Path(__file__).resolve().parents[1] / "apps/api"
sys.path.insert(0, str(API_ROOT))


@pytest.fixture
def media(monkeypatch):
    from app.config import settings
    # These fixtures exercise the bounded Dashscope adapter; a developer's
    # currently selected PPU backend must not redirect them to a live runtime.
    monkeypatch.setattr(settings, "native_video_backend", "dashscope")
    path = API_ROOT / "app/content_skills/media.py"
    assert path.is_file(), "Native media module has not been implemented"
    return importlib.import_module("app.content_skills.media")


@pytest.fixture
def image_asset(tmp_path):
    path = tmp_path / "original.png"
    image = Image.new("RGB", (80, 40), "red")
    for x in range(40, 80):
        for y in range(40):
            image.putpixel((x, y), (0, 0, 255))
    image.save(path)
    return {"path": path, "name": "original.png", "kind": "image"}


@pytest.mark.parametrize("parameters,size", [
    ({"op": "resize", "width": 40}, (40, 20)),
    ({"op": "crop", "box": [40, 0, 80, 40]}, (40, 40)),
    ({"op": "pad", "ratio": "1:1", "background": "white"}, (80, 80)),
    ({"op": "thumbnail", "size": 20}, (20, 10)),
    ({"op": "round", "radius": 8}, (80, 40)),
])
def test_image_editing_produces_actual_transformed_pixels(media, image_asset, tmp_path, parameters, size):
    # Catches routing all image actions to a textual answer or unmodified copy.
    result = media.execute("image-editing", input_text="", assets=[image_asset], parameters=parameters,
                           output_dir=tmp_path / "result", context={})
    assert result["artifact_paths"]
    with Image.open(result["artifact_paths"][0]) as image:
        assert image.size == size
        if parameters["op"] == "crop":
            assert image.convert("RGB").getpixel((10, 10)) == (0, 0, 255)
        if parameters["op"] == "pad":
            assert image.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
        if parameters["op"] == "round":
            assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0
    assert all(Path(path).is_relative_to(tmp_path / "result") for path in result["artifact_paths"])


def test_image_enhance_uses_source_scale_and_reports_real_artifact(media, image_asset, tmp_path):
    result = media.execute("image-enhance", input_text="", assets=[image_asset], parameters={"scale": 2, "auto": True},
                           output_dir=tmp_path / "result", context={})
    with Image.open(result["artifact_paths"][0]) as image:
        assert image.size == (160, 80)


def test_automatic_enhancement_preserves_flat_product_hue_instead_of_black_crush(media, tmp_path):
    import colorsys
    source=tmp_path/'product.png'
    picture=Image.new('RGB',(160,100),(245,243,237))
    from PIL import ImageDraw
    draw=ImageDraw.Draw(picture)
    draw.rectangle((30,15,130,85),fill=(195,107,60))
    draw.rectangle((65,50,105,70),fill=(234,217,190))
    picture.save(source)
    result=media.execute('image-enhance',input_text='',assets=[{'path':source,'kind':'image'}],
        parameters={'scale':2,'auto':True,'sharpen':1.1},output_dir=tmp_path/'out',context={})
    with Image.open(result['artifact_paths'][0]) as enhanced:
        assert enhanced.size==(320,200)
        for original,position in [((195,107,60),(80,50)),((234,217,190),(160,120))]:
            actual=enhanced.getpixel(position)
            assert actual[0]>actual[1]>actual[2] and min(actual)>20, (original,actual)
            old_hue=colorsys.rgb_to_hsv(*(value/255 for value in original))[0]
            new_hue=colorsys.rgb_to_hsv(*(value/255 for value in actual))[0]
            assert abs(new_hue-old_hue)<.04, (original,actual)
            assert max(abs(a-b) for a,b in zip(actual,original))<40


@pytest.mark.parametrize('color',[(0,0,0),(128,128,128),(255,255,255),(180,60,25),(25,85,190)])
def test_automatic_tone_adjustment_keeps_uniform_material_colors_stable(media, tmp_path, color):
    source=tmp_path/'material.png';Image.new('RGB',(64,48),color).save(source)
    result=media.execute('image-enhance',input_text='',assets=[{'path':source,'kind':'image'}],
        parameters={'scale':1,'auto':True,'sharpen':0,'contrast':1,'saturation':1},output_dir=tmp_path/'out',context={})
    with Image.open(result['artifact_paths'][0]) as enhanced:
        assert max(abs(a-b) for a,b in zip(enhanced.getpixel((32,24)),color))<=2


def test_media_output_cannot_be_selected_through_a_user_path(media, image_asset, tmp_path):
    # Catches passing user output/path flags through to an upstream CLI or file operation.
    with pytest.raises(ValueError):
        media.execute("image-editing", input_text="", assets=[image_asset], parameters={"op": "resize", "width": 40, "output": "../../outside.png"},
                      output_dir=tmp_path / "result", context={})
    assert not (tmp_path.parent / "outside.png").exists()


def test_native_image_work_survives_absent_easel_source_and_archive(media, image_asset, tmp_path, monkeypatch):
    # Catches any runtime attempt to read either legacy checkout or vendored source.
    original = Path.open
    def guarded_open(path, *args, **kwargs):
        assert "/workdir/Easel/" not in str(path) and "/skills/easel/" not in str(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    result = media.execute("meme-generator", input_text="Hello", assets=[image_asset], parameters={"layout": "bottom-bar"},
                           output_dir=tmp_path / "result", context={})
    assert result["artifact_paths"][0].is_file()
    with Image.open(result["artifact_paths"][0]) as image:
        assert image.height > 40


def test_unconfigured_ai_media_does_not_emit_placeholder(media, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_image_model", "", raising=False)
    assert media.readiness("ai-image-gen")["status"] == "setup"
    with pytest.raises(ValueError, match="配置"):
        media.execute("ai-image-gen", input_text="A property", assets=[], parameters={}, output_dir=tmp_path / "result", context={})
    assert not list((tmp_path / "result").glob("*"))


def test_supplied_timed_segments_create_real_srt_without_model_call(media, tmp_path):
    # Catches fabricated cue timing or model calls for a provided timed transcript.
    segments = [{"start": 0.125, "end": 1.5, "text": "First"}, {"start": 2, "end": 3.25, "text": "Second"}]
    result = media.execute("auto-subtitle", input_text="", assets=[], parameters={"segments": segments},
                           output_dir=tmp_path / "result", context={"chat": lambda *_args, **_kwargs: pytest.fail("No model required")})
    content = result["artifact_paths"][0].read_text()
    assert "00:00:00,125 --> 00:00:01,500" in content
    assert "00:00:02,000 --> 00:00:03,250" in content
    assert "First" in content and "Second" in content


def test_asr_uses_real_pcm_proxy_and_saves_only_actual_transcript(media, tmp_path):
    # The bounded model boundary is substituted; codec preparation and file output are real.
    path = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", str(path)], check=True)
    requests = []
    def chat(messages, **kwargs):
        requests.append((messages, kwargs))
        return {"choices": [{"message": {"content": "Actual model transcript"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}, "model": "qwen3-asr-flash"}
    result = media.execute("video-to-article", input_text="", assets=[{"path": path, "kind": "audio", "name": "voice.wav"}],
                           parameters={"op": "transcribe"}, output_dir=tmp_path / "result", context={"cloud_chat": chat})
    assert len(requests) == 1
    assert requests[0][0][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,")
    assert result["artifact_paths"][0].read_text() == "Actual model transcript\n"
    assert result["usage"]["prompt_tokens"] == 7


def test_catalog_definitions_publish_asset_and_authorization_requirements(media):
    assert "image-editing" in media.NATIVE_MEDIA_SKILLS
    definition = media.definition("image-editing")
    assert definition["accepts_assets"] is True
    assert "image" in definition["accepted_kinds"]
    assert definition["requires_model_processing"] is False
    assert definition["input_required"] is False
    assert media.definition("ai-video-gen")["requires_model_processing"] is True


def test_configured_image_adapter_saves_and_decodes_actual_supplier_pixels(media, tmp_path, monkeypatch):
    # Model HTTP is isolated; the output must be an actual decoded image, never a URL/text stub.
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_image_model", "qwen-image-2.1-pro", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    pixels = io.BytesIO()
    Image.new("RGB", (32, 16), "green").save(pixels, format="PNG")
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": "data:image/png;base64," + base64.b64encode(pixels.getvalue()).decode()}]}}]}, "usage": {"image_count": 1}})
    client = httpx.Client(transport=httpx.MockTransport(transport))
    result = media.execute("ai-image-gen", input_text="Green room", assets=[], parameters={"size": "1024x1024"},
                           output_dir=tmp_path / "result", context={"media_http_client": client})
    assert requests[0].url.host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
    payload = json.loads(requests[0].content)
    assert payload["model"] == "qwen-image-2.1-pro"
    assert payload["input"]["messages"][0]["content"][-1]["text"] == "Green room"
    with Image.open(result["artifact_paths"][0]) as image:
        assert image.size == (32, 16) and image.getpixel((0, 0)) == (0, 128, 0)


def test_ai_media_validates_frame_size_before_a_billable_request(media, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_image_model", "qwen-image-2.1-pro", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    calls = []
    with pytest.raises(ValueError, match="尺寸|画幅"):
        media.execute("ai-image-gen", input_text="Room", assets=[], parameters={}, output_dir=tmp_path / "result",
                      context={"before_model_call": lambda: calls.append("cost")})
    assert calls == []


def test_batch_process_applies_real_transformation_to_each_selected_asset(media, image_asset, tmp_path):
    second = tmp_path / "second.png"
    Image.new("RGB", (50, 100), "yellow").save(second)
    result = media.execute("batch-process", input_text="", assets=[image_asset, {"path": second, "name": "second.png", "kind": "image"}],
                           parameters={"op": "resize", "width": 20}, output_dir=tmp_path / "result", context={})
    assert len(result["artifact_paths"]) == 2
    assert sorted(Image.open(path).size for path in result["artifact_paths"]) == [(20, 10), (20, 40)]


def test_short_drama_refuses_static_images_as_finished_dynamic_shots(media, image_asset, tmp_path):
    with pytest.raises(ValueError, match="动态|视频"):
        media.execute("short-drama", input_text="", assets=[image_asset], parameters={}, output_dir=tmp_path / "result", context={})


def test_timed_subtitle_rejects_backwards_segment_without_artifact(media, tmp_path):
    with pytest.raises(ValueError, match="字幕|时间"):
        media.execute("auto-subtitle", input_text="", assets=[], parameters={"segments": [{"start": 3, "end": 2, "text": "Bad"}]},
                      output_dir=tmp_path / "result", context={})
    assert not list((tmp_path / "result").glob("*"))


def test_card_creation_uses_its_editorial_task_and_renders_real_pixels(media, tmp_path):
    calls = []
    def chat(messages, **kwargs):
        calls.append(messages)
        return {"choices": [{"message": {"content": "# A quiet living room\n\nA clear layout from the supplied brief.\n\n## One useful detail\n\nNatural light without invented property claims."}}], "usage": {"prompt_tokens": 11, "completion_tokens": 19}, "model": "project-text"}
    result = media.execute("card-design", input_text="Create a card about the provided room", assets=[],
                           parameters={"draft_content": True, "style": "editorial", "scale": 1, "width": 540, "height": 720},
                           output_dir=tmp_path / "result", context={"chat": chat})
    assert len(calls) == 1 and "卡片" in calls[0][0]["content"]
    assert any(path.suffix == ".png" for path in result["artifact_paths"])
    assert any(path.suffix == ".html" for path in result["artifact_paths"])
    assert result["usage"]["input_tokens"] == 11


def test_manual_gzh_format_preserves_content_without_a_model(media, tmp_path):
    result = media.execute("gzh-design", input_text="# Original title\n\nOriginal area 200 sqm.", assets=[],
                           parameters={"draft_content": False}, output_dir=tmp_path / "result", context={})
    html = next(path for path in result["artifact_paths"] if path.suffix == ".html").read_text()
    assert "Original title" in html and "200 sqm" in html


def test_image_count_returns_each_actual_generated_image(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_image_model", "qwen-image-2.1-pro", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    values = []
    for color in ("red", "blue"):
        stream = io.BytesIO()
        Image.new("RGB", (16, 16), color).save(stream, format="PNG")
        values.append({"image": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()})
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": {"choices": [{"message": {"content": values}}]}})))
    result = media.execute("ai-image-gen", input_text="Two rooms", assets=[], parameters={"size": "1024x1024", "n": 2}, output_dir=tmp_path / "result", context={"media_http_client": client})
    assert len(result["artifact_paths"]) == 2
    assert {Image.open(path).getpixel((0, 0)) for path in result["artifact_paths"]} == {(255, 0, 0), (0, 0, 255)}


@pytest.mark.parametrize("finish", ["length", "tool_calls"])
def test_media_editor_rejects_incomplete_or_tool_call_model_text(media, tmp_path, finish):
    def chat(*_args, **_kwargs):
        return {"choices": [{"finish_reason": finish, "message": {"content": "Partial visual brief"}}]}
    with pytest.raises(ValueError, match="完整|格式"):
        media.execute("ecom-details-image", input_text="A real product", assets=[], parameters={}, output_dir=tmp_path / "result", context={"chat": chat})
    assert not list((tmp_path / "result").glob("*"))


def test_wrapped_project_chat_is_not_counted_again_by_media_callback(media, tmp_path):
    callbacks = []
    def chat(*_args, **_kwargs):
        return {"choices": [{"message": {"content": "A complete visual brief"}}], "usage": {"prompt_tokens": 17, "completion_tokens": 23}, "model": "project-text"}
    result = media.execute("ecom-details-image", input_text="A product", assets=[], parameters={}, output_dir=tmp_path / "result", context={"chat": chat, "on_usage": callbacks.append})
    assert callbacks == []
    assert result["usage"]["aggregate"] is True
    assert result["usage"]["input_tokens"] == 17 and result["usage"]["output_tokens"] == 23


def test_batch_artifacts_are_top_level_downloadable_files(media, image_asset, tmp_path):
    result = media.execute("batch-process", input_text="", assets=[image_asset], parameters={"op": "resize", "width": 20}, output_dir=tmp_path / "result", context={})
    assert all(path.parent == tmp_path / "result" for path in result["artifact_paths"])


def test_ordinary_tts_can_use_native_edge_audio_and_actual_boundary_times(media, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_tts_model", "", raising=False)
    audio = tmp_path / "fixture.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.5", str(audio)], check=True)
    class Communicator:
        def __init__(self, text, **kwargs):
            self.text = text
        async def stream(self):
            yield {"type": "audio", "data": audio.read_bytes()}
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": self.text}
    result = media.execute("tts-voiceover", input_text="Hello", assets=[], parameters={"engine": "edge"}, output_dir=tmp_path / "result", context={"edge_tts_factory": Communicator})
    assert any(path.suffix == ".mp3" and path.stat().st_size > 100 for path in result["artifact_paths"])
    srt = next(path for path in result["artifact_paths"] if path.suffix == ".srt").read_text()
    assert "00:00:00,100 --> 00:00:00,300" in srt


def test_short_drama_default_requires_its_actual_media_models(media, tmp_path, monkeypatch):
    from app.config import settings
    for field in ("dashscope_image_model", "dashscope_video_model", "dashscope_tts_model"):
        monkeypatch.setattr(settings, field, "", raising=False)
    assert media.readiness("short-drama")["status"] == "setup"
    with pytest.raises(ValueError, match="配置"):
        media.execute("short-drama", input_text="A native drama", assets=[], parameters={"size": "1080x1920"}, output_dir=tmp_path / "result", context={})
    assert not list((tmp_path / "result").glob("*"))


def test_short_drama_assembly_preserves_full_actual_clip_and_audio(media, tmp_path):
    video = tmp_path / "actual.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=15:duration=0.6", "-f", "lavfi", "-i", "sine=duration=0.6", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)], check=True)
    result = media.execute("short-drama", input_text="", assets=[{"path": video, "kind": "video", "name": "actual.mp4"}], parameters={"op": "assemble", "size": "64x64"}, output_dir=tmp_path / "result", context={})
    target = next(path for path in result["artifact_paths"] if path.suffix == ".mp4")
    probed = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(target)], capture_output=True, check=True).stdout)
    assert float(probed["format"]["duration"]) < 1
    assert any(stream["codec_type"] == "audio" for stream in probed["streams"])


def test_short_drama_planning_has_a_distinct_native_structured_story_task(media, tmp_path):
    plan = {"title": "The locked room", "bible": "A causally linked story", "characters": [{"name": "Lin", "visual_prompt": "A calm young architect"}], "shots": [{"id": "S01", "visual_prompt": "Lin enters the room", "duration": 5, "dialogue": []}]}
    calls = []
    def chat(messages, **kwargs):
        calls.append(messages)
        return {"choices": [{"message": {"content": json.dumps(plan)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 25}, "model": "project-text"}
    result = media.execute("short-drama", input_text="A short drama", assets=[], parameters={"op": "plan"}, output_dir=tmp_path / "result", context={"chat": chat})
    assert len(calls) == 1 and "戏剧承诺" in calls[0][0]["content"]
    assert any(path.name == "series-plan.json" for path in result["artifact_paths"])
    assert not any(path.suffix == ".mp4" for path in result["artifact_paths"])


@pytest.mark.parametrize("provider", ["dashscope", "suno-compatible"])
def test_music_native_protocol_saves_real_audio_after_actual_task_status(media, tmp_path, monkeypatch, provider):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_music_model", "explicit-music-model")
    monkeypatch.setattr(settings, "dashscope_api_key", "project-text-key")
    monkeypatch.setattr(settings, "dashscope_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "native_music_provider", provider)
    monkeypatch.setattr(settings, "native_music_base_url", "https://music.example.test/v1" if provider == "suno-compatible" else "")
    monkeypatch.setattr(settings, "native_music_api_key", "music-test-key")
    monkeypatch.setattr(settings, "native_music_download_hosts", ["cdn.example.test"])
    audio = tmp_path / "source.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.3", str(audio)], check=True)
    requests = []
    def transport(request):
        requests.append(request)
        if request.method == "POST":
            assert request.headers["Authorization"] == "Bearer music-test-key"
            return httpx.Response(200, json={"output": {"task_id": "fixture-task", "task_status": "PENDING"}} if provider == "dashscope" else {"id": "fixture-task", "status": "pending"})
        if request.url.host == "cdn.example.test":
            return httpx.Response(200, content=audio.read_bytes())
        return httpx.Response(200, json={"output": {"task_status": "SUCCEEDED", "audio_url": "https://cdn.example.test/audio.wav"}, "usage": {"audio_seconds": .3}} if provider == "dashscope" else {"status": "complete", "audio_url": "https://cdn.example.test/audio.wav", "usage": {"audio_seconds": .3}})
    assert media.readiness("ai-music")["status"] == "ready"
    receipts = []
    result = media.execute("ai-music", input_text="Soft instrumental background", assets=[], parameters={"duration": 5, "instrumental": True}, output_dir=tmp_path / "result", context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport)), "on_usage": receipts.append})
    assert len(requests) == 3
    assert result["artifact_paths"][0].stat().st_size > 100
    assert any(receipt.get("units", {}).get("audio_seconds") == .3 for receipt in receipts)
    assert result["usage"]["aggregate"] is True


def test_suno_music_does_not_forward_primary_project_key_when_own_key_is_missing(media, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_music_model", "explicit-music-model")
    monkeypatch.setattr(settings, "dashscope_api_key", "private-project-key")
    monkeypatch.setattr(settings, "native_music_provider", "suno-compatible")
    monkeypatch.setattr(settings, "native_music_base_url", "https://music.example.test/v1")
    monkeypatch.setattr(settings, "native_music_api_key", "")
    with pytest.raises(ValueError, match="凭据|key"):
        media.execute("ai-music", input_text="Music", assets=[], parameters={}, output_dir=tmp_path / "result", context={"before_model_call": lambda: pytest.fail("No request without independent key")})


def test_voice_enrollment_sends_only_authorized_normalized_sample_and_saves_real_id(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_tts_model", "qwen3-tts-vc-2026-01-22")
    monkeypatch.setattr(settings, "dashscope_api_key", "voice-test-key")
    sample = tmp_path / "sample.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=6", str(sample)], check=True)
    calls = []
    def transport(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "qwen-voice-enrollment"
        assert payload["input"]["action"] == "create"
        assert payload["input"]["target_model"] == "qwen3-tts-vc-2026-01-22"
        encoded = payload["input"]["audio"]["data"].split(",", 1)[1]
        with wave.open(io.BytesIO(base64.b64decode(encoded)), "rb") as wav:
            assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() >= 16000
        return httpx.Response(200, json={"output": {"voice": "qwen-vc-project-fixture", "status": "OK"}, "usage": {"count": 1}, "request_id": "enroll-fixture"})
    receipts = []
    result = media.execute("voice-clone", input_text="", assets=[{"path": sample, "kind": "audio", "name": "sample.wav"}], parameters={"op": "enroll", "name": "project"}, output_dir=tmp_path / "result", context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport)), "on_usage": receipts.append})
    assert len(calls) == 1
    record = json.loads(result["artifact_paths"][0].read_text())
    assert record["voice_id"] == "qwen-vc-project-fixture" and record["status"] == "OK"
    assert "voice-test-key" not in result["artifact_paths"][0].read_text()
    assert receipts[0]["units"]["count"] == 1


def test_conditional_media_schema_exposes_real_partial_operations(media, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_api_key", "project-key")
    for field in ("dashscope_image_model", "dashscope_video_model", "dashscope_tts_model"):
        monkeypatch.setattr(settings, field, "")
    assert media.readiness_for("short-drama", {"op": "generate"})["status"] == "setup"
    assert media.readiness_for("short-drama", {"op": "plan"})["status"] == "ready"
    assert media.readiness_for("short-drama", {"op": "assemble"})["status"] == "ready"
    schema = media.definition_for("short-drama", {"op": "assemble"})
    assert schema["assets_required"] is True and schema["input_required"] is False
    assert schema["requires_model_authorization"] is False
    segments = [{"start": 0, "end": 1, "text": "Actual cue"}]
    assert media.readiness_for("auto-subtitle", {"segments": segments})["status"] == "ready"
    assert media.definition_for("auto-subtitle", {"segments": segments})["requires_model_authorization"] is False
    assert media.definition_for("voice-clone", {"op": "enroll"})["input_required"] is False


def test_native_paper_extraction_is_real_local_pdf_io_without_model(media, tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    pdf = tmp_path / "paper.pdf"
    canvas = Canvas(str(pdf)); canvas.drawString(40, 700, "A real paper result is 12 percent."); canvas.save()
    assert {"xhs-note-creator", "paper-explainer"} <= media.NATIVE_MEDIA_SKILLS
    assert media.definition_for("paper-explainer", {"op": "extract"})["requires_model_authorization"] is False
    result = media.execute("paper-explainer", input_text="", assets=[{"path": pdf, "kind": "document", "name": "paper.pdf"}], parameters={"op": "extract"}, output_dir=tmp_path / "result", context={"chat": lambda *_a, **_k: pytest.fail("PDF extraction must be local")})
    assert any("12 percent" in path.read_text() for path in result["artifact_paths"] if path.suffix in {".txt", ".json"})


def test_operation_schema_exposes_actual_processing_controls(media):
    speed = {field["key"]: field for field in media.definition_for("video-editing", {"op": "speed"})["parameters"]}
    assert speed["op"]["type"] == "select"
    assert speed["factor"]["type"] == "number"
    crop = {field["key"]: field for field in media.definition_for("image-editing", {"op": "crop"})["parameters"]}
    assert crop["box"]["format"] == "json"
    extract = {field["key"] for field in media.definition_for("paper-explainer", {"op": "extract"})["parameters"]}
    assert "op" in extract and "voice" not in extract and "format" not in extract


def test_beat_sync_contract_distinguishes_true_detection_from_declared_base_modes(media):
    schema=media.definition_for('beat-sync-video',{})
    mode=next(field for field in schema['parameters'] if field['key']=='beat_mode')
    assert mode['type']=='select' and mode['default']=='auto'
    assert {choice['value'] for choice in mode['options']}=={'auto','explicit','equal-interval'}
    assert 'beat_mode' in schema['accepted_parameters']


def test_explicit_uniform_beat_mode_has_numeric_interval_in_actual_api_contract(media):
    from app.native_skills import _parameters
    p={'beat_mode':'equal-interval','fallback_interval':.4,'every':1}
    schema=media.definition_for('beat-sync-video',p)
    fields={field['key']:field for field in schema['parameters']}
    assert fields['fallback_interval']['type']=='number'
    assert fields['every']['integer'] is True and fields['every']['min']==1
    parsed=_parameters(SimpleNamespace(parameters=p,account_id=None),schema)
    assert all(parsed[key]==value for key,value in p.items())


def test_legacy_user_beats_remain_manual_base_in_the_actual_api_defaults(media):
    from app.native_skills import _parameters
    p={'beats':[0,.5,1],'every':1}
    schema=media.definition_for('beat-sync-video',p)
    parsed=_parameters(SimpleNamespace(parameters=p,account_id=None),schema)
    assert parsed['beat_mode']=='explicit'
    assert parsed['beats']==p['beats']


@pytest.mark.parametrize('beats,expected',[('[]','auto'),('[0,0.5,1]','explicit')])
def test_legacy_json_beat_field_only_selects_manual_mode_when_nonempty(media, beats, expected):
    from app.native_skills import _parameters
    p={'beats':beats}
    schema=media.definition_for('beat-sync-video',p)
    parsed=_parameters(SimpleNamespace(parameters=p,account_id=None),schema)
    assert parsed['beat_mode']==expected


def test_uploaded_playlist_cannot_read_an_unselected_local_file(media, tmp_path):
    # A demuxer can otherwise follow references beyond the authorized asset list.
    private = tmp_path / "unselected.png"
    Image.new("RGB", (24, 24), "red").save(private)
    uploaded = tmp_path / "uploaded.mp4"
    uploaded.write_text("ffconcat version 1.0\nfile 'unselected.png'\nduration 1\n")
    with pytest.raises(ValueError):
        media.execute("video-editing", input_text="", assets=[{"path": uploaded, "kind": "video", "name": "uploaded.mp4"}], parameters={"op": "info"}, output_dir=tmp_path / "result", context={})


def test_plain_text_asr_does_not_require_or_invent_timed_cues(media, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_api_key", "asr-test-key")
    monkeypatch.setattr(settings, "native_whisper_model_path", None)
    path = tmp_path / "source.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.3", str(path)], check=True)
    assert media.readiness_for("auto-subtitle", {"format": "txt"})["status"] == "ready"
    schema = media.definition_for("auto-subtitle", {"format": "txt"})
    assert schema["requires_model_authorization"] is True
    result = media.execute("auto-subtitle", input_text="", assets=[{"path": path, "kind": "audio", "name": "source.wav"}], parameters={"format": "txt"}, output_dir=tmp_path / "result", context={"cloud_chat": lambda *_a, **_k: {"choices": [{"message": {"content": "Actual plain transcript"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}})
    assert result["artifact_paths"][0].read_text() == "Actual plain transcript\n"
    assert "时间戳" in result["response"]


@pytest.mark.parametrize("model", ["wan2.6-t2i", "wan2.7-image-pro"])
def test_current_wan_image_protocol_uses_official_message_endpoint(media, tmp_path, monkeypatch, model):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_image_model", model)
    monkeypatch.setattr(settings, "dashscope_api_key", "fixture-key")
    payloads = []
    data = io.BytesIO(); Image.new("RGB", (16, 16), "red").save(data, format="PNG")
    def transport(request):
        assert request.url.path.endswith("/services/aigc/multimodal-generation/generation")
        payload = json.loads(request.content); payloads.append(payload)
        assert payload["input"]["messages"][0]["content"][-1]["text"] == "An actual room"
        return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()}]}}]}})
    result = media.execute("ai-image-gen", input_text="An actual room", assets=[], parameters={"size": "1024x1024"}, output_dir=tmp_path / "result", context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport))})
    assert payloads and result["artifact_paths"][0].is_file()


def test_current_wan_i2v_uses_media_reference_protocol_and_real_video(media, tmp_path, monkeypatch):
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "dashscope_video_model", "wan2.7-i2v-2026-04-25")
    monkeypatch.setattr(settings, "dashscope_api_key", "fixture-key")
    image = tmp_path / "reference.png"; Image.new("RGB", (256, 256), "white").save(image)
    video = tmp_path / "supplier.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:duration=0.3", "-c:v", "libx264", str(video)], check=True)
    def transport(request):
        payload = json.loads(request.content)
        assert payload["input"]["media"][0]["type"] == "first_frame"
        assert payload["input"]["media"][0]["url"].startswith("data:image/png;base64,")
        assert "img_url" not in payload["input"]
        return httpx.Response(200, json={"output": {"video_url": "data:video/mp4;base64," + base64.b64encode(video.read_bytes()).decode()}})
    result = media.execute("ai-video-gen", input_text="The room changes", assets=[{"path": image, "name": "reference.png", "kind": "image"}], parameters={"size": "1024x1024", "duration": 5}, output_dir=tmp_path / "result", context={"media_http_client": httpx.Client(transport=httpx.MockTransport(transport))})
    assert result["artifact_paths"][0].stat().st_size > 100


def test_source_video_basic_ops_and_controls_are_publicly_registered(media):
    options = {option["value"] for field in media.definition("video-editing")["parameters"] if field["key"] == "op" for option in field["options"]}
    assert {"cut", "concat", "speed", "silence-cut", "mute-cut", "text", "aspect", "frame", "gif", "compress", "bgm", "watermark", "info"} <= options
    for op, keys in [("silence-cut", {"noise", "min_silence", "pad"}), ("gif", {"start", "duration", "fps", "width"}), ("bgm", {"music_index", "music_volume", "voice_volume", "replace_audio"})]:
        controls = {field["key"] for field in media.definition_for("video-editing", {"op": op})["parameters"]}
        assert keys <= controls


@pytest.mark.parametrize("operation,parameters,expected_suffix", [
    ("convert", {"format": "webp"}, ".webp"),
    ("compress", {"format": "jpg", "quality": 60, "max_kb": 20}, ".jpg"),
    ("watermark", {"text": "A", "size": 12, "margin": 2}, ".png"),
    ("info", {}, ".json"),
])
def test_remaining_source_image_basic_ops_produce_actual_files(media, image_asset, tmp_path, operation, parameters, expected_suffix):
    result = media.execute("image-editing", input_text="", assets=[image_asset], parameters={"op": operation, **parameters}, output_dir=tmp_path / "result", context={})
    output = result["artifact_paths"][0]
    assert output.suffix == expected_suffix and output.stat().st_size > 0
    if operation == "info":
        assert json.loads(output.read_text())["width"] == 80
    else:
        with Image.open(output) as image:
            assert image.size == (80, 40)


def test_source_image_collage_uses_every_selected_picture(media, image_asset, tmp_path):
    second = tmp_path / "second.png"; Image.new("RGB", (80, 40), "green").save(second)
    result = media.execute("image-editing", input_text="", assets=[image_asset, {"path": second, "kind": "image", "name": "second.png"}], parameters={"op": "collage", "gap": 10}, output_dir=tmp_path / "result", context={})
    with Image.open(result["artifact_paths"][0]) as image:
        assert image.size == (170, 40) and image.convert("RGB").getpixel((150, 20)) == (0, 128, 0)
