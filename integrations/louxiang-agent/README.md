# 楼响远程 Agent

楼响 MCP 和 CLI 操作同一个云端工作区。保存的策划、任务进度和素材会出现在楼响 Web；权限由授权时选定的项目和 scopes 约束。工具不会替人审核、确认事实或发布。

## Codex：两条命令连接

```bash
codex mcp add louxiang --url https://louxiang-preview.lovelingo.love/mcp --oauth-client-registration dcr
codex mcp login louxiang --scopes louxiang:read,louxiang:write,louxiang:production,offline_access --oauth-client-registration dcr
```

第一条命令可能直接启动授权；第二条用于未完成授权时继续登录。浏览器中使用楼响账号登录，核对域名、选择项目和权限。CLI 不接收账号密码，也不需要 OpenAI API key。安装配置后需在新的 Codex 对话中使用工具。

## 作为插件安装

从楼响下载并解压 `louxiang-plugin.zip`，在解压目录执行：

```bash
codex plugin marketplace add ./louxiang-tools
codex plugin add louxiang@louxiang-tools
```

本仓库内也可执行 `codex plugin marketplace add .`，marketplace 名为 `louxiang-tools`。插件包含楼响远程 MCP 与可复用的内容策划方法。未完成浏览器授权的连接仍不能读写项目；安装包不含任何凭据。包里的便携格式和 Codex 兼容格式均指向同一个 HTTPS `/mcp` 地址。

## ChatGPT：个人插件首次连接

在 ChatGPT 的 Plugins／外挂程式页面选择 **新增 → Add custom MCP server**。填写名称「楼响」、URL `https://louxiang-preview.lovelingo.love/mcp`，认证选 OAuth，动态客户端注册由服务器元数据发现。按界面完成连接；在楼响自己的授权页登录、选择允许访问的项目和权限。随后在新对话启用「楼响」。

这一步是 ChatGPT 账户里的真实连接；仅运行 `codex mcp add` 不会替 ChatGPT 网页创建个人插件。若当前账号未显示自定义 MCP 入口，需使用支持该入口的账号或管理员开放权限。注册完成后界面才会生成真实的 `plugin_asdk_app…` 标识；本包不会提供虚构 ID。个人连接和本地 marketplace 均不代表已上架公开插件目录。

连接后可直接说：

> 用楼响读取陆家嘴大宅案例和参考，提出三个选题，列出缺少的镜头。把我选中的方向保存到这个项目，先检查制作条件，再提交制作并告诉我进度。不要替我审核或发布。

## 独立 CLI

Python 3.10+，无第三方依赖。免安装版本用三条命令下载、核对 SHA-256，再打开浏览器授权：

```bash
curl --fail --silent --show-error --proto '=https' -o louxiang-manifest.json https://louxiang-preview.lovelingo.love/api/agent/downloads/manifest.json
curl --fail --silent --show-error --proto '=https' -o louxiang.pyz https://louxiang-preview.lovelingo.love/api/agent/downloads/louxiang.pyz
python3 - <<'PYTHON'
import hashlib, json, pathlib, subprocess, sys
package = pathlib.Path("louxiang.pyz")
expected = json.loads(pathlib.Path("louxiang-manifest.json").read_text())[package.name]["sha256"]
if hashlib.sha256(package.read_bytes()).hexdigest() != expected:
    raise SystemExit("SHA-256 mismatch; do not run this file")
raise SystemExit(subprocess.call([sys.executable, str(package), "login", "--server", "https://louxiang-preview.lovelingo.love"]))
PYTHON
```

需要短命令 `louxiang` 时，也可安装同一版本的 wheel：

```bash
python3 -m pip install --user https://louxiang-preview.lovelingo.love/api/agent/downloads/louxiang_agent-0.1.0-py3-none-any.whl
louxiang login --server https://louxiang-preview.lovelingo.love
louxiang status
louxiang tools
louxiang call list_projects --json '{}'
louxiang call get_project_context --json '{"project_id":"从工具返回的真实项目ID"}'
louxiang connections
louxiang logout
```

