const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../dist/project-store.js');
const A = require('../dist/assistant-model.js');
const first = 'project-yunjing';
const second = 'project-qian';
const textOf = answer => JSON.stringify(answer);
const rowsOf = answer => answer.sections.flatMap(section => section.items);
const byLabel = (answer, label) => rowsOf(answer).find(item => item.label === label);

// These checks catch a wrong project scope, stale snapshot, invented evidence, or unsafe follow-up ranking.
test('project overview reads the chosen project instead of the active project', () => {
  const workspace = P.createWorkspace();
  workspace.activeProjectId = second;
  const result = A?.answer(workspace, first, '请把这个项目从资料到客户完整汇总出来');
  assert.equal(result?.projectId, first);
  assert.equal(result?.projectName, '云境·壹号');
  assert.equal(byLabel(result, '已发布内容').text, '2 条已核验且在线；播放量合计 14,100');
  assert.match(byLabel(result, '资料完整度').text, /86%/);
  assert.match(byLabel(result, '项目实际素材').text, /^0 份/);
  assert.match(byLabel(result, '咨询与客户').text, /4 条咨询.*3 条历史资格线索.*3 位可跟进/);
  assert.doesNotMatch(textOf(result), /栖岸|268/);
});

test('answers do not mutate the workspace and reflect subsequent record changes', () => {
  const workspace = P.createWorkspace();
  const before = JSON.stringify(workspace);
  A.answer(workspace, first, '项目概况');
  assert.equal(JSON.stringify(workspace), before);
  const project = P.getProject(workspace, first);
  project.state.facts[0].value = '412';
  project.state.publications[0].views = 100;
  const result = A.answer(workspace, first, '项目概况');
  assert.match(textOf(result), /412/);
  assert.doesNotMatch(byLabel(result, '已发布内容').text, /14,100/);
});

test('mentioning another known project requests switching without disclosing its records', () => {
  const workspace = P.createWorkspace();
  P.getProject(workspace, second).state.leads.push({ id: 'SECRET', name: '别项目客户' });
  const result = A.answer(workspace, first, '帮我汇总栖岸·观澜并查它的关键客户');
  assert.equal(result.intent, 'switch_project');
  assert.match(result.summary, /切换/);
  assert.doesNotMatch(textOf(result), /别项目客户|SECRET|268/);
  assert.equal(result.sources.length, 0);
});

test('invalid project ids never fall back to the active project', () => {
  const result = A.answer(P.createWorkspace(), 'missing', '项目汇总');
  assert.equal(result.intent, 'project_unavailable');
  assert.equal(result.sources.length, 0);
  assert.doesNotMatch(textOf(result), /328|陈女士|8\.2/);
});

test('prioritization uses operational stage and explicit follow-up rather than purchase prediction', () => {
  const result = A.answer(P.createWorkspace(), first, '谁是关键客户，今天先跟进谁？');
  assert.equal(result.intent, 'leads');
  assert.deepEqual(rowsOf(result).filter(item => item.ref?.kind === 'lead').map(item => item.ref.id), ['LEAD003', 'LEAD002', 'LEAD001']);
  assert.match(result.notice, /阶段.*下一步/);
  assert.doesNotMatch(textOf(result), /成交率|购房概率.*\d+%/);
  assert.match(rowsOf(result)[0].text, /收集实地看房后的取舍与疑问/);
});

test('withdrawn, ended, uncontactable, nonconsenting, and orphan leads are excluded from follow-up', () => {
  const workspace = P.createWorkspace();
  const state = P.getProject(workspace, first).state;
  state.leads[0].stage = 'withdrawn';
  state.inquiries[1].consent = false;
  state.leads[2].inquiryId = 'MISSING';
  state.leads.push({ id: 'ENDED', inquiryId: 'INQ001', name: '结束客户', stage: 'won', nextAction: '联系' });
  const result = A.answer(workspace, first, '有哪些重点客户值得联系');
  assert.equal(rowsOf(result).filter(item => item.ref?.kind === 'lead').length, 0);
  assert.match(result.summary, /没有.*可跟进/);
});

