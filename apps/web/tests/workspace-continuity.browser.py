"""Real Studio and App continuity with isolated HTTP fixtures; never calls model APIs."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json, subprocess, tempfile, threading, unittest
from playwright.sync_api import sync_playwright, expect
WEB=Path(__file__).resolve().parents[1]
SESSION={"user":{"id":"alice","name":"测试运营","email":"test@example.invalid"},"organization":{"id":"org-a","name":"隔离测试"},"role":"owner"}
PROJECT={"id":"project-a","name":"隔离住宅项目","city":"上海","property_type":"住宅","channel":"抖音","owner_name":"测试运营","audience":"改善家庭","sales_owner_name":"测试顾问","marketing_confirmed":True,"marketing_evidence":"测试授权","is_test":True,"completeness":{"percent":100,"completed":1,"total":1,"items":[]}}
CONTENT={"id":"version-a","series_id":"series-a","version":1,"topic":"客厅动线","script":"服务器原稿","status":"draft","channel":"抖音","cover_title":"原封面","aspect_ratio":"9:16","subtitle_style":"minimal","storyboard":[],"fact_refs":[],"created_at":"2026-10-17T00:00:00Z"}
METRICS={"published":0,"inquiries":0,"qualified":0,"appointments":0,"visits":0,"views":None,"unknown_rate":None}

class WorkspaceContinuityTests(unittest.TestCase):
 def test_edit_survives_page_navigation_and_refresh_at_the_same_working_step(self):
  source="""import React from 'react';import {createRoot} from 'react-dom/client';import App from './src/App';createRoot(document.getElementById('root')).render(<App/>);"""
  with tempfile.TemporaryDirectory(prefix='louxiang-workspace-browser-') as temporary:
   fixture=Path(temporary)/'fixture.js'
   build="import {build} from 'esbuild';await build({stdin:{contents:JSON.parse(process.argv[1]),resolveDir:process.cwd(),loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2]});"
   subprocess.run(['node','--input-type=module','-e',build,json.dumps(source),str(fixture)],cwd=WEB,check=True,capture_output=True)
   state={'content':dict(CONTENT),'tasks':[]}
   class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
     if self.path=='/fixture.js':body,kind=fixture.read_bytes(),'application/javascript'
     elif self.path.startswith('/api/'):
      kind='application/json'
      if self.path=='/api/auth/me':value=SESSION
      elif self.path=='/api/projects':value=[PROJECT,{**PROJECT,'id':'project-b','name':'第二个隔离项目'}]
      elif self.path.endswith('/tasks'):value={'timezone':'Asia/Shanghai','as_of':'2026-10-17T00:00:00Z','items':state['tasks']}
      elif self.path.endswith('/agent-changes'):value={'revision':'stable'}
      elif self.path=='/api/jobs/completed-render':value={'id':'completed-render','kind':'render_content','status':'succeeded','progress':1,'created_at':'2026-10-17T00:00:00Z','result':{'content_version_id':'version-a'}}
      elif self.path.endswith('/contents'):value=[state['content']]
      elif self.path.endswith('/overview'):value={'project':PROJECT,'metrics':METRICS,'pending_inquiries':[],'content_versions':[],'publications':[],'proposals':[]}
      elif '/reports?' in self.path:value={'metrics':{'views':None,'likes':None,'comments':None,'saves':None,'inquiries':None,'qualified':None,'window_complete':False,'observed_publications':0,'total_publications':0},'publications':[],'proposals':[],'window':'D7'}
      elif '/content-catalog' in self.path:value={'items':[],'total':0}
      else:value=[]
      body=json.dumps(value).encode()
     else:body,kind=b'<div id="root"></div><script src="/fixture.js"></script>','text/html'
     self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers()
     try:self.wfile.write(body)
     except (BrokenPipeError,ConnectionResetError):pass
   server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
   try:
    with sync_playwright() as playwright:
     browser=playwright.chromium.launch(channel='chrome',headless=True);page=browser.new_page();page.goto(f'http://127.0.0.1:{server.server_port}')
     page.get_by_role('navigation',name='主导航').get_by_role('button',name='内容工作台').click();page.get_by_role('button',name='脚本表达').click()
     script=page.get_by_role('textbox',name='脚本内容');expect(script).to_have_value('服务器原稿');script.fill('用户未提交的脚本')
     page.get_by_role('textbox',name='封面标题').fill('用户未提交的封面')
     page.get_by_role('navigation',name='主导航').get_by_role('button',name='素材库').click();page.get_by_role('navigation',name='主导航').get_by_role('button',name='内容工作台').click()
     expect(script).to_have_value('用户未提交的脚本');expect(page.get_by_role('textbox',name='封面标题')).to_have_value('用户未提交的封面')
     page.reload();expect(script).to_have_value('用户未提交的脚本');expect(page.get_by_role('textbox',name='封面标题')).to_have_value('用户未提交的封面')
     # A server edit is never silently replaced by the local draft.
     state['content']['script']='外部更新的服务器稿';page.reload();expect(page.get_by_text('服务器内容已更新',exact=False)).to_be_visible();expect(script).to_have_value('外部更新的服务器稿')
     page.get_by_role('button',name='恢复本地草稿').click();expect(script).to_have_value('用户未提交的脚本')
     # Failed-task queue entries open their exact durable task and its resulting content.
     state['tasks']=[{'id':'failed-completed-render','kind':'failed_job','title':'任务恢复回归','detail':'查看当前任务实际状态','due_at':None,'target':{'page':'studio','jobId':'completed-render','contentId':'version-a','studioStage':'visual'}}]
     page.get_by_role('navigation',name='主导航').get_by_role('button',name='工作台',exact=True).click();page.get_by_role('button',name='查看任务',exact=True).click()
     expect(page.get_by_role('dialog')).to_be_visible();page.get_by_role('button',name='在内容工作台查看').click();expect(page.get_by_role('dialog')).not_to_be_visible()
     expect(page.get_by_role('heading',name='让表达与画面对应。')).to_be_visible();page.reload();expect(page.get_by_role('dialog')).not_to_be_visible();expect(page.get_by_role('heading',name='让表达与画面对应。')).to_be_visible()
     page.get_by_role('button',name='发现',exact=False).filter(has_text='发现').first.click()
     # A project switch must restore that project's own position, without persisting the previous project's target into it.
     page.get_by_role('button',name='切换当前项目').click();page.locator('.project-choice').filter(has_text='第二个隔离项目').click();expect(page.get_by_role('heading',name='工作台',exact=True)).to_be_visible()
     page.get_by_role('button',name='切换当前项目').click();page.locator('.project-choice').filter(has_text='隔离住宅项目').click();expect(page.get_by_role('heading',name='内容灵感',exact=True)).to_be_visible()
     # Browser storage failures retain the current editor in memory and protect refresh until retried.
     page.get_by_role('button',name='脚本表达',exact=False).click()
     page.evaluate("""() => { const original=Storage.prototype.setItem; window.restoreDraftStorage=()=>{Storage.prototype.setItem=original;}; Storage.prototype.setItem=function(key,value){if(key.startsWith('housignal-workspace:'))throw new DOMException('Storage full','QuotaExceededError');return original.call(this,key,value);}; }""")
     page.get_by_role('textbox',name='脚本内容').fill('存储配额失败时的草稿')
     expect(page.get_by_role('alert').filter(has_text='浏览器未能保存草稿')).to_be_visible()
     self.assertTrue(page.evaluate("() => {const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}"))
     page.get_by_role('navigation',name='主导航').get_by_role('button',name='素材库').click();page.get_by_role('navigation',name='主导航').get_by_role('button',name='内容工作台').click()
     expect(page.get_by_role('textbox',name='脚本内容')).to_have_value('存储配额失败时的草稿')
     page.evaluate('window.restoreDraftStorage()');page.get_by_role('alert').filter(has_text='浏览器未能保存草稿').get_by_role('button',name='重试',exact=True).click()
     expect(page.get_by_role('alert').filter(has_text='浏览器未能保存草稿')).not_to_be_visible()
     self.assertFalse(page.evaluate("() => {const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}"))
     browser.close()
   finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
