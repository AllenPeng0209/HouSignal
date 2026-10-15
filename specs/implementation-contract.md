# 楼响正式应用实现约定

本轮建设 React/TypeScript + FastAPI + PostgreSQL + 私有文件存储 + 独立 Worker 的可运行完整应用。保留 demo 与历史文档；正式代码位于 apps/。百炼配置只在根 .env，禁止打印、前端暴露或打包。

## 协作所有权

- core worker: apps/api/app/config.py, db.py, core_models.py, security.py, core.py, tests/test_core.py。
- business worker: apps/api/app/business_models.py, business.py, tests/test_business.py。
- agent worker: apps/api/app/agent_models.py, agent.py, provider.py, tests/test_agent.py。
- media worker: apps/api/app/job_models.py, jobs.py, media.py, worker.py, tests/test_media.py。
- web worker: apps/web/，不修改其他目录。
- root: app/main.py、tests/conftest.py、infra/、scripts/、数据库迁移、种子导入、部署与集成验证。

各模块导出 router（APIRouter，路由以 /api 开头）。根 main.py 组装。db.py 导出 Base, SessionLocal, engine, get_db。数据库模型主键 String UUID；JSON 字段普通 JSON；UTC 带时区日期。所有业务实体有 tenant_id, property_id；权限由服务端 Context 绑定。

## Core Python 接口（先建立供其他模块复用）

- config.settings: app_env, database_url, migration_database_url, dashscope_api_key（repr=False）, dashscope_base_url, dashscope_model, dashscope_vision_model, dashscope_asr_model, secret_key, pii_encryption_key, storage_root(Path), bootstrap_email, bootstrap_password, allowed_origins, ffmpeg_binary, ffprobe_binary。根 .env 读取。
- security.Context: user_id, tenant_id, role, name, email, project_ids（None 表示全部）。
- security.get_context(request, db=Depends(get_db)) -> Context。httpOnly hs_session；真实成员角色，不能由前端设置。
- security.context_for_user(db, user_id, tenant_id) -> Context；Worker 执行前重新查当前权限。
- security.require_project(db, ctx, project_id, roles=None) -> Property：租户、项目范围、角色校验；跨项目权限失败404。
- security.encrypt_pii/decrypt_pii，hash_password/verify_password。
- core.valid_fact(fact) -> bool；core.project_readiness(db, ctx, property) -> {percent,completed,total,items:[{key,label,complete,detail,target}]}。
- core.project_dict(db, ctx, property) -> DTO。

## 模型字段

core_models: User(id,email,password_hash,name,active), Tenant(id,name), Membership(id,user_id,tenant_id,role,project_ids JSON,active), LoginSession(id,token_hash,user_id,expires_at), Property(id,tenant_id,name,city,property_type,owner_name,audience,sales_owner_name,channel,marketing_confirmed,marketing_evidence,is_test,created_at), Fact(id,tenant_id,property_id,semantic_key,label,value,unit,source,status,revision,required,valid_until,updated_at), FactRevision(id,tenant_id,property_id,fact_id,revision,payload JSON,created_at), Asset(id,tenant_id,property_id,name,kind,storage_key,mime_type,sha256,byte_size,rights,rights_evidence,origin_type,metadata_json JSON,extracted_text,created_at)。Fact.value/source 使用文本但必须验证非占位，价格/面积需要明确单位。origin_type=actual/template/ai_generated/test；rights=pending/authorized/revoked。

business_models: ContentVersion(id,tenant_id,property_id,series_id,version,topic,script,fact_refs JSON,storyboard JSON,channel,cover_title,aspect_ratio,subtitle_style,render_asset_id,status,created_by,created_at), Release(id,tenant_id,property_id,content_version_id,status,snapshot JSON,snapshot_sha256,reviewer_id,review_note,created_at), Publication(id,tenant_id,property_id,release_id,title,channel,url,platform_state,receipt_status,metrics JSON,created_at), Inquiry(id,tenant_id,property_id,name,need,channel,source_confidence,source_text,contact_cipher,contactable,consent,qualified,owner_id,created_at), Lead(id,tenant_id,property_id,inquiry_id,name,need,stage,owner_id,next_action,source_confidence,stage_history JSON,created_at,updated_at), FollowUp(id,tenant_id,property_id,lead_id,note,next_action,created_by,created_at), Proposal(id,tenant_id,property_id,title,reason,status,source_refs JSON,created_at)。业务 worker 可增字段但先通知同事。

agent_models: Conversation(id,tenant_id,property_id,user_id,title,created_at), ChatMessage(id,tenant_id,property_id,conversation_id,role,content,citations JSON,status,job_id,created_at), ToolCall(id,tenant_id,property_id,conversation_id,job_id,name,arguments JSON,result_refs JSON,created_at), ModelUsage(id,tenant_id,property_id,job_id,model,input_tokens,output_tokens,created_at)。

job_models: Job(id,tenant_id,property_id,user_id,kind,status,payload JSON,result JSON,error,progress,attempt,lease_token,lease_until,created_at,updated_at)。状态 queued/running/succeeded/failed/cancelled。jobs.enqueue_job(db,ctx,project_id,kind,payload)->Job（不隐式commit）；jobs.job_dict(job)->DTO。worker 持久claim SKIP LOCKED、租约、attempt；晚结果不能覆盖新attempt。handler(db,job)->dict，同步接口，调用 provider 可用同步 httpx。

## HTTP API 统一合同

返回 JSON snake_case，集合用数组；错误 {detail:可展示简短说明}，不含secret/provider原始body。前端 /api 经Vite proxy到8017，端口5177；正式同源。Cookie会话，写操作校验Origin。

