# 楼响手机端

Expo SDK 57 / React Native 0.86 / Expo Router，覆盖 iOS 和 Android。连接楼响现有 FastAPI、PostgreSQL 和 Worker，项目、资料、内容和客户与网页端共用。界面与数据不是本地演示。

## 已开发功能

- 可撤销的设备会话、凭证安全存储、会话恢复、实时组织和项目权限。
- 工作台、多项目搜索/新建/切换、七项资料准备度、营销范围与事实核验。
- 拍照、拍视频、相册、文件、录音、上传；私有预览/分享、素材授权和独立 AI 处理许可。
- 楼管家真实对话、项目会话、服务器草稿、任务进度/取消、原记录引用。
- 选题、生成、脚本/分镜/版本编辑、真实渲染、成片预览、人审、JSON/MP4/SRT/封面分享。
- 发布回执、独立平台状态、D7/D30 指标；未观察数据保持未知。
- 咨询幂等录入、四项资格确认、授权撤回、销售分配、客户阶段/跟进、经审计的联系查看和拨号。
- 账号策略、选题机会、内容排期、真实技能执行/结果/产物、效果复盘、组织成员和权限。
- 任务中心、前台任务完成提醒、返回前台后的任务恢复。

权限由服务器执行。内容编辑、资料编辑、人审、销售、组织管理分别按实际角色和成员项目范围控制。分享文件先鉴权下载到私有临时目录，经手机分享菜单导出，然后清理；App 创建的录音和文档副本也会清理，保留相册/相机原件。客户联系方式不持久存储、不进入模型工具。

## 本地运行

在仓库根目录先启动原有 PostgreSQL 和 Worker；已有运行实例不需要重复启动。手机端验收使用独立 API 端口 8018，不中断现有 8017 网页服务：

```sh
ALLOWED_ORIGINS=http://localhost:8081,http://127.0.0.1:8081,http://localhost:8087,http://127.0.0.1:8087 PYTHONPATH=apps/api .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8018
cd apps/mobile
npm ci
npm start -- --lan --port 8081
```

iOS 模拟器默认服务为 `http://127.0.0.1:8018`，Android 模拟器为 `http://10.0.2.2:8018`。登录页可设置服务地址。真实手机使用部署后的 HTTPS 地址，或开发构建连接本人允许的局域网开发服务。生产版本拒绝 HTTP；不在 App 内打包模型密钥、数据库密码或固定用户凭证。

```sh
scripts/ios-build-local.sh Debug --install
npx expo run:android --port 8081
npm run web -- --port 8087
```

App 包标识当前为 `com.housignal.mobile`，首次正式发行前应与自己的 Apple/Google 开发者账号和域名核对。

iOS 模拟器的已验收启动方式使用 `scripts/ios-build-local.sh`，默认 Metro 为 8081，通过 `-RCT_jsLocation 127.0.0.1:8081` 启动；可用 `RCT_METRO_PORT` 和 `SIMULATOR_UDID` 覆盖端口及设备。脚本在编译后执行仅适用于本项目模拟器产物的 ad-hoc 签名，唯一权限为 `com.apple.security.application-groups=[group.com.housignal.mobile.simulator]`，用于模拟器内安全凭证存储。不使用用户 Apple 证书，未将该 group 写入生产配置；真机与商店发行须使用自己的 Apple provisioning profile。详见 `reports/ios-verification.md`。

## 验证

```sh
npm test
npm run check
npx expo lint --no-cache
npx expo-doctor
npm run export:web
npx expo export --platform ios --platform android --output-dir dist-native
```

`../../.venv/bin/python scripts/run_browser_acceptance.py` 会通过内存传递开发账号，创建明确标识的合成验收项目，并验证真实后端和手机网页渲染。报告和截图在 `reports/`。真实模型/渲染验收会调用已有百炼配置，合成项目与产物保留供复核；报告不得包含凭证。

## 打包与发布

仓库提供 `eas.json`。填写正式 `EXPO_PUBLIC_API_URL`、自己的 EAS 项目和签名账号后，可执行 `npx eas-cli@latest build --platform all --profile preview`；生产用 `production`。iOS 真机、TestFlight/App Store 和 Android 商店提交需要对应开发者签名与实际设备验收，本地模拟器构建不等于上架。

当前任务提醒由前台/返回前台的服务器轮询和本地通知实现，不承诺 App 被系统挂起或关闭时实时推送。后台远程推送需要正式 APNs/FCM/EAS 配置和服务器发送链路，当前未启用。素材单文件上限 100MB；SDK 57 安全 multipart 上传会在内存缓冲，下载采用分块写文件。网络失败不会自动重复提交收费生成任务；咨询重试采用相同请求键。