`tools` 返回当前已授权工具及其 JSON schema，调用时以该 schema 为准。`status` 与 `connections` 显示当前授权身份和项目，不枚举其他人的连接。`logout` 撤销当前访问与刷新令牌后移除本机凭据。刷新令牌会自动轮换。凭据位于 `~/.config/louxiang/credentials.json`（目录 0700，文件 0600；可用 `--config-dir` 指定另一目录）。

例如，把已选方向保存为 Web 可见草稿：

```bash
louxiang call save_plan --json '{"project_id":"替换为真实项目ID","idempotency_key":"为本次保存生成的唯一标识","plan":{"title":"三代同住，504㎡空间如何分配","angle":"比较家庭成员动线与公共空间，缺少实拍证据的部分列入待补镜头","channel":"B站","status":"draft"}}'
```

同一个保存或制作请求在重试时复用 `idempotency_key`，新的修改使用新 key。用 `get_plan` 重新读取真实返回的 `plan_id`，再在 Web 内容工作台核对。提交制作前先 `list_skills` → `preflight_skill`，满足条件后 `run_skill`，用返回的真实 `job_id` 调用 `get_job`。仅在服务返回审核有效时才把 `get_approved_delivery` 的结果称为正式交付。

免安装版把以下 `louxiang` 换为 `python3 ./louxiang.pyz` 即可：

```bash
python3 ./louxiang.pyz login --server https://louxiang-preview.lovelingo.love
python3 ./louxiang.pyz tools
```

下载制作预览文件时，使用 `get_job` 返回的实际任务标识、文件名和 SHA-256：

```bash
louxiang download --project-id PROJECT_ID --job-id JOB_ID --name short-video.mp4 --out ./研究视频.mp4 --sha256 ACTUAL_SHA256
```

下载使用当前授权的受保护接口，不能把私有文件链接交给另一台没有授权的 HTTP 客户端冒充已连接。CLI 只向当前服务器同源发送令牌，不跟随下载重定向；流式写入临时文件、完成 SHA-256 校验后保存，默认不覆盖已有文件。当前单文件上限 2 GiB。任务产物保持「预览」状态，审核通过交付仍以正式交付接口为准。

公共安装包和清单均在 `https://louxiang-preview.lovelingo.love/api/agent/downloads/`；插件包为 `louxiang-plugin.zip`。可先下载 `manifest.json`，按文件名取得 SHA-256，确认文件完整后再安装或解压。

浏览器 OAuth 使用 DCR、S256 PKCE、仅监听 `127.0.0.1` 的临时回调、随机 state 和 issuer 校验。公网仅接受 HTTPS；所有发现端点必须与服务器同源；不跟随重定向转发凭据。默认权限为 `louxiang:read`、`louxiang:write`、`louxiang:production`、`offline_access`，实际授权以浏览器中确认的范围为准。工具响应中的已知凭据与敏感字段会脱敏。

## 构建和核对

```bash
python3 integrations/louxiang-agent/build.py
.venv/bin/python -m pytest tests/test_external_agent_cli.py -q
```

产物在 `integrations/louxiang-agent/dist/`，包括 zipapp、纯 Python wheel、插件 marketplace zip 和 `manifest.json` SHA-256 清单。 此目录由源码构建产生，不随 Git 或正式源码 ZIP 分发。本地首次启动 API 前执行上述构建；Docker API 镜像在构建时从仓库源码运行同一构建器，不依赖开发机的 `dist/`。构建脚本不读取本机凭据、不修改全局配置，也不会联网。安装与连接成功必须分别验证：工具发现、一次真实读写、重新读取同一记录，以及 Web 展示对应变化。

官方格式说明：[插件包与 marketplace](https://developers.openai.com/plugins/build/plugins)、[OAuth 认证](https://developers.openai.com/plugins/build/auth)。
