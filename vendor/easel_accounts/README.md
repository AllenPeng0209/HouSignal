# Easel platform account adapters in HouSignal

The seven platform login and publication adapters were copied from the local
Easel checkout of [ZJU-REAL/Easel](https://github.com/ZJU-REAL/Easel). The exact
source commit, original file hashes, copied file hashes and scoped-path changes
are recorded in `PROVENANCE.json`. `LICENSE` preserves Apache License 2.0.
No `.env`, cookies, account configuration or browser profiles are included.

`app.platform_runtime.PlatformRuntime` is the supported entry point. It uses
private sessions under `runtime/platform-accounts/<tenant hash>/<project hash>/`
and always passes explicit browser profile, QR, status, SMS and Bilibili cookie
paths. Each platform has one browser session per project. Session folders have
mode 0700; private artifacts have mode 0600. Identity probes distinguish an
unknown result from a confirmed logged-out account. A copied status marker is
last-known state; a successful live `whoami` probe is required before publishing.

The runtime uses `HOUSIGNAL_PLATFORM_PYTHON` when configured, then a sibling
`Easel/.venv/bin/python` if present, then the API interpreter. The last option
requires the minimal dependencies below. The current workstation reuses existing
Easel dependencies and browser binaries; no large package installation was run.

For a standalone Linux deployment, install in the selected interpreter:

```sh
python -m pip install -r vendor/easel_accounts/requirements.txt
python -m playwright install --with-deps chromium
```

The Playwright command installs Chromium and Linux system dependencies. Browser
binaries require disk capacity beyond this small source bundle. Bilibili login
needs `segno`; upload additionally needs the `biliup` executable on PATH. The
runtime prepends the selected Python environment's `bin` directory to PATH.
The account adapters themselves do not require FFmpeg; HouSignal publication
workers use its existing FFmpeg installation for validated video and cover work.

Browser platform selectors are best effort and can change. Platform anti-risk
checks may require the account holder's QR/SMS challenge and a normal network.
The optional local CloakBrowser binary is discovered by the copied XHS adapter;
regular Playwright Chromium remains its fallback. The account holder must finish
login in their phone app. Software and offline tests cannot establish that seven
real accounts have logged in.

`build_publish_command` only prepares fixed argv and defaults to dry-run.
`validate_publish_command` verifies the fixed adapter, project session paths and
minimal environment. `publish_lock` protects login, identity probing, logout and
publication from concurrent use of the same profile. Workers keep durable
one-shot publication state separately. Exit code zero alone is insufficient for
public acceptance: platform readback must confirm the submitted item. WeChat OA
`publish` creates a draft and never sends it to subscribers.

The vendored dependency closure includes `login_state`, `content_guard`,
`platform_readback`, `human_pace`, `calendar_ops`, `account_stats` and the Bilibili
upload wrapper. The six browser platforms use four scripts: XHS, Douyin, the
generic Kuaishou/Channels/Zhihu publisher, and the WeChat MP backend adapter.
Modified files carry a HouSignal change notice and honor `EASEL_ROOT` so fallback
diagnostics stay inside the private session.