Core:
- POST /api/auth/login {email,password} -> {user:{id,name,email},organization:{id,name},role}; GET /api/auth/me 同形；POST /api/auth/logout。
- GET /api/projects -> Property DTO[]；POST /api/projects {name,city,property_type?,owner_name?,audience?,sales_owner_name?,channel?,marketing_confirmed?,marketing_evidence?} -> DTO；GET/PATCH /api/projects/{pid} -> DTO。
- GET /api/projects/{pid}/facts -> Fact DTO[]；PATCH /api/projects/{pid}/facts/{fid} {value,unit,source,valid_until?} ->新revision、unverified；POST .../{fid}/verify {confirmed:true}；POST .../{fid}/expire。
- GET /api/projects/{pid}/assets -> Asset DTO[]；POST .../assets multipart file -> Asset DTO；POST .../assets/{aid}/rights {rights,evidence}；GET .../assets/{aid}/file 鉴权文件；Asset DTO 不暴露本地storage_key；metadata_json可用于预览。
- Property DTO 至少 id,name,city,property_type,owner_name,audience,sales_owner_name,channel,marketing_confirmed,marketing_evidence,is_test,completeness。

Business:
- GET /api/projects/{pid}/overview -> {project,metrics:{published,inquiries,qualified,appointments,visits,views,unknown_rate},pending_inquiries,content_versions,publications,proposals}。
- GET .../topics -> [{id,title,angle,fact_refs,eligible,missing}]。
- GET .../contents -> ContentVersion[]；POST .../contents/generate {topic_id,channel?} -> {job_id}；POST .../contents/{id}/revise {script,channel?,cover_title?,aspect_ratio?,subtitle_style?,storyboard?} ->新ContentVersion。
- POST .../contents/{id}/render -> {job_id}；POST .../contents/{id}/approve {confirmed:true,note} -> Release；GET .../contents/{id}/export -> JSON包，须有效审批。
- GET .../publications -> Publication[]；POST .../publications {release_id,url,platform_state}；POST .../publications/{id}/verify {confirmed:true,platform_state,metrics?}。
- GET/POST .../inquiries；POST .../inquiries/{id}/qualify {contactable,consent,demand_confirmed,fit_confirmed,need?} -> Lead（四项人工确认）。
- GET .../leads -> Lead[]（sales只自己分配）；GET .../leads/{id} -> {lead,inquiry(脱敏),followups}；POST .../leads/{id}/followups {note,next_action}；POST .../leads/{id}/stage {stage}。
- GET .../reports?window=D7 -> {metrics,publications,proposals,window}；POST .../proposals/{id}/approve。
- Business handlers: handle_generate(db,job)-> {content_version_id}; business.registered_handlers 提供 dict(kind->handler)，render由media实现。

Agent:
- GET /api/model/status -> {provider:'bailian',model,configured}（不暴露key/endpoint）。
- GET/POST /api/projects/{pid}/conversations ->列表/新conversation；GET /api/conversations/{cid}/messages -> ChatMessage[]；POST .../messages {text} -> {job_id,user_message_id}。
- agent.handle_chat(db,job) -> dict；agent.registered_handlers={'chat':handle_chat}。
- 白名单工具查询实时数据库、固定服务端项目上下文，不能模型自选tenant/project/SQL。需支持summary/gaps/customer search/followups/content stats/document search，多轮Qwen function calling最多4轮、最多8工具/轮；不伪造工具调用。phone不送模型；仅保存授权范围内记录，sources权限重查。
- provider.chat(messages,tools=None,response_format=None,model=None)->dict（真实百炼HTTP compatible chat，保留usage，安全错误）；provider.generate_content(data)->结构化新稿（可由业务worker调用）。
- Citation {kind:'fact'|'asset'|'lead'|'inquiry'|'publication'|'content'|'page',id?,label,page?,revision?}；前端据kind打开对应业务页/记录。

Jobs/media:
- GET /api/projects/{pid}/jobs ->Job[]；GET /api/jobs/{jid} ->DTO；GET /api/jobs/{jid}/events SSE event:status data JSON，直到完成，断线后GET可恢复；POST .../{jid}/cancel。
- kinds: chat, generate_content, extract_asset, render_content。media.registered_handlers 提供 extract_asset/render_content。
- core上传后可在函数内import jobs.enqueue_job 创建 extract_asset任务（避免循环import）。media在合法用户授权下提取PDF/TXT/DOCX、图像OCR、音视频ASR；返回事实候选草稿，永不自动verified；FFmpeg渲染真正MP4与字幕，不运行模型命令。原文件私有，代理文件仍鉴权。

## 验证边界

TDD写有意义行为测试先红后绿。root conftest提供client/db/ctx fixtures（SQLAlchemy SQLite测试+最终真实PostgreSQL角色/RLS端到端验证）。各worker如需自有fixture放自己测试文件，不能覆盖共享conftest。不能只以source字符串证明功能。真实Qwen调用、真实Postgres持久数据、实际可解码MP4和浏览器交互由root最终验收。生产日志、测试报告、ZIP不得包含.env或api key。

## 验收后的兼容补充

- Conversation增加draft TEXT，默认空串；本人会话PATCH /api/conversations/{cid}/draft持久保存，发送成功清空。
- Inquiry增加request_key/request_hash/linked_lead_id；重试按request_key幂等，电话HMAC仅作非唯一候选索引，不自动证明身份；人工资格确认可显式关联同项目已有Lead。撤回覆盖所有关联咨询。
- 成片所有可见数字（脚本、封面、分镜字幕）共用事实校验；历史权限覆盖全部工具查询来源，不能只检查模型选择的显示引用。未授权原文不发模型。
