"""Private Douyin public-research sessions, isolated from publishing profiles."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

from .platform_runtime import PlatformRuntime

_MESSAGES = {
    'unknown':'尚未连接抖音研究账号', 'starting':'正在打开抖音官方登录窗口',
    'qr_ready':'请用抖音 App 扫描官方二维码',
    'window_login':'请在抖音官方窗口完成登录或验证',
    'success':'抖音研究会话已登录', 'challenge':'抖音要求安全验证，请在官方窗口手动完成',
    'expired':'登录未完成或已过期，请重新连接', 'error':'登录未完成，请检查官方窗口或稍后重试',
    'busy':'研究会话正在登录、校验或搜索，请稍后重试',
    'not_configured':'研究浏览器依赖未就绪',
}
_ACTIVE = {'starting', 'qr_ready', 'window_login'}


class DouyinResearchRuntime:
    """Only fixed login/probe commands are exposed; there is no publish method."""
    def __init__(self, root=None, python=None):
        # Reuse private-directory helpers, never publisher state or credentials.
        self._paths = PlatformRuntime(root=root, python=python)
        self.root, self.python = self._paths.root, self._paths.python
        self.runner = Path(__file__).parent / 'content_skills' / 'douyin_browser.py'
        self._processes = {}
        self._cache = None
        self._cache_lock = threading.Lock()

    _read = staticmethod(PlatformRuntime._read)
    _write = staticmethod(PlatformRuntime._write)
    _private = staticmethod(PlatformRuntime._private)

    def scope(self, tenant_id, pid, *, create=False):
        base = self._paths._scope(tenant_id, pid, 'douyin', create=create)
        scope = base / 'research'
        if scope.is_symlink():
            raise ValueError('研究账号目录无效')
        if create:
            scope.mkdir(exist_ok=True, mode=0o700)
            scope.chmod(0o700)
        return scope

    def _env(self, scope):
        return self._paths._env(scope)

    def readiness(self):
        with self._cache_lock:
            if self._cache and time.monotonic() - self._cache[0] < 30:
                return dict(self._cache[1])
            data = {'python':False, 'browser':False}
            probe = """import json,pathlib
