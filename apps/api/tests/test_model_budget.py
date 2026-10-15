"""Daily quotas are optional; usage receipts survive unlimited admission."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.db import Base
from app.core_models import User,Tenant,Membership,Property
from app.agent_models import Conversation,ModelUsage
from app.agent import create_message,MessageInput
from app.config import settings
from types import SimpleNamespace
import app.agent as agent_module
from app.security import context_for_user
import app.business_models
import app.job_models


@pytest.mark.parametrize(('environment','quota','used','accepted'),[
    ('development',0,3_000_000,True),('production',0,3_000_000,True),
    ('development',200000,150000,True),('development',200000,200000,False),
    ('production',1000,999,True),('production',1000,1000,False),
])
def test_optional_daily_budget_keeps_usage_and_explicit_quotas(environment,quota,used,accepted,monkeypatch):
    monkeypatch.setattr(settings,'app_env',environment)
    monkeypatch.setattr(settings,'development_token_budget',quota)
    monkeypatch.setattr(settings,'daily_token_budget',quota)
    monkeypatch.setattr(agent_module.provider,'settings',SimpleNamespace(app_env=environment,development_token_budget=quota,daily_token_budget=quota))
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        db.add_all([Tenant(id='t',name='测试'),User(id='u',name='测试',email='test@example.test',password_hash='unused')]);db.flush()
        db.add_all([Membership(user_id='u',tenant_id='t',role='admin'),Property(id='p',tenant_id='t',name='测试',city='上海')]);db.flush()
        db.add_all([Conversation(id='c',tenant_id='t',property_id='p',user_id='u'),ModelUsage(id='usage',tenant_id='t',property_id='p',job_id='earlier',model='test',input_tokens=used,output_tokens=0)]);db.commit()
        ctx=context_for_user(db,'u','t')
        if accepted:
            assert create_message('c',MessageInput(text='查看当前项目'),ctx,db)['job_id']
        else:
            with pytest.raises(HTTPException) as error: create_message('c',MessageInput(text='查看当前项目'),ctx,db)
            assert error.value.status_code==429
        assert db.get(ModelUsage,'usage').input_tokens==used
    engine.dispose()
