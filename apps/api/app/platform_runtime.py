"""Private, project-scoped execution of the vendored Easel account adapters.

No credentials are read from Easel's global browser directory. Login success is
last-known state; only a successful whoami probe sets verified=True.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .config import ROOT

PLATFORMS = {
    'xiaohongshu': {'name': '小红书', 'backend': 'xhs', 'script': 'xhs_publish.py', 'profile': 'XiaohongshuProfile'},
    'kuaishou': {'name': '快手', 'backend': 'web', 'script': 'web_publisher.py', 'profile': 'KuaishouProfile'},
    'weixin-channels': {'name': '微信视频号', 'backend': 'web', 'script': 'web_publisher.py', 'profile': 'ChannelsProfile'},
    'zhihu': {'name': '知乎', 'backend': 'web', 'script': 'web_publisher.py', 'profile': 'ZhihuProfile'},
    'bilibili': {'name': 'B站', 'backend': 'biliup', 'script': 'bili_login.py'},
    'douyin': {'name': '抖音', 'backend': 'douyin', 'script': 'douyin_publish.py', 'profile': 'DouyinProfile'},
    'wechat-oa': {'name': '微信公众号', 'backend': 'wechat-oa', 'script': 'weixin_mp_stats.py', 'profile': 'WeixinMpProfile'},
}
STATES = {'starting', 'qr_ready', 'window_login', 'scanned', 'sms_required', 'verifying',
          'success', 'expired', 'error', 'unknown'}
STATE_MESSAGES = {'starting': '正在启动登录', 'qr_ready': '请用手机 App 扫码登录',
    'window_login': '请在登录窗口完成验证', 'scanned': '扫码成功，正在验证',
    'sms_required': '平台要求短信验证，请输入手机收到的验证码', 'verifying': '正在验证验证码',
    'success': '登录成功', 'expired': '二维码已过期，请重新登录',
    'error': '登录未完成，请检查网络、浏览器依赖或会话占用', 'unknown': '尚未登录'}


class PlatformRuntime:
    def __init__(self, root: Path | None = None, python: str | None = None):
        self.root = Path(root or ROOT).resolve()
        self.vendor = self.root / 'vendor' / 'easel_accounts'
        self.scripts = self.vendor / 'skills' / 'shared' / 'scripts'
        candidate = self.root / '.venv' / 'bin' / 'python'
        self.python = str(python or os.environ.get('HOUSIGNAL_PLATFORM_PYTHON') or
                          (candidate if candidate.is_file() else sys.executable))
        self._processes: dict[str, subprocess.Popen] = {}
        self._readiness_cache: tuple[float, dict] | None = None
        self._cache_lock = threading.Lock()
        self._session_local = threading.local()
        self.browser_policy = Path(__file__).resolve().parent / 'platform_browser_policy'

    @staticmethod
    def _hash(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('团队与项目不能为空')
        return hashlib.sha256(value.encode()).hexdigest()[:32]

    def _scope(self, tenant_id: str, pid: str, platform: str, *, create=False) -> Path:
        if platform not in PLATFORMS:
            raise ValueError('未知平台')
        scope = self.root / 'runtime' / 'platform-accounts' / self._hash(tenant_id) / self._hash(pid) / platform
        current = self.root
        for part in scope.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('账号目录无效')
            if create:
                current.mkdir(exist_ok=True, mode=0o700)
                current.chmod(0o700)
        return scope

    @staticmethod
    def _private(scope: Path):
        if not scope.exists():
            return
        scope.chmod(0o700)
        for root, dirs, files in os.walk(scope, followlinks=False):
            for name in dirs + files:
                path = Path(root) / name
                if not path.is_symlink():
                    try:
                        path.chmod(0o700 if path.is_dir() else 0o600)
                    except FileNotFoundError:
                        pass

    @staticmethod
    def _read(path: Path) -> dict:
        if path.is_symlink():
            return {}
        try:
            if path.stat().st_size > 64 * 1024:
                return {}
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(path: Path, data: dict):
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.account-', suffix='.tmp')
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(data, file, ensure_ascii=False)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _session_id(self, scope: Path) -> str:
        value = self._read(scope / 'session.json').get('session_id')
        return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else ''

    def _rotate_session(self, scope: Path) -> str:
        # Call only while holding the platform session lock. This is an opaque
        # generation marker, never a browser cookie or credential identifier.
        value = secrets.token_hex(32)
        self._write(scope / 'session.json', {'session_id': value})
        return value

    @contextmanager
    def _guard(self, scope: Path):
        fd = os.open(scope / '.guard.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def publish_lock(self, tenant_id: str, pid: str, platform: str):
        """Serialize a whole publish/probe against login and other publications."""
        scope = self._scope(tenant_id, pid, platform, create=True)
        holders = getattr(self._session_local, 'holders', None)
        if holders is None:
            holders = self._session_local.holders = set()
        if str(scope) in holders:
            yield
            return
        fd = os.open(scope / '.session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('该平台正在登录、校验或发布，请稍后重试') from exc
            holders.add(str(scope))
            yield
        finally:
            holders.discard(str(scope))
            os.close(fd)

    def _env(self, scope: Path) -> dict[str, str]:
        policy_files = (self.browser_policy / 'sitecustomize.py', self.browser_policy / 'browser_policy.py')
        try:
            if self.browser_policy.is_symlink() or any(
                    not path.is_file() or path.is_symlink() for path in policy_files):
                raise ValueError('Invalid browser policy files')
            # A syntax error in sitecustomize is otherwise suppressed by Python
            # startup, before our fail-closed exception handler can run.
            for path in policy_files:
                compile(path.read_bytes(), str(path), 'exec')
        except (OSError, ValueError, SyntaxError):
            raise RuntimeError('平台浏览器保护策略未就绪') from None
        allowed = {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                   'REQUESTS_CA_BUNDLE', 'PLAYWRIGHT_BROWSERS_PATH'}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        # Headed platform sessions may use the container's private Xvfb display.
        # Never let a child connect to a remote X server through inherited env.
        display = os.environ.get('DISPLAY', '')
        if re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', display):
            env['DISPLAY'] = display
            # xvfb-run creates a private, user-owned authority file. Inspect
            # metadata only; the browser reads its cookie without exposing it.
            authority = Path(os.environ.get('XAUTHORITY', ''))
            try:
                if authority.is_absolute() and authority.resolve(strict=True) == authority:
                    info = authority.lstat()
                    if (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1):
                        env['XAUTHORITY'] = str(authority)
            except (OSError, ValueError, RuntimeError):
                pass
        env.update(EASEL_ROOT=str(scope), EASEL_CALENDAR_AUTORECORD='0',
                   PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', NO_PROXY='*', no_proxy='*',
                   PYTHONPATH=str(self.browser_policy))
        env['PATH'] = str(Path(self.python).parent) + os.pathsep + env.get('PATH', os.defpath)
        return env

    def readiness(self) -> dict:
        with self._cache_lock:
            if self._readiness_cache and time.monotonic() - self._readiness_cache[0] < 30:
                return self._readiness_cache[1]
            probe = """import importlib.util,json,pathlib
