"""Browser-level draft continuity using the real resource and external-sync hooks."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import tempfile
import threading
import time
import unittest

from playwright.sync_api import sync_playwright, expect

WEB = Path(__file__).resolve().parents[1]


class ExternalSyncBrowserTests(unittest.TestCase):
    def test_background_refresh_keeps_the_same_focused_input_and_user_draft(self):
        source = """
import React,{useCallback,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AppContext,useResource} from './src/hooks';
import {useExternalAgentSync} from './src/useExternalAgentSync';
function Editor({projectId}){const result=useResource(`/api/projects/${projectId}/data`);const [draft,setDraft]=useState('');if(result.loading)return <p>Loading</p>;return <><p data-testid="saved">{result.data?.value}</p><textarea aria-label="创作草稿" value={draft} onChange={e=>setDraft(e.target.value)}/></>;}
function Harness(){const[refresh,setRefresh]=useState(0),[identity,setIdentity]=useState({id:'owner'}),[projectId,setProjectId]=useState('p1');const reload=useCallback(async()=>{setIdentity({id:'owner'});setRefresh(value=>value+1);},[]);useExternalAgentSync({projectId,identityKey:identity.id,onChange:reload});return <AppContext.Provider value={{refresh,reload}}><button onClick={reload}>刷新记录</button><button onClick={()=>setProjectId('p2')}>切换项目</button><Editor projectId={projectId}/></AppContext.Provider>;}
createRoot(document.getElementById('root')).render(<Harness/>);
"""
        with tempfile.TemporaryDirectory(prefix="louxiang-external-browser-") as temporary:
            fixture = Path(temporary) / "fixture.js"
            build_script = """import {build} from 'esbuild'; const source=JSON.parse(process.argv[1]); await build({stdin:{contents:source,resolveDir:process.cwd(),loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',write:true,outfile:process.argv[2]});"""
            subprocess.run(["node", "--input-type=module", "-e", build_script, json.dumps(source), str(fixture)], cwd=WEB, check=True, capture_output=True)
            state = {"revision": "first", "value": "已保存的原始标题", "delay": 0, "reads": 0, "resource_reads": 0}

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    if self.path == "/fixture.js":
                        value, kind = fixture.read_bytes(), "application/javascript"
                    elif self.path.endswith("/agent-changes"):
                        state["reads"] += 1
                        value, kind = json.dumps({"revision": state["revision"]}).encode(), "application/json"
                    elif self.path.endswith("/data"):
                        state["resource_reads"] += 1
                        current = state["value"] if self.path == "/api/projects/p1/data" else "另一个项目的资料"
                        time.sleep(state["delay"] if self.path == "/api/projects/p1/data" else 0)
                        value, kind = json.dumps({"value": current}).encode(), "application/json"
                    else:
                        value, kind = b'<div id="root"></div><script src="/fixture.js"></script>', "text/html"
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(value)))
                    self.end_headers()
                    try:
                        self.wfile.write(value)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    expect(page.get_by_test_id("saved")).to_have_text("已保存的原始标题")
                    field = page.get_by_role("textbox", name="创作草稿")
                    field.fill("我的未保存草稿，保持光标")
                    field.focus()
                    page.evaluate("window.savedTextarea=document.querySelector('textarea')")
                    state.update(revision="second", value="外部 Agent 更新后的标题", delay=0.7)
                    expect(page.get_by_test_id("saved")).to_have_text("外部 Agent 更新后的标题", timeout=7000)
                    self.assertTrue(page.evaluate("document.querySelector('textarea')===window.savedTextarea"), "background refresh replaced the active input")
                    self.assertTrue(page.evaluate("document.activeElement===window.savedTextarea"), "background refresh stole editor focus")
                    expect(field).to_have_value("我的未保存草稿，保持光标")
                    page.wait_for_timeout(3200)
                    self.assertEqual(state["resource_reads"], 2, "identity-object refresh reset the baseline or caused a reload loop")
                    state.update(value="旧项目的迟到响应", delay=1)
                    with page.expect_request("**/api/projects/p1/data"):
                        page.get_by_role("button", name="刷新记录").click()
                    page.get_by_role("button", name="切换项目").click()
                    expect(page.get_by_test_id("saved")).to_have_text("另一个项目的资料")
                    page.wait_for_timeout(1200)
                    expect(page.get_by_test_id("saved")).to_have_text("另一个项目的资料")
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()


    def test_member_can_view_and_revoke_own_connection_without_exposing_credentials(self):
        source = """
import React,{useCallback,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AppContext} from './src/hooks';
import {ExternalAgentConnections} from './src/ExternalAgentConnections';
function Harness(){const [refresh,setRefresh]=useState(0),[message,setMessage]=useState('');const reload=useCallback(async()=>setRefresh(value=>value+1),[]);return <AppContext.Provider value={{refresh,reload,notify:setMessage,canManage:false,projects:[{id:'p1',name:'陆家嘴大宅'}]}}><ExternalAgentConnections/><p role="status">{message}</p></AppContext.Provider>;}
createRoot(document.getElementById('root')).render(<Harness/>);
"""
        with tempfile.TemporaryDirectory(prefix="louxiang-connections-browser-") as temporary:
            fixture = Path(temporary) / "fixture.js"
            build_script = """import {build} from 'esbuild'; const source=JSON.parse(process.argv[1]); await build({stdin:{contents:source,resolveDir:process.cwd(),loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',write:true,outfile:process.argv[2]});"""
            subprocess.run(["node", "--input-type=module", "-e", build_script, json.dumps(source), str(fixture)], cwd=WEB, check=True, capture_output=True)
            state = {"connections": [{"id": "owned-grant", "client_name": "ChatGPT", "scopes": ["louxiang:read", "louxiang:write"], "project_ids": ["p1"], "expires_at": "2099-11-15T00:00:00Z", "access_token": "never-render-a-credential"}], "deletes": []}

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def respond(self, value, kind="application/json"):
                    encoded = value if isinstance(value, bytes) else json.dumps(value).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

                def do_GET(self):
                    if self.path == "/fixture.js":
                        self.respond(fixture.read_bytes(), "application/javascript")
                    elif self.path == "/api/agent/connections":
                        self.respond(state["connections"])
                    else:
                        self.respond(b'<div id="root"></div><script src="/fixture.js"></script>', "text/html")

                def do_DELETE(self):
                    state["deletes"].append(self.path)
                    if self.path == "/api/agent/connections/owned-grant":
                        state["connections"] = []
                    self.respond({"status": "revoked"})

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    expect(page.get_by_text("ChatGPT", exact=True)).to_be_visible()
                    expect(page.get_by_text("项目：陆家嘴大宅")).to_be_visible()
                    self.assertNotIn("never-render-a-credential", page.content())
                    page.get_by_role("button", name="撤销连接", exact=True).click()
                    expect(page.get_by_text("尚未连接外部 Agent。连接后可在这里查看授权范围和撤销访问。")).to_be_visible()
                    expect(page.get_by_role("status")).to_have_text("外部 Agent 连接已撤销")
                    self.assertEqual(state["deletes"], ["/api/agent/connections/owned-grant"])
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()


    def test_logout_discards_late_identity_and_project_responses_even_after_another_login(self):
        source = """
import React from 'react';import {createRoot} from 'react-dom/client';import App from './src/App';createRoot(document.getElementById('root')).render(<App/>);
"""
        with tempfile.TemporaryDirectory(prefix="louxiang-app-session-browser-") as temporary:
            fixture = Path(temporary) / "fixture.js"
            build_script = r"""import {build} from 'esbuild';const source=JSON.parse(process.argv[1]);await build({stdin:{contents:source,resolveDir:process.cwd(),loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',write:true,outfile:process.argv[2],plugins:[{name:'session-observer',setup(builder){builder.onResolve({filter:/^\.\/Workbench$/},()=>({path:'workbench',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'tsx',resolveDir:process.cwd()+'/src',contents:`import {useApp} from './hooks';export function Workbench(){const app=useApp();return <><p data-testid="identity">{app.session.user.id}:{app.project.name}</p><button onClick={()=>void app.reload()}>刷新全部记录</button><button onClick={()=>void app.logout()}>退出账户</button></>;}`}));}}]});"""
            subprocess.run(["node", "--input-type=module", "-e", build_script, json.dumps(source), str(fixture)], cwd=WEB, check=True, capture_output=True)
            state = {"user": "alice", "hold": False, "pending": 0}
            release = threading.Event()

            def identity(user):
                return {"user": {"id": user, "name": user.title(), "email": f"{user}@example.test"}, "organization": {"id": f"org-{user}", "name": f"{user} team"}, "role": "owner"}

            def project(user):
                return {"id": f"p-{user}", "name": f"{user} 项目", "city": "上海", "property_type": "大平层", "owner_name": user, "audience": "家庭", "sales_owner_name": "", "channel": "B站", "marketing_confirmed": False, "marketing_evidence": "", "is_test": True, "completeness": {"percent": 0, "completed": 0, "total": 7, "items": []}}

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def respond(self, value, kind="application/json"):
                    encoded = value if isinstance(value, bytes) else json.dumps(value).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    try:
                        self.wfile.write(encoded)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                def do_GET(self):
                    captured_user = state["user"]
                    if self.path == "/fixture.js":
                        self.respond(fixture.read_bytes(), "application/javascript")
                    elif self.path in {"/api/auth/me", "/api/projects"}:
                        if state["hold"] and captured_user == "alice":
                            state["pending"] += 1
                            release.wait(15)
                        self.respond(identity(captured_user) if self.path == "/api/auth/me" else [project(captured_user)])
                    elif self.path.endswith("/agent-changes"):
                        self.respond({"revision": "unchanged"})
                    elif self.path.startswith("/api/"):
                        self.respond([])
                    else:
                        self.respond(b'<div id="root"></div><script src="/fixture.js"></script>', "text/html")

                def do_POST(self):
                    self.rfile.read(int(self.headers.get("content-length", "0")))
                    if self.path == "/api/auth/logout":
                        state["user"] = None
                        self.respond({"ok": True})
                    elif self.path == "/api/auth/login":
                        state["user"] = "bob"
                        self.respond(identity("bob"))
                    else:
                        self.respond({})

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    for new_login in (False, True):
                        state.update(user="alice", hold=False, pending=0)
                        release.clear()
                        page = browser.new_page()
                        page.goto(f"http://127.0.0.1:{server.server_port}")
                        expect(page.get_by_test_id("identity")).to_have_text("alice:alice 项目")
                        state["hold"] = True
                        page.get_by_role("button", name="刷新全部记录").click()
                        deadline = time.monotonic() + 5
                        while state["pending"] < 2 and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertEqual(state["pending"], 2)
                        page.get_by_role("button", name="退出账户").click()
                        expect(page.get_by_role("button", name="登录工作区")).to_be_visible()
                        if new_login:
                            page.get_by_placeholder("你的工作邮箱").fill("bob@example.test")
                            page.get_by_placeholder("输入密码").fill("not-a-real-password")
                            page.get_by_role("button", name="登录工作区").click()
                            expect(page.get_by_test_id("identity")).to_have_text("bob:bob 项目")
                        release.set()
                        page.wait_for_timeout(400)
                        if new_login:
                            expect(page.get_by_test_id("identity")).to_have_text("bob:bob 项目")
                            self.assertNotIn("alice 项目", page.content())
                        else:
                            expect(page.get_by_role("button", name="登录工作区")).to_be_visible()
                            expect(page.get_by_test_id("identity")).to_have_count(0)
                        page.close()
                    browser.close()
            finally:
                release.set()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
