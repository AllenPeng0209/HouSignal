import { readFileSync, mkdirSync, writeFileSync, existsSync, statSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire('/Users/pengyanlun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json');
const { chromium } = require('playwright');
const config = JSON.parse(readFileSync(0, 'utf8'));
mkdirSync('reports/screenshots', { recursive: true });
const receiptPath = 'reports/browser-acceptance.json';
let prior = {};
if (existsSync(receiptPath)) { try { prior = JSON.parse(readFileSync(receiptPath, 'utf8')); } catch {} }
const receipt = { ...prior, created_at: prior.created_at || new Date().toISOString(), last_run_at: new Date().toISOString(), surface: 'Expo React Native Web with real FastAPI/PostgreSQL; browser evidence, not native device evidence', synthetic_fixture: true, checks: prior.checks || {}, screenshots: prior.screenshots || [], provider_calls: prior.provider_calls || { assistant_submitted: false, generation_submitted: false }, limitations: ['Camera, microphone, native notification delivery, native OS share sheets, and native device media playback require device acceptance.', 'No platform publication was performed.'] };
receipt.failure_history ||= [];
if (prior.status === 'failed' && prior.error && !receipt.failure_history.some(entry => entry.at === prior.last_run_at && entry.error === prior.error)) receipt.failure_history.push({ at: prior.last_run_at, stage: prior.stage, error: prior.error, generation_job_id: prior.generate_job_id });
receipt.generation_job_ids ||= receipt.generate_job_id ? [receipt.generate_job_id] : [];
delete receipt.checks.bounded_one_chat_one_generation;
const secrets = [config.email, config.password].filter(Boolean);
function redact(value) { let text = String(value); for (const secret of secrets) text = text.split(secret).join('[redacted]'); return text.replace(/Bearer\s+[\w-]+/gi, 'Bearer [redacted]'); }
function persist() { writeFileSync(receiptPath, JSON.stringify(receipt, null, 2)); }
function stage(name) { receipt.stage = name; persist(); console.log(JSON.stringify({ stage: name })); }
function isJobSubmission(path, method = 'GET') { return method.toUpperCase() === 'POST' && /(?:\/messages|\/contents\/generate|\/contents\/[^/]+\/render|\/jobs\/[^/]+\/retry)$/.test(new URL(path, config.api).pathname); }
function forbidJobSubmission(path, method) { assert.ok(!config.disallowNewJobs || !isJobSubmission(path, method), 'Final regression forbids submitting any chat, generation, render, or retry task'); }
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, acceptDownloads: true });
let blockedJobRequests = 0;
await page.route('**/*', async route => { const request = route.request(); if (config.disallowNewJobs && isJobSubmission(request.url(), request.method())) { blockedJobRequests++; await route.abort('blockedbyclient'); } else await route.continue(); });
page.setDefaultTimeout(45000);
const errors = [], consoleErrors = [], consoleDetails = []; page.on('pageerror', error => errors.push(redact(error.message)));
page.on('console', message => { if (message.type() === 'error') { const text = redact(message.text()); consoleErrors.push(text); consoleDetails.push({ stage: receipt.stage, message: text, location: message.location() }); } });
async function shot(name) {
  // Credentials are never persisted in pixels. Failure may occur on the login screen.
  for (const label of ['工作邮箱', '密码']) { const field = page.getByLabel(label, { exact: true }); if (await field.count()) await field.fill('').catch(() => {}); }
  await page.evaluate(values => { for (const node of document.querySelectorAll('*')) { for (const child of node.childNodes) if (child.nodeType === Node.TEXT_NODE && child.textContent) { let text = child.textContent; for (const value of values) if (value) text = text.split(value).join('[redacted]'); if (text !== child.textContent) child.textContent = text; } } }, secrets);
  const path = `reports/screenshots/${name}.png`; await page.screenshot({ path, fullPage: false });
  if (!receipt.screenshots.includes(path)) receipt.screenshots.push(path);
  persist();
}
async function go(route, marker) { await page.goto(config.web + route, { waitUntil: 'domcontentloaded', timeout: 120000 }); await page.getByText(marker, { exact: true }).first().waitFor({ timeout: 90000 }); }
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let api;
async function waitJob(id, timeout = 240000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const job = await api(`/api/jobs/${id}`);
    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
      receipt.job_results ||= {}; receipt.job_results[id] = { kind: job.kind, status: job.status, phase: job.phase, error: redact(job.error || ''), result_keys: Object.keys(job.result || {}) }; persist();
      if (job.status !== 'succeeded') throw new Error(`Task ${id} ${job.status}: ${redact(job.error || 'unknown')}`);
      return job;
    }
    await pause(1200);
  }
  throw new Error(`Task ${id} did not finish within the bounded acceptance window`);
}
async function uiPost(button, suffix) {
  forbidJobSubmission(suffix, 'POST');
  const responsePromise = page.waitForResponse(response => response.url().endsWith(suffix) && response.request().method() === 'POST', { timeout: 90000 });
  await button.click(); const response = await responsePromise;
  const data = await response.json(); if (!response.ok()) throw new Error(`UI POST ${suffix}: ${response.status()} ${redact(typeof data.detail === 'string' ? data.detail : 'request rejected')}`);
  return data;
}
try {
  stage('login');
  if (config.disallowNewJobs) { for (const key of ['project_id', 'chat_job_id', 'generate_retry_job_id', 'revised_content_id', 'render_job_id', 'release_id']) assert.ok(receipt[key], `Final regression needs retained ${key}`); }
  await go('/', '进入你的工作区');
  await shot('mobile-login');
  await page.getByLabel('工作邮箱', { exact: true }).fill(config.email);
  await page.getByLabel('密码', { exact: true }).fill(config.password);
  await page.getByRole('button', { name: '服务地址设置', exact: true }).click();
  await page.getByLabel('楼响服务地址', { exact: true }).fill(config.api);
  await page.getByRole('button', { name: '登录工作区', exact: true }).click();
  await page.getByText('今天，让项目再向前一步。', { exact: true }).waitFor({ timeout: 90000 });
  receipt.checks.login_real_backend = true;
  const saved = await page.evaluate(() => JSON.parse(sessionStorage.getItem('housignal.credentials.v1'))); assert.ok(saved?.token); secrets.push(saved.token);
  api = async (path, options = {}) => {
    forbidJobSubmission(path, options.method || 'GET');
    let response;
    try { response = await fetch(config.api + path, { ...options, headers: { Authorization: 'Bearer ' + saved.token, ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); }
    catch (error) { throw new Error(`${options.method || 'GET'} ${path} transport failed: ${redact(error.message)} (${redact(error.cause?.code || error.cause?.message || 'no cause')})`); }
    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(`${options.method || 'GET'} ${path} -> ${response.status}: ${redact(typeof error.detail === 'string' ? error.detail : 'request rejected')}`); }
    return response.headers.get('content-type')?.includes('json') ? response.json() : response.arrayBuffer();
  };
  const auth = await api('/api/auth/me');
  stage('synthetic-project');
  let project = receipt.project_id ? (await api('/api/projects')).find(row => row.id === receipt.project_id && row.name.startsWith('手机端验收 · 合成测试 ')) : null;
  if (!project) {
    const name = `手机端验收 · 合成测试 ${Date.now()}`; receipt.fixture_name = name; persist();
    await go('/projects', '你的项目');
    await page.getByRole('button', { name: '新建', exact: true }).click();
    await page.getByLabel('项目名称 *', { exact: true }).fill(name);
    await page.getByLabel('所在城市 *', { exact: true }).fill('上海');
    await page.getByLabel('内容负责人', { exact: true }).fill('合成验收内容负责人');
    await page.getByLabel('目标客群', { exact: true }).fill('手机端验收使用的虚构改善型家庭');
    await page.getByLabel('销售承接负责人', { exact: true }).fill('合成验收承接负责人');
    await page.getByRole('switch', { name: '已确认此项目可开展内容营销', exact: true }).check();
    await page.getByLabel('授权依据与适用范围', { exact: true }).fill('仅用于手机端开发验收的虚构项目与自制示意素材；不用于真实房源宣传。');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.getByText('今天，让项目再向前一步。', { exact: true }).waitFor({ timeout: 60000 });
    project = (await api('/api/projects')).find(row => row.name === name); assert.ok(project); receipt.project_id = project.id; persist();
    receipt.checks.project_create_select = true;
  } else {
    await go('/projects', '你的项目'); await page.getByLabel('搜索项目', { exact: true }).fill(project.name); await page.getByRole('button', { name: `切换到${project.name}`, exact: true }).click();
    await page.getByText('今天，让项目再向前一步。', { exact: true }).waitFor(); receipt.checks.project_resume_select = true;
  }
  const mark = assetId => { const input = { user_id: auth.user.id, tenant_id: auth.organization.id, project_id: project.id, ...(assetId ? { asset_id: assetId } : {}) }; const output = execFileSync(config.python, ['scripts/run_browser_acceptance.py', '--mark-test-record'], { input: JSON.stringify(input), encoding: 'utf8' }); assert.equal(JSON.parse(output).marked, true); };
  mark();
  receipt.assets_navigation_mode = 'Direct /materials URL, deep link, and browser refresh; backend endpoints remain /api/projects/:id/assets';
  const base = `/api/projects/${project.id}`;
  const jobsBefore = (await api(base + '/jobs')).map(job => job.id).sort();
  const usageInput = { user_id: auth.user.id, tenant_id: auth.organization.id, project_id: project.id };
  const readUsage = () => JSON.parse(execFileSync(config.python, ['scripts/run_browser_acceptance.py', '--fixture-usage'], { input: JSON.stringify(usageInput), encoding: 'utf8' })).by_job.sort((a, b) => a.job_id.localeCompare(b.job_id));
  const usageBefore = readUsage();
  stage('fixture-facts-and-material');
  await api(base, { method: 'PATCH', body: JSON.stringify({ owner_name: '合成验收内容负责人', audience: '仅开发验收的虚构家庭空间需求', sales_owner_name: '合成验收承接负责人', marketing_confirmed: true, marketing_evidence: '自制虚构项目资料和合成示意素材，仅开发验收；非真实房源营销授权。' }) });
  const values = { area: ['420', '㎡'], width: ['8.5', 'm'], layout: ['4 房同层', ''], kitchen: ['厨房与餐厅相邻', ''] };
  for (const fact of await api(base + '/facts')) {
    if (!values[fact.semantic_key]) continue;
    const [value, unit] = values[fact.semantic_key];
    if (fact.value !== value || fact.unit !== unit || fact.status !== 'verified') {
      await api(`${base}/facts/${fact.id}`, { method: 'PATCH', body: JSON.stringify({ value, unit, source: `自制合成验收资料 · ${fact.label} · 非真实楼盘事实` }) });
      await api(`${base}/facts/${fact.id}/verify`, { method: 'POST', body: JSON.stringify({ confirmed: true }) });
    }
  }
  let material = (await api(base + '/assets')).find(row => row.name === '手机端合成验收示意.jpg');
  if (!material) { const body = new FormData(); body.append('file', new Blob([Buffer.from(config.testImageBase64, 'base64')], { type: 'image/jpeg' }), '手机端合成验收示意.jpg'); material = await api(base + '/assets', { method: 'POST', body }); }
  if (material.rights !== 'authorized' || material.metadata_json?.model_processing_authorized === true) await api(`${base}/assets/${material.id}/rights`, { method: 'POST', body: JSON.stringify({ rights: 'authorized', evidence: '自制合成测试图片，只用于手机端剪辑验收，不是真实楼盘照片；未授权模型处理。', model_processing_authorized: false }) });
  mark(material.id); receipt.asset_id = material.id;
  const fixture = (await api('/api/projects')).find(row => row.id === project.id); assert.equal(fixture.is_test, true);
  assert.ok((await api(base + '/facts')).filter(fact => values[fact.semantic_key]).every(fact => fact.valid && fact.status === 'verified'));
  const approvedMaterial = (await api(base + '/assets')).find(row => row.id === material.id); assert.equal(approvedMaterial.origin_type, 'test'); assert.equal(approvedMaterial.rights, 'authorized'); assert.notEqual(approvedMaterial.metadata_json?.model_processing_authorized, true);
  receipt.checks.synthetic_fixture_verified_facts_and_authorized_material = true; persist();
  for (const [route, marker, key] of [['/project', '楼盘资料', 'facts'], ['/materials', '项目素材', 'assets'], ['/assistant', '向楼管家提问', 'assistant'], ['/studio', '内容工作台', 'studio'], ['/leads', '咨询与客户', 'crm'], ['/reports', '效果复盘', 'reports'], ['/accounts', '内容运营', 'operations'], ['/skills', '技能库', 'skills'], ['/jobs', '任务中心', 'jobs'], ['/settings', '我的与团队', 'settings']]) {
    stage(`route-${key}`); await go(route, marker); receipt.checks[key + '_renders'] = true;
    if (key === 'assets') { await page.reload({ waitUntil: 'domcontentloaded', timeout: 120000 }); await page.getByText(marker, { exact: true }).first().waitFor({ timeout: 90000 }); receipt.checks.materials_direct_deeplink_and_refresh = true; }
    if (['assets', 'assistant', 'studio', 'crm'].includes(key)) await shot('mobile-' + key); persist();
  }
  stage('assistant-real-ui');
  if (!receipt.chat_job_id && !receipt.provider_calls.assistant_submitted) {
    await go('/assistant', '向楼管家提问');
    await page.getByLabel('向楼管家提问', { exact: true }).fill('请仅根据当前合成测试项目的已核验资料，汇总面积、面宽与资料缺口，附上项目依据。');
    await page.getByRole('button', { name: '发送', exact: true }).waitFor();
    receipt.provider_calls.assistant_submitted = true; persist();
    const reply = await uiPost(page.getByRole('button', { name: '发送', exact: true }), '/messages'); receipt.chat_job_id = reply.job_id; persist();
  }
  if (!receipt.chat_job_id) receipt.chat_job_id = (await api(base + '/jobs')).find(job => job.kind === 'chat')?.id;
  assert.ok(receipt.chat_job_id, 'Bounded assistant submission has no recoverable task');
  const chat = await waitJob(receipt.chat_job_id); const conversations = await api(base + '/conversations'); let assistantReply;
  for (const conversation of conversations) { const messages = await api(`/api/conversations/${conversation.id}/messages`); assistantReply = messages.find(message => message.role === 'assistant' && message.job_id === chat.id); if (assistantReply) { receipt.conversation_id = conversation.id; break; } }
  assert.equal(assistantReply?.status, 'completed'); assert.ok(assistantReply.citations?.length); receipt.checks.real_assistant_completed_with_citations = true; receipt.assistant_reply = { id: assistantReply.id, citation_count: assistantReply.citations.length, characters: assistantReply.content.length }; persist();
  await go(`/assistant?conversationId=${receipt.conversation_id}`, '向楼管家提问'); await page.getByText(assistantReply.content, { exact: true }).waitFor({ timeout: 60000 }); await shot('mobile-assistant-result');
  try {
  stage('generation-real-ui');
  if (!receipt.generate_job_id && !receipt.provider_calls.generation_submitted) {
    await go('/studio', '内容工作台'); await page.getByRole('button', { name: '生成脚本草稿', exact: true }).first().waitFor();
    receipt.provider_calls.generation_submitted = true; persist(); const generated = await uiPost(page.getByRole('button', { name: '生成脚本草稿', exact: true }).first(), '/contents/generate'); receipt.generate_job_id = generated.job_id; persist();
  }
  if (!receipt.generate_job_id) receipt.generate_job_id = (await api(base + '/jobs')).find(job => job.kind === 'generate_content')?.id;
  assert.ok(receipt.generate_job_id, 'Bounded generation submission has no recoverable task');
  if (!receipt.generation_job_ids.includes(receipt.generate_job_id)) receipt.generation_job_ids.push(receipt.generate_job_id);
  const firstGeneration = await api(`/api/jobs/${receipt.generate_job_id}`);
  let activeGenerationId = receipt.generate_retry_job_id || receipt.generate_job_id;
  if (firstGeneration.status === 'failed' && !receipt.generate_retry_job_id) {
    // Explicitly authorized: one additional job after the retained duration-only
    // failure. Resuming never creates a third job, even after a lost UI response.
    assert.equal(config.allowOneGenerationRetry, true, 'Additional generation is not authorized');
    assert.equal(firstGeneration.error, '模型分镜总时长需为15至60秒', 'Retry is limited to the observed duration validation failure');
    const existing = (await api(base + '/jobs')).filter(job => job.kind === 'generate_content');
    assert.ok(existing.length <= 2, 'Generation job budget exceeded');
    const recovered = existing.find(job => job.id !== receipt.generate_job_id);
    if (recovered) receipt.generate_retry_job_id = recovered.id;
    else {
      assert.notEqual(receipt.provider_calls.generation_retry_submitted, true, 'Additional generation submission already attempted; do not submit again');
      receipt.generation_retry_authorization = { previous_failed_job_id: receipt.generate_job_id, permitted_additional_jobs: 1, generation_supplier_call_budget: 3, reason: 'Verified duration contract correction deployed after the original failed job' };
      receipt.provider_calls.generation_retry_submitted = true; persist();
      await go('/studio', '内容工作台');
      const generated = await uiPost(page.getByRole('button', { name: '生成脚本草稿', exact: true }).first(), '/contents/generate'); receipt.generate_retry_job_id = generated.job_id;
    }
    activeGenerationId = receipt.generate_retry_job_id;
    if (!receipt.generation_job_ids.includes(activeGenerationId)) receipt.generation_job_ids.push(activeGenerationId);
    persist();
  }
  const generated = await waitJob(activeGenerationId); receipt.generated_content_id = generated.result.content_version_id; assert.ok(receipt.generated_content_id); receipt.checks.real_generation_completed = true; persist();
  stage('revise-content-ui');
  let contentId = receipt.revised_content_id || receipt.generated_content_id;
  await go(`/content?id=${contentId}`, '脚本与分镜');
  if (!receipt.revised_content_id) {
    await page.getByLabel('封面标题', { exact: true }).fill('手机端合成验收：家庭空间');
    // A single 15-second authorized test image is enough to exercise real FFmpeg rendering.
    const shots = (await api(base + '/contents')).find(row => row.id === contentId).storyboard;
    if (!shots.length) await page.getByRole('button', { name: '添加镜头', exact: true }).click();
    for (let count = await page.getByRole('button', { name: '移除镜头', exact: true }).count(); count > 1; count--) await page.getByRole('button', { name: '移除镜头', exact: true }).last().click();
    await page.getByRole('radio', { name: material.name, exact: true }).first().click();
    await page.getByLabel('镜头 1 时长（秒）', { exact: true }).fill('15');
    await page.getByLabel('镜头 1 字幕', { exact: true }).fill('合成测试素材，仅用于手机端验收');
    const revised = await uiPost(page.getByRole('button', { name: '保存新版本', exact: true }).last(), `/contents/${contentId}/revise`); contentId = revised.id; receipt.revised_content_id = contentId; receipt.checks.script_and_storyboard_revision_ui = true; persist();
    await go(`/content?id=${contentId}`, '脚本与分镜');
  }
  stage('render-and-preview-ui');
  if (!receipt.render_job_id) { await page.getByRole('radio', { name: '成片预览', exact: true }).click(); const rendering = await uiPost(page.getByRole('button', { name: '制作成片', exact: true }), `/contents/${contentId}/render`); receipt.render_job_id = rendering.job_id; persist(); }
  await waitJob(receipt.render_job_id); receipt.checks.real_render_completed = true; persist();
  const content = (await api(base + '/contents')).find(row => row.id === contentId); assert.ok(content.render_asset_id); receipt.render_asset_id = content.render_asset_id; persist();
  await go(`/content?id=${contentId}`, '脚本与分镜'); await page.getByRole('radio', { name: '成片预览', exact: true }).click();
  await page.locator('video').waitFor({ timeout: 60000 });
  await page.waitForFunction(() => { const video = document.querySelector('video'); return video && video.readyState >= 2 && video.videoWidth > 0; }, null, { timeout: 60000 });
  const videoInfo = await page.locator('video').evaluate(async video => { video.muted = true; await video.play(); await new Promise(resolve => setTimeout(resolve, 1800)); video.pause(); return { duration: video.duration, width: video.videoWidth, height: video.videoHeight, played_seconds: video.currentTime }; });
  assert.ok(videoInfo.duration >= 14.9 && videoInfo.played_seconds > 0); receipt.browser_video = videoInfo; receipt.checks.actual_browser_video_playback = true; await shot('mobile-video-preview');
  stage('review-and-approved-download-ui');
  await page.getByRole('button', { name: '进入人工审核', exact: true }).click();
  if (!content.release_id) {
    for (const label of ['脚本文字与封面表达符合项目事实', '引用事实有效，数值、单位和来源准确', '素材来源、使用授权和项目营销范围已核对', '我已播放并核对最终画面、字幕与音频']) await page.getByRole('switch', { name: label, exact: true }).check();
    await page.getByLabel('审核备注', { exact: true }).fill('自动化开发验收确认：全部事实与图像为显式合成测试数据，已播放测试视频；此项不替代真实商业内容的人类审核。');
    const approval = await uiPost(page.getByRole('button', { name: '审核并冻结当前版本', exact: true }), `/contents/${contentId}/approve`); receipt.release_id = approval.id; receipt.checks.review_switches_and_approval_ui = true; persist();
  } else receipt.release_id = content.release_id;
  await page.getByRole('radio', { name: '导出与回执', exact: true }).click();
  receipt.downloads ||= {};
  for (const [kind, buttonLabel, filename] of [['package', '导出核验发布包 JSON', 'mobile-approved-test-package.json'], ['video', '导出实际成片 MP4', 'mobile-approved-test-render.mp4'], ['subtitle', '导出字幕 SRT', 'mobile-approved-test-subtitle.srt'], ['cover', '导出封面 JPG', 'mobile-approved-test-cover.jpg']]) {
    const downloadPromise = page.waitForEvent('download', { timeout: 60000 }); await page.getByRole('button', { name: buttonLabel, exact: true }).click(); const download = await downloadPromise; const path = 'reports/screenshots/' + filename; await download.saveAs(path); assert.ok(statSync(path).size > 0); receipt.downloads[kind] = { path, byte_size: statSync(path).size, suggested_filename: download.suggestedFilename() };
    if (kind === 'package') { const bundle = JSON.parse(readFileSync(path, 'utf8')); assert.equal(bundle.content.id, contentId); assert.equal(bundle.release.id, receipt.release_id); assert.ok(bundle.render_url); assert.ok(bundle.snapshot.render.metadata.content_snapshot_sha256); }
    if (kind === 'subtitle') assert.match(readFileSync(path, 'utf8'), /00:00:00,000 --> 00:00:15,000/);
    if (kind === 'cover') assert.equal(readFileSync(path).subarray(0, 2).toString('hex'), 'ffd8');
    receipt.checks[`approved_${kind}_browser_download`] = true; persist();
  }
  receipt.checks.approved_mp4_browser_download = true;
  for (let attempts = 0; attempts < 50 && !(await page.getByRole('button', { name: '导出核验发布包 JSON', exact: true }).isEnabled()); attempts++) await pause(100);
  assert.equal(await page.getByRole('button', { name: '导出核验发布包 JSON', exact: true }).isEnabled(), true); await shot('mobile-approved-export');
  receipt.checks.content_workflow_browser_complete = true; delete receipt.content_flow_error; persist();
  } catch (error) { receipt.content_flow_error = redact(error.message); receipt.checks.content_workflow_browser_complete = false; persist(); console.log(JSON.stringify({ content_flow_failed: receipt.content_flow_error })); }
  stage('synthetic-crm-ui');
  await go('/leads', '咨询与客户');
  const inquiryName = '合成手机验收客户 · 非真实线索';
  let inquiry = (await api(base + '/inquiries')).find(row => row.name === inquiryName);
  if (!inquiry) {
    await page.getByRole('button', { name: '录入咨询', exact: true }).click();
    await page.getByLabel('客户称呼 *', { exact: true }).fill(inquiryName);
    await page.getByLabel('实际需求', { exact: true }).fill('仅开发验收：虚构改善型家庭希望了解厨餐与同层空间关系。');
    await page.getByLabel('联系方式', { exact: true }).fill('SYNTHETIC-TEST-CONTACT-NO-REAL-PERSON');
    await page.getByRole('switch', { name: '客户已同意就本项目联系与跟进', exact: true }).check();
    await page.getByRole('switch', { name: '已确认联系方式真实可用', exact: true }).check();
    await page.getByLabel('来源依据', { exact: true }).fill('合成测试用例，不是真实咨询、客户、联系方式或客户授权；不进入真实销售统计。');
    inquiry = await uiPost(page.getByRole('button', { name: '保存咨询', exact: true }), '/inquiries'); receipt.inquiry_id = inquiry.id; receipt.checks.synthetic_inquiry_create_ui = true; persist();
  }
  let lead = (await api(base + '/leads')).find(row => row.inquiry_id === inquiry.id);
  if (!lead) {
    await page.getByLabel('搜索客户', { exact: true }).fill(inquiryName); await page.getByRole('button', { name: '确认客户资格', exact: true }).click();
    for (const label of ['可联系：已确认联系方式可用', '已授权：客户同意本项目联系', '真实需求：已与客户确认购房诉求', '项目匹配：需求与当前项目匹配']) await page.getByRole('switch', { name: label, exact: true }).check();
    await page.getByLabel('下一步', { exact: true }).fill('合成验收：核对厨餐关系资料'); lead = await uiPost(page.getByRole('button', { name: '确认成为资格客户', exact: true }), `/inquiries/${inquiry.id}/qualify`); receipt.lead_id = lead.id; receipt.checks.synthetic_qualification_ui = true; persist();
  } else receipt.lead_id = lead.id;
  await go(`/lead?id=${lead.id}`, '实际沟通记录 *');
  const note = '合成手机端验收跟进：非真实客户沟通，不构成销售成果。';
  if (!(await api(`${base}/leads/${lead.id}`)).followups.some(item => item.note === note)) {
    await page.getByLabel('实际沟通记录 *', { exact: true }).fill(note); await page.getByLabel('下一步安排', { exact: true }).fill('合成验收：查看家庭空间示意并核对事实来源'); await uiPost(page.getByRole('button', { name: '保存跟进', exact: true }), `/leads/${lead.id}/followups`);
  }
  const detail = await api(`${base}/leads/${lead.id}`); const nextAction = '合成验收：查看家庭空间示意并核对事实来源';
  assert.ok(detail.followups.some(item => item.note === note && item.next_action === nextAction)); assert.equal(detail.lead.next_action, nextAction); assert.equal(detail.lead.inquiry_id, inquiry.id); assert.equal(detail.inquiry.name, inquiryName); assert.equal(detail.inquiry.consent, true); assert.equal(detail.inquiry.contactable, true); assert.match(detail.lead.need, /仅开发验收/); receipt.checks.synthetic_followup_and_customer_fields_persisted_ui = true; receipt.checks.synthetic_followup_persisted_ui = true; receipt.crm_persistence = { inquiry_id: detail.inquiry.id, lead_id: detail.lead.id, stage: detail.lead.stage, followup_count: detail.followups.length, next_action: detail.lead.next_action, synthetic: true }; await shot('mobile-synthetic-crm-result');
  const ownedJobs = await api(base + '/jobs'); const generationJobs = ownedJobs.filter(job => job.kind === 'generate_content'); assert.equal(ownedJobs.filter(job => job.kind === 'chat').length, 1); assert.ok(generationJobs.length >= 1 && generationJobs.length <= 2); assert.equal(generationJobs.length, receipt.generation_job_ids.length); receipt.checks.bounded_one_chat_at_most_two_generation_jobs = true; receipt.provider_calls.generation_job_count = generationJobs.length;
  const usage = { by_job: readUsage() }; receipt.model_usage = usage;
  if (config.disallowNewJobs) { assert.deepEqual(ownedJobs.map(job => job.id).sort(), jobsBefore); assert.deepEqual(usage.by_job, usageBefore); assert.equal(blockedJobRequests, 0); receipt.zero_call_revalidation = { enforced: true, jobs_before: jobsBefore, jobs_after: ownedJobs.map(job => job.id).sort(), new_job_count: 0, blocked_job_request_count: blockedJobRequests, model_usage_unchanged: true, additional_supplier_responses: 0, additional_input_tokens: 0, additional_output_tokens: 0 }; }
  // The original failure preceded per-attempt accounting. Preserve its known
  // one completed supplier response separately from current database receipts.
  const oldUsage = usage.by_job.find(item => item.job_id === receipt.generate_job_id)?.response_count || 0; const newUsage = usage.by_job.find(item => item.job_id === receipt.generate_retry_job_id)?.response_count || 0;
  if (receipt.generate_retry_job_id && receipt.checks.real_generation_completed) assert.ok(newUsage >= 1, 'Completed additional generation must have actual per-response usage accounting');
  receipt.provider_calls.generation_supplier_responses = receipt.generate_retry_job_id ? Math.max(1, oldUsage) + newUsage : oldUsage;
  assert.ok(receipt.provider_calls.generation_supplier_responses <= 3); receipt.checks.bounded_generation_supplier_responses_at_most_three = true;
  receipt.runtime_errors = errors; receipt.console_errors = consoleErrors; receipt.console_error_details = consoleDetails; receipt.checks.no_runtime_errors = errors.length === 0 && consoleErrors.length === 0; assert.deepEqual(errors, []); assert.deepEqual(consoleErrors, []); receipt.status = receipt.content_flow_error ? 'failed' : 'passed'; receipt.completed_at = new Date().toISOString(); if (receipt.content_flow_error) receipt.error = receipt.content_flow_error; else { delete receipt.error; if (receipt.visual_audit) receipt.visual_audit.resolved_at = receipt.completed_at; }
} catch (error) { receipt.status = 'failed'; receipt.error = redact(error.message); receipt.runtime_errors = errors; receipt.console_errors = consoleErrors; receipt.console_error_details = consoleDetails; receipt.checks.no_runtime_errors = errors.length === 0 && consoleErrors.length === 0; await shot('mobile-failure-' + Date.now()).catch(() => {}); }
finally { persist(); await browser.close(); }
console.log(JSON.stringify({ status: receipt.status, stage: receipt.stage, checks: receipt.checks, error: receipt.error }));
if (receipt.status !== 'passed') process.exitCode = 1;
