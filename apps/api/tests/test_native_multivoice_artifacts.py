"""Real codec delivery checks with controlled TTS responses; no service calls."""
from pathlib import Path
import base64
import hashlib
import json
import subprocess
import sys
import wave

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.content_skills import media
from app.config import settings


def generated_audio(tmp_path, name, frequency, duration, suffix):
    path=tmp_path/(name+suffix)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        f'sine=frequency={frequency}:duration={duration}',str(path)],check=True)
    return path


def decoded_pcm(path):
    return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-ac','1','-ar','24000',
        '-f','s16le','-'],check=True,capture_output=True).stdout


def assert_role_delivery(result, output_dir, lines, raw_files, sent_texts, gap):
    paths=result['artifact_paths']
    receipts=[path for path in paths if path.name=='voice-roles.json']
    assert receipts, 'Every role needs a voice/text/audio binding receipt'
    receipt=json.loads(receipts[0].read_text())
    assert receipt['human_review_required'] is True
    assert receipt['voice_identity_verified'] is False
    assert len(receipt['roles'])==len(lines)
    delivered=[]
    for index,row in enumerate(receipt['roles']):
        assert row['index']==index and row['voice']==lines[index]['voice']
        assert row['text_hash']==hashlib.sha256(sent_texts[index].encode()).hexdigest()
        path=output_dir/row['audio_file']
        assert path in paths and path.parent==output_dir
        assert path.read_bytes()==raw_files[index].read_bytes()
        assert row['audio_hash']==hashlib.sha256(path.read_bytes()).hexdigest()
        assert row['byte_size']==path.stat().st_size
        delivered.append(decoded_pcm(path))
    merged=next(path for path in paths if path.name=='voice.wav')
    with wave.open(str(merged)) as sound:
        assert sound.getframerate()==24000 and sound.getnchannels()==1
        combined=sound.readframes(sound.getnframes())
    assert combined==delivered[0]+b'\0\0'*round(gap*24000)+delivered[1]
    assert receipt['merged_audio_hash']==hashlib.sha256(merged.read_bytes()).hexdigest()
    assert receipt['gap_seconds']==gap
    assert 'Authorization' not in receipts[0].read_text() and 'fixture-secret-key' not in receipts[0].read_text()


def test_edge_multivoice_retains_original_role_audio_and_bound_receipts(tmp_path):
    raw=[generated_audio(tmp_path,'first',440,.35,'.mp3'),generated_audio(tmp_path,'second',880,.55,'.mp3')]
    lines=[{'voice':'zh-CN-XiaoxiaoNeural','text':'第一段合成示意。'},
        {'voice':'zh-CN-YunxiNeural','text':'第二段合成示意。'}]
    calls=[]
    class Communicator:
        def __init__(self,text,*,voice,**_kwargs):
            self.index=len(calls);calls.append({'voice':voice,'text':text})
        async def stream(self):
            yield {'type':'audio','data':raw[self.index].read_bytes()}
            yield {'type':'WordBoundary','offset':1000000,'duration':1000000,'text':lines[self.index]['text']}
    output=tmp_path/'out'
    result=media.execute('multi-voice-dubbing',input_text='',assets=[],parameters={'engine':'edge','gap':.25,'lines':lines},
        output_dir=output,context={'edge_tts_factory':Communicator})
    assert calls==lines
    assert_role_delivery(result,output,lines,raw,[line['text'] for line in lines],.25)


def test_cloud_multivoice_retains_actual_returned_audio_without_additional_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(settings,'dashscope_api_key','fixture-secret-key')
    monkeypatch.setattr(settings,'dashscope_tts_model','qwen3-tts-flash')
    monkeypatch.setattr(settings,'dashscope_base_url','https://dashscope.aliyuncs.com/compatible-mode/v1')
    raw=[generated_audio(tmp_path,'first',440,.35,'.wav'),generated_audio(tmp_path,'second',880,.55,'.wav')]
    lines=[{'voice':'Cherry','text':'  First original sentence.  '},
        {'voice':'Serena','text':'Second original sentence.'}]
    calls=[]
    def transport(request):
        assert request.method=='POST'
        payload=json.loads(request.content);index=len(calls);calls.append(payload['input'])
        return httpx.Response(200,json={'output':{'audio':{'data':'data:audio/wav;base64,'+base64.b64encode(raw[index].read_bytes()).decode()}},
            'usage':{'characters':len(payload['input']['text'])}})
    output=tmp_path/'out'
    result=media.execute('multi-voice-dubbing',input_text='',assets=[],parameters={'engine':'cloud','gap':.25,'lines':lines},
        output_dir=output,context={'media_http_client':httpx.Client(transport=httpx.MockTransport(transport))})
    assert len(calls)==2
    assert [row['voice'] for row in calls]==[row['voice'] for row in lines]
    assert_role_delivery(result,output,lines,raw,[line['text'].strip() for line in lines],.25)
