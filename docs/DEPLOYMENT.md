# 楼响 Web 单机部署与交付

本方案面向 Linux 云服务器：PostgreSQL、API、Worker、Web 和 Caddy 由 Docker Compose 管理。客户使用 HTTPS 网址和独立账号；Mac 关闭后业务服务器应仍可工作。生产配置叠加根目录的 `compose.yaml`，不修改本地启动方式。

本地配置检查和脚本测试不能证明云端已交付。只有目标服务器的镜像构建、HTTPS、真实登录、素材上传、模型任务、成片下载、重启和恢复演练都验收后，才可对外宣布完整交付。

## 2026-10-15 交付状态

本次入口为 [楼响试用环境](https://louxiang-preview.lovelingo.love)，部署目录 `/srv/louxiang`，Compose project 为 `louxiang-preview`。已完成云端试用部署。公网 HTTPS、真实浏览器登录、私有素材与成片下载、楼管家只读对话、ECS 整机重启恢复均已通过；这不等于商业效果或全部外部平台业务验收。

| 项目 | 已有证据与边界 |
| --- | --- |
| 组件验收 | 历史快照已验证真实登录和会话、合成素材上传、私有素材鉴权、FFmpeg Worker 成片和鉴权下载、完整音视频解码；CPU Whisper 与 U2NetP 执行成功，识别与抠图质量未审核 |
| 楼管家 Agent | 历史事故已精确取消并修复；最终真实只读对话成功、返回有效项目引用且没有写入回执。复测与诊断新增观测用量共 9,847 token，原临时额度已恢复 |
| AI 路由 | 普通文字技能为 `resident_ppu`；Pi Agent 按现有设计使用百炼 `qwen-plus`。PPU 身份与视频队列元信息可读，不能等同于 GPU 视频生成通过 |
| 数据恢复 | 已执行真实隔离恢复：29 张表，迁移版本 `c24101500002`，归档校验通过，未改生产数据；该演练未启动完整应用验证登录 |
| 定时备份 | 服务器时区 `Asia/Shanghai`，每天 03:30，加 0–15 分钟随机延迟，`Persistent=true`；备份服务已有实际成功运行记录 |
| 服务器外副本 | 初始备份已复制到运维本机并通过哈希校验；这不代表服务器外的持续定时复制已配置 |
| 最终发布 | 最终 API 镜像为 `louxiang-api:deploy-20261015-55025e68b9a1`；完整回归 2,665 项通过、5 项跳过、4 个子测试通过；已通过真实 ECS 重启，完整源码文件哈希与镜像 ID 见脱敏收据 |

初始组件验收仅使用明确标记的合成测试项目。3.6 秒、720×1280 的 H.264/AAC 视频来自 FFmpeg 制作和完整解码检查；没有提交 GPU 视频生成、人工审批、平台发布或 CRM 客户回执，不能据此证明这些能力或业务效果。

证据文件为 `reports/deployment-20261015/component-acceptance.json`、`agent-loop-incident.json` 和脱敏 `deployment-receipt.json`。组件报告的记录时间为 `2026-10-15T06:56:33.961380+00:00`，保留当时的失败状态；后续事故记录证明该任务最终为 `cancelled`、lease 已清除且连续两次观察没有新增用量。最终修复验收已分别保存在 `final-agent-canary.json`、`public-web-acceptance.json`、`reboot-acceptance.json` 和 `code-verification.json`，没有覆盖历史快照。事故用量来自应用台账，未核实供应商账单。

登录与运维访问资料保存在本机私有 `.runtime/cloud-deploy/access.txt`；本文档和脱敏收据均不包含密码、密钥或账号身份信息。交接时通过私有渠道提供该文件，不将其加入源码或公开报告。

## 服务器与网络

- 适用于有公网出口、Docker Engine、Compose v2 和 Python 3.6+ 的 Linux 主机。小团队可从 4 核 8 GB、100 GB 可扩容磁盘开始，容量须按真实素材与并发实测；模型推理使用独立服务。
- 域名解析至服务器公网 IP，安全组开放 TCP 80/443。SSH 22 仅允许运维来源 IP。数据库与 API 不发布公网端口；原 Web 的 8087 仅绑定服务器 loopback。
- Caddy 管理可信证书并将 HTTP 转为 HTTPS，证书数据写入持久 volume。必须使用能签发可信证书的域名；生产登录 Cookie 具有 Secure 属性，公网 IP 的普通 HTTP 页面不能作为完整使用入口。
- 中国内地云服务器的域名备案等接入要求按阿里云当时规则完成；境外地域、域名和原有网关按实际账户条件选择。
- 确认主机能拉取容器基础镜像和 npm/PyPI 依赖。首次镜像构建会安装 Chromium 等依赖，需要足够磁盘和时间。

2 核 2 GB / 40 GB 这类现有小主机可以先做低并发验收，但不能据此承诺实际团队容量。应在本地或构建机预构建 `linux/amd64` 镜像，传到主机 `docker load` 后使用 `--no-build`，避免云端镜像构建占满内存；保留磁盘余量并观察 Chromium、FFmpeg 和并发任务的资源使用。

## 首次配置

本次源码位于 `/srv/louxiang`。只部署审阅过的版本，不将开发数据库、`.runtime`、`.env`、模型权重或账号会话随源码公开上传。后续命令均从源码根目录执行。下列复制配置步骤仅适用于新环境；本次已配置的服务器继续使用现有私有 `.env`。

```sh
cp .env.example .env
chmod 600 .env
```

把 `infra/deploy/production.env.example` 中的生产字段合并至私有 `.env`：

| 字段 | 要求 |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | 本次固定为 `louxiang-preview`；改名会切换数据库和素材 volumes |
| `LOUXIANG_API_IMAGE` / `LOUXIANG_WEB_IMAGE` / `LOUXIANG_TUNNEL_IMAGE` | 明确的镜像版本标签或 digest；预构建部署时须先导入服务器本地。默认分别为 `louxiang-api:local`、`louxiang-web:local`、`louxiang-tunnel:local` |
| `DEPLOY_ORIGIN` | 仅填公共 HTTPS origin，例如 `https://studio.example.org`，末尾不带 `/` |
| `POSTGRES_PASSWORD` | 独立随机密码，至少 32 位，仅字母、数字、`_`、`-`，避免数据库 URL 转义问题 |
| `SECRET_KEY` | 独立随机密钥，至少 32 位 |
| `PII_ENCRYPTION_KEY` | Fernet 兼容的 32 随机字节 URL-safe base64；丢失后无法解密已有敏感数据 |
| `BOOTSTRAP_EMAIL` / `BOOTSTRAP_PASSWORD` | 管理员邮箱及独立密码，密码至少 12 位 |
| AI 与平台配置 | 依当前实际服务配置；留空的付费云模型并不等于已开通 |

密钥应在目标服务器私下生成并写入 `.env`，不通过聊天、命令回显或 Git 传递。首次创建数据库后，不可只改 `.env` 的数据库密码；已有 PostgreSQL 角色不会自动改密。密码轮换须先协调数据库角色、配置、备份与维护窗口。

当前项目实际使用的 `resident_ppu` 文本服务及 `lightx2v` 视频服务，不能直接沿用 Mac 上的 loopback 隧道。迁移到服务器时需同时处理：

- API、Worker 和 SSH 隧道共享 API 的 network namespace，Worker 使用 `network_mode: service:api`；仅两个容器都写 `127.0.0.1` 并不代表同一网络空间。
- SSH 隧道只监听该 namespace 的 loopback，端口不发布公网；主机指纹经可信渠道核验，不能关闭 host-key 校验。
- 只挂载楼响专属私有目录至 `/app/.runtime`，账号与认证文件属于 UID/GID `10001`，目录 0700、认证文件 0600。目录需要可写，运行时会使用锁文件；避免把整个开发者 HOME 挂进容器。
- `.env` 中 `PPU_RUNTIME_DIR` 和 `PPU_SSH_DIR` 为宿主机绝对路径，分别包含运行认证文件和专属 SSH 配置。目录检查不会读取或显示密钥内容。
- 若改用云模型，明确配置对应 provider、API key 和模型名，并单独验收费用与输出能力。不要将旧 PPU 服务的运行结果归因于尚未配置的云模型。

PPU 的云端连接需在目标服务器实际配置并验收后才能使用。脚本检测到 `resident_ppu` 或 `lightx2v` 后会自动叠加 `compose.ppu.yaml` 并管理 `ppu-tunnel`；只有文本后端为 `resident_ppu` 时才要求 `native-qwen.auth`，纯视频 PPU 配置不依赖文本认证文件；基础生产叠加文件本身不创建隧道，也不迁移任何具体密钥。

API 和 Worker 使用非 root UID `10001`，新增空 volume 应由镜像内对应目录的所有权初始化。已有 volume 若属于 root，需要在维护窗口检查并只修正这两个业务 volumes 的文件所有权，不要递归修改宿主机其他目录。Chromium 使用官方 Playwright 受限 seccomp profile、`init` 和每容器 512 MB `/dev/shm`；来源与 SHA-256 见 `infra/deploy/seccomp-profile.README.md`。若服务器内核或安全模块禁止 user namespace，先诊断主机策略，不应直接添加 `SYS_ADMIN` 或关闭 sandbox。

## 首次启动与管理员

```sh
python3 scripts/deploy/manage.py check
python3 scripts/deploy/manage.py deploy
python3 scripts/deploy/manage.py bootstrap
python3 scripts/deploy/manage.py verify
python3 scripts/deploy/manage.py status
```

若使用预构建镜像，首次启动将 `deploy` 替换为 `deploy --no-build`。部署前先通过 `docker load` 或 `docker pull` 从私有镜像仓库导入所有所需业务镜像，标签与 `.env` 一致；生产容器的架构须与目标主机一致。脚本以私有 `.env` 为预检与执行的同一配置来源，同名 shell 导出不会覆盖其中的镜像、数据库或入口字段，缺省镜像标签也保持一致。脚本会在启动数据库或迁移前通过 `docker image inspect` 确认 API/Web，以及启用 PPU 时的隧道镜像均已存在；缺少任何业务镜像会直接停止，不进入构建或启动。PostgreSQL、Caddy 和恢复演练的 Alpine 镜像也需要可用的拉取来源。

兼容 Compose 2.27：`--no-build` 是本脚本的部署模式，并保留在 `compose up`；`compose run` 不传此选项。管理员初始化与备份同样先检查其所需 API 镜像，防止一次性容器因缺镜像而隐式构建。

`check` 检查私有配置与 Compose 渲染；不输出渲染后的密钥。`deploy` 构建镜像、启动数据库、执行迁移和 RLS 安装、再启动 API/Worker/Web/Caddy；数据库迁移失败时不会替换现有应用容器。首次初始化管理员是独立步骤，不加载虚构项目；已存在的管理员不会被覆盖或重置。

`verify` 验证公共可信 HTTPS、生产 PostgreSQL 健康接口及 Web HTML，不能替代登录和内容制作验收。浏览器还需核验：登录与刷新、创建真实项目、上传授权素材、完成文本及视频任务、下载成片、成员权限、退出登录。平台扫码和验证码需通过受保护的远程验证方式由账号持有人完成。

部署脚本隐藏 Docker 错误原文，避免其夹带环境变量或访问地址。失败时在服务器私下查看对应容器日志，勿把完整 `.env`、`docker compose config` 或认证日志贴入公开渠道。

## 系统启动

`infra/deploy/louxiang.service` 供本次 `/srv/louxiang` 单项目服务器使用。当前源码版本在 Docker 与网络上线后执行 `/usr/bin/python3 /srv/louxiang/scripts/deploy/manage.py start`，由脚本预检镜像并按当前后端选择 Compose 叠加文件，然后执行 `up -d --no-build`。该入口与备份、升级共用 `/srv/louxiang/.runtime/deployment.lock` 的独占锁，避免开机启动在备份期间重新拉起写入服务；项目名沿用私有 `.env`。

由部署负责人在服务器安装：

```sh
sudo install -m 0644 infra/deploy/louxiang.service /etc/systemd/system/louxiang.service
sudo install -m 0644 infra/deploy/louxiang-backup.service /etc/systemd/system/louxiang-backup.service
sudo systemctl daemon-reload
sudo systemctl enable --now louxiang.service
sudo systemctl status louxiang.service --no-pager
```

该 oneshot unit 在启动后保持 active 状态，最长等待 180 秒，不包含 `ExecStop`；系统关机由 Docker 管理容器，避免额外给容器设置人工停止标志。此 unit 不构建镜像，也不替代部署脚本、数据库备份或业务验收。2026-10-15 安装的旧版 unit 曾通过真实 ECS 整机重启，自动启动服务、数据库、API、文字模型身份与视频服务元信息，以及重启后的浏览器登录和成片下载均已核验。当前源码新增的锁协调入口仅通过本地隔离测试，尚未重新安装到云端或进行 systemd 实机重启验收。更新服务器时须重新安装两个 service 并执行 `daemon-reload`。

## 升级

先上传并审阅新源码，保留上一个源码版本，再运行：

```sh
python3 scripts/deploy/manage.py upgrade
```

使用预构建交付时，先导入新版本镜像、更新私有 `.env` 的镜像标签，然后运行 `upgrade --no-build`。保留上个版本的镜像标签，避免 `:local` 被覆盖后失去版本回溯。

升级先生成完整备份，再构建、迁移并替换应用容器。备份期间短暂停止 Web/API/Worker 和已启用的 PPU 隧道（最多等待 60 秒结束进程），完成后只启动原来正在运行的服务。请安排维护窗口，运行中的长任务可能需要重新执行。数据库迁移可能不向后兼容；不要仅切换旧镜像就宣称完成回滚。恢复生产应在维护窗口用已验证备份恢复到新项目/volumes，经业务核验后再切换入口，保留原 volumes 作为回退点。

日常操作不要运行 `docker compose down -v`。生产项目名、`.env`、原数据库和素材 volumes 都是恢复所需上下文。

## 备份与恢复演练

```sh
python3 scripts/deploy/manage.py backup
python3 scripts/deploy/manage.py restore-drill /srv/louxiang/.runtime/production-backups/backup-时间标识
```

备份目录为 `.runtime/production-backups/backup-*`（目录 0700、文件 0600），包含：

- `database.dump`：容器中的 `pg_dump` 自定义格式导出；
- `storage.tar.gz`：上传素材、生成成片及其余私有存储；
- `platform_accounts.tar.gz`：平台登录会话和运行目录，属于敏感数据；
- `manifest.json`：完整性 SHA-256、时间与一致性说明。

备份先停止业务写入，数据库保持运行。归档 helper 仅以 root 执行 tar，读取 UID `10001` 的文件并写入宿主机预创建的私有文件，保留运维用户对输出的所有权；日常 API/Worker 仍使用非 root。平台 profile 只排除 Chromium 临时的 `SingletonLock`、`SingletonSocket`、`SingletonCookie`，不跟随任意链接；其余 symlink/hardlink 或不安全归档项会使校验失败，不能发布完整备份。

恢复服务时，脚本使用停止前记录的原容器 ID，按 API → PPU 隧道 → Worker → Web 逐个执行 `docker start`，避免共享 PID/网络空间的容器同时恢复。某个服务启动失败仍尝试后续服务，保留原始备份异常；私有 `recovery.json` 只记录失败服务及异常类型。存在恢复失败时输出保留为 `.partial-*`，不计为完整备份。脚本不自动删除历史备份。

`.env`、加密密钥、SSH 私钥、PPU token 和 Caddy 证书卷不在此业务备份中。它们需使用独立的私有密钥托管或加密备份；恢复时必须取得原 `PII_ENCRYPTION_KEY` 和有效的服务凭据。平台会话归档也必须加密后再送到独立存储。至少每天备份，并在首次交付和重要迁移后执行恢复演练；异地副本与定时调度需按目标账户配置，不由脚本悄悄创建云资源或定时任务。

本次已安装的 `louxiang-backup.timer` 使用服务器 `Asia/Shanghai` 时区，`OnCalendar=*-*-* 03:30:00`、`RandomizedDelaySec=15m`、`Persistent=true`。当前源码中的备份 service 还声明 `After=louxiang.service`，使开机补跑等待同时启动的应用 unit 结束，并与应用启动共用上述锁；这项顺序调整尚未在云端重新安装验收。服务器旧版 oneshot 服务实际执行 `/usr/bin/python3 /srv/louxiang/scripts/deploy/manage.py backup`，已有 `Result=success`、`ExecMainStatus=0` 记录；这项证据区别于仅启用 timer。下次运行曾显示为 `2026-10-16 03:32:02 CST`，后续时间以 `systemctl list-timers louxiang-backup.timer` 为准。

已验证的初始云备份为 `backup-20261015T071309Z-b25ed8e0`；隔离恢复得到 29 张表，版本 `c24101500002`。对应服务器外初始副本位于本机私有 `.runtime/cloud-deploy/backups/backup-20261015T071309Z-b25ed8e0`，四个文件已复制并通过哈希校验。服务器外的持续定时复制、长期保留和密钥托管仍需单独确认，不能由这次初始复制推定完成。

恢复演练先校验清单、全部归档 SHA-256 和 tar 路径安全，然后创建随机唯一的 `louxiang-restore-*` Compose project。它只使用独立数据库和素材 volumes，不读取生产 `.env`，不发布主机端口，也不连接生产 volumes。数据库执行 `pg_restore --exit-on-error`，素材与平台目录完整解包，随后检查表和迁移版本。无论成功或失败，都仅清理此次演练项目及其新 volumes；成功报告保存在 `.runtime/restore-reports/`。

演练证明备份文件可校验、数据库可导入、归档可解包；它不启动完整应用，不证明外部 AI、平台登录、真实用户流程或商业效果。

## 交付记录

记录实际云账号归属、服务器 ID/地域、固定入口域名、源码版本、镜像版本、管理员交接方式、模型连接与费用责任、备份位置、最近演练时间，以及已验证和待验证的流程。不要把真实密码或带访问授权的 URL 放入交付记录。

最低验收条件：运维 Mac 断开后，用户能独立完成登录、上传、内容任务、预览下载；服务器重启后服务与数据保留；最新备份通过隔离恢复演练。
