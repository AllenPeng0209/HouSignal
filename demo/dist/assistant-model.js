(function (root, factory) {
  'use strict';
  const isNode = typeof module === 'object' && module.exports;
  const api = factory(isNode ? require('./model.js') : root.LouxiangModel, isNode ? require('./project-store.js') : root.LouxiangProjects);
  if (isNode) module.exports = api;
  root.LouxiangAssistant = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (Model, Projects) {
  'use strict';
  const text = value => value == null ? '' : String(value).trim();
  const useful = value => !!text(value) && !/^(?:—|-|n\/a|unknown)$/i.test(text(value)) && !/待.*确认|待补|待指定|待分配|待核验|待核对|待填写|待上传|未提供|未知|未确定|暂无|^tbd$/.test(text(value));
  const list = value => Array.isArray(value) ? value : [];
  const distinct = (values, key) => Array.from(new Map(values.map(value => [key(value), value])).values());
  const number = value => Number.isFinite(value) && value >= 0;
  const format = value => value.toLocaleString('zh-CN');
  const stageNames = { qualified: '资格已确认', appointment: '已预约看房', visit_completed: '已完成带看', opportunity: '推进事项待确认', won: '已成交', lost: '已结束', withdrawn: '联系授权已撤回' };
  const stagePriority = { qualified: 1, appointment: 2, visit_completed: 3, opportunity: 4 };
  const notice = '本地记录检索演示，模型与项目数据库待接入。';
  const leadNotice = '优先级依据已记录的销售阶段与下一步，供销售判断；跟进由销售执行。';
  const defaultSuggestions = () => [
    { label: '完整汇总项目', question: '请完整汇总当前项目的资料、内容、客户和下一步' },
    { label: '还缺哪些资料', question: '当前项目还缺哪些关键资料？' },
    { label: '关键客户是谁', question: '当前项目关键客户是谁，优先跟进谁？' },
    { label: '今天先做什么', question: '今天这个项目最应该先做什么？' }
  ];
  const ref = (kind, id, label, page) => ({ kind, ...(id ? { id } : {}), ...(page ? { page } : {}), label });
  const pageRef = (page, label) => ref('page', null, label, page);
  const row = (label, detail, reference, tone) => ({ label, text: detail, ...(reference ? { ref: reference } : {}), ...(tone ? { tone } : {}) });
  const factRef = fact => ref('fact', fact.id, fact.label, 'property');
  const leadRef = lead => ref('lead', lead.id, lead.name, 'leads');
  const inquiryRef = inquiry => ref('inquiry', inquiry.id, inquiry.name, 'leads');
  const assetRef = asset => ref('asset', asset.id, asset.name, 'assets');
  const publicationRef = publication => ref('publication', publication.id, publication.title, 'reports');
  const validFact = fact => fact.status === 'verified' && useful(fact.value) && useful(fact.source) && Model.hasValidFactEvidence(fact);
  const actualAsset = asset => useful(asset.src) && !['template_photo', 'ai_generated', 'ai_illustration'].includes(asset.previewType) && asset.generated !== true;

  function projectData(project) {
    const state = project.state;
    const facts = list(state.facts), assets = list(state.assets);
    const inquiries = distinct(list(state.inquiries), item => item.id);
    const leads = distinct(list(state.leads), item => item.inquiryId || item.id);
    const publications = distinct(list(state.publications), item => item.id);
    const live = publications.filter(item => item.status === 'verified' && item.platformState === 'live');
    const eligible = leads.filter(lead => {
      const inquiry = inquiries.find(item => item.id === lead.inquiryId);
      return !!stagePriority[lead.stage] && !!inquiry && inquiry.contactable === true && inquiry.consent === true;
    }).sort((a, b) => stagePriority[b.stage] - stagePriority[a.stage] || Number(useful(b.nextAction)) - Number(useful(a.nextAction)));
    const readiness = Projects.getCompleteness(project);
    const required = facts.filter(fact => fact.required);
    const factPreparation = readiness.items.find(item => item.id === 'facts');
    if (factPreparation) {
      const count = required.filter(validFact).length;
      factPreparation.complete = required.length > 0 && count === required.length;
      factPreparation.detail = count + ' / ' + required.length + ' 项核心事实已有有效值和依据';
      readiness.completed = readiness.items.filter(item => item.complete).length;
      readiness.percent = Math.round(readiness.completed / readiness.total * 100);
    }
    return { state, property: state.property || {}, campaign: state.campaign || {}, facts, assets, inquiries, leads, publications, live, eligible, readiness };
  }

  function contentProgress(data) {
    const campaign = data.campaign;
    const detail = campaign.projectNameNeedsReview ? '项目名称已变更，旧稿须重新核对名称。' : !text(campaign.script) ? '尚无脚本；先选择已核验事实支持的选题，再生成并人工核对。' : campaign.renderedVersion !== campaign.scriptVersion ? '脚本 v' + campaign.scriptVersion + ' 尚缺对应版本预览。' : campaign.release?.valid !== true ? '当前内容版本仍需人工审核。' : '当前版本有有效演示审批；导出及平台发布需人工处理。';
    return row('当前内容进度', detail, pageRef('studio', '内容工作室'));
  }

  function contentMetric(data) {
    const windows = Array.from(new Set(data.live.map(item => useful(item.window) ? item.window : '观察窗口待确认')));
    if (!data.reportWindow && windows.some(window => window !== 'D7')) {
      return data.live.length + ' 条已核验且在线；' + windows.map(window => {
        const records = data.live.filter(item => (useful(item.window) ? item.window : '观察窗口待确认') === window);
        const sum = records.reduce((total, item) => total + (number(item.views) ? item.views : 0), 0);
        const missing = records.filter(item => !number(item.views)).length;
        return window + ' 播放量 ' + format(sum) + (missing ? '（已录入部分，' + missing + ' 条待录入）' : '');
      }).join('；');
    }
    if (data.reportWindow && !data.live.length) return '0 条已核验且在线；' + data.reportWindow + ' 播放量待观察';
    const sum = data.live.reduce((total, item) => total + (number(item.views) ? item.views : 0), 0);
    const missing = data.live.filter(item => !number(item.views)).length;
    return data.live.length + ' 条已核验且在线；播放量合计 ' + format(sum) + (missing ? '（已录入部分，' + missing + ' 条播放量待录入）' : '');
  }

  function metricRows(data) {
    const unknown = data.inquiries.filter(item => item.sourceConfidence === 'unknown' || !['confirmed', 'self_reported'].includes(item.sourceConfidence)).length;
    const reported = data.inquiries.filter(item => item.sourceConfidence === 'self_reported').length;
    return [
      row('已发布内容', contentMetric(data), pageRef('reports', '效果分析')),
      row('咨询与客户', data.inquiries.length + ' 条咨询；' + data.leads.length + ' 条历史资格线索；' + data.eligible.length + ' 位可跟进客户', pageRef('leads', '咨询与客户')),
      row('来源归因', unknown + ' 条未知来源；' + reported + ' 条客户自述来源，不能直接算作已确认内容归因', pageRef('reports', '来源归因'), unknown ? 'amber' : undefined)
    ];
  }

  function factRows(facts) {
    return facts.map(fact => validFact(fact)
      ? row(fact.label, text(fact.value) + (text(fact.unit) ? ' ' + text(fact.unit) : '') + '；依据：' + text(fact.source), factRef(fact))
      : row(fact.label, fact.status === 'expired' ? '记录已过期，需资料负责人重新核验，不能作为当前事实引用。' : '尚未核验或缺少有效值与依据；目前不能确认。', factRef(fact), 'amber'));
  }

  function gapRows(data) {
    const pageFor = action => action === 'project-facts' ? 'property' : action === 'project-assets' ? 'assets' : 'settings';
    const missing = data.readiness.items.filter(item => !item.complete).map(item => row(item.label, item.detail, ref('completeness', item.id, item.label, pageFor(item.action)), 'amber'));
    const unverified = data.facts.filter(fact => !validFact(fact));
    return [...missing, ...factRows(unverified)];
  }

  function leadRows(data, limit) {
    return data.eligible.slice(0, limit == null ? data.eligible.length : limit).map(lead => row(lead.name,
      (stageNames[lead.stage] || '阶段待确认') + ' · ' + (useful(lead.owner) ? lead.owner : '负责人待分配') + '；需求：' + (useful(lead.need) ? lead.need : '待销售确认') + '；下一步：' + (useful(lead.nextAction) ? lead.nextAction : '由销售确认下一步，尚无明确安排'), leadRef(lead)));
  }

  function reportRows(data) {
    return data.live.map(item => row(item.title, item.channel + ' · ' + (number(item.views) ? '播放 ' + format(item.views) : '播放量待录入') + '；' + (number(item.inquiries) ? '作品记录咨询 ' + item.inquiries : '作品咨询待录入') + '；观察窗口 ' + (useful(item.window) ? item.window : '待观察'), publicationRef(item)));
  }

  function resolveCustomer(data, question, history, projectId, allowContext) {
    const named = data.leads.filter(lead => (useful(lead.name) && question.toLowerCase().includes(text(lead.name).toLowerCase())) || (useful(lead.id) && question.toLowerCase().includes(text(lead.id).toLowerCase())));
    if (named.length) return { leads: named };
    const inquiryNamed = data.inquiries.filter(inquiry => (useful(inquiry.name) && question.toLowerCase().includes(text(inquiry.name).toLowerCase())) || (useful(inquiry.id) && question.toLowerCase().includes(text(inquiry.id).toLowerCase())));
    if (inquiryNamed.length) return { inquiries: inquiryNamed };
    if (allowContext && /(?:^|[\s，。！？:：,!?])(?:那|那么|关于|请问)?\s*(?:(?:他|她)(?:的|呢|现在|目前|下一步|接下来|该|要|会|如何|怎么样|怎么办|是否|还|最近|今天|可以|能)|这位|这个客户)|\b(?:that client|this customer)\b/i.test(question)) {
      for (let index = list(history).length - 1; index >= 0; index -= 1) {
        const entry = history[index], previous = entry && (entry.answer || entry);
        if (previous && previous.projectId === projectId && previous.contextLeadId) {
          const lead = data.leads.find(item => item.id === previous.contextLeadId);
          if (lead) return { leads: [lead] };
        }
      }
    }
    return null;
  }

  function customerSections(data, customer) {
    if (customer.leads) return customer.leads.map(lead => {
      const inquiry = data.inquiries.find(item => item.id === lead.inquiryId);
      const contactAllowed = !!stagePriority[lead.stage] && !!inquiry && inquiry.contactable === true && inquiry.consent === true;
      const stopped = lead.stage === 'withdrawn' ? '联系授权已撤回，停止后续联系。' : ['won', 'lost'].includes(lead.stage) ? '这条线索已结束，当前不安排销售跟进。' : '当前没有有效可联系记录与联系授权，先由销售核对，不能继续营销联系。';
      const notes = list(lead.notes).filter(note => useful(note.text)).slice(-3);
      return { title: lead.name + ' · 当前记录', items: [
        row('客户阶段', stageNames[lead.stage] || '阶段待确认', leadRef(lead)),
        row('已记录需求', useful(lead.need) ? lead.need : '没有完整需求记录，待销售确认', leadRef(lead)),
        row('负责顾问', useful(lead.owner) ? lead.owner : '负责人待分配', leadRef(lead)),
        row('下一步', contactAllowed ? (useful(lead.nextAction) ? lead.nextAction : '尚未记录下一步；由负责顾问确认') : stopped, leadRef(lead), contactAllowed ? undefined : 'amber'),
        row('最近记录', useful(lead.lastAt) ? lead.lastAt : '尚无最近沟通时间', leadRef(lead)),
        ...(inquiry ? [row('来源记录', inquiry.sourceConfidence === 'confirmed' ? '人工确认来源；' + text(inquiry.sourceText) : inquiry.sourceConfidence === 'self_reported' ? '客户自述来源，关联待核对；' + text(inquiry.sourceText) : '来源未知，继续保留未知', inquiryRef(inquiry))] : []),
        ...notes.map((note, index) => row('销售反馈 ' + (index + 1), text(note.text), leadRef(lead)))
      ] };
    });
    return customer.inquiries.map(inquiry => ({ title: inquiry.name + ' · 咨询记录', items: [
      row('已记录需求', useful(inquiry.need) ? inquiry.need : '需求待销售确认', inquiryRef(inquiry)),
      row('客户状态', '仅有咨询记录，尚无对应资格线索；不能推断为关键客户。', inquiryRef(inquiry)),
      row('下一步', inquiry.contactable === true && inquiry.consent === true ? '由销售核对需求与项目适配，再决定是否建立资格线索。' : '先核对可联系路径与联系授权；当前不安排营销联系。', inquiryRef(inquiry), 'amber')
    ] }));
  }

  function matchingFacts(data, question) {
    const requested = [];
    if (/面积|多大|多少平|size|area/i.test(question)) requested.push('F01');
    if (/开间|多宽|宽度|width/i.test(question)) requested.push('F02');
    if (/卧室|几房|几室|房型|房间|bedroom|layout/i.test(question)) requested.push('F03');
    if (/厨|餐厅|餐关系|kitchen/i.test(question)) requested.push('F04');
    if (/报价|价格|多少钱|总价|单价|price|pricing/i.test(question)) requested.push('F05');
    if (/预约时间|什么时候.*看房|可预约|看房时间/i.test(question)) requested.push('F06');
    const selected = data.facts.filter(fact => requested.includes(fact.id) || (text(fact.label) && question.includes(fact.label)));
    return selected.length ? selected : data.facts;
  }

  function answer(workspace, projectId, question, history) {
    const questionText = text(question).slice(0, 2000);
    const project = Projects.getProject(workspace, projectId);
    const result = { projectId, projectName: project ? text(project.state.property.name) : '项目未找到', intent: 'unsupported', title: '楼管家', summary: '', sections: [], sources: [], suggestions: defaultSuggestions(), notice };
    if (!project) {
      result.intent = 'project_unavailable'; result.title = '请重新选择项目'; result.summary = '当前项目已不存在或尚未选定，请通过“当前项目”选择器重新选择。'; return result;
    }
    const otherProject = list(workspace.projects).find(item => item.id !== projectId && useful(item.state?.property?.name) && questionText.includes(item.state.property.name));
    if (otherProject) {
      result.intent = 'switch_project'; result.title = '请先切换项目'; result.summary = '当前查询范围是“' + result.projectName + '”。要查询“' + text(otherProject.state.property.name) + '”，请先切换到该项目，再发送问题。'; return result;
    }
    const data = projectData(project);
    const fullOverview = /完整.*汇总|全面.*汇总|项目全貌|项目总览|project summary|project overview/i.test(questionText);
    if (/预测|涨幅|涨跌|走势|明年|自动.*(?:打电话|联系|发送|发布|爬取)|购房概率|成交概率|forecast|predict/i.test(questionText)) {
      result.summary = '本地演示可以查阅当前项目已保存的记录；市场预测、自动联系或自动发布尚未接入。请使用下方建议查询项目资料与客户。';
      return result;
    }
    const customer = resolveCustomer(data, questionText, history, projectId, !fullOverview);
    if (customer) {
      result.intent = 'customer'; result.title = '客户记录与下一步';
      const names = [...list(customer.leads), ...list(customer.inquiries)].map(item => item.name);
      result.summary = '在当前项目找到 ' + names.join('、') + ' 的记录。以下内容来自已保存的销售记录。';
      result.sections = customerSections(data, customer);
      if (customer.leads?.length === 1) result.contextLeadId = customer.leads[0].id;
      result.notice = notice + ' ' + leadNotice;
      result.suggestions = customer.leads?.length === 1 ? [{ label: '查看最新下一步', question: customer.leads[0].name + '现在下一步应该做什么？' }, ...defaultSuggestions().slice(0, 2)] : defaultSuggestions();
    } else if (/(?:[^\s，。？?]{1,8}(?:先生|女士)|(?:LEAD|INQ)\d+)/i.test(questionText)) {
      result.intent = 'customer'; result.title = '没有匹配的客户记录'; result.summary = '当前项目没有找到匹配的客户记录。请核对姓名，或通过“咨询与客户”查看已保存的记录。';
    } else if (!fullOverview && /缺|不足|待补|完整度|差哪些|missing|gap/i.test(questionText)) {
      result.intent = 'gaps'; result.title = '资料缺口与限制';
      const gaps = gapRows(data);
      result.summary = '当前项目资料完整度 ' + data.readiness.percent + '%；' + data.readiness.items.filter(item => !item.complete).length + ' 项准备工作待完善。完整度仅表示资料准备情况。';
      result.sections = [{ title: '需要补齐或重新核验', items: gaps.length ? gaps : [row('准备情况', '七项准备工作已完成；发布前仍需按具体内容版本人工审核。', ref('completeness', null, '项目资料完整度', 'property'))] }];
    } else if (!fullOverview && /关键客户|重点客户|优先客户|优先.*跟进|先.*跟进|跟进.*谁|谁.*跟进|值得联系|客户名单|有哪些客户|客户列表|客户是谁|priority|\bleads\b|\bclients\b|\bcustomers\b/i.test(questionText)) {
      result.intent = 'leads'; result.title = '关键客户与跟进顺序';
      result.summary = data.eligible.length ? '当前项目有 ' + data.eligible.length + ' 位可跟进客户，按已记录阶段和明确下一步排列。' : '当前项目没有具备有效联系路径与授权的可跟进客户；不生成假定名单。';
      result.sections = [{ title: '建议销售优先检查', items: leadRows(data) }];
      result.notice = notice + ' ' + leadNotice;
    } else if (!fullOverview && /效果|表现|播放|复盘|转化|归因|performance|metrics|views|report/i.test(questionText)) {
      result.intent = 'reports'; result.title = '内容效果与来源归因';
      const windowMatch = questionText.match(/\bD(1|7|30)\b/i);
      data.reportWindow = windowMatch ? windowMatch[0].toUpperCase() : 'D7';
      data.live = data.live.filter(item => item.window === data.reportWindow);
      result.summary = data.live.length ? '按 ' + data.reportWindow + ' 观察窗口汇总已核验且在线的内容，不与其他窗口合并。咨询、资格线索与成交分别保留。' : data.reportWindow + ' 尚无已核验且在线的数据，播放量待观察。咨询与客户数字为项目总记录。';
      result.sections = [{ title: '当前项目指标', items: metricRows(data) }, { title: '已核验且在线的作品', items: reportRows(data) }];
      result.notice = notice + ' 未知来源保持未知；客户自述需要进一步核对，不能推导内容带来的成交或完整转化率。';
    } else if (!fullOverview && /素材|实拍|镜头|图片|assets|materials/i.test(questionText)) {
      result.intent = 'assets'; result.title = '项目素材与用途';
      const actual = data.assets.filter(actualAsset), illustrative = data.assets.filter(item => !actualAsset(item));
      result.summary = actual.length + ' 份项目实际素材；' + illustrative.length + ' 份模板或 AI 示意素材。用途确认不代表真实商业授权已完成。';
      result.sections = [{ title: '已保存素材', items: data.assets.map(asset => row(asset.name, (actualAsset(asset) ? '项目素材' : '模板或 AI 示意，不能代表本项目实际情况') + '；' + (asset.rights === 'authorized' ? '已确认本地演示用途' : '用途待确认'), assetRef(asset), asset.rights === 'authorized' ? undefined : 'amber')) }];
    } else if (!fullOverview && /面积|开间|多宽|报价|价格|多少钱|几房|几室|卧室|房型|厨餐|厨房|可预约|看房时间|事实|已核验资料|area|price|fact|bedroom|width/i.test(questionText)) {
      result.intent = 'facts'; result.title = '楼盘事实与依据'; result.summary = '从当前项目资料中查找相关记录；只有已核验且具有有效值与依据的记录作为事实列出。';
      result.sections = [{ title: '相关资料', items: factRows(matchingFacts(data, questionText)) }];
    } else if (!fullOverview && /下一步|先做|该做|怎么办|待办|待处理|今天|任务|接下来|推进|哪三件事|todo|next step|what.*do/i.test(questionText)) {
      result.intent = 'tasks'; result.title = '当前项目下一步'; result.summary = '先补影响内容可信度的资料，再处理已授权客户与内容任务。以下是建议安排，尚未自动执行。';
      const gaps = gapRows(data);
      const requiredGaps = gaps.filter(item => item.ref?.kind === 'completeness');
      const tasks = [...requiredGaps, ...leadRows(data, 1), contentProgress(data), ...gaps.filter(item => item.ref?.kind !== 'completeness')].slice(0, 3);
      result.sections = [{ title: '建议先推进的三件事', items: tasks }];
    } else if (/汇总|总结|概况|概览|情况|全貌|总览|完整|进展|overview|summary|status/i.test(questionText)) {
      result.intent = 'overview'; result.title = result.projectName + ' · 项目全貌';
      result.summary = '我已读取当前项目的楼盘资料、素材、发布记录和客户记录。资料完整度 ' + data.readiness.percent + '%；当前有 ' + data.eligible.length + ' 位可跟进客户。';
      result.sections = [
        { title: '项目总览', items: [row('项目基本信息', [data.property.city, data.property.propertyType].filter(useful).join(' · ') || '基础资料待完善', pageRef('settings', '项目基础资料')), row('资料负责人', useful(data.property.owner) ? data.property.owner : '负责人待分配', pageRef('settings', '资料负责人')), row('资料完整度', data.readiness.percent + '%（' + data.readiness.completed + ' / ' + data.readiness.total + ' 项准备工作）', ref('completeness', null, '项目资料完整度', 'property')), row('项目实际素材', data.assets.filter(actualAsset).length + ' 份；模板和 AI 示意不计作本项目实际素材', pageRef('assets', '素材库')), row('内容目标与渠道', [data.property.audience, data.campaign.channel].filter(useful).join(' · ') || '待完善', pageRef('settings', '内容目标与渠道')), contentProgress(data), ...metricRows(data)] },
        { title: '已核验核心事实', items: factRows(data.facts.filter(fact => fact.required && validFact(fact))) },
        { title: '缺口与需要确认的事项', items: gapRows(data) },
        { title: '关键客户与明确下一步', items: leadRows(data, 3) }
      ];
      result.notice = notice + ' ' + leadNotice;
    } else {
      result.summary = '这是本地规则查询演示，当前支持项目汇总、资料缺口、客户与跟进、楼盘事实、素材及内容效果。这个问题尚未匹配支持的查询；请使用下方建议，或换一种具体问法。';
    }
    const recordSources = result.sections.flatMap(section => section.items.map(item => item.ref).filter(Boolean));
    if (['overview', 'reports'].includes(result.intent)) recordSources.push(...data.live.map(publicationRef), ...data.inquiries.map(inquiryRef));
    if (result.intent === 'gaps') recordSources.push(ref('completeness', null, '项目资料完整度', 'property'));
    result.sources = distinct(recordSources, reference => [reference.kind, reference.id || '', reference.page || ''].join(':'));
    return result;
  }

  return { answer };
});
