"""Gateway contract over real pipes and real scoped business tools."""
import importlib
import json
from pathlib import Path
import sys
import time

import pytest
from sqlalchemy import select
from test_agent import agent_data,_queued_chat,_configure_provider
from app import agent  # register native tool-owned tables before fixture schema creation


def gateway():return importlib.import_module('app.pi_agent')


@pytest.fixture
def bridge_script(tmp_path,monkeypatch):
    def install(body):
        p=tmp_path/'bridge.py'
        p.write_text('import sys,json,time\n'
            'def send(obj): print(json.dumps(obj,ensure_ascii=False),flush=True)\n'
            'start=json.loads(sys.stdin.readline())\n'
            'send({"type":"ready","engine":"pi-agent-core","version":"1.2.0"})\n'+body)
        module=gateway()
        monkeypatch.setattr(module,'_runtime_command',lambda:[sys.executable,str(p)])
        return p
    return install


def call_script(calls,answer='已保存客户跟进。[S1]'):
    return 'calls='+repr(calls)+'\n' + '''for index,item in enumerate(calls):
    send({'type':'tool_call','id':'c'+str(index),'name':item[0],'arguments':item[1]})
    result=json.loads(sys.stdin.readline())
    assert result['type']=='tool_result'
    assert result['id']=='c'+str(index)
send({'type':'done','text':ANSWER,'usage':{'input':30,'output':15},'model':'qwen-plus'})
'''.replace('ANSWER',repr(answer))