test('sensitive personal attributes do not change ranking and explicit next actions break stage ties', () => {
  const workspace = P.createWorkspace();
  const state = P.getProject(workspace, first).state;
  state.leads.forEach(lead => { lead.stage = 'qualified'; });
  state.leads[0].nextAction = '';
  state.leads[0].wealth = '超级富有';
  const result = A.answer(workspace, first, '优先客户');
  assert.deepEqual(rowsOf(result).filter(item => item.ref?.kind === 'lead').map(item => item.ref.id), ['LEAD002', 'LEAD003', 'LEAD001']);
  assert.doesNotMatch(textOf(result), /超级富有/);
});

test('a named customer answer includes real saved feedback and a follow-up carries its context', () => {
  const workspace = P.createWorkspace();
  P.getProject(workspace, first).state.leads[0].notes.push({ text: '希望先核对主卧到餐厅的距离', at: '2026-10-12', by: '林顾问' });
  const result = A.answer(workspace, first, '陈女士目前什么情况？');
  assert.equal(result.contextLeadId, 'LEAD001');
  assert.match(textOf(result), /希望先核对主卧到餐厅的距离/);
  const followup = A.answer(workspace, first, '她下一步怎么办？', [{ role: 'assistant', answer: result }]);
  assert.equal(followup.contextLeadId, 'LEAD001');
  assert.match(textOf(followup), /通过已授权原会话回应动线问题/);
});

test('pronoun context is ignored after switching projects', () => {
  const workspace = P.createWorkspace();
  const previous = A.answer(workspace, first, '陈女士详情');
  const result = A.answer(workspace, second, '她下一步怎么办？', [{ answer: previous }]);
  assert.notEqual(result.contextLeadId, 'LEAD001');
  assert.doesNotMatch(textOf(result), /陈女士|林顾问/);
});

test('a withdrawn named customer is visible with a clear prohibition on further contact', () => {
  const workspace = P.createWorkspace();
  const result = P.dispatch(workspace, first, { type: 'MOVE_LEAD', id: 'LEAD001', stage: 'withdrawn' });
  assert.equal(result.error, undefined);
  const answer = A.answer(result.workspace, first, '陈女士下一步跟进什么');
  assert.match(textOf(answer), /撤回.*停止|停止.*撤回/);
  assert.doesNotMatch(textOf(answer), /通过已授权原会话回应动线问题/);
});

test('pending, expired and placeholder facts are reported as unconfirmed without publishing their values', () => {
  const workspace = P.createWorkspace();
  const state = P.getProject(workspace, first).state;
  state.facts[0].status = 'expired';
  state.facts[1].source = '未知';
  const answer = A.answer(workspace, first, '建筑面积和客厅开间分别多少？报价是多少？');
  assert.equal(answer.intent, 'facts');
  assert.doesNotMatch(textOf(answer), /328|8\.2/);
  assert.match(textOf(answer), /过期|重新核验/);
  assert.match(textOf(answer), /具体房源报价/);
  assert.match(textOf(answer), /未核验|未确认/);
});

test('gap answer lists readiness gaps and optional quote limitations with record references', () => {
  const result = A.answer(P.createWorkspace(), first, '还缺少啥才能完整跑起来？');
  assert.equal(result.intent, 'gaps');
  assert.match(textOf(result), /当前项目素材/);
  assert.match(textOf(result), /具体房源报价/);
  assert.ok(result.sources.some(ref => ref.kind === 'fact' && ref.id === 'F05'));
  assert.ok(result.sources.some(ref => ref.kind === 'completeness'));
});

test('template and AI assets never count as project actual materials', () => {
  const workspace = P.createWorkspace();
  const state = P.getProject(workspace, first).state;
  state.assets.push({ id: 'AI01', name: '生成图', src: 'generated.jpg', previewType: 'ai_illustration', rights: 'authorized' });
  state.assets.push({ id: 'REAL01', name: '实拍客厅', src: 'actual.jpg', previewType: 'property_photo', rights: 'pending' });
  const answer = A.answer(workspace, first, '有哪些项目素材可以用');
  assert.equal(answer.intent, 'assets');
  assert.match(answer.summary, /1 份.*项目.*5 份.*示意/);
  assert.match(textOf(answer), /实拍客厅.*用途待确认/);
});