r={'python':True,'browser':False}
try:
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p: r['browser']=pathlib.Path(p.chromium.executable_path).is_file()
except Exception: pass
print(json.dumps(r))
"""
            try:
                proc = subprocess.run([self.python, '-c', probe], capture_output=True,
                    text=True, timeout=12, env=self._env(self.root))
                value = json.loads(proc.stdout)
                data.update({key:value.get(key) is True for key in data})
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            if not self.runner.is_file():
                data['browser'] = False
            self._cache = time.monotonic(), data
            return dict(data)

    def command(self, scope, op):
        if op not in {'login', 'probe'} or scope.name != 'research' or scope.parent.name != 'douyin':
            raise ValueError('研究操作无效')
        command = [self.python, str(self.runner), '--op', op, '--profile',
                   str(scope / 'profiles' / 'DouyinResearchProfile')]
        if op == 'login':
            command += ['--headed', '--qr-out', str(scope / 'qr.png'),
                        '--status-file', str(scope / 'status.json'), '--timeout', '240']
        return command

    @contextmanager
    def session_lock(self, tenant_id, pid):
        scope = self.scope(tenant_id, pid, create=True)
        fd = os.open(scope / '.session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(_MESSAGES['busy']) from exc
            yield
        finally:
            os.close(fd)

    def _guard(self, scope):
        return self._paths._guard(scope)

    def _owned_process(self, scope):
        proc = self._processes.get(str(scope))
        if proc is not None and proc.poll() is not None:
            self._processes.pop(str(scope), None)
        record = self._read(scope / 'process.json')
        pid, command = record.get('pid'), record.get('command')
        if type(pid) is not int or pid <= 1 or command != self.command(scope, 'login'):
            return None
        if not record.get('stamp') or self._paths._process_stamp(pid) != record['stamp']:
            return None
        try:
            args = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True,
                                  text=True, timeout=3).stdout.strip()
            if str(self.runner) in args and str(scope / 'status.json') in args:
                return pid
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    def qr_path(self, tenant_id, pid):
        scope = self.scope(tenant_id, pid)
        state = self._read(scope / 'status.json').get('state')
        path = scope / 'qr.png'
        if state not in _ACTIVE or not self._owned_process(scope):
            return None
        if path.is_symlink() or not path.is_file() or not 8 <= path.stat().st_size <= 2_000_000:
            return None
        with path.open('rb') as source:
            if source.read(8) != b'\x89PNG\r\n\x1a\n':
                return None
        path.chmod(0o600)
        return path

    def login_status(self, tenant_id, pid):
        scope = self.scope(tenant_id, pid)
        data = self._read(scope / 'status.json')
        state = data.get('state') if data.get('state') in _MESSAGES else 'unknown'
        if state in _ACTIVE and not self._owned_process(scope):
            state = 'expired'
        stamp = data.get('ts')
        recent = type(stamp) in (float, int) and 0 <= time.time() - stamp < 600
        logged = state == 'success' and data.get('loggedIn') is True
        verified = logged and recent and data.get('verified') is True
        qr = self.qr_path(tenant_id, pid)
        ready = self.readiness()
        return {'state':state, 'message':_MESSAGES[state], 'qrReady':qr is not None,
                'qrTs':int(qr.stat().st_mtime * 1000) if qr else None, 'loggedIn':logged,
                'verified':verified, 'ready':bool(ready['python'] and ready['browser']), 'scope':'research'}

    def start_login(self, tenant_id, pid):
        scope = self.scope(tenant_id, pid, create=True)
        with self._guard(scope):
            if self._owned_process(scope):
                return self.login_status(tenant_id, pid)
            ready = self.readiness()
            if not ready['python'] or not ready['browser']:
                self._write(scope / 'status.json', {'state':'not_configured'})
                return self.login_status(tenant_id, pid)
            fd = os.open(scope / '.session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError(_MESSAGES['busy']) from exc
                for path in (scope / 'profiles', scope / 'profiles' / 'DouyinResearchProfile'):
                    if path.is_symlink():
                        raise ValueError('研究账号目录无效')
                    path.mkdir(exist_ok=True, mode=0o700)
                    path.chmod(0o700)
                (scope / 'qr.png').unlink(missing_ok=True)
                self._write(scope / 'status.json', {'state':'starting', 'ts':int(time.time())})
                command = self.command(scope, 'login')
                outcome = os.open(scope / 'login-result.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                try:
                    # The controlled runner emits only a bounded, allowlisted
                    # public result; keep it private so a later human probe does
                    # not erase the login failure's safe stage/type receipt.
                    proc = subprocess.Popen(command, cwd=scope, env=self._env(scope),
                        stdout=outcome, stderr=subprocess.DEVNULL, pass_fds=(fd,),
                        start_new_session=True, umask=0o077)
                finally:
                    os.close(outcome)
                self._processes[str(scope)] = proc
                self._write(scope / 'process.json', {'pid':proc.pid, 'command':command,
                                                    'stamp':self._paths._process_stamp(proc.pid)})
            except OSError:
                self._write(scope / 'status.json', {'state':'error'})
            finally:
                # The child holds the inherited lock until the official window closes.
                os.close(fd)
            return self.login_status(tenant_id, pid)

    @staticmethod
    def _stop_child(proc):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate(timeout=3)

    def verify(self, tenant_id, pid):
        scope = self.scope(tenant_id, pid, create=True)
        with self._guard(scope):
            # The person may still be scanning or completing a challenge in the
            # official window. Its runner observes success; do not contend with
            # its profile or replace the visible state with a lock error.
            if self._owned_process(scope):
                return self.login_status(tenant_id, pid)
        with self.session_lock(tenant_id, pid):
            command = self.command(scope, 'probe')
            proc = subprocess.Popen(command, cwd=scope, env=self._env(scope), stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, start_new_session=True, umask=0o077)
            try:
                output, _ = proc.communicate(timeout=45)
                value = json.loads(output) if proc.returncode == 0 and len(output) < 10_000 else {}
            except subprocess.TimeoutExpired:
                self._stop_child(proc)
                value = {}
            except ValueError:
                value = {}
            if not isinstance(value, dict):
                value = {}
            success = value.get('loggedIn') is True and value.get('verified') is True
            state = 'success' if success else ('challenge' if value.get('status') == 'challenge' else 'unknown')
            self._write(scope / 'status.json', {'state':state, 'loggedIn':success, 'verified':success,
                                               'ts':int(time.time())})
            self._private(scope)
        return self.login_status(tenant_id, pid)

    def disconnect(self, tenant_id, pid):
        scope = self.scope(tenant_id, pid, create=True)
        with self._guard(scope):
            owned = self._owned_process(scope)
            if owned:
                proc = self._processes.get(str(scope))
                if proc is not None and proc.pid == owned:
                    self._stop_child(proc)
                else:
                    os.killpg(owned, signal.SIGTERM)
                    deadline = time.monotonic() + 3
                    while self._owned_process(scope) and time.monotonic() < deadline:
                        time.sleep(.05)
                    if self._owned_process(scope):
                        os.killpg(owned, signal.SIGKILL)
            with self.session_lock(tenant_id, pid):
                profile = scope / 'profiles'
                if profile.is_symlink():
                    profile.unlink()
                elif profile.exists():
                    shutil.rmtree(profile)
                for name in ('qr.png', 'process.json', 'login-result.json'):
                    (scope / name).unlink(missing_ok=True)
                self._write(scope / 'status.json', {'state':'unknown'})
        return self.login_status(tenant_id, pid)


research_runtime = DouyinResearchRuntime()
