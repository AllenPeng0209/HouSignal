"""Portable CLI contract: exercise real loopback HTTP without cloud credentials."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "integrations/louxiang-agent/src/louxiang_agent/cli.py"


@pytest.fixture
def cli():
    assert CLI.is_file(), "The portable CLI implementation is not present yet"
    spec = importlib.util.spec_from_file_location("louxiang_cli_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def oauth_server():
    state = {"requests": [], "refresh": 0, "redirect_token": False, "bad_issuer": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, body, status=200):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            state["requests"].append(("GET", self.path, self.headers.get("Authorization"), None))
            if self.path == "/.well-known/oauth-authorization-server":
                self.respond({"issuer": origin if not state["bad_issuer"] else "https://other.invalid",
                    "authorization_endpoint": origin + "/api/agent/oauth/authorize",
                    "registration_endpoint": origin + "/api/agent/oauth/register",
                    "token_endpoint": origin + "/api/agent/oauth/token",
                    "revocation_endpoint": origin + "/api/agent/oauth/revoke",
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True})
            elif self.path == "/api/agent/tools":
                self.respond({"tools": [{"name": "louxiang_list_projects"}]})
            elif self.path == "/api/agent/whoami":
                self.respond({"connections": [{"id": "grant-1", "project_ids": ["p-1"]}],
                              "access_token": "must-not-print", "client_secret": "secret-echo"})
            elif self.path == "/gateway":
                self.send_response(307)
                self.send_header("Location", origin + "/api/agent/tools")
                self.end_headers()
            elif self.path == "/api/agent/projects/p-1/jobs/j-1/artifacts/short-video.mp4":
                if state.get("download_redirect"):
                    self.send_response(307)
                    self.send_header("Location", origin + "/trap")
                    self.end_headers()
                    return
                payload = b"original-video-bytes"
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.respond({"error": "not_found"}, 404)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw) if self.headers.get("Content-Type") == "application/json" else dict(urllib.parse.parse_qsl(raw.decode()))
            state["requests"].append(("POST", self.path, self.headers.get("Authorization"), body))
            if self.path == "/api/agent/oauth/register":
                state["registration"] = body
                self.respond({"client_id": "public-cli", "token_endpoint_auth_method": "none"}, 201)
            elif self.path == "/api/agent/oauth/token":
                if state["redirect_token"]:
                    self.send_response(307)
                    self.send_header("Location", origin + "/trap")
                    self.end_headers()
                    return
                if body["grant_type"] == "authorization_code":
                    assert body["code"] == "auth-code"
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).decode().rstrip("=")
                    assert challenge == state["challenge"]
                else:
                    time.sleep(state.get("refresh_delay", 0))
                    state["refresh"] += 1
                    assert body["refresh_token"] == "refresh-private-1"
                token_scope = state.get("token_scope", "louxiang:read louxiang:write louxiang:production offline_access")
                response = {"access_token": "access-private-" + str(1 + state["refresh"]),
                            "token_type": "Bearer", "expires_in": 600,
                            "resource": state.get("token_resource", origin + "/mcp"), "scope": token_scope}
                if "offline_access" in token_scope.split():
                    response["refresh_token"] = "refresh-private-" + str(1 + state["refresh"])
                self.respond(response)
            elif self.path == "/api/agent/tools/call":
                self.respond({"result": {"project_id": body["arguments"]["project_id"], "saved": True}})
            elif self.path == "/api/agent/oauth/revoke":
                self.respond({})
            else:
                self.respond({"error": "invalid_request"}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = "http://127.0.0.1:" + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def browser_callback(state, *, bad_state=False, bad_issuer=False):
    def open_url(url):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert set(query["scope"][0].split()) == {"louxiang:read", "louxiang:write", "louxiang:production", "offline_access"}
        state["challenge"] = query["code_challenge"][0]
        callback = query["redirect_uri"][0]
        issuer = url.split("/api/agent/oauth/authorize")[0]
        params = {"code": "auth-code", "state": "wrong" if bad_state else query["state"][0],
                  "iss": "https://other.invalid" if bad_issuer else issuer}
        try:
            with urllib.request.urlopen(callback + "?" + urllib.parse.urlencode(params), timeout=3) as response:
                assert response.status == 200
        except urllib.error.HTTPError as error:
            assert error.code == 400
        return True
    return open_url


def login(cli, tmp_path, oauth_server, browser=None):
    origin, state = oauth_server
    store = cli.CredentialStore(tmp_path / "private")
    client = cli.Client(origin, store=store)
    client.login(browser_open=browser or browser_callback(state), timeout=3)
    return client, store


def test_oauth_pkce_login_calls_tools_and_persists_private_credentials(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    assert client.tools()["tools"][0]["name"] == "louxiang_list_projects"
    assert client.call("louxiang_save_plan", {"project_id": "p-1"}) == {"result": {"project_id": "p-1", "saved": True}}
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    registration = oauth_server[1]["registration"]
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["grant_types"] == ["authorization_code", "refresh_token"]
    assert registration["redirect_uris"][0].startswith("http://127.0.0.1:")


def test_refresh_rotates_and_persists_new_refresh_token(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    oauth_server[1]["refresh_delay"] = 0.25
    data = store.load()
    data["connections"][client.server]["expires_at"] = 0
    store.save(data)
    assert client.tools()["tools"]
    assert store.load()["connections"][client.server]["refresh_token"] == "refresh-private-2"
    assert oauth_server[1]["refresh"] == 1
    assert oauth_server[1]["requests"][-1][2] == "Bearer access-private-2"


def test_simultaneous_cli_calls_share_one_refresh_rotation(cli, tmp_path, oauth_server):
    """Without a cross-process lock both calls replay a single-use refresh token."""
    client, store = login(cli, tmp_path, oauth_server)
    oauth_server[1]["refresh_delay"] = 0.25
    data = store.load()
    data["connections"][client.server]["expires_at"] = 0
    store.save(data)
    source = "from louxiang_agent.cli import main;raise SystemExit(main())"
    env = dict(os.environ, PYTHONPATH=str(CLI.parents[1]))
    commands = [subprocess.Popen([sys.executable, "-c", source, "--config-dir", str(store.directory), "tools"],
                                 env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(6)]
    results = [process.communicate(timeout=8) for process in commands]
    assert all(process.returncode == 0 for process in commands), results
    assert oauth_server[1]["refresh"] == 1


def test_accepts_user_read_only_consent_without_offline_permission(cli, tmp_path, oauth_server):
    oauth_server[1]["token_scope"] = "louxiang:read"
    client, store = login(cli, tmp_path, oauth_server)
    assert client.tools()["tools"]
    data = store.load()
    assert data["connections"][client.server]["scope"] == "louxiang:read"
    assert not data["connections"][client.server].get("refresh_token")
    data["connections"][client.server]["expires_at"] = 0
    store.save(data)
    before = len(oauth_server[1]["requests"])
    with pytest.raises(cli.ClientError):
        client.tools()
    assert len(oauth_server[1]["requests"]) == before


def test_rejects_token_resource_mismatch(cli, tmp_path, oauth_server):
    oauth_server[1]["token_resource"] = "https://other.invalid/mcp"
    with pytest.raises(cli.ClientError):
        login(cli, tmp_path, oauth_server)


@pytest.mark.parametrize("case", ["state", "issuer", "metadata_issuer"])
def test_oauth_rejects_callback_mixup_without_saving_credentials(cli, tmp_path, oauth_server, case):
    origin, state = oauth_server
    if case == "metadata_issuer":
        state["bad_issuer"] = True
    store = cli.CredentialStore(tmp_path / "private")
    client = cli.Client(origin, store=store)
    with pytest.raises(cli.ClientError):
        client.login(browser_open=browser_callback(state, bad_state=case == "state", bad_issuer=case == "issuer"), timeout=3)
    assert not store.path.exists()
    assert not any(item[1] == "/api/agent/oauth/token" for item in state["requests"])


@pytest.mark.parametrize("url", ["http://example.com", "https://user:pass@example.com", "https://example.com/path", "https://example.com?x=1", "https://example.com/#x", "https://example.com/\n"])
def test_rejects_unsafe_server_urls(cli, tmp_path, url):
    with pytest.raises(cli.ClientError):
        cli.Client(url, store=cli.CredentialStore(tmp_path / "private"))


def test_token_request_never_follows_redirect(cli, tmp_path, oauth_server):
    origin, state = oauth_server
    state["redirect_token"] = True
    with pytest.raises(cli.ClientError):
        login(cli, tmp_path, oauth_server)
    assert not any(item[1] == "/trap" for item in state["requests"])


def test_incomplete_local_callback_request_cannot_hang_login_shutdown(cli, tmp_path, oauth_server):
    source = '''
import socket,sys,urllib.parse
from louxiang_agent.cli import Client, ClientError, CredentialStore
connections=[]
def partial_request(url):
    redirect=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['redirect_uri'][0]
    address=urllib.parse.urlsplit(redirect)
    connection=socket.create_connection(('127.0.0.1',address.port))
    connection.sendall(b'GET /callback HTTP/1.1\\r\\nHost: 127.0.0.1\\r\\n')
    connections.append(connection)
    return True
try:
    Client(sys.argv[1],store=CredentialStore(sys.argv[2])).login(browser_open=partial_request,timeout=0.1)
except ClientError:
    pass
'''
    env = dict(os.environ, PYTHONPATH=str(CLI.parents[1]))
    try:
        result = subprocess.run([sys.executable, "-c", source, oauth_server[0], str(tmp_path / "private")],
                                env=env, capture_output=True, text=True, timeout=2)
    except subprocess.TimeoutExpired:
        pytest.fail("Incomplete local HTTP headers hung CLI login beyond its timeout")
    assert result.returncode == 0, result.stderr


def test_rejects_cross_origin_saved_token_endpoint_before_sending_any_secret(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    before = len(oauth_server[1]["requests"])
    data = store.load()
    data["connections"][client.server].update({"expires_at": 0, "token_endpoint": "https://other.invalid/token"})
    store.save(data)
    with pytest.raises(cli.ClientError):
        client.tools()
    assert len(oauth_server[1]["requests"]) == before


def test_logout_revokes_then_removes_local_tokens(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    client.logout()
    assert client.server not in store.load()["connections"]
    assert any(item[1] == "/api/agent/oauth/revoke" for item in oauth_server[1]["requests"])


def test_cli_never_prints_tokens_even_if_server_echoes_them(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.main(["--config-dir", str(store.directory), "connections"])
    assert result == 0
    assert "p-1" in stdout.getvalue()
    for secret in ["must-not-print", "secret-echo", "access-private-1", "refresh-private-1"]:
        assert secret not in stdout.getvalue() + stderr.getvalue()


def test_cli_call_outputs_actual_result_and_rejects_non_object_json(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.main(["--config-dir", str(store.directory), "call", "louxiang_save_plan", "--json", '{"project_id":"p-1"}'])
    assert result == 0
    assert json.loads(stdout.getvalue())["result"]["saved"] is True
    with contextlib.redirect_stderr(stderr):
        assert cli.main(["--config-dir", str(store.directory), "call", "louxiang_save_plan", "--json", "[]"]) == 2


def test_cli_download_saves_authenticated_artifact_and_checks_hash(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    output = tmp_path / "original.mp4"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = cli.main(["--config-dir", str(store.directory), "download", "--project-id", "p-1", "--job-id", "j-1",
                           "--name", "short-video.mp4", "--out", str(output),
                           "--sha256", "eef57f61c0b8e9b14d94fa07383fc51ad113adc9ca7a4a062fe30bbf279f7dbf"])
    assert result == 0
    assert output.read_bytes() == b"original-video-bytes"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    report = json.loads(stdout.getvalue())
    assert report["verified"] is True
    assert report["bytes"] == 20
    assert oauth_server[1]["requests"][-1][2] == "Bearer access-private-1"


def test_download_keeps_existing_file_and_removes_failed_hash_temporary_file(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"keep-me")
    before = len(oauth_server[1]["requests"])
    with pytest.raises(cli.ClientError):
        client.download("p-1", "j-1", "short-video.mp4", output)
    assert output.read_bytes() == b"keep-me"
    assert len(oauth_server[1]["requests"]) == before
    with pytest.raises(cli.ClientError):
        client.download("p-1", "j-1", "short-video.mp4", tmp_path / "failed.mp4", sha256="f" * 64)
    assert not (tmp_path / "failed.mp4").exists()
    assert not list(tmp_path.glob(".louxiang-download-*"))


def test_download_rejects_redirect_and_path_escape(cli, tmp_path, oauth_server):
    client, store = login(cli, tmp_path, oauth_server)
    oauth_server[1]["download_redirect"] = True
    with pytest.raises(cli.ClientError):
        client.download("p-1", "j-1", "short-video.mp4", tmp_path / "redirect.mp4")
    assert not any(item[1] == "/trap" for item in oauth_server[1]["requests"])
    assert not (tmp_path / "redirect.mp4").exists()
    before = len(oauth_server[1]["requests"])
    with pytest.raises(cli.ClientError):
        client.download("p-1", "j-1", "../credentials.json", tmp_path / "escape")
    assert len(oauth_server[1]["requests"]) == before


def test_credential_store_refuses_symlink_and_public_file(cli, tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    victim = tmp_path / "victim.json"
    victim.write_text('{}')
    (directory / "credentials.json").symlink_to(victim)
    store = cli.CredentialStore(directory)
    with pytest.raises(cli.ClientError):
        store.save({"connections": {}})
    (directory / "credentials.json").unlink()
    (directory / "credentials.json").write_text('{}')
    os.chmod(directory / "credentials.json", 0o644)
    with pytest.raises(cli.ClientError):
        store.load()


def test_build_distributes_working_zipapp_and_installable_dependency_free_wheel(tmp_path):
    builder = ROOT / "integrations/louxiang-agent/build.py"
    assert builder.is_file(), "The downloadable CLI build script is missing"
    subprocess.run([sys.executable, str(builder), "--output", str(tmp_path / "dist")], check=True, capture_output=True)
    archive = tmp_path / "dist/louxiang.pyz"
    wheel = tmp_path / "dist/louxiang_agent-0.1.0-py3-none-any.whl"
    assert archive.is_file() and wheel.is_file()
    portable = subprocess.run([sys.executable, str(archive), "--help"], check=True, text=True, capture_output=True)
    assert "login" in portable.stdout and "call" in portable.stdout
    rejected = subprocess.run([sys.executable, str(archive), "--config-dir", str(tmp_path / "none"), "tools", "--server", "http://public.example"], text=True, capture_output=True)
    assert rejected.returncode == 2
    environment = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, capture_output=True)
    subprocess.run([str(environment / "bin/python"), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], check=True, capture_output=True)
    installed = subprocess.run([str(environment / "bin/louxiang"), "--help"], check=True, text=True, capture_output=True)
    assert "login" in installed.stdout and "call" in installed.stdout
