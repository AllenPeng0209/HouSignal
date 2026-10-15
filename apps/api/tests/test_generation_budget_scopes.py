"""Configured local and cloud generation consume separate real usage budgets."""
import json

import pytest
from fastapi import HTTPException

from app import provider
from app.agent_models import ModelUsage
from app.business import handle_generate
from app.config import settings
from test_generation_duration import draft, generation_job
from test_business import scenario


@pytest.mark.parametrize(('backend','existing_model'), [('mlx','cloud-fixture'),('dashscope','local:fixture')])
def test_other_backend_usage_cannot_exhaust_this_generation_budget(scenario,monkeypatch,backend,existing_model):
    db,*_=scenario
    monkeypatch.setattr(settings,'native_text_backend',backend)
    monkeypatch.setattr(settings,'app_env','development')
    monkeypatch.setattr(settings,'development_token_budget',1000)
    monkeypatch.setattr(settings,'native_local_text_daily_token_budget',1000)
    db.add(ModelUsage(tenant_id='tenant',property_id='p',job_id='old-other-model',model=existing_model,
                      input_tokens=1500,output_tokens=0));db.commit()
    seen=[]
    def chat(messages,**kwargs):
        seen.append(True)
        return {'model':'local:fixture' if backend=='mlx' else 'cloud-fixture',
            'choices':[{'message':{'content':json.dumps(draft(30),ensure_ascii=False)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':40,'completion_tokens':60}}
    monkeypatch.setattr(provider,'chat',chat)
    assert handle_generate(db,generation_job())['content_version_id']
    assert seen==[True]


@pytest.mark.parametrize(('backend','existing_model'), [('mlx','local:fixture'),('dashscope','cloud-fixture')])
def test_this_backend_limit_still_blocks_before_calling_model(scenario,monkeypatch,backend,existing_model):
    db,*_=scenario
    monkeypatch.setattr(settings,'native_text_backend',backend)
    monkeypatch.setattr(settings,'app_env','development')
    monkeypatch.setattr(settings,'development_token_budget',1000)
    monkeypatch.setattr(settings,'native_local_text_daily_token_budget',1000)
    db.add(ModelUsage(tenant_id='tenant',property_id='p',job_id='old-same-model',model=existing_model,
                      input_tokens=1000,output_tokens=0));db.commit()
    monkeypatch.setattr(provider,'chat',lambda *args,**kwargs:pytest.fail('exhausted budget must not call a model'))
    with pytest.raises(HTTPException) as error:handle_generate(db,generation_job())
    assert error.value.status_code==429