test('content report excludes unverified or offline counters and preserves unknown attribution', () => {
  const workspace = P.createWorkspace();
  const state = P.getProject(workspace, first).state;
  state.publications[2].views = 9000000;
  state.publications.push({ id: 'OFF', title: '下架', status: 'verified', platformState: 'offline', views: 10000000 });
  const answer = A.answer(workspace, first, '内容发布后效果怎样，来源清楚吗');
  assert.equal(answer.intent, 'reports');
  assert.equal(byLabel(answer, '已发布内容').text, '2 条已核验且在线；播放量合计 14,100');
  assert.match(byLabel(answer, '来源归因').text, /1 条未知.*1 条客户自述/);
  assert.doesNotMatch(textOf(answer), /9000000|10000000/);
});

test('missing view counts are not represented as measured zeros', () => {
  const workspace = P.createWorkspace();
  P.getProject(workspace, first).state.publications[0].views = null;
  const result = A.answer(workspace, first, '播放量多少');
  assert.match(byLabel(result, '已发布内容').text, /4,300.*1 条播放量待录入/);
  assert.match(rowsOf(result).find(item => item.ref?.id === 'PUB001').text, /播放量待录入/);
});

test('empty new project has no fabricated customer, publication, material or fact data', () => {
  const created = P.createProject(P.createWorkspace(), { name: '澜庭', city: '苏州' });
  const answer = A.answer(created.workspace, created.projectId, '完整汇总当前项目');
  assert.match(byLabel(answer, '咨询与客户').text, /0 条咨询.*0 条历史資格|0 条咨询.*0 条历史资格/);
  assert.match(byLabel(answer, '已发布内容').text, /0 条/);
  assert.doesNotMatch(textOf(answer), /陈女士|328|8\.2|14,100|云境/);
  assert.equal(answer.sources.filter(ref => ['lead', 'inquiry', 'asset', 'publication'].includes(ref.kind)).length, 0);
});

test('unknown questions explicitly state local demo limits and expose useful suggestions', () => {
  const answer = A.answer(P.createWorkspace(), first, '帮我分析明年楼市价格涨幅并自动打电话');
  assert.equal(answer.intent, 'unsupported');
  assert.match(answer.summary, /本地.*演示|演示.*本地/);
  assert.equal(answer.sources.length, 0);
  assert.ok(answer.suggestions.length >= 3);
});

test('English keywords select the same bounded database query intents', () => {
  const workspace = P.createWorkspace();
  assert.equal(A.answer(workspace, first, 'project summary').intent, 'overview');
  assert.equal(A.answer(workspace, first, 'missing information').intent, 'gaps');
  assert.equal(A.answer(workspace, first, 'priority leads').intent, 'leads');
});

test('a customer not in this project cannot be answered from another project or fabricated', () => {
  const workspace = P.createWorkspace();
  P.getProject(workspace, second).state.leads.push({ id: 'PRIVATE', name: '赵女士', need: '另一项目的私有需求' });
  const answer = A.answer(workspace, first, '赵女士的客户情况和下一步呢');
  assert.match(answer.summary, /当前项目.*没有.*匹配/);
  assert.doesNotMatch(textOf(answer), /另一项目的私有需求|PRIVATE/);
});

test('a full-project summary mentioning gaps, customers, and next steps remains comprehensive', () => {
  const answer = A.answer(P.createWorkspace(), first, '请完整汇总当前项目情况，并列出缺项、关键客户和下一步。');
  assert.equal(answer.intent, 'overview');
  assert.ok(byLabel(answer, '资料完整度'));
  assert.ok(byLabel(answer, '已发布内容'));
  assert.ok(answer.sources.some(source => source.kind === 'lead'));
  assert.ok(answer.sources.some(source => source.kind === 'fact'));
});

test('D7 reports do not add D30 views and missing D30 data remains waiting observation', () => {
  const workspace = P.createWorkspace();
  const state = P.getProject(workspace, first).state;
  state.publications[1].window = 'D30';
  const d7 = A.answer(workspace, first, 'D7内容表现如何');
  assert.match(byLabel(d7, '已发布内容').text, /1 条.*9,800/);
  assert.doesNotMatch(byLabel(d7, '已发布内容').text, /14,100/);
  assert.equal(d7.sources.some(source => source.kind === 'publication' && source.id === 'PUB002'), false);
  const d30 = A.answer(P.createWorkspace(), first, 'D30内容效果如何');
  assert.match(d30.summary, /D30.*待观察/);
  assert.match(byLabel(d30, '已发布内容').text, /待观察/);
  assert.equal(d30.sources.some(source => source.kind === 'publication'), false);
});