r={'python':True,'segno':bool(importlib.util.find_spec('segno')),'browser':False}
try:
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p: r['browser']=pathlib.Path(p.chromium.executable_path).is_file()
except Exception: pass
print(json.dumps(r))
"""
            data = {'python': False, 'segno': False, 'browser': False}
            try:
                proc = subprocess.run([self.python, '-c', probe], capture_output=True, text=True, timeout=12)
                value = json.loads(proc.stdout)
                data.update({key: bool(value.get(key)) for key in data})
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            self._readiness_cache = (time.monotonic(), data)
            return data

    def _ready(self, platform: str) -> bool:
        probe = self.readiness()
        return (self.scripts / PLATFORMS[platform]['script']).is_file() and probe['python'] and bool(
            probe['segno'] if platform == 'bilibili' else probe['browser'])

    def _command(self, scope: Path, platform: str, action: str) -> list[str]:
        cfg = PLATFORMS[platform]
        cmd = [self.python, str(self.scripts / cfg['script']),
               'login-qr' if action == 'login' and cfg['backend'] == 'web' else action]
        if platform == 'bilibili':
            cmd += ['--cookie', str(scope / 'cookies.json')]
        else:
            cmd += ['--profile-base', str(scope / 'profiles')]
            if cfg['backend'] == 'web':
                cmd += ['--platform', platform]
            else:
                cmd += ['--no-proxy']
        if action == 'login':
            cmd += ['--qr-out', str(scope / 'qr.png'), '--status-file', str(scope / 'status.json'), '--timeout', '240']
            if platform == 'douyin':
                cmd += ['--sms-code-file', str(scope / 'sms.code')]
        return cmd

    @staticmethod
    def _process_stamp(pid: int) -> str:
        try:
            return subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True,
                                  text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ''

    def _owned_process(self, scope: Path) -> int | None:
        local = self._processes.get(str(scope))
        if local is not None and local.poll() is not None:
            self._processes.pop(str(scope), None)
        record = self._read(scope / 'process.json')
        pid = record.get('pid')
        command = record.get('command')
        if not isinstance(pid, int) or pid <= 1 or not isinstance(command, list) or len(command) < 2:
            return None
        script = Path(str(command[1]))
        if script.parent != self.scripts or '--status-file' not in command:
            return None
        if command[command.index('--status-file') + 1] != str(scope / 'status.json'):
            return None
        try:
            args = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True,
                                  text=True, timeout=3).stdout.strip()
            if str(script) not in args or str(scope / 'status.json') not in args:
                return None
            if not record.get('stamp') or self._process_stamp(pid) != record['stamp']:
                return None
            return pid
        except (OSError, subprocess.TimeoutExpired, IndexError):
            return None

    @staticmethod
    def _public_identity(value: dict) -> dict:
        avatar = str(value.get('avatar') or '')[:2048]
        try:
            parsed = urlsplit(avatar)
            if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password:
                avatar = ''
        except ValueError:
            avatar = ''
        return {'name': re.sub(r'[\x00-\x1f]', '', str(value.get('name') or ''))[:80], 'avatar': avatar}

    def login_status(self, tenant_id: str, pid: str, platform: str) -> dict:
        scope = self._scope(tenant_id, pid, platform)
        data = self._read(scope / 'status.json')
        state = data.get('state', 'unknown')
        if state not in STATES:
            state = 'unknown'
        if state in {'starting', 'qr_ready', 'window_login', 'scanned', 'sms_required', 'verifying'}:
            if not self._owned_process(scope):
                state = 'expired' if state in {'qr_ready', 'sms_required'} else 'error'
        qr = self.qr_path(tenant_id, pid, platform)
        identity = self._read(scope / 'identity.json')
        session_id = self._session_id(scope)
        stamp = identity.get('ts')
        verified = bool(session_id and identity.get('session_id') == session_id and
                        identity.get('verified') and isinstance(stamp, (int, float)) and
                        not isinstance(stamp, bool) and 0 <= time.time() - stamp < 600 and state == 'success')
        self._private(scope)
        message = STATE_MESSAGES[state]
        raw_message = str(data.get('message') or '')
        if platform == 'xiaohongshu' and state == 'error' and any(
                text in raw_message for text in ('300012', 'IP 存在风险', 'IP存在风险')):
            message = '小红书提示当前网络存在风险（300012），请切换正常网络后重新登录'
        unconfirmed = (platform == 'douyin' and identity.get('probe_status') == 'unconfirmed'
                       and identity.get('session_id') == session_id)
        if unconfirmed:
            message = ('平台显示安全验证，账号状态暂未确认，请查看官方窗口后重试'
                       if identity.get('auth_state') == 'challenge' else
                       '账号状态暂未确认，页面加载或认证控件未能核对，请稍后重试')
        return {'state': ('challenge' if identity.get('auth_state') == 'challenge' else 'unknown')
                    if unconfirmed else state, 'message': message, 'qrReady': qr is not None,
                'qrTs': int(qr.stat().st_mtime) if qr else 0,
                'loggedIn': state == 'success', 'verified': verified,
                'session_id': session_id,
                **(self._public_identity(identity) if verified else {'name': '', 'avatar': ''})}

    def list_accounts(self, tenant_id: str, pid: str) -> list[dict]:
        rows = []
        for platform, cfg in PLATFORMS.items():
            status = self.login_status(tenant_id, pid, platform)
            ready = self._ready(platform)
            rows.append({'platform': platform, 'name': cfg['name'], 'backend': cfg['backend'],
                'supported': True, 'ready': ready,
                'note': '' if ready else '运行依赖未就绪，请配置平台 Python 与浏览器',
                'identity_name': status['name'], **status, 'name': cfg['name']})
        return rows

    def start_login(self, tenant_id: str, pid: str, platform: str) -> dict:
        scope = self._scope(tenant_id, pid, platform, create=True)
        with self._guard(scope):
            if self._owned_process(scope):
                return self.login_status(tenant_id, pid, platform)
            if not self._ready(platform):
                self._write(scope / 'status.json', {'state': 'error'})
                return {**self.login_status(tenant_id, pid, platform),
                        'message': '平台运行依赖未就绪，请配置平台 Python 与 Chromium'}
            fd = os.open(scope / '.session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError('该平台正在校验或发布，请稍后重试') from exc
                self._rotate_session(scope)
                for name in ('qr.png', 'sms.code', 'identity.json'):
                    (scope / name).unlink(missing_ok=True)
                self._write(scope / 'status.json', {'state': 'starting', 'ts': int(time.time())})
                command = self._command(scope, platform, 'login')
                log = os.open(scope / 'runner.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                try:
                    # The child retains the advisory lock until it exits; startup across
                    # API processes is serialized by the short-lived guard above.
                    proc = subprocess.Popen(command, cwd=scope, env=self._env(scope),
                        stdout=log, stderr=subprocess.STDOUT, pass_fds=(fd,), start_new_session=True,
                        umask=0o077)
                finally:
                    os.close(log)
                self._processes[str(scope)] = proc
                self._write(scope / 'process.json', {'pid': proc.pid, 'command': command,
                                                   'stamp': self._process_stamp(proc.pid)})
            except OSError:
                self._write(scope / 'status.json', {'state': 'error'})
            finally:
                # Do not LOCK_UN: the inherited descriptor owns the same lock.
                os.close(fd)
            return self.login_status(tenant_id, pid, platform)

    def submit_sms(self, tenant_id: str, pid: str, platform: str, code: str) -> dict:
        scope = self._scope(tenant_id, pid, platform, create=True)
        if platform != 'douyin' or not re.fullmatch(r'[0-9]{4,8}', code or ''):
            raise ValueError('请输入 4 至 8 位数字验证码')
        with self._guard(scope):
            if self.login_status(tenant_id, pid, platform)['state'] != 'sms_required':
                raise ValueError('当前没有待处理的短信验证')
            fd = os.open(scope / 'sms.code', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write(code)
            (scope / 'sms.code').chmod(0o600)
        return {'ok': True, 'state': 'verifying', 'message': '验证码已提交'}

    def qr_path(self, tenant_id: str, pid: str, platform: str) -> Path | None:
        scope = self._scope(tenant_id, pid, platform)
        path = scope / 'qr.png'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            return None
        path.chmod(0o600)
        return path

    @staticmethod
    def _identity_result(proc: subprocess.CompletedProcess, *, allow_unconfirmed=False) -> dict | None:
        if proc.returncode != 0:
            return None
        for line in reversed(proc.stdout.splitlines()):
            try:
                value = json.loads(line)
                if (isinstance(value, dict) and isinstance(value.get('loggedIn'), bool)
                        and (not value.get('error') or (allow_unconfirmed and
                             value.get('auth_state') in {'unconfirmed', 'challenge'}))):
                    return value
            except ValueError:
                pass
        return None

    def _run_probe(self, scope: Path, platform: str, *, timeout=150) -> subprocess.CompletedProcess:
        """Run only the fixed identity adapter and own its complete process group."""
        command = self._command(scope, platform, 'whoami')
        proc = subprocess.Popen(command, cwd=scope, env=self._env(scope),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, umask=0o077)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            # This group was created for our fixed argv above. Keeping the Popen
            # child unreaped prevents its PID being reused while signaling it.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # The Python parent may exit on TERM while a browser child
                # ignores it and has detached stdio. Always close our group.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate(timeout=5)
            raise

    def whoami(self, tenant_id: str, pid: str, platform: str) -> dict:
        scope = self._scope(tenant_id, pid, platform, create=True)
        known = self.login_status(tenant_id, pid, platform)['loggedIn']
        uncertain = {'loggedIn': known, 'verified': False, 'name': '', 'avatar': '',
                     'message': '账号校验未完成，请稍后重试', 'session_id': self._session_id(scope)}
        try:
            with self.publish_lock(tenant_id, pid, platform):
                def unconfirmed_probe(value=None):
                    current = self.login_status(tenant_id, pid, platform)
                    challenged = isinstance(value, dict) and value.get('auth_state') == 'challenge'
                    result = {**uncertain, 'loggedIn': current['loggedIn'], 'session_id': current['session_id'],
                              'state': 'challenge' if challenged else 'unknown', 'probe_status': 'unconfirmed',
                              'auth_state': 'challenge' if challenged else 'unconfirmed',
                              'message': ('平台显示安全验证，账号状态暂未确认，请查看官方窗口后重试' if challenged else
                                  '账号状态暂未确认，页面加载或认证控件未能核对，请稍后重试')}
                    # Invalidate only verification metadata, retaining status and session.
                    self._write(scope / 'identity.json', {**result, 'ts': int(time.time())})
                    self._private(scope)
                    return result

                try:
                    proc = self._run_probe(scope, platform)
                    value = self._identity_result(proc, allow_unconfirmed=platform == 'douyin')
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    if platform == 'douyin':
                        return unconfirmed_probe()
                    raise
                if platform == 'douyin':
                    confirmed = (isinstance(value, dict) and not value.get('error') and
                        ((value.get('loggedIn') is True and value.get('auth_state') == 'authenticated'
                          and value.get('auth_evidence') == 'visible_creator_publish_control') or
                         (value.get('loggedIn') is False and value.get('auth_state') == 'logged_out'
                          and value.get('auth_evidence') == 'visible_official_login_prompt')))
                    if not confirmed:
                        return unconfirmed_probe(value)
                if value is None:
                    return uncertain
                session_id = self._session_id(scope)
                if value['loggedIn'] and not session_id:
                    # Legacy authenticated profiles gain a marker only after a
                    # successful current probe, under the same session lock.
                    session_id = self._rotate_session(scope)
                result = {'loggedIn': value['loggedIn'], 'verified': True,
                          **self._public_identity(value),
                          'message': ('账号校验通过' if value['loggedIn'] else
                              '平台明确显示未登录，请前往账号页登录' if platform == 'douyin' else '会话已过期，请重新登录'),
                          'session_id': session_id}
                # Persist before releasing the same session lock that guards logout
                # and a new login; otherwise an old probe can resurrect a session.
                self._write(scope / 'identity.json', {**result, 'ts': int(time.time())})
                self._write(scope / 'status.json', {'state': 'success' if value['loggedIn'] else 'unknown', 'ts': int(time.time())})
                self._private(scope)
                return result
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return uncertain

    def publish_status(self, tenant_id: str, pid: str, platform: str) -> dict:
        scope = self._scope(tenant_id, pid, platform)
        data = self._read(scope / 'publish-status.json')
        state = data.get('state', 'unknown')
        if state not in STATES:
            state = 'unknown'
        self._private(scope)
        return {'state': state, 'message': STATE_MESSAGES[state] if state != 'success'
                else '平台提交已读回确认，请查看发布任务回执', 'verified': False}

    def submit_publish_sms(self, tenant_id: str, pid: str, platform: str, code: str) -> dict:
        scope = self._scope(tenant_id, pid, platform, create=True)
        if platform != 'douyin' or not re.fullmatch(r'[0-9]{4,8}', code or ''):
            raise ValueError('请输入 4 至 8 位数字验证码')
        with self._guard(scope):
            if self.publish_status(tenant_id, pid, platform)['state'] != 'sms_required':
                raise ValueError('当前没有待处理的发布短信验证')
            fd = os.open(scope / 'publish-sms.code', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write(code)
            (scope / 'publish-sms.code').chmod(0o600)
        return {'ok': True, 'state': 'verifying', 'message': '验证码已提交'}

    def logout(self, tenant_id: str, pid: str, platform: str) -> dict:
        scope = self._scope(tenant_id, pid, platform, create=True)
        with self._guard(scope):
            process_id = self._owned_process(scope)
            if process_id:
                try:
                    if os.getpgid(process_id) == process_id:
                        os.killpg(process_id, signal.SIGTERM)
                    else:
                        os.kill(process_id, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                local = self._processes.pop(str(scope), None)
                if local:
                    try:
                        local.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if self._owned_process(scope):
                            local.kill(); local.wait(timeout=5)
                else:
                    until = time.monotonic() + 5
                    while self._owned_process(scope) and time.monotonic() < until:
                        time.sleep(.05)
            with self.publish_lock(tenant_id, pid, platform):
                for child in scope.iterdir():
                    if child.name in {'.guard.lock', '.session.lock', 'research'}:
                        continue
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
                session_id = self._rotate_session(scope)
                self._write(scope / 'status.json', {'state': 'unknown', 'ts': int(time.time())})
        return {'ok': True, 'loggedIn': False, 'verified': False, 'state': 'unknown',
                'message': '已退出登录', 'session_id': session_id}

    def build_publish_command(self, tenant_id: str, pid: str, platform: str, *,
                              title='', body='', media: list[str] | None = None,
                              tags='', execute=False) -> dict:
        scope = self._scope(tenant_id, pid, platform, create=True)
        media = [str(Path(item).resolve()) for item in (media or [])]
        if any(not Path(item).is_file() for item in media):
            raise ValueError('发布素材不存在')
        videos = [item for item in media if Path(item).suffix.lower() in {'.mp4', '.mov', '.m4v', '.webm'}]
        images = [item for item in media if Path(item).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}]
        htmls = [item for item in media if Path(item).suffix.lower() in {'.html', '.htm'}]
        allowed_count = len(videos) + len(images) + (len(htmls) if platform == 'wechat-oa' else 0)
        if allowed_count != len(media) or (videos and images) or (platform == 'wechat-oa' and videos):
            raise ValueError('请选择图片或视频素材')
        if platform in {'douyin', 'weixin-channels', 'bilibili'} and len(videos) != 1:
            raise ValueError('该平台需要一条视频')
        if platform in {'xiaohongshu', 'kuaishou'} and not media:
            raise ValueError('该平台需要图片或视频')
        py = self.python
        cfg = PLATFORMS[platform]
        cmd = [py, str(self.scripts / cfg['script'])]
        draft_only = platform == 'wechat-oa'
        if platform == 'bilibili':
            upload = self.vendor / 'skills/openclaw/skill-bilibili-upload/scripts/bili_upload.py'
            cmd = [py, str(upload), 'upload', '--video', videos[0], '--cookie', str(scope / 'cookies.json'),
                   '--title', title[:80], '--desc', body[:2000], '--tag', tags or '日常', '--tid', '36']
        elif platform in {'xiaohongshu', 'douyin'}:
            cmd += ['publish-video' if videos else 'publish', '--profile-base', str(scope / 'profiles'), '--no-proxy',
                    '--title', title, '--content', body, '--tags', tags,
                    '--video' if videos else '--images', videos[0] if videos else ','.join(images)]
            if platform == 'douyin':
                cmd += ['--status-file', str(scope / 'publish-status.json'), '--sms-code-file', str(scope / 'publish-sms.code')]
        elif platform == 'wechat-oa':
            if not images:
                raise ValueError('公众号草稿需要封面图')
            if len(htmls) > 1:
                raise ValueError('公众号只接受一份已审核 HTML')
            html = Path(htmls[0]) if htmls else scope / 'draft.html'
            if not htmls:
                html.write_text(body, encoding='utf-8'); html.chmod(0o600)
            if execute:
                cmd += ['publish', '--profile-base', str(scope / 'profiles'), '--no-proxy',
                        '--html', str(html), '--cover', images[0], '--title', title]
            else:
                cmd += ['selftest']  # the upstream mp publisher has no dry-run flag
        else:
            if platform == 'kuaishou' and len(media) != 1:
                raise ValueError('快手每次发布一条视频或一张图片')
            cmd += ['publish', '--platform', platform, '--profile-base', str(scope / 'profiles'),
                    '--title', title, '--desc', body, '--tags', tags]
            if media:
                cmd += ['--media', media[0]]
        if execute and platform != 'wechat-oa':
            cmd += ['--exec']
        return {'command': cmd, 'cwd': str(scope), 'env': self._env(scope),
                'execute': bool(execute), 'draft_only': draft_only}

    def validate_publish_command(self, spec: dict, tenant_id: str, pid: str, platform: str) -> bool:
        """Check the execution boundary; workers accept only our scoped fixed adapters."""
        scope = self._scope(tenant_id, pid, platform)
        command = spec.get('command')
        if not isinstance(command, list) or len(command) < 3 or any(
            not isinstance(arg, str) or '\x00' in arg for arg in command):
            raise ValueError('发布命令无效')
        expected_script = (self.vendor / 'skills/openclaw/skill-bilibili-upload/scripts/bili_upload.py'
                           if platform == 'bilibili' else self.scripts / PLATFORMS[platform]['script'])
        if command[:2] != [self.python, str(expected_script)] or spec.get('cwd') != str(scope):
            raise ValueError('发布命令不属于当前平台与项目')
        actions = {'upload'} if platform == 'bilibili' else (
            {'publish', 'selftest'} if platform == 'wechat-oa' else {'publish', 'publish-video'})
        if command[2] not in actions or '--allow-unsafe' in command:
            raise ValueError('不允许的发布动作')
        if spec.get('env') != self._env(scope):
            raise ValueError('发布环境无效')
        if spec.get('draft_only') != (platform == 'wechat-oa'):
            raise ValueError('发布模式无效')
        flag = '--cookie' if platform == 'bilibili' else '--profile-base'
        target = scope / ('cookies.json' if platform == 'bilibili' else 'profiles')
        if command[2] != 'selftest':
            try:
                if command.count(flag) != 1 or command[command.index(flag) + 1] != str(target):
                    raise ValueError('发布登录态未隔离')
            except IndexError as exc:
                raise ValueError('发布登录态未隔离') from exc
        if bool(spec.get('execute')) != ('--exec' in command or (platform == 'wechat-oa' and command[2] == 'publish')):
            raise ValueError('发布执行模式无效')
        return True


runtime = PlatformRuntime()