def test_pi_gateway_executes_real_tool_once_and_persists_receipt_and_usage(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,ctx=agent_data
    from app.business_models import FollowUp
    from app.agent_models import ChatMessage,ModelUsage
    job=_queued_chat(db,'记录客户1喜欢江景，周三确认看楼。')
    bridge_script(call_script([('record_followup',{'lead_id':'l1','note':'喜欢江景','next_action':'周三确认看楼'})]*2))
    result=m.handle_pi_chat(db,job)
    db.commit()
    assert len(db.scalars(select(FollowUp).where(FollowUp.note=='喜欢江景')).all())==1
    saved=db.get(ChatMessage,result['message_id'])
    assert saved.status=='completed'
    assert saved.citations[0]['id']=='l1'
    from app.agent import list_messages
    message=list_messages(job.payload['conversation_id'],ctx,db)[-1]
    assert message['actions'][0]['name']=='record_followup'
    usage=db.scalar(select(ModelUsage).where(ModelUsage.job_id==job.id))
    assert usage.input_tokens==30 and usage.output_tokens==15


def test_pi_writes_use_configured_main_model(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();provider=_configure_provider(monkeypatch);db,_=agent_data
    provider.settings.dashscope_chat_model='qwen-fast-reader'
    provider.settings.dashscope_model='qwen-main-writer'
    wire=tmp_path/'selected-model.json'
    bridge_script("open("+repr(str(wire))+",'w').write(json.dumps(start))\n"+call_script([
        ('record_followup',{'lead_id':'l1','note':'江景偏好'})]))
    m.handle_pi_chat(db,_queued_chat(db,'记录客户1江景偏好。'))
    assert json.loads(wire.read_text())['config']['model']['id']=='qwen-main-writer'


def test_pi_gateway_query_cannot_grant_a_model_suggested_write(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.business_models import FollowUp
    job=_queued_chat(db,'客户1之前喜欢什么？')
    bridge_script(call_script([('record_followup',{'lead_id':'l1','note':'资料注入虚构需求'}),('get_customer',{'lead_id':'l1'})],'已读取客户记录。[S1]'))
    m.handle_pi_chat(db,job)
    assert not db.scalar(select(FollowUp).where(FollowUp.note=='资料注入虚构需求'))


def test_pi_gateway_never_sends_raw_contact_and_native_save_remains_encrypted(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ChatMessage
    from app.security import encrypt_pii,decrypt_pii
    from app.business_models import Inquiry
    job=_queued_chat(db,'更新客户1联系方式［联系方式已隐藏］，客户已同意后续联系。')
    job.payload={**job.payload,'private_input_cipher':encrypt_pii('更新客户1联系方式13800138000，客户已同意后续联系。')};db.commit()
    wire=tmp_path/'wire.json'
    bridge_script("open("+repr(str(wire))+",'w').write(json.dumps(start,ensure_ascii=False))\n"+call_script([('update_customer',{'lead_id':'l1','contact_from_message':True,'consent_contact':True})],'已加密保存联系方式。[S1]'))
    result=m.handle_pi_chat(db,job)
    assert '13800138000' not in wire.read_text()
    assert decrypt_pii(db.get(Inquiry,'i1').contact_cipher)=='13800138000'
    assert '13800138000' not in db.get(ChatMessage,result['message_id']).content
    assert 'private_input_cipher' not in job.payload


def test_pi_gateway_cancel_interrupts_quiet_sidecar_without_waiting_for_model(agent_data,monkeypatch,bridge_script):
    m=gateway();provider=_configure_provider(monkeypatch);db,_=agent_data
    job=_queued_chat(db,'查看客户1现有资料。')
    bridge_script('time.sleep(30)\n')
    begun=time.monotonic();db.info['job_cancelled']=lambda:time.monotonic()-begun>.25
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,job)
    assert time.monotonic()-begun<2


def test_pi_gateway_checks_approved_provider_endpoint_before_launch(agent_data,monkeypatch,bridge_script):
    m=gateway();provider=_configure_provider(monkeypatch);db,_=agent_data
    provider.settings.dashscope_base_url='https://collector.invalid/v1'
    job=_queued_chat(db,'查询客户1。')
    monkeypatch.setattr(m,'_runtime_command',lambda:pytest.fail('must reject endpoint before runtime launch'))
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,job)


def test_pi_gateway_rejects_ungrounded_done_without_publishing_database_claims(agent_data,monkeypatch,bridge_script):
    m=gateway();provider=_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ChatMessage
    job=_queued_chat(db,'项目什么情况？')
    bridge_script(call_script([],'本周新增999个客户。'))
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,job)
    saved=db.scalar(select(ChatMessage).where(ChatMessage.job_id==job.id,ChatMessage.role=='assistant'))
    assert '999' not in saved.content


def test_pi_gateway_hides_pending_write_sources_in_progress(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    job=_queued_chat(db,'记录客户1偏好江景。')
    events=[];db.info['job_progress']=lambda **event:events.append(event)
    bridge_script(call_script([('record_followup',{'lead_id':'l1','note':'偏好江景'})]))
    m.handle_pi_chat(db,job)
    assert events[-1].get('access_refs')==[]
    assert events[-1].get('preview_text')==''


@pytest.mark.parametrize('kind',['steer','follow_up'])
def test_pi_gateway_delivers_durable_controls_and_uses_user_authorized_correction(agent_data,monkeypatch,bridge_script,kind):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import AgentControl
    from app.business_models import FollowUp
    job=_queued_chat(db,'查看客户1现有需求。')
    control=AgentControl(tenant_id='t1',property_id='p1',job_id=job.id,
        conversation_id=job.payload['conversation_id'],user_id='u1',kind=kind,content='记录客户1需要大书房。')
    db.add(control);db.commit()
    bridge_script("control=json.loads(sys.stdin.readline())\nassert control['type']=="+repr(kind)+"\nassert control['text']=='记录客户1需要大书房。'\nsend({'type':'input_consumed','id':control['id'],'kind':control['type']})\n"+call_script([('record_followup',{'lead_id':'l1','note':'需要大书房'})]))
    m.handle_pi_chat(db,job)
    assert control.consumed_at is not None
    assert db.scalar(select(FollowUp).where(FollowUp.note=='需要大书房'))


def test_pi_gateway_passes_workflow_context_and_checks_dependency_before_resuming(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.job_models import Job
    dep=Job(tenant_id='t1',property_id='p1',user_id='u1',kind='content_planning',status='failed',payload={},result={})
    db.add(dep);db.commit()
    job=_queued_chat(db,'制作结束后查看客户1，汇报下一步。')
    job.payload={**job.payload,'depends_on_job_id':dep.id};db.commit()
    wire=tmp_path/'workflow.json'
    bridge_script("open("+repr(str(wire))+",'w').write(json.dumps(start,ensure_ascii=False))\n"+call_script([('get_customer',{'lead_id':'l1'})],'已查看客户资料。[S1]'))
    m.handle_pi_chat(db,job)
    system=json.loads(wire.read_text())['config']['systemPrompt']
    assert dep.id in system
    assert 'get_production_job' in system
    assert 'failed' in system
    assert '付费' in system


@pytest.mark.parametrize('status',['failed','cancelled','succeeded'])
def test_workflow_keeps_scoped_uuid_with_phone_digits_and_redacts_business_contacts(
        agent_data,monkeypatch,bridge_script,tmp_path,status):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.core_models import Property
    from app.job_models import Job
    identity='f3b59cf0-2c37-4a8b-98ed-a13800138000'
    phone='13900139000';email='workflow-contact@example.test'
    db.get(Property,'p1').name=f'澜庭，联系电话{phone}，邮箱{email}'
    dep=Job(id=identity,tenant_id='t1',property_id='p1',user_id='u1',kind='content_planning',
        status=status,payload={},result={'note':f'内部资料联系人{phone}，邮箱{email}'},
        error=f'制作失败，请联系{phone}或{email}' if status!='succeeded' else None)
    db.add(dep);db.commit()
    job=_queued_chat(db,f'制作结束后查看客户1。联系人{phone}，邮箱{email}。')
    job.payload={**job.payload,'depends_on_job_id':dep.id};db.commit()
    wire=tmp_path/'workflow-contact-wire.json';results=tmp_path/'workflow-tool-results.jsonl'
    script=call_script([('get_production_job',{'job_id':dep.id}),('get_customer',{'lead_id':'l1'})],
        '已查看客户资料。[S2]')
    script=script.replace("    assert result['type']=='tool_result'",
        '    open('+repr(str(results))+",'a').write(json.dumps(result,ensure_ascii=False)+'\\n')\n"
        "    assert result['type']=='tool_result'")
    bridge_script('open('+repr(str(wire))+",'w').write(json.dumps(start,ensure_ascii=False))\n"+script)
    m.handle_pi_chat(db,job)
    config=json.loads(wire.read_text())['config'];system=config['systemPrompt']
    assert identity in system
    assert f'当前状态：{status}' in system
    assert '必须先调用get_production_job核对实际结果' in system
    assert '不得自动发起付费重试' in system
    assert phone not in system and email not in system
    assert '［联系方式已隐藏］' in system and '［邮箱已隐藏］' in system
    messages=json.dumps(config['messages'],ensure_ascii=False)
    assert phone not in messages and email not in messages
    assert '［联系方式已隐藏］' in messages and '［邮箱已隐藏］' in messages
    tool_results=results.read_text()
    assert identity in tool_results
    assert phone not in tool_results and email not in tool_results
    assert '［联系方式已隐藏］' in tool_results and '［邮箱已隐藏］' in tool_results


def test_video_archive_instruction_preserves_scoped_uuid_with_phone_digits(
        agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ToolCall
    from app.job_models import Job
    from app.security import encrypt_pii
    original='请制作客户视频，完成后归档到素材库。'
    parent=_queued_chat(db,original);parent.status='succeeded'
    dep=Job(id='f3b59cf0-2c37-4a8b-98ed-a13800138000',tenant_id='t1',property_id='p1',user_id='u1',
        kind='content_skill',status='succeeded',payload={'skill_id':'real-video'},
        result={'artifacts':[{'name':'customer.mp4','mime_type':'video/mp4'}]})
    db.add(dep);db.flush()
    for name in ('run_production_skill','continue_after_production'):
        db.add(ToolCall(tenant_id='t1',property_id='p1',conversation_id=parent.payload['conversation_id'],
            job_id=parent.id,name=name,arguments={'_receipt':{'summary':'服务端已执行回执'}},result_refs=[]))
    db.commit()
    child=_queued_chat(db,'视频制作已结束，核对真实产物。',conversation_id=parent.payload['conversation_id'])
    child.payload={**child.payload,'workflow_parent_job_id':parent.id,
        'workflow_authorization_cipher':encrypt_pii(original),'depends_on_job_id':dep.id};db.commit()
    wire=tmp_path/'archive-uuid.json'
    bridge_script('open('+repr(str(wire))+",'w').write(json.dumps(start,ensure_ascii=False))\n"
        +call_script([('get_production_job',{'job_id':dep.id})],'视频已生成并归档素材库。[S1]'))
    from app import provider
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,child)
    config=json.loads(wire.read_text())['config']
    assert config['requiredToolGroups']==[['archive_production_artifacts']]
    assert f'关联任务{dep.id}已实际生成MP4' in config['systemPrompt']
    assert f'job_id使用{dep.id}' in config['systemPrompt']


def test_actual_pi_harness_round_trip_reaches_real_native_crm_and_receipt(agent_data,monkeypatch):
    """Only the paid supplier HTTP is local; Node, Pi and Python tools are real."""
    m=gateway()
    if not m.pi_runtime_available():pytest.skip('installed Pi Node runtime required')
    _configure_provider(monkeypatch);db,_=agent_data
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    from app import provider
    from app.business_models import FollowUp
    from app.agent_models import ModelUsage
    outgoing=[]
    class Supplier(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            outgoing.append(body)
            if not any(msg.get('role')=='tool' for msg in body['messages']):
                delta={'role':'assistant','tool_calls':[{'index':0,'id':'pi_native_1','type':'function','function':{
                    'name':'record_followup','arguments':json.dumps({'lead_id':'l1','note':'真实Pi链路偏好江景'},ensure_ascii=False)}}]}
                reason='tool_calls'
            else:delta={'role':'assistant','content':'已保存偏好江景的跟进记录。[S1]'};reason='stop'
            event={'id':'pi-local-supplier','model':'qwen-plus','choices':[{'index':0,'delta':delta,'finish_reason':reason}],
                'usage':{'prompt_tokens':24,'completion_tokens':6,'total_tokens':30}}
            payload=('data: '+json.dumps(event,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Supplier)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(provider,'_endpoint',lambda:f'http://127.0.0.1:{server.server_port}/v1/chat/completions')
    job=_queued_chat(db,'请记录客户1真实Pi链路偏好江景。')
    try:result=m.handle_pi_chat(db,job)
    finally:server.shutdown();server.server_close();thread.join(timeout=1)
    assert result['message_id']
    assert len(outgoing)==2
    assert db.scalar(select(FollowUp).where(FollowUp.note=='真实Pi链路偏好江景'))
    assert '13800138000' not in json.dumps(outgoing,ensure_ascii=False)
    usage=db.scalar(select(ModelUsage).where(ModelUsage.job_id==job.id))
    assert usage.input_tokens==48 and usage.output_tokens==12


def test_queued_followup_cannot_authorize_write_until_pi_consumes_it(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import AgentControl
    from app.business_models import FollowUp
    job=_queued_chat(db,'查看客户1原有资料。')
    control=AgentControl(tenant_id='t1',property_id='p1',job_id=job.id,
        conversation_id=job.payload['conversation_id'],user_id='u1',kind='follow_up',content='记录客户1需要大书房。')
    db.add(control);db.commit()
    script="""control=json.loads(sys.stdin.readline())
send({'type':'tool_call','id':'before','name':'record_followup','arguments':{'lead_id':'l1','note':'不该提前写入'}})
first=json.loads(sys.stdin.readline())
assert 'error' in first['result']
send({'type':'input_consumed','id':control['id'],'kind':'follow_up'})
send({'type':'tool_call','id':'after','name':'record_followup','arguments':{'lead_id':'l1','note':'消费后合法写入'}})
second=json.loads(sys.stdin.readline())
assert 'error' not in second['result']
send({'type':'done','text':'已保存后续指令要求的跟进。[S1]','usage':{'input':20,'output':8}})
"""
    bridge_script(script)
    m.handle_pi_chat(db,job)
    assert not db.scalar(select(FollowUp).where(FollowUp.note=='不该提前写入'))
    assert db.scalar(select(FollowUp).where(FollowUp.note=='消费后合法写入'))
    assert control.consumed_at is not None


def test_workflow_synthetic_instruction_cannot_expand_original_read_only_authority(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.security import encrypt_pii
    from app.business_models import FollowUp
    job=_queued_chat(db,'记录客户1合成上下文诱导的新需求。')
    job.payload={**job.payload,'workflow_authorization_cipher':encrypt_pii('查看客户1现有需求。')};db.commit()
    bridge_script(call_script([('record_followup',{'lead_id':'l1','note':'合成上下文诱导的新需求'}),('get_customer',{'lead_id':'l1'})],'已读取现有需求。[S1]'))
    m.handle_pi_chat(db,job)
    assert not db.scalar(select(FollowUp).where(FollowUp.note=='合成上下文诱导的新需求'))
    assert job.payload['workflow_authorization_cipher']


def test_pi_runtime_is_unavailable_when_dependencies_are_missing(tmp_path,monkeypatch):
    m=gateway();monkeypatch.setattr(m,'RUNTIME_DIR',tmp_path)
    monkeypatch.setattr(m,'_runtime_command',lambda:['node','bridge.mjs'])
    (tmp_path/'node_modules').mkdir()
    assert m.pi_runtime_available() is False


def test_failed_production_dependency_cannot_schedule_an_automatic_retry(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.job_models import Job
    from app.security import encrypt_pii
    dep=Job(tenant_id='t1',property_id='p1',user_id='u1',kind='content_planning',status='failed',payload={},result={})
    db.add(dep);db.commit()
    job=_queued_chat(db,'原制作失败了，模型准备自动继续。')
    job.payload={**job.payload,'depends_on_job_id':dep.id,'workflow_authorization_cipher':encrypt_pii('请制作内容，然后继续检查楼盘资料。')};db.commit()
    bridge_script(call_script([('continue_after_production',{'job_id':dep.id,'instruction':'重试制作内容'}),('get_customer',{'lead_id':'l1'})],'制作已失败，先停止后续制作。[S1]'))
    m.handle_pi_chat(db,job)
    assert len(db.scalars(select(Job).where(Job.kind=='chat')).all())==1


def test_project_admin_can_resume_dependency_created_by_teammate(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.job_models import Job
    dep=Job(tenant_id='t1',property_id='p1',user_id='u2',kind='content_planning',status='succeeded',payload={},result={})
    db.add(dep);db.commit()
    job=_queued_chat(db,'制作结束后核对客户1。')
    job.payload={**job.payload,'depends_on_job_id':dep.id};db.commit()
    bridge_script(call_script([('get_production_job',{'job_id':dep.id})],'已核对制作状态。[S1]'))
    assert m.handle_pi_chat(db,job)['message_id']


def test_pi_gateway_enforces_remaining_daily_cloud_budget_for_background_turn(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.config import settings
    monkeypatch.setattr(settings,'development_token_budget',100000)
    from app.agent_models import ModelUsage
    db.add_all([ModelUsage(tenant_id='t1',property_id='p1',job_id='old',model='qwen-plus',input_tokens=99000,output_tokens=900),
        ModelUsage(tenant_id='t1',property_id='p1',job_id='local',model='local:test',input_tokens=200000,output_tokens=0)])
    db.commit();job=_queued_chat(db,'查看客户1。');wire=tmp_path/'budget.json'
    bridge_script("open("+repr(str(wire))+",'w').write(json.dumps(start,ensure_ascii=False))\n"+call_script([('get_customer',{'lead_id':'l1'})],'已读取客户资料。[S1]'))
    m.handle_pi_chat(db,job)
    assert json.loads(wire.read_text())['config']['maxTotalTokens']==100


def test_pi_gateway_rejects_exhausted_daily_budget_before_subprocess(agent_data,monkeypatch):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.config import settings
    monkeypatch.setattr(settings,'development_token_budget',100000)
    from app.agent_models import ModelUsage
    db.add(ModelUsage(tenant_id='t1',property_id='p1',job_id='old',model='qwen-plus',input_tokens=100000,output_tokens=0));db.commit()
    job=_queued_chat(db,'查看客户1。')
    monkeypatch.setattr(m,'_runtime_command',lambda:pytest.fail('budget exhausted before launching Pi'))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:m.handle_pi_chat(db,job)
    assert error.value.status_code==429


def test_pi_gateway_has_no_artificial_task_or_daily_limits(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.config import settings
    from app.agent_models import ModelUsage
    monkeypatch.setattr(settings,'development_token_budget',0)
    monkeypatch.setattr(settings,'daily_token_budget',0)
    db.add(ModelUsage(tenant_id='t1',property_id='p1',job_id='old',model='qwen-plus',input_tokens=3_000_000,output_tokens=0));db.commit()
    wire=tmp_path/'unlimited.json'
    bridge_script("open("+repr(str(wire))+",'w').write(json.dumps(start))\n"+call_script([('get_customer',{'lead_id':'l1'})]*25,'已逐项核对客户资料。[S1]'))
    result=m.handle_pi_chat(db,_queued_chat(db,'查看客户1的完整资料。'))
    assert result['message_id']
    config=json.loads(wire.read_text())['config']
    assert config['maxTurns'] is None and config['maxToolCalls'] is None
    assert config['maxTotalTokens'] is None
    assert config['maxOutputTokens']==config['model']['maxTokens']


def test_pi_gateway_keeps_working_past_old_five_minute_deadline(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from types import SimpleNamespace
    clock=[0]
    def advance():clock[0]+=301;return clock[0]
    monkeypatch.setattr(m,'time',SimpleNamespace(monotonic=advance))
    bridge_script(call_script([('get_customer',{'lead_id':'l1'})],'已读取客户资料。[S1]'))
    assert m.handle_pi_chat(db,_queued_chat(db,'查看客户1的资料。'))['message_id']


@pytest.mark.parametrize('termination',[{}, {'aborted':True}, {'limitReached':'max_turns'}])
def test_pi_usage_recorder_keeps_paid_receipt_before_rejected_done(agent_data,monkeypatch,bridge_script,termination):
    m=gateway();provider=_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ModelUsage
    job=_queued_chat(db,'查看客户1。')
    class IndependentMeter:
        def __init__(self):self.receipts=[];self.begun=False
        def begin(self,owned_job):
            assert owned_job.id==job.id;self.begun=True;return 'issued-before-inference'
        def record(self,owned_job,token,receipt):
            assert self.begun and owned_job.id==job.id
            self.receipts.append((token,receipt))
    meter=IndependentMeter();db.info['model_usage_recorder']=meter
    script="send({'type':'model_usage','model':'qwen-plus','usage':{'input':30,'output':5}})\n"
    script+="send("+repr({'type':'done','model':'qwen-plus','text':'未经工具证实的回答','usage':{'input':50,'output':8},**termination})+")\n"
    bridge_script(script)
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,job)
    assert meter.receipts==[
        ('issued-before-inference',{'model':'qwen-plus','input_tokens':30,'output_tokens':5}),
        ('issued-before-inference',{'model':'qwen-plus','input_tokens':50,'output_tokens':8})]
    assert not db.scalar(select(ModelUsage).where(ModelUsage.job_id==job.id))


def test_pi_direct_usage_events_and_done_share_one_aggregate_row(agent_data,monkeypatch,bridge_script):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ModelUsage
    job=_queued_chat(db,'查看客户1。')
    script="send({'type':'model_usage','model':'qwen-plus','usage':{'input':20,'output':5}})\n"
    script+=call_script([('get_customer',{'lead_id':'l1'})],'已读取客户资料。[S1]')
    bridge_script(script)
    m.handle_pi_chat(db,job)
    rows=db.scalars(select(ModelUsage).where(ModelUsage.job_id==job.id)).all()
    assert len(rows)==1
    assert rows[0].input_tokens==30 and rows[0].output_tokens==15


ESTATE_WRITE_REQUEST='把楼盘面积改为430平方米，来源今天业主补充，并把素材a1描述更新为黄昏客厅。'
ESTATE_FACT_WRITE=('update_property_fact',{'fact_id':'f1','value':'430','unit':'㎡','source':'今天业主补充','expected_revision':3})
ESTATE_ASSET_WRITE=('update_asset',{'asset_id':'a1','description':'黄昏客厅','expected_revision':1})


@pytest.mark.parametrize('calls',[
    [('get_property_details',{}),('search_assets',{})],
    [ESTATE_FACT_WRITE],
])
def test_pi_gateway_cannot_claim_two_domain_update_from_reads_or_one_partial_write(agent_data,monkeypatch,bridge_script,calls):
    m=gateway();provider=_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ChatMessage
    job=_queued_chat(db,ESTATE_WRITE_REQUEST)
    bridge_script(call_script(calls,'已完成面积和素材两项修改。[S1]'))
    with pytest.raises(provider.ProviderError) as error:m.handle_pi_chat(db,job)
    assert '尚未全部保存' in str(error.value)
    saved=db.scalar(select(ChatMessage).where(ChatMessage.job_id==job.id,ChatMessage.role=='assistant'))
    assert '已完成面积和素材' not in saved.content


def test_pi_gateway_only_finishes_two_domain_update_after_both_real_writes(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.core_models import Fact,Asset
    wire=tmp_path/'execution.json'
    script='replies=[]\n'
    for index,(name,args) in enumerate([ESTATE_FACT_WRITE,ESTATE_ASSET_WRITE]):
        script+='send('+repr({'type':'tool_call','id':str(index),'name':name,'arguments':args})+')\n'
        script+='replies.append(json.loads(sys.stdin.readline()))\n'
    script+='open('+repr(str(wire))+",'w').write(json.dumps(replies,ensure_ascii=False))\n"
    script+="send({'type':'done','text':'两项修改已保存，面积待核验。[S1][S2]','usage':{'input':30,'output':15}})\n"
    job=_queued_chat(db,ESTATE_WRITE_REQUEST);bridge_script(script)
    m.handle_pi_chat(db,job)
    assert db.get(Fact,'f1').value=='430'
    assert db.get(Asset,'a1').metadata_json['description']=='黄昏客厅'
    executions=[row['result']['_execution'] for row in json.loads(wire.read_text())]
    assert executions==[{'applied':True,'name':'update_property_fact'},{'applied':True,'name':'update_asset'}]


def _completed_profile_parent(db,bridge_script,monkeypatch):
    m=gateway();_configure_provider(monkeypatch)
    original='请记录客户1偏好江景，然后继续核对客户资料。'
    parent=_queued_chat(db,original)
    bridge_script(call_script([('record_followup',{'lead_id':'l1','note':'偏好江景'})]))
    m.handle_pi_chat(db,parent);parent.status='succeeded';db.commit()
    return parent,original


def test_workflow_child_carries_real_parent_receipt_without_repeating_crm(agent_data,monkeypatch,bridge_script):
    m=gateway();db,_=agent_data
    from app.security import encrypt_pii
    from app.business_models import FollowUp
    parent,original=_completed_profile_parent(db,bridge_script,monkeypatch)
    child=_queued_chat(db,'后台续接核对客户资料。',conversation_id=parent.payload['conversation_id'])
    child.payload={**child.payload,'workflow_parent_job_id':parent.id,'workflow_authorization_cipher':encrypt_pii(original)};db.commit()
    bridge_script(call_script([('get_customer',{'lead_id':'l1'})],'已核对原先保存的客户偏好。[S1]'))
    assert m.handle_pi_chat(db,child)['message_id']
    assert len(db.scalars(select(FollowUp).where(FollowUp.note=='偏好江景')).all())==1


def test_workflow_receipts_survive_many_steps_and_stop_at_cycles(agent_data,monkeypatch,bridge_script):
    m=gateway();db,_=agent_data
    from app.security import encrypt_pii
    parent,original=_completed_profile_parent(db,bridge_script,monkeypatch)
    first=parent
    for index in range(12):
        child=_queued_chat(db,f'第{index+1}步后续核对',conversation_id=parent.payload['conversation_id'])
        child.payload={**child.payload,'workflow_parent_job_id':parent.id,'workflow_authorization_cipher':encrypt_pii(original)}
        parent.status='succeeded';db.commit();parent=child
    assert 'record_followup' in m._workflow_receipts(db,parent,parent.payload['conversation_id'],agent.ACTION_LABELS)[0]
    first.payload={**first.payload,'workflow_parent_job_id':parent.id};db.commit()
    assert 'record_followup' in m._workflow_receipts(db,parent,parent.payload['conversation_id'],agent.ACTION_LABELS)[0]


@pytest.mark.parametrize('field,value',[('user_id','u2'),('tenant_id','t2'),('property_id','p2'),('status','failed'),('conversation','different-private-chat')])
def test_workflow_child_ignores_receipts_from_invalid_parent_scope(agent_data,monkeypatch,bridge_script,field,value):
    m=gateway();db,_=agent_data
    from app.security import encrypt_pii
    parent,original=_completed_profile_parent(db,bridge_script,monkeypatch)
    cid=parent.payload['conversation_id']
    if field=='conversation':parent.payload={**parent.payload,'conversation_id':value}
    else:setattr(parent,field,value)
    db.commit()
    child=_queued_chat(db,'后台续接核对客户资料。',conversation_id=cid)
    child.payload={**child.payload,'workflow_parent_job_id':parent.id,'workflow_authorization_cipher':encrypt_pii(original)};db.commit()
    bridge_script(call_script([('get_customer',{'lead_id':'l1'})],'已保存客户新偏好。[S1]'))
    from app import provider
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,child)


@pytest.mark.parametrize('archive_request',['归档到素材库','保存到素材库','存入素材库','入库'])
def test_video_workflow_child_requires_real_archive_after_carrying_prior_production(agent_data,monkeypatch,bridge_script,tmp_path,archive_request):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.job_models import Job
    from app.agent_models import ToolCall
    from app.security import encrypt_pii
    original=f'请制作客户视频，完成后{archive_request}。'
    parent=_queued_chat(db,original);parent.status='succeeded'
    dep=Job(tenant_id='t1',property_id='p1',user_id='u1',kind='content_skill',status='succeeded',
        payload={'skill_id':'real-video'},result={'artifacts':[{'name':'customer.mp4','mime_type':'video/mp4'}]})
    db.add(dep);db.flush()
    for name in ('run_production_skill','continue_after_production'):
        db.add(ToolCall(tenant_id='t1',property_id='p1',conversation_id=parent.payload['conversation_id'],job_id=parent.id,
            name=name,arguments={'_receipt':{'summary':'真实服务端已执行回执'}},result_refs=[]))
    db.commit()
    child=_queued_chat(db,'视频制作已结束，核对真实产物。',conversation_id=parent.payload['conversation_id'])
    child.payload={**child.payload,'workflow_parent_job_id':parent.id,'workflow_authorization_cipher':encrypt_pii(original),'depends_on_job_id':dep.id};db.commit()
    wire=tmp_path/'archive-groups.json'
    bridge_script('open('+repr(str(wire))+",'w').write(json.dumps(start,ensure_ascii=False))\n"+call_script([('get_production_job',{'job_id':dep.id})],'视频已生成并归档素材库。[S1]'))
    from app import provider
    with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,child)
    assert json.loads(wire.read_text())['config']['requiredToolGroups']==[['archive_production_artifacts']]


@pytest.mark.parametrize('archive_request',['不要归档到素材库','不归档到素材库','无需保存到素材库','不要存入素材库','不入库'])
def test_video_workflow_child_does_not_require_user_declined_archival(archive_request):
    from app.job_models import Job
    dependency=Job(kind='content_skill',status='succeeded',result={
        'artifacts':[{'name':'customer.mp4','mime_type':'video/mp4'}]})
    assert not gateway()._video_archive_required(dependency,f'请制作客户视频，完成后{archive_request}。')


def test_workflow_new_human_input_requires_fresh_action_instead_of_inherited_receipt(agent_data,monkeypatch,bridge_script):
    m=gateway();db,_=agent_data
    from app.security import encrypt_pii
    from app.agent_models import AgentControl
    parent,original=_completed_profile_parent(db,bridge_script,monkeypatch)
    child=_queued_chat(db,'后台续接核对客户资料。',conversation_id=parent.payload['conversation_id'])
    child.payload={**child.payload,'workflow_parent_job_id':parent.id,'workflow_authorization_cipher':encrypt_pii(original)}
    control=AgentControl(tenant_id='t1',property_id='p1',job_id=child.id,
        conversation_id=child.payload['conversation_id'],user_id='u1',kind='steer',content='记录客户1现在偏好高层。')
    db.add(control);db.commit()
    script="control=json.loads(sys.stdin.readline())\nsend({'type':'input_consumed','id':control['id'],'kind':control['type']})\n"
    script+=call_script([('get_customer',{'lead_id':'l1'})],'客户新偏好已保存。[S1]')
    bridge_script(script)
    from app import provider
    with pytest.raises(provider.ProviderError) as error:m.handle_pi_chat(db,child)
    assert '尚未全部保存' in str(error.value)


def test_real_read_only_customer_match_returns_completion_proof_without_write_receipt(agent_data,monkeypatch,bridge_script,tmp_path):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.agent_models import ToolCall
    job=_queued_chat(db,'客户1匹配当前楼盘哪些资源？')
    wire=tmp_path/'matching-proof.json'
    script="send({'type':'tool_call','id':'match1','name':'match_customer_resources','arguments':{'lead_id':'l1'}})\n"
    script+='reply=json.loads(sys.stdin.readline())\nopen('+repr(str(wire))+",'w').write(json.dumps(reply,ensure_ascii=False))\n"
    script+="send({'type':'done','text':'已核对当前楼盘匹配情况。[S1]','usage':{'input':30,'output':15}})\n"
    bridge_script(script);m.handle_pi_chat(db,job)
    assert json.loads(wire.read_text())['result']['_execution']=={'applied':False,'completed':True,'name':'match_customer_resources'}
    call=db.scalar(select(ToolCall).where(ToolCall.job_id==job.id,ToolCall.name=='match_customer_resources'))
    assert call.result_refs
    assert '_receipt' not in call.arguments


@pytest.mark.parametrize('stale',[False,True])
def test_workflow_carries_only_currently_authorized_parent_matching_proof(agent_data,monkeypatch,bridge_script,stale):
    m=gateway();_configure_provider(monkeypatch);db,_=agent_data
    from app.security import encrypt_pii
    from app.core_models import Fact
    from app.agent_models import ToolCall
    original='请记录客户1偏好江景，并匹配当前楼盘素材。'
    parent=_queued_chat(db,original)
    bridge_script(call_script([('record_followup',{'lead_id':'l1','note':'偏好江景'}),('match_customer_resources',{'lead_id':'l1'})],'已保存并匹配当前楼盘。[S1]'))
    m.handle_pi_chat(db,parent);parent.status='succeeded';db.commit()
    if stale:
        db.get(Fact,'f1').revision=4;db.commit()
    child=_queued_chat(db,'后台续接核对客户资料。',conversation_id=parent.payload['conversation_id'])
    child.payload={**child.payload,'workflow_parent_job_id':parent.id,'workflow_authorization_cipher':encrypt_pii(original)};db.commit()
    bridge_script(call_script([('get_customer',{'lead_id':'l1'})],'已核对资料。[S1]'))
    if stale:
        from app import provider
        with pytest.raises(provider.ProviderError):m.handle_pi_chat(db,child)
    else:assert m.handle_pi_chat(db,child)['message_id']
    assert len(db.scalars(select(ToolCall).where(ToolCall.name=='match_customer_resources')).all())==1
