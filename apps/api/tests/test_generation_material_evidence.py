"""Rendered captions are audience text and synthetic footage cannot prove capture."""
import json

import pytest

from app import provider
from app.business import handle_generate
from app.core_models import Asset
from test_business import scenario
from test_generation_duration import draft, generation_job


def reply(monkeypatch, value, seen=None):
    def chat(messages, **kwargs):
        if seen is not None:
            seen.append(json.loads(messages[-1]['content']))
        return {'model':'fixture','choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)},
                'finish_reason':'stop'}],'usage':{'prompt_tokens':40,'completion_tokens':60}}
    monkeypatch.setattr(provider,'chat',chat)


def test_generation_exposes_material_origin_and_excludes_research_only_actual_assets(scenario, monkeypatch):
    db,_,_,_,a,_=scenario
    db.add(Asset(id='research-only',tenant_id='tenant',property_id='p',name='研究用原片',kind='video',
        storage_key='research.mp4',mime_type='video/mp4',sha256='digest',byte_size=3,
        rights='authorized',rights_evidence='研究用途许可',origin_type='actual',metadata_json={'usage_scope':'research_only'}))
    db.commit();seen=[];reply(monkeypatch,draft(30),seen)
    handle_generate(db,generation_job())
    assert seen[0]['assets']==[{'id':a.id,'name':a.name,'kind':'image','description':'','origin_type':'actual'}]


@pytest.mark.parametrize('field,claim', [('script','这是现场实拍的420平方米。'),
    ('caption','真实拍摄的空间'),('cover_title','现房实景')])
def test_synthetic_selected_material_cannot_become_real_capture_claim(scenario,monkeypatch,field,claim):
    db,_,p,_,a,_=scenario;p.is_test=True;a.origin_type='test';db.commit()
    value=draft(30);value['script']='合成示意：420平方米用于软件验收。'
    if field=='caption':value['storyboard'][0]['caption']=claim
    else:value[field]=claim
    reply(monkeypatch,value)
    with pytest.raises(ValueError,match='示意素材不能声称实拍'):
        handle_generate(db,generation_job())


def test_caption_cannot_burn_camera_directions_into_viewer_text(scenario,monkeypatch):
    value=draft(30);value['storyboard'][0]['caption']='镜头收束到提问互动，字幕引导留言'
    reply(monkeypatch,value)
    with pytest.raises(ValueError,match='观众字幕'):
        handle_generate(scenario[0],generation_job())


def test_synthetic_disclaimer_and_verified_quantities_remain_valid(scenario,monkeypatch):
    db,_,p,_,a,_=scenario;p.is_test=True;a.origin_type='template';db.commit()
    value=draft(30);value['script']='合成示意，非实拍。核验面积420平方米。'
    value['cover_title']='软件验收示例';value['storyboard'][0]['caption']='合成示意 · 420平方米'
    reply(monkeypatch,value)
    assert handle_generate(db,generation_job())['content_version_id']
