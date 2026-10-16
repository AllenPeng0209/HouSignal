"""Small OAuth/PKCE client; uses Python's standard library only."""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import re
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


DEFAULT_SERVER = "https://louxiang-preview.lovelingo.love"
SCOPES = "louxiang:read louxiang:write louxiang:production offline_access"
MAX_BODY = 4 * 1024 * 1024
MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024
SENSITIVE_KEYS = {"access_token", "refresh_token", "client_secret", "id_token", "password", "authorization", "code_verifier", "registration_access_token"}


class ClientError(Exception):
    """A safe, user-facing failure without response bodies or credentials."""


def normalize_server(value):
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ClientError("服务器地址包含无效字符。")
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise ClientError("服务器地址无效。") from None
    if not host or parts.username is not None or parts.password is not None or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ClientError("请提供服务器根地址，例如 https://louxiang-preview.lovelingo.love。")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise ClientError("公网服务器必须使用 HTTPS；HTTP 仅供本机调试。")
    host = host.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port and not (parts.scheme == "https" and port == 443) and not (parts.scheme == "http" and port == 80):
        host += ":" + str(port)
    return parts.scheme + "://" + host


def same_origin_url(url, origin):
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ClientError("OAuth 服务返回了无效端点。")
    try:
        parsed = urllib.parse.urlsplit(url)
        candidate = normalize_server(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    except (ValueError, ClientError):
        raise ClientError("OAuth 服务返回了无效端点。") from None
    if candidate != origin or parsed.fragment or parsed.username is not None or parsed.password is not None:
        raise ClientError("拒绝将楼响凭据发送到不同来源的服务；请重新确认服务器地址。")
    return url


def redact(value, known_secrets=()):
    if isinstance(value, dict):
        return {key: ("[redacted]" if str(key).lower().replace("-", "_") in SENSITIVE_KEYS else redact(item, known_secrets)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, known_secrets) for item in value]
    if isinstance(value, str):
        for secret in known_secrets:
            if isinstance(secret, str) and secret:
                value = value.replace(secret, "[redacted]")
    return value


class CredentialStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "louxiang"
        self.path = self.directory / "credentials.json"

    def _check_directory(self, create=False):
        if self.directory.is_symlink():
            raise ClientError("凭据目录不能是符号链接。")
        if create:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.exists():
            if not self.directory.is_dir():
                raise ClientError("凭据路径不是目录。")
            os.chmod(self.directory, 0o700)

    def _check_file(self):
        if self.path.is_symlink():
            raise ClientError("凭据文件不能是符号链接。")
        if self.path.exists():
            mode = self.path.stat().st_mode
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                raise ClientError("凭据文件权限过宽，请设为仅当前用户可读写（chmod 600）。")

    def load(self):
        self._check_directory()
        self._check_file()
        if not self.path.exists():
            return {"version": 1, "active_server": None, "connections": {}}
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            raise ClientError("无法读取本机楼响凭据，请重新登录。") from None
        if not isinstance(data, dict) or not isinstance(data.get("connections"), dict):
            raise ClientError("本机楼响凭据格式无效，请重新登录。")
        return data

    def save(self, data):
        self._check_directory(create=True)
        self._check_file()
        descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextlib.contextmanager
    def locked(self):
        """Serialize single-use refresh tokens across separate CLI processes."""
        self._check_directory(create=True)
        descriptor = os.open(self.directory / ".credentials.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if os.name == "nt":
                import msvcrt
                os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, server, *, store=None):
        self.server = normalize_server(server)
        self.store = store if store is not None else CredentialStore()
        self.opener = urllib.request.build_opener(NoRedirect())
        self.known_secrets = []

    def _request(self, url, *, method="GET", body=None, form=None, token=None):
        same_origin_url(url, self.server)
        headers = {"Accept": "application/json", "User-Agent": "louxiang-cli/0.1.0"}
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            encoded = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            self._validate_secret(token)
            self.known_secrets.append(token)
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise ClientError("服务响应过大，请缩小查询范围。")
                if not raw:
                    return {}
                result = json.loads(raw)
                if not isinstance(result, (dict, list)):
                    raise ClientError("服务未返回有效 JSON。")
                return result
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                raise ClientError("服务器尝试重定向请求，已拒绝发送凭据。请使用最终 HTTPS 根地址。") from None
            if error.code == 401:
                raise ClientError("楼响授权已失效，请运行 louxiang login 重新授权。") from None
            if error.code == 403:
                raise ClientError("当前授权不包含此项目或操作，请在楼响重新授权。") from None
            if error.code == 409:
                raise ClientError("数据已被其他人修改，请重新读取后再提交。") from None
            if error.code == 429:
                raise ClientError("请求过于频繁，请稍后再试。") from None
            raise ClientError("楼响服务请求失败（HTTP " + str(error.code) + "）。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ClientError("无法连接楼响服务，请检查网络和 HTTPS 证书。") from None
        except (ValueError, UnicodeError):
            raise ClientError("楼响服务未返回有效 JSON。") from None

    @staticmethod
    def _validate_secret(value):
        if not isinstance(value, str) or not value or len(value) > 16384 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
            raise ClientError("OAuth 服务返回了无效凭据。")

    def _metadata(self):
        data = self._request(self.server + "/.well-known/oauth-authorization-server")
        if not isinstance(data, dict) or data.get("issuer") != self.server:
            raise ClientError("OAuth issuer 与楼响服务器不一致。")
        if "S256" not in data.get("code_challenge_methods_supported", []):
            raise ClientError("OAuth 服务未声明安全的 S256 PKCE 支持。")
        for name in ("authorization_endpoint", "registration_endpoint", "token_endpoint", "revocation_endpoint"):
            same_origin_url(data.get(name), self.server)
        return data

    def _token_record(self, response, previous):
        if not isinstance(response, dict) or str(response.get("token_type", "")).lower() != "bearer":
            raise ClientError("OAuth 服务返回了无效令牌类型。")
        self._validate_secret(response.get("access_token"))
        if response.get("resource", previous["resource"]) != previous["resource"]:
            raise ClientError("OAuth 返回的授权资源与楼响 MCP 不匹配。")
        try:
            duration = float(response["expires_in"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ClientError("OAuth 服务未返回有效凭据有效期。") from None
        scope = response.get("scope", previous.get("scope", SCOPES))
        if not isinstance(scope, str) or not set(scope.split()) <= set(SCOPES.split()):
            raise ClientError("OAuth 返回的权限范围无效。")
        record = dict(previous)
        if response.get("refresh_token") is not None or "offline_access" in scope.split():
            self._validate_secret(response.get("refresh_token"))
            record["refresh_token"] = response["refresh_token"]
            self.known_secrets.append(response["refresh_token"])
        else:
            record.pop("refresh_token", None)
        record.update({"access_token": response["access_token"], "expires_at": time.time() + duration, "scope": scope})
        self.known_secrets.append(response["access_token"])
        return record

    def login(self, *, browser_open=None, timeout=180):
        metadata = self._metadata()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        received = {}
        event = threading.Event()
        expected_server = self.server

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                valid = (query.get("state") == [state] and query.get("iss") == [expected_server]
                         and len(query.get("code", [])) == 1 and bool(query["code"][0]) and not query.get("error"))
                received["code"] = query["code"][0] if valid else None
                received["valid"] = valid
                body = ("Louxiang connected. You can close this window." if valid else "Louxiang authorization was rejected. Return to the terminal and try again.").encode()
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                event.set()

        class CallbackServer(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(2)
                return connection, address

        callback_server = CallbackServer(("127.0.0.1", 0), Callback)
        callback_server.timeout = 1
        redirect = "http://127.0.0.1:" + str(callback_server.server_port) + "/callback"
        thread = threading.Thread(target=callback_server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        try:
            registration = self._request(metadata["registration_endpoint"], method="POST", body={
                "client_name": "楼响 CLI", "redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": SCOPES})
            if not isinstance(registration, dict) or registration.get("token_endpoint_auth_method", "none") != "none":
                raise ClientError("楼响 CLI 需要公共 PKCE 客户端，服务返回的注册方式不兼容。")
            client_id = registration.get("client_id")
            self._validate_secret(client_id)
            resource = self.server + "/mcp"
            authorization = metadata["authorization_endpoint"] + "?" + urllib.parse.urlencode({
                "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": SCOPES,
                "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource})
            if not (browser_open or webbrowser.open)(authorization):
                raise ClientError("无法打开授权浏览器。请在有桌面浏览器的设备上运行 louxiang login。")
            if not event.wait(timeout):
                raise ClientError("浏览器授权等待超时，请重新运行 louxiang login。")
            if not received.get("valid"):
                raise ClientError("OAuth 回调的 state 或 issuer 不匹配，未保存任何凭据。")
            response = self._request(metadata["token_endpoint"], method="POST", form={
                "grant_type": "authorization_code", "client_id": client_id, "code": received["code"],
                "redirect_uri": redirect, "code_verifier": verifier, "resource": resource})
            record = self._token_record(response, {"client_id": client_id, "token_endpoint": metadata["token_endpoint"],
                "revocation_endpoint": metadata["revocation_endpoint"], "resource": resource, "scope": SCOPES})
            with self.store.locked():
                data = self.store.load()
                data["connections"][self.server] = record
                data["active_server"] = self.server
                self.store.save(data)
            return {"server": self.server, "connected": True, "scopes": record["scope"].split()}
        finally:
            callback_server.shutdown()
            callback_server.server_close()
            thread.join(timeout=2)

    def _record(self):
        with self.store.locked():
            return self._record_unlocked()

    def _record_unlocked(self):
        data = self.store.load()
        record = data["connections"].get(self.server)
        if not isinstance(record, dict):
            raise ClientError("尚未连接此楼响服务器，请先运行 louxiang login --server " + self.server + "。")
        for name in ("token_endpoint", "revocation_endpoint"):
            same_origin_url(record.get(name), self.server)
        if record.get("resource") != self.server + "/mcp":
            raise ClientError("本机授权资源不匹配，请重新登录。")
        self._validate_secret(record.get("access_token"))
        self.known_secrets.append(record["access_token"])
        if record.get("refresh_token") is not None:
            self._validate_secret(record["refresh_token"])
            self.known_secrets.append(record["refresh_token"])
        try:
            expired = float(record.get("expires_at", 0)) <= time.time() + 30
        except (TypeError, ValueError):
            raise ClientError("本机授权有效期无效，请重新登录。") from None
        if expired:
            if not record.get("refresh_token"):
                raise ClientError("授权已到期，且未允许保持连接。请运行 louxiang login 重新授权。")
            response = self._request(record["token_endpoint"], method="POST", form={
                "grant_type": "refresh_token", "client_id": record["client_id"],
                "refresh_token": record["refresh_token"], "resource": record["resource"]})
            record = self._token_record(response, record)
            data["connections"][self.server] = record
            self.store.save(data)
        return record

    def _authorized(self, path, *, body=None):
        record = self._record()
        return self._request(self.server + path, method="POST" if body is not None else "GET", body=body, token=record["access_token"])

    def tools(self):
        return self._authorized("/api/agent/tools")

    def call(self, name, arguments):
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            raise ClientError("调用需要工具名称和 JSON 对象参数。")
        return self._authorized("/api/agent/tools/call", body={"name": name, "arguments": arguments})

    def status(self):
        return self._authorized("/api/agent/whoami")

    def download(self, project_id, job_id, name, output, *, sha256=None):
        """Fetch a private preview with the same grant, without exposing its bearer token."""
        if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) for value in (project_id, job_id)):
            raise ClientError("下载需要有效的项目和任务标识。")
        if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in ("/", "\\")) or any(ord(c) < 32 for c in name):
            raise ClientError("产物名称必须是单个文件名，不能包含路径。")
        if sha256 is not None and (not isinstance(sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256)):
            raise ClientError("--sha256 必须是 64 位十六进制摘要。")
        output = Path(output).expanduser().absolute()
        if output.exists() or output.is_symlink():
            raise ClientError("目标文件已存在，请通过 --out 指定新的文件名。")
        record = self._record()
        path = "/api/agent/projects/" + urllib.parse.quote(project_id, safe="") + "/jobs/" + urllib.parse.quote(job_id, safe="") + "/artifacts/" + urllib.parse.quote(name, safe="")
        url = same_origin_url(self.server + path, self.server)
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + record["access_token"], "User-Agent": "louxiang-cli/0.1.0"})
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".louxiang-download-", dir=output.parent)
        size = 0
        digest = hashlib.sha256()
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                with self.opener.open(request, timeout=30) as response:
                    declared = response.headers.get("Content-Length")
                    try:
                        declared = int(declared) if declared is not None else None
                    except ValueError:
                        raise ClientError("产物响应包含无效文件长度。") from None
                    if declared is not None and (declared < 0 or declared > MAX_DOWNLOAD):
                        raise ClientError("产物过大，CLI 单次下载上限为 2 GiB。")
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise ClientError("产物过大，CLI 单次下载上限为 2 GiB。")
                        digest.update(chunk)
                        stream.write(chunk)
                    if declared is not None and size != declared:
                        raise ClientError("产物传输不完整，未保存目标文件，请重试。")
                stream.flush()
                os.fsync(stream.fileno())
            actual = digest.hexdigest()
            if sha256 is not None and not secrets.compare_digest(actual, sha256.lower()):
                raise ClientError("产物 SHA-256 校验失败，未保存目标文件。")
            try:
                os.link(temporary, output)
            except FileExistsError:
                raise ClientError("目标文件已存在，未覆盖任何内容。") from None
            return {"path": str(output), "bytes": size, "sha256": actual, "verified": sha256 is not None,
                    "state": "preview", "note": "任务产物为预览文件；审核通过状态以楼响正式交付接口为准。"}
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                raise ClientError("产物请求被重定向，已拒绝转发楼响凭据。") from None
            if error.code in (401, 403):
                raise ClientError("产物授权已失效或不包含此项目，请重新连接楼响。") from None
            raise ClientError("产物下载失败（HTTP " + str(error.code) + "）。") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise ClientError("产物下载或保存失败，目标文件未覆盖，请检查网络及输出目录。") from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def logout(self):
        with self.store.locked():
            return self._logout_unlocked()

    def _logout_unlocked(self):
        data = self.store.load()
        record = data["connections"].get(self.server)
        if not record:
            return {"server": self.server, "connected": False}
        same_origin_url(record.get("revocation_endpoint"), self.server)
        for key, hint in (("refresh_token", "refresh_token"), ("access_token", "access_token")):
            if record.get(key):
                self._validate_secret(record[key])
                self.known_secrets.append(record[key])
                self._request(record["revocation_endpoint"], method="POST", form={
                    "token": record[key], "token_type_hint": hint, "client_id": record["client_id"]})
        del data["connections"][self.server]
        if data.get("active_server") == self.server:
            data["active_server"] = next(iter(data["connections"]), None)
        self.store.save(data)
        return {"server": self.server, "connected": False, "revoked": True}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="louxiang", description="楼响远程 Agent CLI：浏览器授权后调用同一个云端工作区。")
    parser.add_argument("--config-dir", help="凭据保存目录（默认 ~/.config/louxiang）")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("login", "logout", "status", "connections", "tools", "call", "download"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("--server", help="楼响 HTTPS 根地址，默认使用当前连接")
        if command == "call":
            command_parser.add_argument("name")
            command_parser.add_argument("--json", required=True, dest="arguments", help="JSON 对象参数")
        if command == "download":
            command_parser.add_argument("--project-id", required=True)
            command_parser.add_argument("--job-id", required=True)
            command_parser.add_argument("--name", required=True, help="get_job 返回的产物文件名")
            command_parser.add_argument("--out", required=True, help="保存路径，不覆盖已有文件")
            command_parser.add_argument("--sha256", help="get_job 返回的 SHA-256；提供后校验一致才保存")
    args = parser.parse_args(argv)
    client = None
    try:
        store = CredentialStore(args.config_dir)
        server = args.server or store.load().get("active_server") or DEFAULT_SERVER
        client = Client(server, store=store)
        if args.command == "login":
            result = client.login()
        elif args.command == "logout":
            result = client.logout()
        elif args.command in ("status", "connections"):
            result = client.status()
        elif args.command == "tools":
            result = client.tools()
        elif args.command == "download":
            result = client.download(args.project_id, args.job_id, args.name, args.out, sha256=args.sha256)
        else:
            try:
                arguments = json.loads(args.arguments)
            except ValueError:
                raise ClientError("--json 必须是有效 JSON 对象。") from None
            if not isinstance(arguments, dict):
                raise ClientError("--json 必须是 JSON 对象，不能使用数组或字符串。")
            result = client.call(args.name, arguments)
        print(json.dumps(redact(result, client.known_secrets), ensure_ascii=False, indent=2))
        return 0
    except ClientError as error:
        print("louxiang: " + str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("louxiang: 操作已取消。", file=sys.stderr)
        return 130
    except (OSError, ValueError, TypeError, KeyError):
        print("louxiang: 本机配置或服务响应无效，请重新登录。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