test('customer record ids locate the saved record without requiring the displayed name', () => {
  const answer = A.answer(P.createWorkspace(), first, '查下LEAD002的下一步');
  assert.equal(answer.contextLeadId, 'LEAD002');
  assert.match(textOf(answer), /许先生|再次确认双方已约定的看房时间/);
});

test('all four displayed quick prompts route to their actual query and the three-item plan stays focused', () => {
  const workspace = P.createWorkspace();
  const prompts = [
    ['请完整汇总当前项目情况，包括资料、内容、客户与下一步。', 'overview'],
    ['当前项目还缺什么资料？哪些问题会影响内容制作与发布？', 'gaps'],
    ['当前项目的关键客户是谁？请说明判断依据和下一步跟进动作。', 'leads'],
    ['根据当前项目记录，接下来最应该推进哪三件事？', 'tasks']
  ];
  for (const [question, expected] of prompts) assert.equal(A.answer(workspace, first, question).intent, expected);
  const plan = A.answer(workspace, first, prompts[3][0]);
  assert.equal(rowsOf(plan).length, 3);
  assert.ok(rowsOf(plan).some(item => item.ref?.kind === 'lead'));
});

test('overview separates observation windows rather than presenting their sum as one number', () => {
  const workspace = P.createWorkspace();
  P.getProject(workspace, first).state.publications[1].window = 'D30';
  const answer = A.answer(workspace, first, '完整汇总当前项目');
  const metric = byLabel(answer, '已发布内容').text;
  assert.match(metric, /D7.*9,800/);
  assert.match(metric, /D30.*4,300/);
  assert.doesNotMatch(metric, /14,100/);
});

test('each missing contact permission or ended stage independently prevents a priority recommendation', () => {
  for (const scenario of ['no_contact', 'no_consent', 'orphan', 'lost', 'won', 'withdrawn']) {
    const workspace = P.createWorkspace();
    const state = P.getProject(workspace, first).state;
    state.leads = [state.leads[0]];
    if (scenario === 'no_contact') state.inquiries[0].contactable = false;
    else if (scenario === 'no_consent') state.inquiries[0].consent = false;
    else if (scenario === 'orphan') state.leads[0].inquiryId = 'removed-inquiry';
    else state.leads[0].stage = scenario;
    const result = A.answer(workspace, first, '关键客户是谁');
    assert.equal(rowsOf(result).filter(item => item.ref?.kind === 'lead').length, 0, scenario);
  }
});

test('a nominally verified fact with a pending-evidence placeholder is still withheld', () => {
  const workspace = P.createWorkspace();
  P.getProject(workspace, first).state.facts[0].source = '待核验';
  const answer = A.answer(workspace, first, '面积是多少');
  assert.doesNotMatch(textOf(answer), /328/);
  assert.match(textOf(answer), /不能确认/);
});

test('other-record queries do not interpret the character 他 inside 其他 as customer context', () => {
  const workspace = P.createWorkspace();
  const previous = A.answer(workspace, first, '陈女士目前情况');
  const history = [{ answer: previous }];
  const others = A.answer(workspace, first, '其他关键客户是谁？', history);
  assert.equal(others.intent, 'leads');
  assert.notEqual(others.contextLeadId, 'LEAD001');
  const gaps = A.answer(workspace, first, '其他资料还缺什么？', history);
  assert.equal(gaps.intent, 'gaps');
  const summary = A.answer(workspace, first, '请完整汇总当前项目的其他资料和客户情况', history);
  assert.equal(summary.intent, 'overview');
  const pronoun = A.answer(workspace, first, '他的下一步是什么？', history);
  assert.equal(pronoun.contextLeadId, 'LEAD001');
});

test('an explicit full summary does not reuse pronoun context from the previous customer', () => {
  const workspace = P.createWorkspace();
  const previous = A.answer(workspace, first, '陈女士目前情况');
  const result = A.answer(workspace, first, '请完整汇总当前项目情况，也提一下她的下一步', [{ answer: previous }]);
  assert.equal(result.intent, 'overview');
  assert.notEqual(result.contextLeadId, 'LEAD001');
});
