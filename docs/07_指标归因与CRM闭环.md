# 楼响 HouSignal：指标、归因与轻 CRM 闭环

版本：v0.1｜日期：2026-10-11｜适用范围：首个付费试点，1 个豪宅销售团队、1 个楼盘、1 条内容生产链路、1 个主线索入口。本文为待开发的数据与运营合同，不代表已有平台权限或真实获客结果。

业务语义以本文为准；数据库字段、约束、事件和端点的正式定义统一在 [数据模型与 API 契约](06_数据模型与API契约.md)，实施时两份共同核对。QualifiedLead 按 tenant+property+contact 唯一，通过 origin_campaign_id 和 CampaignLeadLink 保留多活动关系；未来新活动不能给同楼盘同人再建第二个有效线索以抬高总数。

## 1. 产品要回答的问题

楼响要让负责人知道：哪些内容帮助购房者提出了真实问题，销售有没有及时响应，咨询为什么进入或没有进入预约，以及下一条内容应该补充什么证据。播放量、粉丝量、手机号数不能直接证明内容带来有效购房需求。

MVP 默认内置轻 CRM。运营在抖音人工发布，在平台内人工承接咨询，再依据合法处理依据和客户授权录入最小必要摘要；小红书只做适配导出及人工回执。外部 CRM 双向集成、自动私信、自动投放不是闭环的前提。

业务验收同时看三类证据：发布与内容的对应关系、销售对需求的人工确认、能够复查的后续状态记录。未发生真实咨询时，可以验收技术链路，但不能宣布获客假设已成立。

## 2. 漏斗定义与状态合同

下表的 `view` 属于平台汇总数据，不在 CRM 中给每个观看者建档。`inquiry_candidate` 是资格前的咨询阶段，`contactable` 是咨询联系属性；两者不属于 `QualifiedLead.current_stage`。资格前使用 Inquiry+Contact，人工确认需求后创建 QualifiedLead 与 lead_id。历史事件与当前状态分别保存。

| 阶段 | 进入条件 | 必要凭证与字段 | 不算进入的情况 |
| --- | --- | --- | --- |
| `view` 观看 | 平台后台有该发布记录的观看数及统计窗口 | `publication_id`、平台原始指标名、观测时间、回执 | 把曝光当观看；把播放次数当独立人数 |
| `inquiry_candidate` 待确认咨询 | 购房者主动提出楼盘、居住需求或看房相关问题；运营/销售确认非垃圾互动 | `inquiry_id`、内容摘要、首次咨询时间、录入人、来源证据等级 | 点赞、泛评论、抽奖留资、同行报价、机器人、纯素材下载 |
| `contactable` 可联系属性 | 存在可合法继续回应的渠道，且回应/跟进目的有明确授权或相应处理依据 | 平台原会话或客户提供渠道、`consent_id`/合法依据、有效期、负责销售；Inquiry/Contact.contactable=true | 仅有手机号，无法确认来源或跟进授权；抓取的用户账号 |
| `qualified` 销售确认需求 | 已可联系；销售通过交流确认真实购房需求、与试点楼盘的相关性及愿意继续沟通 | 创建 qualified_lead 对象；人工 `qualified_by`、`qualified_at`、需求摘要、判断理由 | 模型评分高；预算看起来高；销售未沟通；电话打通但无需求 |
| `appointment` 已预约 | 双方确认具体时间及看房/咨询方式 | 预约时间、确认渠道、销售、证据摘要 | 销售单方提出时间；“有空来看看” |
| `visit_completed` 已完成到访 | 实际看房或约定的正式远程看房完成；爽约不算 | 完成时间、到访方式、销售确认、客户反馈摘要 | 预约未到；一段未确认观看的楼盘视频 |
| `opportunity` 商机 | 销售确认进入具体房源/户型的后续交易讨论 | 商机建立时间、目标房源/户型、下一行动、销售理由 | 一次到访自动转商机 |
| `won` 成交 | 有可核对的成交确认，统一采用试点客户的合同签署口径 | 成交日期、脱敏内部合同编号、主管确认；金额为可选受限字段 | 口头意向、定金截图未经确认、预计成交 |
| `lost` 流失 | 客户明确不再推进该楼盘或销售确认机会结束 | 时间、标准原因、销售说明 | 暂时未回复直接判流失 |
| `withdrawn` 撤回 | 客户撤回相应同意或明确要求停止联系/删除 | 目的范围、时间、接收人、停止跟进与删除执行回执 | 删除一个手机号但仍继续通过另一渠道营销 |

