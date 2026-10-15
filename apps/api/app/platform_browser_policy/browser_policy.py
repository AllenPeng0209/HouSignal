"""Browser launch policy loaded only in scoped platform child interpreters."""
from functools import wraps

_UNSAFE_FLAGS = {
    '--no-sandbox', '--disable-sandbox', '--disable-setuid-sandbox',
    '--disable-seccomp-filter-sandbox', '--disable-gpu-sandbox',
    '--disable-web-security', '--allow-running-insecure-content',
    '--disable-site-isolation-trials', '--unsafely-treat-insecure-origin-as-secure',
}
_UNSAFE_FEATURES = {
    '--disable-blink-features': {'automationcontrolled'},
    '--disable-features': {'isolateorigins', 'site-per-process'},
}


def _options(options):
    secured = dict(options)
    safe_args = []
    for argument in options.get('args') or []:
        if not isinstance(argument, str):
            raise ValueError('Browser launch arguments must be strings')
        flag, separator, value = argument.partition('=')
        key = flag.lower()
        if key in _UNSAFE_FLAGS:
            continue
        if key in _UNSAFE_FEATURES:
            if not separator:
                continue
            features = [feature for feature in value.split(',')
                        if feature.lower() not in _UNSAFE_FEATURES[key]]
            if features:
                safe_args.append(flag + '=' + ','.join(features))
            continue
        safe_args.append(argument)
    if 'args' in options:
        secured['args'] = safe_args
    secured['chromium_sandbox'] = True
    ignored = options.get('ignore_default_args')
    if ignored is True:
        secured['ignore_default_args'] = False
    elif isinstance(ignored, list):
        secured['ignore_default_args'] = [argument for argument in ignored
            if isinstance(argument, str) and argument.partition('=')[0].lower() != '--enable-automation']
    elif ignored not in (None, False):
        raise ValueError('Browser default argument policy is invalid')
    for name in ('bypass_csp', 'ignore_https_errors'):
        if name in secured:
            secured[name] = False
    return secured


def _protect(browser_type, asynchronous):
    for method in ('launch', 'launch_persistent_context'):
        original = getattr(browser_type, method)
        if getattr(original, '_housignal_browser_policy', False):
            continue
        if asynchronous:
            @wraps(original)
            async def guarded(self, *args, _delegate=original, **kwargs):
                return await _delegate(self, *args, **(_options(kwargs) if self.name == 'chromium' else kwargs))
        else:
            @wraps(original)
            def guarded(self, *args, _delegate=original, **kwargs):
                return _delegate(self, *args, **(_options(kwargs) if self.name == 'chromium' else kwargs))
        guarded._housignal_browser_policy = True
        setattr(browser_type, method, guarded)


def install():
    from playwright.sync_api import BrowserType as SyncBrowserType
    from playwright.async_api import BrowserType as AsyncBrowserType
    _protect(SyncBrowserType, False)
    _protect(AsyncBrowserType, True)
