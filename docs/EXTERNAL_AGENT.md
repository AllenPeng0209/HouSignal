# 外部 Agent 连接楼响

安装、授权与使用说明见 [楼响远程 Agent 包](../integrations/louxiang-agent/README.md)。

正式连接地址是 `https://louxiang-preview.lovelingo.love/mcp`。ChatGPT／Codex 通过 OAuth 选择项目后，访问同一个楼响云端数据库；保存的项目策划、制作任务和进度与 Web 共用记录。独立 CLI 的 `tools` 和 `call` 使用 `/api/agent/tools`、`/api/agent/tools/call`，走相同授权与业务边界。

本仓库提供可供安装的便携插件格式 `plugin.json`／`mcp.json`、Codex 兼容描述、项目 marketplace、可复用 Skill，以及无第三方依赖的 Python CLI。它们不含凭据或伪造的 ChatGPT 应用标识。安装一个本地插件包、完成 OAuth、ChatGPT 真实调用、公开插件目录上架应分别核实，不能互相替代。

独立 CLI 可直接下载，无需配置本地模型或 OpenAI key：

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

先调用 `get_job` 读取真实产物名称与摘要，再用 `download --project-id … --job-id … --name … --out … --sha256 …` 下载受保护的预览。该命令不会转发凭据到别的域名、不会跟随重定向，也不会覆盖已有文件。正式交付必须另行通过审批有效性检查。

外部授权分为读取、保存策划、提交制作三类能力，并限定到明确选择的项目。事实确认、人工审核、对外发布、CRM 与交易信息不通过这些通用外部工具开放。制作预览与审核通过交付物在工具结果中保留不同状态。