`QualifiedLead.current_stage` 唯一枚举为 `qualified/appointment/visit_completed/opportunity/won/lost/withdrawn`。`qualified_lead` 是对象与业务数量称谓，不是状态值。所有过去的合法阶段按事件保留最小审计痕迹；涉及删除时移除可识别信息。业务结果统计与“当前允许跟进的线索”分开展示。任意个体阶段都可撤回：资格前更新授权和咨询处理状态；已有 QualifiedLead 则可记为 withdrawn，优先于未执行的跟进任务。

正常业务路径为 `inquiry_candidate → contactable=true → qualified → appointment → visit_completed → opportunity → won/lost`；其中资格前路径不写入 QualifiedLead 状态机。允许记录真实跳跃，例如客户直接到访，但必须同时补齐可联系与需求确认凭证。状态倒退、爽约、重复预约作为事件保存；不能覆写首次到达阶段的时间。`lost` 后重启须人工新增重启事件；撤回后再次授权须新建授权记录，旧授权不得恢复。

## 3. 线索入口与发布绑定

`LeadEntry.capability_mode` 固定为以下三种之一，试点默认 `manual_native`：

| 模式 | 真实入口 | 启用条件 | MVP 记录方式 |
| --- | --- | --- | --- |
| `manual_native` | 平台站内人工咨询承接；由购房者主动发起 | 账号能够正常发布、回复；相关行业与商业规则核对完成 | 运营人工录入咨询摘要；经授权补充联系方式 |
| `approved_native_component` | 已获准的官方预约/留资组件 | 客户主体、类目、账号、组件和权限均已批准；实机测试形成回执 | 优先官方后台导出/人工录入；API 后续单独验收 |
| `owned_form_allowed` | 自有合规表单 | 该平台、账号、具体发布位置允许相应入口；授权与隐私告知上线；人工测试通过 | 保存合法来源参数与授权回执，不假设平台允许外链 |

一套试点使用一个主 `entry_id`，每次发布仍有独立 `publication_id`。入口属于 campaign，但不存在“一个入口就能天然识别所有内容”的承诺。入口缺少内容参数时，允许只确认平台/活动，内容来源为未知。

每份发布包包含已批准内容版本、平台标题与封面、CTA 文案、入口模式、事实校验记录、AI 标识说明。发布后人工登记 URL/平台作品编号、账号、时间、`release_id`、入口是否实际可见、脱敏截图。Publication.status 统一为 `recorded/verified/removed/inconsistent`：人工录入 recorded，核对版本、作品、入口和标识一致且有回执后 verified；下架 removed；回执与批准版本不符 inconsistent。`publication.confirmed` 可以保留为将 recorded 核对为 verified 的事件名，不作为状态枚举。导出文件不算发布。

