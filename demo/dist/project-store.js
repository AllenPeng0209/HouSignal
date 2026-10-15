(function (root, factory) {
  'use strict';
  const model = typeof module === 'object' && module.exports ? require('./model.js') : root.LouxiangModel;
  const api = factory(model);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.LouxiangProjects = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (Model) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const text = value => value == null ? '' : String(value).trim();
  const useful = value => !!text(value) && !/^(?:—|-|n\/a|unknown)$/i.test(text(value)) && !/待.*确认|待补|待指定|待分配|未提供|未知|未确定|暂无/.test(text(value));
  const blankProperty = {
    name: '', city: '', propertyType: '豪宅住宅', owner: '', audience: '', salesOwner: '',
    entryMode: 'manual_native', heroSrc: null, visualLabel: '等待上传项目素材',
    unit: '', size: '', subtitle: '', status: '待完善资料', marketingPermission: { confirmed: false, evidence: '' }
  };

  function emptyTopics() {
    return [
      { id: 'T01', title: '家的尺度，如何回应日常生活？', audience: '待补充目标客户', angle: '结合已核验面积与开间，解释真实交流距离', refs: ['F01', 'F02'], tag: '空间尺度' },
      { id: 'T02', title: '一家人的日常，先看怎么走', audience: '待补充目标客户', angle: '核对卧室安排与厨餐关系，再解释日常生活动线', refs: ['F03', 'F04'], tag: '生活动线' },
      { id: 'T03', title: '厨房与餐厅，如何连接生活？', audience: '待补充目标客户', angle: '用项目实际厨餐关系说明备餐与家人交流', refs: ['F04'], tag: '厨餐日常' },
      { id: 'T04', title: '面积之外，怎样理解使用价值？', audience: '待补充目标客户', angle: '区分面积口径，结合已核验房型讨论居住适配', refs: ['F01', 'F03'], tag: '专业取舍' },
      { id: 'T05', title: '带着真实问题，安排一场看房', audience: '待补充目标客户', angle: '从已核验事实准备现场问题，并由销售确认预约', refs: ['F02', 'F03', 'F04', 'F06'], tag: '看房准备' }
    ];
  }

  function emptyState(id, details) {
    const property = { ...clone(blankProperty), id, name: text(details.name), city: text(details.city) };
    for (const field of ['propertyType', 'owner', 'audience', 'salesOwner']) {
      if (details[field] !== undefined) property[field] = text(details[field]);
    }
    property.subtitle = [property.city, property.propertyType].filter(Boolean).join(' · ');
    property.marketingPermission = { confirmed: details.marketingConfirmed === true, evidence: text(details.marketingEvidence) };
    return {
      demo: true, property,
      campaign: { id: 'CMP-' + id, name: '项目内容任务', channel: details.channel || '抖音', coverTitle: null, aspectRatio: '9:16', subtitleStyle: 'standard', topicId: null, step: 'topics', script: '', scriptVersion: 0, renderedVersion: null, release: null, reviewDecision: null, exported: false, factRefs: [] },
      facts: [
        { id: 'F01', label: '建筑面积', unit: '㎡', required: true },
        { id: 'F02', label: '客厅开间', unit: '米', required: true },
        { id: 'F03', label: '卧室布局', unit: '', required: true },
        { id: 'F04', label: '厨餐关系', unit: '', required: true },
        { id: 'F05', label: '具体房源报价', unit: '', required: false },
        { id: 'F06', label: '可预约时间', unit: '', required: false }
      ].map(fact => ({ ...fact, value: '', source: '', status: 'unverified', updatedAt: '—' })),
      assets: [], topics: emptyTopics(), inquiries: [], leads: [], publications: [], proposals: [], activities: []
    };
  }

  function secondExample() {
    const state = emptyState('project-qian', { name: '栖岸·观澜', city: '杭州', propertyType: '低密庭院住宅', owner: '沈负责人', audience: '庭院生活家庭', salesOwner: '沈顾问' });
    state.property.status = '示例楼盘';
    state.property.visualLabel = '项目资料待完善';
    state.property.size = '268';
    state.facts[0] = { ...state.facts[0], value: '268', source: '栖岸示例销售资料 · 第 1 页', status: 'verified', updatedAt: '2026-10-10' };
    state.facts[1] = { ...state.facts[1], value: '7.1', source: '栖岸示例现场记录 · 等待负责人核对', updatedAt: '2026-10-10' };
    state.facts[2] = { ...state.facts[2], value: '3 房＋多功能厅', source: '栖岸示例户型图 · 第 2 页', status: 'verified', updatedAt: '2026-10-10' };
    state.facts[3] = { ...state.facts[3], value: '中岛连接餐厅', source: '栖岸示例现场记录 · 等待负责人核对', updatedAt: '2026-10-10' };
    state.assets = [];
    return { id: 'project-qian', state };
  }

  function normalizeState(state, id, defaults) {
    const next = clone(state);
    const propertyDefaults = defaults || blankProperty;
    next.property = { ...clone(propertyDefaults), ...(next.property || {}), id };
    next.property.marketingPermission = { ...clone(propertyDefaults.marketingPermission), ...(next.property.marketingPermission || {}) };
    return next;
  }

  function createWorkspace(legacyState) {
    if (legacyState && legacyState.schemaVersion === 1 && Array.isArray(legacyState.projects) && legacyState.projects.length) {
      const restored = clone(legacyState);
      restored.team = { name: '云境内容团队', ...(restored.team || {}) };
      restored.projects = restored.projects.map(project => ({ ...project, state: normalizeState(project.state, project.id, project.id === 'project-yunjing' ? Model.createInitialState().property : blankProperty) }));
      if (!restored.projects.some(project => project.id === restored.activeProjectId)) restored.activeProjectId = restored.projects[0].id;
      return restored;
    }
    const seed = Model.createInitialState();
    const original = legacyState && legacyState.campaign && legacyState.property ? legacyState : seed;
    return {
      schemaVersion: 1, activeProjectId: 'project-yunjing', team: { name: '云境内容团队' },
      projects: [{ id: 'project-yunjing', state: normalizeState(original, 'project-yunjing', seed.property) }, secondExample()]
    };
  }

  function getProject(workspace, id) {
    return workspace && Array.isArray(workspace.projects) ? workspace.projects.find(project => project.id === id) : undefined;
  }

  function getActiveProject(workspace) {
    return getProject(workspace, workspace && workspace.activeProjectId);
  }

  function failure(workspace, error) { return { workspace: clone(workspace), error }; }

  function switchProject(workspace, id) {
    if (!getProject(workspace, id)) return failure(workspace, '找不到这个项目，请重新选择。');
    const next = clone(workspace);
    next.activeProjectId = id;
    return { workspace: next, message: '已切换到“' + getProject(next, id).state.property.name + '”。' };
  }

  function validateDetails(details, creating) {
    if (!details || typeof details !== 'object') return '请填写项目资料。';
    if ((creating || details.name !== undefined) && !useful(details.name)) return '请填写项目名称。';
    if ((creating || details.city !== undefined) && !useful(details.city)) return '请填写项目城市。';
    if (details.channel !== undefined && !['抖音', '小红书'].includes(details.channel)) return '当前仅支持抖音与小红书渠道。';
    return null;
  }

  function createProject(workspace, details) {
    const error = validateDetails(details, true);
    if (error) return failure(workspace, error);
    const next = clone(workspace);
    let number = next.projects.length + 1;
    while (getProject(next, 'project-' + String(number).padStart(3, '0'))) number += 1;
    const id = 'project-' + String(number).padStart(3, '0');
    next.projects.push({ id, state: emptyState(id, details) });
    next.activeProjectId = id;
    return { workspace: next, projectId: id, message: '项目已创建，请补充资料与素材后开始内容任务。' };
  }

  function dispatch(workspace, projectId, action) {
    const project = getProject(workspace, projectId);
    if (!project) return failure(workspace, '找不到当前操作对应的项目。');
    if (action && action.propertyId !== undefined && action.propertyId !== projectId) return failure(workspace, '操作与项目不一致，已阻止跨项目写入。');
    if (action && action.type === 'RESET') return failure(workspace, '请使用工作区重置入口，不能把其他项目替换成默认楼盘。');
    const result = Model.transition(project.state, action);
    if (result.error) return failure(workspace, result.error);
    const next = clone(workspace);
    getProject(next, projectId).state = result.state;
    return { workspace: next, message: result.message };
  }

  function invalidate(state, reason, discardPreview) {
    if (state.campaign.release) {
      state.campaign.release.valid = false;
      state.campaign.release.invalidReason = reason;
    }
    state.campaign.exported = false;
    if (discardPreview) state.campaign.renderedVersion = null;
  }

  function updateProject(workspace, id, details) {
    if (!getProject(workspace, id)) return failure(workspace, '找不到需要修改的项目。');
    const error = validateDetails(details, false);
    if (error) return failure(workspace, error);
    const next = clone(workspace);
    const state = getProject(next, id).state;
    const before = JSON.stringify(state.property);
    const previousName = state.property.name;
    for (const field of ['name', 'city', 'propertyType', 'owner', 'audience', 'salesOwner', 'entryMode']) {
      if (details[field] !== undefined) state.property[field] = text(details[field]);
    }
    if (details.marketingConfirmed !== undefined) state.property.marketingPermission.confirmed = details.marketingConfirmed === true;
    if (details.marketingEvidence !== undefined) state.property.marketingPermission.evidence = text(details.marketingEvidence);
    if (previousName !== state.property.name) {
      if (state.campaign.script.trim()) {
        if (!state.campaign.projectNameNeedsReview) state.campaign.previousProjectName = previousName;
        state.campaign.projectNameNeedsReview = true;
      } else {
        state.campaign.projectNameNeedsReview = false;
        state.campaign.previousProjectName = null;
      }
    }
    state.property.subtitle = [state.property.city, state.property.propertyType].filter(Boolean).join(' · ');
    if (JSON.stringify(state.property) !== before) invalidate(state, '项目资料或营销范围已变化，请重新审核。', previousName !== state.property.name);
    if (details.channel !== undefined) {
      const result = Model.transition(state, { type: 'UPDATE_PACKAGING', channel: details.channel });
      if (result.error) return failure(workspace, result.error);
      getProject(next, id).state = result.state;
    }
    return { workspace: next, projectId: id, message: '项目资料已保存；变更仅影响当前项目。' };
  }

  function getCompleteness(projectOrState) {
    const state = projectOrState && projectOrState.state ? projectOrState.state : projectOrState || {};
    const property = state.property || {};
    const campaign = state.campaign || {};
    const assets = state.assets || [];
    const required = (state.facts || []).filter(fact => fact.required);
    const verified = required.filter(fact => fact.status === 'verified' && Model.hasValidFactEvidence(fact));
    const actual = assets.filter(asset => useful(asset.src) && !['template_photo', 'ai_generated', 'ai_illustration'].includes(asset.previewType) && asset.generated !== true);
    const marketing = property.marketingPermission || {};
    const items = [
      { id: 'basic', label: '项目基础资料', complete: ['name', 'city', 'propertyType', 'owner'].every(field => useful(property[field])), detail: '项目名称、城市、物业类型与资料负责人', action: 'edit-project-details' },
      { id: 'marketing', label: '项目营销范围', complete: marketing.confirmed === true && useful(marketing.evidence), detail: '确认本项目用途，并保存范围依据', action: 'edit-project-details' },
      { id: 'facts', label: '核心事实核验', complete: required.length > 0 && verified.length === required.length, detail: verified.length + ' / ' + required.length + ' 项核心事实已有有效值和依据', action: 'project-facts' },
      { id: 'assets', label: '当前项目素材', complete: actual.length > 0, detail: actual.length ? actual.length + ' 份项目素材可用于本地预览' : '请上传本项目真实素材；模板与 AI 示意图不计入', action: 'project-assets' },
      { id: 'rights', label: '素材用途确认', complete: assets.length > 0 && assets.every(asset => asset.rights === 'authorized'), detail: '逐项确认本地演示用途；真实商业授权需另行核验', action: 'project-assets' },
      { id: 'goal', label: '内容目标与渠道', complete: useful(property.audience) && useful(campaign.channel), detail: '填写目标受众与主运营渠道', action: 'edit-project-details' },
      { id: 'handoff', label: '销售承接设置', complete: useful(property.salesOwner) && useful(property.entryMode), detail: '填写销售负责人及人工咨询承接路径', action: 'edit-project-details' }
    ];
    const completed = items.filter(item => item.complete).length;
    return { percent: Math.round(completed / items.length * 100), completed, total: items.length, items };
  }

  function sanitizeForStorage(workspace) {
    const next = clone(workspace);
    next.projects.forEach(project => {
      const state = project.state;
      const originalCount = state.assets.length;
      state.assets = state.assets.filter(asset => asset.localUpload !== true && !/^blob:/i.test(text(asset.src)));
      if (state.assets.length !== originalCount) invalidate(state, '本地上传素材不随页面保存，请重新上传后预览和审批。', true);
      if (/^(?:blob:|data:)/i.test(text(state.property.heroSrc))) {
        state.property.heroSrc = null;
        state.property.visualLabel = '等待重新上传项目素材';
      }
    });
    return next;
  }

  return { createWorkspace, getActiveProject, getProject, switchProject, createProject, dispatch, getCompleteness, updateProject, sanitizeForStorage };
});
