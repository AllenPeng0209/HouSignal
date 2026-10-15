"""Operator search reuses the explicit operating account without copying cookies."""
from datetime import datetime,timezone
from types import SimpleNamespace
from test_discovery import discovery_client,discovery_http,execute_search,search


def test_operator_search_uses_verified_scoped_account_and_server_owned_generation(discovery_client,discovery_http,monkeypatch):
    from app import discovery,douyin_discovery
    import app.platform_runtime
    client,factory,_,_=discovery_client
    sid='a'*64
    monkeypatch.setattr(app.platform_runtime.runtime,'login_status',lambda *args:
        {'state':'success','loggedIn':True,'verified':True,'session_id':sid})
    now=datetime.now(timezone.utc).isoformat()
    item=discovery._candidate('douyin','7559057198329981234','https://www.douyin.com/video/7559057198329981234','住宅户型实拍',now)
    item.update(published_at=now,metrics={'views':None,'likes':2000,'comments':0,'saves':None,'shares':None},metrics_observed_at=now,metrics_source='douyin_public_search')
    called=[]
    def read(tenant,pid,request,cancelled=None,expected_session_id=None):
        called.append((tenant,pid,request.account_scope,expected_session_id))
        return [item],{'platform':'douyin','adapter':'douyin_operating_public_browser','status':'partial','reason':'真实首屏公开来源','session_id':sid,'account_scope':'operations'}
    monkeypatch.setattr(douyin_discovery,'search_operating_douyin',read,raising=False)
    success,job=execute_search(client,factory,platforms=['douyin'],account_scope='operations')
    assert success,job.error
    assert called==[('t','p','operations',sid)]
    assert job.payload['operating_session_id']==sid
    result=job.result['discovery_search']
    assert result['items'][0]['qualification']=='verified_high_engagement'
    assert result['sources'][0]['session_id']==sid
    assert discovery_http['posted']==[]


def test_unconnected_operator_search_reports_login_without_opening_another_profile(discovery_client,discovery_http,monkeypatch):
    from app import douyin_discovery
    import app.platform_runtime
    client,factory,_,_=discovery_client
    monkeypatch.setattr(app.platform_runtime.runtime,'login_status',lambda *args:
        {'state':'unknown','loggedIn':False,'verified':False,'session_id':''})
    def forbidden(*args,**kwargs):
        raise AssertionError('must not search a later or unrelated account')
    monkeypatch.setattr(douyin_discovery,'search_operating_douyin',forbidden,raising=False)
    success,job=execute_search(client,factory,platforms=['douyin'],account_scope='operations')
    assert success,job.error
    assert job.result['discovery_search']['items']==[]
    assert job.result['discovery_search']['sources'][0]['status']=='login_required'
    assert discovery_http['posted']==[]
    assert search(client,platforms=['douyin'],account_scope='operations',operating_session_id='b'*64).status_code==422


def test_existing_but_unconfirmed_operator_session_does_not_claim_logout_or_search_success(discovery_client,discovery_http,monkeypatch):
    from app import douyin_discovery
    import app.platform_runtime
    client,factory,_,_=discovery_client
    monkeypatch.setattr(app.platform_runtime.runtime,'login_status',lambda *args:
        {'state':'unknown','loggedIn':False,'verified':False,'session_id':'a'*64,
         'message':'账号状态暂未确认，页面加载或认证控件未能核对，请稍后重试'})
    monkeypatch.setattr(douyin_discovery,'search_operating_douyin',lambda *args,**kwargs:
        (_ for _ in ()).throw(AssertionError('unconfirmed identity cannot authorize browser search')))
    success,job=execute_search(client,factory,platforms=['douyin'],account_scope='operations')
    assert success,job.error
    source=job.result['discovery_search']['sources'][0]
    assert source['status']=='partial'
    assert source['search_attempted'] is False
    assert '会话待核验' in source['reason'] and '尚未读取' in source['reason']
    assert '已执行' not in source['reason'] and '完成登录' not in source['reason']
    assert job.payload['operating_session_id'] is None
    assert job.result['discovery_search']['items']==[] and discovery_http['posted']==[]