抖音官方公开的线索小程序组件包含房地产相关类目，但仍需申请开通及配置。官方私信开放资料当前提示新增准入受限，不能把第三方私信能力作为首版依赖。[抖音线索组件](https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/tutorial/open-capabilities/monetization/clue)、[抖音线索能力开放](https://partner.open-douyin.com/docs/resource/zh-CN/dop/ability/enterprise-account-open-ability/leads-b2c-im-ability/clue-component-open)。资料存在不等于客户已获权限；上线前的核对见 [内容真实性、数据安全与平台规则](08_内容真实性数据安全与平台规则.md)。

## 4. 核心数据字典

### 4.1 全链路主键

ID 使用服务端随机生成的不可猜测标识。所有业务表、对象存储元信息、任务和查询都带 `tenant_id`；数据库关系校验同租户，不能只靠前端隐藏。

| 字段 | 对象与含义 | 必要关联/规则 |
| --- | --- | --- |
| `tenant_id` | 客户企业租户 | 数据隔离的边界；一家企业跨渠道仍为同租户 |
| `property_id` | 试点楼盘 | 归属租户；可附具体房源子对象，不能跨楼盘误归因 |
| `campaign_id` | 内容获客试点/活动 | 绑定楼盘、试点日期、统一口径与主入口 |
| `content_id` | 选题内容的稳定身份 | 同一内容的版本、平台适配和发布可追溯 |
| `content_version_id` | 冻结的具体内容版本 | 任何审核后的脚本/视觉/CTA 改动生成新版本 |
| `fact_id` | 经核验的事实卡 | 源材料、范围、日期、审核人、失效时间；输出逐条关联 |
| `asset_id` | 原始或衍生素材 | 来源、授权、处理链与 AI 参与情况 |
| `release_id` | 审批通过的可导出发布包 | 绑定版本、平台、入口和审批；重导出可复用但保留导出事件 |
| `publication_id` | 一次真实的平台发布 | 绑定 release、平台、账号、作品编号及发布时间；跨平台各建一条 |
| `entry_id` | 主线索入口 | 保存能力模式、权限证据、告知版本；变更入口时保留历史 |
| `source_touch_id` | 证据支持的一次来源触点 | 发生时间、平台、可确认到的 campaign/publication、证据等级 |
| `inquiry_id` | 一次主动业务咨询 | 允许无联系方式；与触点多对多；不能把泛互动当咨询 |
| `lead_id` | 某联系人在该楼盘的 QualifiedLead 对象 | 人工确认 qualified 时创建；同租户、同楼盘去重；资格前 Inquiry.lead_id 为空 |
| `contact_id` | 客户身份/可联系渠道对象 | 可有多个平台渠道；原始联系信息独立加密存储 |
| `consent_id` | 一次目的明确的授权记录 | 主体、目的、范围、告知版本、时间、取得方式、撤回状态 |
| `run_id` | 一次 Agent/生成/复盘执行 | 模型版本、输入事实集合、费用与产物；不携带原始联系人信息 |

### 4.2 业务最小字段

下面采用正式对象名和主要字段；用于报表的 first_inquiry_at、contactable、first_stage_times 等为派生业务视图，不是另一套写入API或数据库合同。具体字段、约束、端点和枚举由[数据模型与 API 契约](06_数据模型与API契约.md)唯一裁定。Inquiry 是 InquiryCandidate 的运营简称。

| 表/对象 | 首版字段 | 校验与展示 |
| --- | --- | --- |
| `Publication` | 上述 ID、platform、account_ref、platform_post_id、published_at、receipt_asset_id、status、platform_state、traffic_type | status=recorded/verified/removed/inconsistent；platform_state=review_pending/live/rejected/restricted/removed/unknown；platform_post_id+账号+租户唯一；traffic_type为报表分类 |
| `MetricSnapshot` | metric_snapshot_id、publication_id、metric_name、metric_value、metric_unit、metric_definition、window_start/end、observed_at、source_type、receipt_asset_id、recorded_by、unknown_reason | 原始名与统一名并存；unknown=null，不能存 0；同观察窗口比较 |
| `SourceTouch` | source_touch_id、entry_id、publication_id 可空、channel、occurred_at、source_confidence、source_granularity、evidence_type、evidence_ref、confirmed_by | 必须注明事实发生时间，不能用录入时间假装首次触达；A/B/C/U 仅为派生看板等级；联系人经咨询关联，不向匿名触点默认注入身份 |
| `InquiryCandidate` | inquiry_id、campaign_id、property_id、entry_id、contact_id 可空、received_at、inquiry_summary、requested_service、source_touch_id、assigned_sales_id、status、qualification_decision_ref、is_test | 只记录购房问题；first_inquiry_at和contactable由事件/授权派生；原始私信不默认上传；QualifiedLead关系由资格确认产生 |
| `QualifiedLead` | lead_id、origin_campaign_id、contact_id、property_id、current_stage、owner_sales_id、next_action_at、property_fit_or_followup_reason、qualified_by/at、is_test | current_stage 从 qualified 起；首次阶段时间由事件派生；必须人工操作者和理由；未知预算/时间可为空 |
| `Contact` | contact_id、display_alias、identity_resolution、pii_status、merged_into_contact_id、is_test；渠道另存 EncryptedContactChannel | 昵称非身份认证；展示默认掩码；非唯一匹配 key 也按个人信息控制 |
| `Consent` | consent_id、contact_id、purpose、notice_revision、text_snapshot_sha256、granted_at、recorded_at、grant_method、evidence_ref、status；撤回另记ConsentEvent | purpose=inquiry_reply/appointment/continuing_marketing；grant_method=web_form/native_component_evidence/manual_attestation；生成模型处理默认关闭 |
| `StageEvent` | lead_id、from_stage、to_stage、occurred_at、recorded_at、actor_id、reason、evidence_ref | 只追加；更正用 correction_of 指向原事件，不删除历史以提高指标 |
| `Feedback` | lead_id、reason_code、deidentified_summary、approved_for_content_learning、approved_by | “需求不匹配”“事实不清”等受控标签；不写净资产、银行账户、家庭隐私 |

示例 reason_code：`layout_mismatch`、`location_mismatch`、`price_unclear`、`evidence_missing`、`timing_not_ready`、`project_not_fit`、`no_reply`、`duplicate`、`peer_or_spam`、`customer_withdrew`。`no_reply` 只说明目前未回复，不暗示无购房能力。

时间存 UTC，展示 Asia/Shanghai；同时存 `occurred_at` 与 `recorded_at`。人民币金额记录币种和口径，MVP 不收资产证明。浏览事件不存设备指纹、精确位置；无必要不增加 cookie 或匿名访客身份链。

## 5. 去重与一人多渠道

1. 每次咨询先保存 `inquiry_id`，不能为去重丢掉真实询问次数；首次咨询计数按合并后的 contact+property 去重。缺少可确认身份时，仅按合法可用的同一会话记录去重，否则保留独立咨询对象并标“身份未确认”；不能称全网独立人数。
2. 同租户已有合法取得的同一联系方式时，系统只给出合并候选。销售确认是否为同一人；共享家庭手机号不能自动认定是同一人。
3. 客户主动确认不同平台账号属于本人，或有官方授权范围内可验证的统一标识时，可以合并 `contact_id`。名字、头像相似不能证明同一人，不做平台身份反查。
4. 合并保留原 ID、渠道、授权、全部触点与操作日志；提供人工拆分。授权只按原目的生效，不因合并扩大到所有渠道。
5. 同一联系人对同一楼盘在人工 qualified 后使用一个主 lead，不因换平台增加有效线索数。未来涉及另一个楼盘时是独立 lead；是否可以跟进须重新核对目的与授权。
6. 严禁跨租户匹配/共享/出售联系人。同一个人咨询两个客户企业，也不向双方透露这一事实。

租户内匹配键采用租户隔离的 HMAC，不用裸手机号 hash。原始号码独立加密；匹配键不得进入模型、日志或导出默认模板。

## 6. 归因合同：证据决定精度

### 6.1 证据等级

统一存储字段为 `source_confidence=confirmed/self_reported/inferred/unknown`，分别表示直接确认、客户自述、待核推断、未知。另用 `source_granularity=publication/campaign/channel/unknown` 表示能确认到哪一层。`evidence_type=observed_link/platform_export/customer_statement/manual_attestation/none` 只表示凭据来自链接、平台导出、客户陈述、人工见证或无凭据，不代替置信判断。以下 A/B/C/U 是看板的证据与精度等级，不另建互斥置信枚举：A 对应 confirmed 且可到具体作品；B 对应 self_reported 且可到具体作品；C 为 confirmed 或 self_reported 但只能到渠道/活动；U 为 unknown。`inferred` 仅可作为内部待核备注，不能进入作品归因或 first/last 已确认触点；不能将渠道已知的 C 等同猜测。

| 等级 | 能说什么 | 证据示例 | 看板处理 |
| --- | --- | --- | --- |
| A：官方或直接参数证据 | 该咨询由已批准入口的具体作品/活动承接 | 官方来源字段、获准入口的真实 publication 参数、客户直接发送该作品链接 | 可显示确认到的作品来源；同时记录 capture_mode |
| B：客户明确确认 | 客户称看过该平台某内容后发起咨询 | 客户明确指出作品/主题，销售人工确认并写摘要 | 显示“客户自述”；不是独立验证曝光，也不是因果证明 |
| C：只知渠道/活动 | 确认平台或楼盘活动，无法确认具体作品 | 平台站内咨询，但无作品参数或明确自述 | 计渠道/活动咨询，作品归因未知 |
| U：未知 | 无可靠来源，或证据矛盾 | 电话询问不记得来源；销售猜测来自爆款 | 放入 unknown，不能分摊给播放量最高的作品 |

截图是可复查的人工证据，不是平台 API 验证。记录截图原始窗口、账号、采集时间、录入人；私信证据脱敏并限制查看。后台自动识别截图数值只能生成待确认草稿，不直接记账。

### 6.2 首次与末次已确认触点

归因对象为该楼盘 contact 的首次有效咨询，qualified 后关联 lead。使用首次咨询前 30 天内、且 `touch_at <= first_inquiry_at` 的已确认触点。最早为 `first_confirmed_touch`，最晚为 `last_confirmed_touch`。这是本产品建议的分析窗口，不是行业默认或平台官方归因规则。

无法识别实际观看时间时不伪造 touch_at：客户只说“前几天看过”保存时间区间及不确定性，不参与精确的先后排名。销售回访、咨询后再发的视频不回填为咨询前触点。没有已确认触点则两个字段为空；保留 channel_known/content_unknown 或 source_unknown 原因。

两种视角分别做报表：首次触点用于发现需求来源，末次触点用于理解咨询前的最近信息。禁止把两张报表的数字相加。每张报表内同一楼盘 contact/qualified lead 只能贡献一次；多内容辅助触点单独列为“参与触点”，不再加进总有效线索。

自然与付费来源分开；原有客户、转介绍、存量跟进标 `existing/referral/returning`，避免把历史资源全部记为 AI 获客。没有对照或随机实验，不能从“使用楼响期间增加”推断“楼响让算法涨量”或“AI 导致成交”。

## 7. 时间窗口与可比口径

| 报表 | 起点/窗口 | 计数对象 | 未成熟处理 |
| --- | --- | --- | --- |
| 单发布 D7 | published_at 后 168 小时 | 该 publication 的平台累计指标及窗口内首咨 | 距发布不足 168 小时标进行中，不与成熟 D7 排名 |
| 单发布 D30 | published_at 后 720 小时 | 该 publication 的平台指标及窗口内首咨 | 同上；超窗数据另列累计，不能冒充 D30 |
| 首咨 cohort D30 | 每个去重业务咨询对象的 first_inquiry_at 后 720 小时 | 可联系属性、需求确认、预约、到访、商机、成交状态 | 发布满 D30 不代表首咨 cohort 满 D30，分开标明 |
| 试点总览 | 试点实际开始至截止日 | 去重业务咨询对象、唯一 qualified lead、生产成本、耗时、已观察状态 | 标截止时间、成熟人数、身份未确认数、未知来源率；不当完整 D30 |

人工截图未在准确 D7/D30 时取得，保存实际观测窗口；标成“约 D7”而非精确 D7，不纳入精确窗口比较。数据仅后台提供累计量时不做小时级拆分。抖音与小红书的原始定义不同，不把完播率、阅读量等拼成同一排行榜。

内容改版重发是新 `publication_id`；同版内容发布在不同账号也是新发布。同一个帖子更换封面/标题记录 change event，比较时排除或明确分段。账号、内容长度、平台、房源、自然/付费、观察窗口作为分组维度。

## 8. 指标公式与分母

所有指标先限定 tenant、property、campaign、cohort、排除规则和截止时间。技术模拟对象、演示发布/咨询和内部联系人必须 `is_test=true`，默认排除全部商业报表；测试页面显著标注模拟。分母为 0 显示 `N/A`。小样本展示原始分子/分母，不只展示百分比。“有效咨询人数”指同楼盘的去重业务相关咨询对象：已知身份用 contact 去重，未能确认身份的咨询另列，不假称已完成跨渠道人的去重。后续 qualified 数量以唯一 contact+property 计数。

| 指标 | 公式 | 用途/限制 |
| --- | --- | --- |
| 审核后可发布产出数 | 有批准 release 且事实审查通过的内容版本数 | 不把反复生成草稿当生产产出 |
| 实际发布完成率 | status=verified 且 platform_state=live 的 publication / 计划且到期的 publication | 取消计划保留理由；导出与仅录入不算完成；其他回执或平台状态单列 |
| 单条生产总人时 | 资料准备、选题、事实核验、拍摄、文案/分镜、剪辑、审批、包装、发布、指标与咨询录入的人工作业分钟总和 | 共享前置工作按活动预定规则分摊，不能每条重复全计；等待和销售跟进独列；模型等待不混为人工劳动 |
| 一次审核通过率 | 首次提交即通过的版本数 / 首次提交版本数 | 未审核版本不入分母；安全问题零容忍，不用均值掩盖 |
| 事实完整率 | 有有效 fact_id 的可核验陈述数 / 可核验陈述总数 | 主观观点不伪装事实；高风险事实不通过不能发布 |
| 咨询率（描述性） | 已确认到具体 publication 的首次咨询人数 / 该 publication 的同窗平台观看量 × 1,000 | 单位“每千次观看的已确认咨询”；观看不是独立人数，不称用户转化率 |
| 可联系率 | D30 内 contactable=true 的去重咨询 contact / 同首咨 cohort 的全部去重业务咨询对象 | 授权缺失、无可回应渠道、身份未确认分别展示 |
| 需求确认率 | D30 内人工 qualified 的去重 contact / 同首咨 cohort D30 内 contactable=true 的去重咨询 contact | 衡量已可联系咨询中确认需求的比例；分母不含无授权或无法回应咨询 |
| 咨询到有效线索率 | D30 内人工 qualified 的去重 contact / 同首咨 cohort 的全部去重业务咨询对象 | 分母包含不可联系与身份未确认的相关咨询对象；与需求确认率分开展示 |
| 预约率 | D30 内到达 appointment 的 lead / 同 cohort qualified lead | 多次预约仍按人计一次 |
| 到访率 | D30 内 visit_completed lead / 同 cohort appointment lead | 远程/现场分别显示；爽约另列 |
| 商机率 | D30 内 opportunity lead / 同 cohort visit_completed lead | 销售人工理由必需 |
| 成交率 | D30 内 won lead / 同 cohort opportunity lead | 豪宅周期可能超过 D30，仅作观测，不是首月必达指标 |
| 可归因覆盖率 | 存 A/B 作品证据的去重业务咨询对象 / 首咨 cohort 全部去重业务咨询对象 | C/U 不算具体作品可归因；同时列渠道覆盖率 |
| CRM 完整率 | 必填来源/授权/负责人/状态/下一行动完整的活跃 lead / 活跃 lead | withdrawn 不进入允许跟进的活跃数 |
| 首次响应耗时 | 合法咨询发生到第一次人工回应的时差 | 中位数与 P90，区分工作时间/自然时长；未回复显示未响应 |
| 活动运营 CPL | 同周期全部约定成本 / 当期新增去重 qualified contact | 包含 unknown 来源但分开展示；非因果归因；分母为 0 显示 N/A；分开模型、人工、投放、拍摄与服务费 |
| 作品可归因 CPL | 该作品按预定规则分摊的约定成本 / 具备 A/B 作品证据且符合窗口的去重 qualified contact | 必须注明 first/last 口径；归因不足或分母为 0 不计算；共享成本不得重复分摊给全部作品 |
| 撤回处理完成率 | 按承诺完成停联及删除/限存的请求 / 到期撤回请求 | 法定例外保存需单独说明，不继续营销 |

平台点赞/收藏/分享/评论/完播率只作内容诊断。拿不到完播或独立人数显示不可获取；不能用点赞数补成“意向客户”。成本与销售数量时间窗必须一致，不能用本周费用除累计线索。活动运营 CPL 是费用发生期与当期新增资格对象的运营比值，不能推断本期费用导致全部本期线索；作品 CPL 还要满足已声明的触点/观察窗口。工时和共享费用分摊规则在基线前冻结，基线与试点一致执行。

## 9. 埋点与人工回执

统一事件 envelope：`event_id, event_name, schema_version, tenant_id, occurred_at, recorded_at, actor_type, actor_id, source, idempotency_key`，按事件附相关 ID。来源为 `system/manual/official_import/official_api`；浏览平台视频无权限时不伪造自动埋点。

| 事件 | 必需关联 | 必需属性/处理 |
| --- | --- | --- |
| `fact.verified/disputed/expired` | property_id、fact_id | Fact.status=unverified/verified/disputed/expired；审核人、来源、有效期、结论 |
| `fact_check.submitted/rejected` | property_id、fact_id | 审批工单及拒绝理由，不将工单状态混入 Fact.status |
| `content.review_submitted/approved/rejected` | content_id、content_version_id | 审核类别、原因、actor |
| `release.exported` | release_id、run_id 可空 | 平台、文件校验和、AI 标识检查、入口 |
| `publication.confirmed/corrected/removed` | publication_id、release_id | 作品编号、发布时间、回执、变化原因 |
| `metric.observed` | publication_id | 指标原名、值、单位、真实窗口、截图/导出 |
| `inquiry.recorded/excluded` | inquiry_id、entry_id、campaign_id | 业务相关性、真实时间、排除理由；不可含原始私信正文 |
| `inquiry.contactability_changed` | inquiry_id、contact_id、consent_id 可空 | contactable、合法依据/授权、操作者与可回应渠道 |
| `source_touch.confirmed/disputed` | source_touch_id、inquiry_id 可空 | source_confidence、source_granularity、evidence_type、派生 A/B/C/U 级别、证据、可确认精度 |
| `contact.created/merge_proposed/merged/split` | contact_id | 人工确认、映射、授权范围核对 |
| `consent.granted/withdrawn` | consent_id、contact_id | 目的与版本、证明；撤回触发停止队列 |
| `lead.qualified` | lead_id、contact_id、inquiry_id | 人工 qualified_by/at、需求摘要、理由；创建 QualifiedLead |
| `lead.stage_changed/corrected` | lead_id、contact_id | from/to、人工理由、证据、首次阶段时间 |
| `lead.followup_due/completed` | lead_id | 任务、负责销售、响应类型；只提醒，不自动触达 |
| `feedback.proposed/approved/applied` | campaign_id、run_id、content_version_id 可空 | 脱敏摘要、样本量、建议、批准人、验证计划 |
| `privacy.requested/completed` | contact_id、consent_id 可空 | 类型、范围、处理结果、受限审计回执 |

系统事件与 CRM 状态写入同一事务或 outbox，重复导入靠幂等键去重。平台作品编号去重失败不创建第二个真实发布；重复咨询记录可保留但不会放大去重 lead。publication.confirmed 事件对应 verified 状态，publication.corrected 按实际核对结果写 verified 或 inconsistent，publication.removed 对应 removed。埋点中不得放手机号、姓名、完整对话、精确地址或 token。

## 10. 反馈如何改善下一条内容

每周按同平台、同楼盘和同窗口回看以下链路：选题承诺 → 事实与视觉证据 → 咨询问题 → 销售答复 → 推进/未推进理由。

| 观察到的现象 | 可提出的内容建议 | 禁止结论 |
| --- | --- | --- |
| 多次问“这是实景还是效果图” | 提高素材类型标识；补充授权实景及拍摄日期 | AI 可以把效果图做成实景 |
| 已确认咨询常问某户型动线 | 拍摄有来源的户型解释及生活使用演示 | 凭用户画像推断家庭成员隐私 |
| 播放高但只有同行评论 | 选题回到居住决策问题，核对 CTA 与入口 | 热门视频一定带来高净值客户 |
| qualified 多、预约少 | 人工核对响应时间、预约表达、事实不清与客户时间 | 自动催促或未经同意发营销消息 |
| 来源长期未知 | 培训销售询问一次来源，改善获准入口记录 | 按播放比例强行分配归因 |

复盘 Agent 只读取经过人工批准的匿名聚合与脱敏业务原因，不读原始联系人档案。默认对内容角色隐藏人数少于 5 的细分反馈，避免结合豪宅场景再识别；这是首版的产品保护阈值，非法律规定。少样本只给“待验证建议”，不产生“高净值客户画像”。

每条建议保存 `run_id`、输入 cohort/观察截止时间、证据、置信表述、建议改动、审核人和实验计划。负责人批准后生成新 content_version；系统不能自己改写事实、改变售价、扩大跟进授权或启动触达。

## 11. 日常 SOP 与责任

| 时间/触发 | 责任人 | 操作 | 完成回执 |
| --- | --- | --- | --- |
| 发布当天 | 运营 | 人工发布、登记作品、验证 CTA/入口、保存脱敏截图 | publication.confirmed；入口不可用则标失败并处理 |
| 新咨询 | 站内承接销售 | 回应问题；核对是否业务咨询、来源及跟进目的 | inquiry、touch、合法处理依据/consent |
| 工作日咨询进入后 | 销售 | 按双方约定时段回应、人工确认需求 | contactable/qualified 的真实状态与理由 |
| 每工作日结束 | 销售负责人 | 检查未响应、待确认、到期下一行动、撤回 | 跟进清单处理记录，不自动群发 |
| D7/D30 到点 | 运营 | 记录真实窗口的平台指标、处理缺失 | MetricObservation 和证据 |
| 每周 | 内容负责人+销售负责人 | 核对来源、去重、误判、反馈；批准下周建议 | 复盘报告、建议批准与新版本 |
| 客户撤回/删除 | 租户隐私负责人+楼响运维 | 停止跟进，限制访问，完成删除/限存与外部同步 | privacy.completed 与客户反馈 |

建议试点服务目标：工作时段内 4 小时首回应、工作日末完成分配、24 小时内完成需要人工确认的登记；这些是待与试点客户约定的运营目标，不是平台或行业基准。无法支持时先降低承诺并记录实际人力。

## 12. 两周基线与四周 Pilot

开始前运行或回收可复查的两周人工基线：完整生产总人时、审核修改、真实发布、咨询来源、qualified、预约与到访；拿不到历史数据就先做两周前瞻记录。后续四周使用楼响，以相同团队、楼盘和主账号运行；出现价格、房源、投放或团队重大变化必须备注。等待与销售跟进人时独列；活动共用的资料和拍摄工时按预先约定分摊，避免将一次准备重复计入每条内容。

试点前由双方填写目标，不引用未经证明的行业 benchmark：

| 项目 | v0.1 建议验收 | 性质 |
| --- | --- | --- |
| 闭环可追溯 | 试点已发布内容全部能追至批准版本、事实、素材、入口及发布回执 | 产品控制要求 |
| 内容真实性 | 禁止性/未核验高风险事实在验收样本中 0 条对外发布 | 产品控制要求，不等于合规认证 |
| 联系授权 | 允许主动跟进的样本 100% 有目的明确的依据/授权 | 产品控制要求 |
| CRM 记录完整 | 活跃业务 lead 必填字段完整率建议 ≥90% | 试点建议，需双方确认 |
| 人工生产效率 | 建议同类型中位生产总人时较基线降低 ≥30%，审核质量不下降 | 内部商业假设，需实际两周基线及双方确认，非行业 benchmark |
| 获客价值 | 展示真实 qualified、预约、到访及成本；与基线做描述性比较 | 无保底线索、无成交保证 |
| 续费意愿 | 有客户书面付费续用或明确不续用原因 | 商业证据，不用满意评分替代 |

每周少量按原始分子/分母报告；四周结束时只使用成熟窗口做比较，其余等待成熟。豪宅成交通常无法在短试点中充分验证，先展示长期跟进台账而不承诺销售额。

## 13. 首版验收用例

1. 同一内容导出抖音与小红书、人工发布两次：一个 content、各自 release/publication，不出现一个发布重复统计两平台。
2. 只有手机号而无真实需求：contactable 需核对授权；不能自动 qualified。
3. 同一客户在两平台咨询：两个 inquiry，经人工确认合并一个 contact，qualified 后形成一个同楼盘 lead，保留所有来源，qualified 只计一次。
4. 销售只知道客户来自抖音：channel 已知，publication 未知；看板不得自动归给播放最高的作品。
5. 一条 D2、一条 D7 内容：不放在成熟 D7 转化排名；缺失完播显示不可获取。
6. 客户撤回：待办停止、号码和可识别反馈按范围删除/限存、后续生成不再读该数据；审计记录不含原始号码。
7. 人工更正 qualified 判定：保存原事件与更正，指标重算并标注 correction，不能删掉失败样本。
8. 一个租户查询另一个租户的 publication/lead/证据资源：服务端拒绝，导出/缓存/签名 URL 也拒绝。
9. 没有真实咨询：模拟数据只用于技术验证，商业报表不得显示模拟 qualified 或声称实现获客。

相关执行边界参见 [内容真实性、数据安全与平台规则](08_内容真实性数据安全与平台规则.md)。
