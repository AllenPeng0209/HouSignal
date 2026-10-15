(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.LouxiangModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DEMO_TIME = '2026-10-11T09:00:00+08:00';
  const STAGES = ['qualified', 'appointment', 'visit_completed', 'opportunity', 'won'];
  const clone = value => JSON.parse(JSON.stringify(value));

  function createInitialState() {
    return {
      demo: true,
      property: { id: 'project-yunjing', name: '云境·壹号', subtitle: '上海 · 滨江改善', unit: 'A-1801', size: '328', status: '示例楼盘', city: '上海', propertyType: '滨江改善住宅', owner: '林嘉宁', audience: '家庭改善', salesOwner: '林顾问', entryMode: 'manual_native', heroSrc: 'assets/hero-interior.jpg', visualLabel: 'AI 空间示意', marketingPermission: { confirmed: true, evidence: '示例营销范围确认记录 · 仅用于本地产品演示' } },
      campaign: { id: 'CMP-2026-001', name: '让空间，回应生活', channel: '抖音', coverTitle: null, aspectRatio: '9:16', subtitleStyle: 'standard', topicId: null, step: 'topics', script: '', scriptVersion: 0, renderedVersion: null, release: null, reviewDecision: null, exported: false, factRefs: [] },
      facts: [
        { id: 'F01', label: '建筑面积', value: '328', unit: '㎡', source: '示例销售资料 · 第 2 页', status: 'verified', updatedAt: '2026-10-09', required: true },
        { id: 'F02', label: '客厅开间', value: '8.2', unit: '米', source: '示例实测记录 · 客厅镜头 00:16', status: 'verified', updatedAt: '2026-10-09', required: true },
        { id: 'F03', label: '卧室布局', value: '4 房同层', unit: '', source: '示例户型图 · A-1801', status: 'verified', updatedAt: '2026-10-09', required: true },
        { id: 'F04', label: '厨餐关系', value: '厨房与餐厅相邻', unit: '', source: '示例现场拍摄 · 厨餐镜头 00:32', status: 'verified', updatedAt: '2026-10-09', required: true },
        { id: 'F05', label: '具体房源报价', value: '待负责人确认', unit: '', source: '未提供当前有效报价凭据', status: 'unverified', updatedAt: '2026-10-08', required: false },
        { id: 'F06', label: '可预约时间', value: '需销售逐次确认', unit: '', source: '示例接待说明 · 第 1 页', status: 'verified', updatedAt: '2026-10-10', required: false }
      ],
      assets: [
        { id: 'A01', name: '客厅全景 · 空间分镜示意', space: '会客', type: 'image', duration: '—', rights: 'authorized', src: 'assets/interior-1.jpg', previewType: 'template_photo', note: '模板照片预览，非本楼盘现场素材；仅演示分镜布局。' },
        { id: 'A02', name: '厨餐动线 · 空间分镜示意', space: '厨餐', type: 'image', duration: '—', rights: 'authorized', src: 'assets/interior-2.jpg', previewType: 'template_photo', note: '模板照片预览，非本楼盘现场素材；仅演示分镜布局。' },
        { id: 'A03', name: '家庭起居 · 空间分镜示意', space: '起居', type: 'image', duration: '—', rights: 'authorized', src: 'assets/interior-3.jpg', previewType: 'template_photo', note: '模板照片预览，非本楼盘现场素材；仅演示分镜布局。' },
        { id: 'A04', name: '私密休息 · 空间分镜示意', space: '私密', type: 'image', duration: '—', rights: 'authorized', src: 'assets/interior-1.jpg', previewType: 'template_photo', note: '模板照片预览，非本楼盘现场素材；仅演示分镜布局。' }
      ],
      topics: [
        { id: 'T01', title: '大客厅之后，家的尺度在哪里？', audience: '重视日常会客与家庭共处的改善家庭', angle: '从开间到起居，用真实尺度解释一家人的相处方式', refs: ['F01', 'F02'], tag: '空间尺度' },
        { id: 'T02', title: '三代同住，先看每天怎么走', audience: '有长辈同住需求的家庭', angle: '关注同层卧室与厨餐动线，同时提醒现场确认无障碍细节', refs: ['F03', 'F04'], tag: '生活动线' },
        { id: 'T03', title: '厨房和餐厅之间，藏着日常生活', audience: '经常在家做饭、招待朋友的家庭', angle: '比较真实厨餐距离，解释备餐与交流的生活场景', refs: ['F04'], tag: '厨餐日常' },
        { id: 'T04', title: '328 ㎡，哪些面积真正为你所用？', audience: '重视空间效率的改善购房者', angle: '区分建筑面积与实际使用感，不推导未核验套内面积', refs: ['F01', 'F03'], tag: '专业取舍' },
        { id: 'T05', title: '好房的标准，从一场真实看房开始', audience: '希望现场理解居住适配的购房者', angle: '以事实和问题清单组织看房，把专业判断交还给客户', refs: ['F02', 'F03', 'F04', 'F06'], tag: '看房准备' }
      ],
      inquiries: [
        { id: 'INQ001', name: '陈女士', channel: '抖音', sourceConfidence: 'confirmed', sourceText: '示例作品 PUB001 · 站内主动咨询', need: '希望了解长辈同住的动线安排', contactable: true, consent: true, qualified: true, owner: '林顾问', lastAt: '今天 08:40', avatar: '陈' },
        { id: 'INQ002', name: '许先生', channel: '抖音', sourceConfidence: 'confirmed', sourceText: '示例作品 PUB001 · 人工核对原会话', need: '日常在家会客，关注客厅与餐厅关系', contactable: true, consent: true, qualified: true, owner: '林顾问', lastAt: '昨天 16:20', avatar: '许' },
        { id: 'INQ003', name: '周女士', channel: '抖音', sourceConfidence: 'self_reported', sourceText: '客户自述看过厨餐内容，作品关联待核对', need: '改善自住，关注厨房动线与房间安排', contactable: true, consent: true, qualified: true, owner: '孟顾问', lastAt: '昨天 11:30', avatar: '周' },
        { id: 'INQ004', name: '一位咨询者', channel: '抖音', sourceConfidence: 'unknown', sourceText: '尚未确认具体作品或其他来源', need: '询问房源资料，具体需求待销售确认', contactable: false, consent: false, qualified: false, owner: '待分配', lastAt: '今天 08:55', avatar: '询' }
      ],
      leads: [
        { id: 'LEAD001', inquiryId: 'INQ001', name: '陈女士', stage: 'qualified', need: '长辈同住，确认日常动线及空间适配', owner: '林顾问', sourceConfidence: 'confirmed', lastAt: '今天 08:40', nextAction: '通过已授权原会话回应动线问题', notes: [], stageHistory: [{ stage: 'qualified', at: '2026-10-11T08:40:00+08:00' }] },
        { id: 'LEAD002', inquiryId: 'INQ002', name: '许先生', stage: 'appointment', need: '经常在家会客，希望现场感受客厅尺度', owner: '林顾问', sourceConfidence: 'confirmed', lastAt: '昨天 16:20', nextAction: '再次确认双方已约定的看房时间', notes: [], stageHistory: [{ stage: 'qualified', at: '2026-10-09T10:00:00+08:00' }, { stage: 'appointment', at: '2026-10-10T16:20:00+08:00' }] },
        { id: 'LEAD003', inquiryId: 'INQ003', name: '周女士', stage: 'visit_completed', need: '改善自住，已实地查看厨餐动线', owner: '孟顾问', sourceConfidence: 'self_reported', lastAt: '昨天 11:30', nextAction: '收集实地看房后的取舍与疑问', notes: [], stageHistory: [{ stage: 'qualified', at: '2026-10-07T10:00:00+08:00' }, { stage: 'appointment', at: '2026-10-08T14:00:00+08:00' }, { stage: 'visit_completed', at: '2026-10-10T11:30:00+08:00' }] }
      ],
      publications: [
        { id: 'PUB001', title: '三代同住，先看每天怎么走', channel: '抖音', status: 'verified', platformState: 'live', views: 9800, inquiries: 2, qualified: 2, at: '2026-10-01', window: 'D7', url: 'https://example.com/demo/PUB001', demo: true },
        { id: 'PUB002', title: '厨房和餐厅之间，藏着日常生活', channel: '抖音', status: 'verified', platformState: 'live', views: 4300, inquiries: 1, qualified: 1, at: '2026-10-03', window: 'D7', url: 'https://example.com/demo/PUB002', demo: true },
        { id: 'PUB003', title: '328 ㎡，哪些面积真正为你所用？', channel: '抖音', status: 'recorded', platformState: 'review_pending', views: null, inquiries: null, qualified: null, at: '2026-10-10', window: 'D7', url: 'https://example.com/demo/PUB003', demo: true }
      ],
      proposals: [
        { id: 'P01', title: '下一条，讲清楚长辈的日常动线', reason: '销售匿名反馈出现同层居住与无障碍细节问题；先补核验再创作', status: 'pending', refs: ['F03', 'INQ001'] },
        { id: 'P02', title: '用一组真实镜头解释厨餐关系', reason: '已有厨餐选题关联 1 条资格线索；增加具体生活场景并保留取舍', status: 'pending', refs: ['F04', 'PUB002'] },
        { id: 'P03', title: '把未知来源的咨询交还销售确认', reason: '1 条咨询没有可确认的内容来源；继续保留未知，主动询问而不推断', status: 'pending', refs: ['INQ004'] }
      ],
      activities: [
        { id: 'ACT001', title: '示例资料负责人已核验 4 项核心事实', at: '今天 08:30', type: 'fact', demo: true },
        { id: 'ACT002', title: '陈女士已由销售确认真实需求', at: '今天 08:40', type: 'lead', demo: true },
        { id: 'ACT003', title: '1 条新咨询等待销售确认', at: '今天 08:55', type: 'inquiry', demo: true }
      ]
    };
  }

  function invalidateCampaign(state, reason, discardPreview) {
    const campaign = state.campaign;
    if (campaign.release) {
      campaign.release.valid = false;
      campaign.release.invalidReason = reason;
    }
    campaign.exported = false;
    if (discardPreview) campaign.renderedVersion = null;
  }

  function approvalBlock(state) {
    const campaign = state.campaign;
    if (campaign.projectNameNeedsReview) return '项目名称已变更，旧稿已保留。请重新生成脚本，或把旧名称改为当前项目名称后再审核。';
    const marketing = state.property && state.property.marketingPermission;
    if (marketing && (marketing.confirmed !== true || !validEvidenceText(marketing.evidence))) return '当前项目的营销范围尚未确认，请填写用途和依据后再审核。';
    if (!campaign.script.trim()) return '请先选择选题并完成脚本。';
    const factIds = new Set(campaign.factRefs || []);
    if (Array.from(factIds).some(id => !state.facts.some(fact => fact.id === id))) return '脚本引用的事实不存在，请重新核对依据。';
    const blocked = state.facts.find(fact => (fact.required || factIds.has(fact.id)) && (fact.status !== 'verified' || !hasValidFactEvidence(fact)));
    if (blocked) return '事实“' + blocked.label + '”尚未有效核验，请由资料负责人处理。';
    if (!state.assets.length || state.assets.some(asset => !validEvidenceText(asset.src))) return '当前项目尚缺可预览的素材，请上传后再审核。';
    if (state.assets.some(asset => asset.rights !== 'authorized')) return '素材授权尚未确认，不能批准对外内容。';
    if (campaign.renderedVersion !== campaign.scriptVersion) return '当前脚本尚未完成对应版本的分镜预览，请重新预览。';
    return null;
  }

  function activity(state, type, title) {
    state.activities.unshift({ id: 'ACT' + String(state.activities.length + 1).padStart(3, '0'), title, type, at: '刚刚', demo: true });
  }

  function validEvidenceText(value) {
    const text = value == null ? '' : String(value).trim();
    return !!text && !/^(?:—|-|n\/a|unknown)$/i.test(text) && !/待.*确认|待补|未提供|未知|未确定|暂无/.test(text);
  }

  function hasValidFactEvidence(fact) {
    return !!fact && validEvidenceText(fact.value) && validEvidenceText(fact.source);
  }

  function buildScript(topic, state) {
    const facts = new Map(state.facts.map(fact => [fact.id, fact]));
    const claim = id => {
      const fact = facts.get(id);
      return fact ? fact.value + (fact.unit ? ' ' + fact.unit : '') : '';
    };
    const propertyName = state.property.name || '当前项目';
    const openings = {
      T01: '一套房的尺度，不只写在面积数字里，也写在一家人怎么坐下来。',
      T02: '三代同住，先别急着数房间。我们先从每天怎么走开始。',
      T03: '家里的烟火气，往往发生在厨房和餐厅之间。',
      T04: '看到 ' + claim('F01') + '，先问一句：这是什么面积口径，又如何对应你的生活？',
      T05: '好房适不适合你，应该从一场带着问题的真实看房开始。'
    };
    const details = {
      T01: propertyName + '的建筑面积为 ' + claim('F01') + ' [F01]，客厅开间实测 ' + claim('F02') + ' [F02]。镜头先交代边界，再看看不同位置的交流距离。',
      T02: propertyName + '的卧室布局为' + claim('F03') + ' [F03]，厨餐关系为' + claim('F04') + ' [F04]。从卧室走向餐桌，用同一段真实镜头把这条路线讲清楚。',
      T03: propertyName + '的厨餐关系为' + claim('F04') + ' [F04]。备餐、上菜和餐桌交流如何衔接，要结合你的做饭习惯现场体验。',
      T04: propertyName + '资料中的 ' + claim('F01') + ' 指建筑面积 [F01]；卧室布局为' + claim('F03') + ' [F03]。没有套内面积依据，就不把建筑面积换成可使用面积。',
      T05: propertyName + '客厅开间实测 ' + claim('F02') + ' [F02]，卧室布局为' + claim('F03') + ' [F03]，厨餐关系为' + claim('F04') + ' [F04]。可预约时间为' + claim('F06') + ' [F06]。每一处，都可以变成一次现场确认的问题。'
    };
    return '[开场 · 00–08 秒]\n' + openings[topic.id] +
      '\n\n[事实 · 08–32 秒]\n' + details[topic.id] +
      '\n\n[生活意义 · 32–50 秒 · 顾问观点]\n' + topic.angle + '。这是一种使用场景的解释，不代表对所有家庭都适用。' +
      '\n\n[取舍 · 50–65 秒]\n如果你的日常需求与这里不同，空间再大也可能需要重新权衡。动线、采光和使用细节，请以现场核对为准。' +
      '\n\n[咨询邀请 · 65–75 秒]\n如果你正在考虑类似居住需求，可以在平台内主动咨询。我们先聊生活方式，再确认是否适合安排看房。' +
      '\n\n（本地交互样稿；楼盘与事实均为演示数据，时间仅为编辑目标。）';
  }

  function transition(state, action) {
    if (!action || typeof action.type !== 'string') return { state: clone(state), error: '缺少可执行的操作。' };
    if (action.type === 'RESET') return { state: createInitialState(), message: '示例工作区已重置。' };
    const next = clone(state);
    const campaign = next.campaign;
    const fail = error => ({ state: clone(state), error });
    let message;
    switch (action.type) {
      case 'SELECT_TOPIC': {
        const topic = next.topics.find(item => item.id === action.id);
        if (!topic) return fail('找不到这个选题，请重新选择。');
        if (campaign.topicId !== topic.id) {
          invalidateCampaign(next, '选题已变化，需重新生成并审核内容。', true);
          campaign.script = '';
          campaign.factRefs = topic.refs.slice();
        }
        campaign.topicId = topic.id;
        campaign.step = 'script';
        message = '已选择选题，下一步生成口播样稿。';
        break;
      }
      case 'GENERATE_SCRIPT': {
        const topic = next.topics.find(item => item.id === campaign.topicId);
        if (!topic) return fail('请先选择一个选题。');
        const missing = topic.refs.some(id => !next.facts.some(fact => fact.id === id && fact.status === 'verified' && hasValidFactEvidence(fact)));
        if (missing) return fail('选题引用的事实尚未有效核验，请补齐资料后再生成。');
        invalidateCampaign(next, '脚本已生成新版本，需重新预览和审核。', true);
        campaign.script = buildScript(topic, next);
        campaign.projectNameNeedsReview = false;
        campaign.previousProjectName = null;
        campaign.scriptVersion += 1;
        campaign.factRefs = topic.refs.slice();
        campaign.step = 'script';
        activity(next, 'content', '已组装示例口播脚本 v' + campaign.scriptVersion);
        message = '已生成本地示例样稿；请人工核对每句事实和取舍。';
        break;
      }
      case 'UPDATE_SCRIPT': {
        if (typeof action.text !== 'string' || !action.text.trim()) return fail('脚本不能为空。');
        if (campaign.projectNameNeedsReview) {
          const currentName = next.property.name;
          const previousName = campaign.previousProjectName;
          if (currentName && previousName && action.text.includes(currentName) && !action.text.split(currentName).join('').includes(previousName)) {
            campaign.projectNameNeedsReview = false;
            campaign.previousProjectName = null;
          }
        }
        if (action.text === campaign.script) return { state: next, message: '脚本没有变化。' };
        invalidateCampaign(next, '脚本已编辑，原审批失效。', true);
        campaign.script = action.text;
        const refs = Array.from(action.text.matchAll(/\[(F\d+)\]/g), match => match[1]);
        campaign.factRefs = Array.from(new Set([...(campaign.factRefs || []), ...refs]));
        campaign.scriptVersion += 1;
        campaign.step = 'script';
        activity(next, 'content', '脚本更新至 v' + campaign.scriptVersion + '，等待重新审核');
        message = '新脚本已保存；原审批失效，请重新预览并审核。';
        break;
      }
      case 'UPDATE_PACKAGING': {
        if (action.channel !== undefined && !['抖音', '小红书'].includes(action.channel)) return fail('首轮仅支持抖音与小红书包装。');
        if (action.coverTitle !== undefined && typeof action.coverTitle !== 'string') return fail('封面标题需填写文字。');
        if (action.aspectRatio !== undefined && !['9:16', '16:9'].includes(action.aspectRatio)) return fail('画面比例仅支持 9:16 或 16:9。');
        if (action.subtitleStyle !== undefined && !['standard', 'emphasis'].includes(action.subtitleStyle)) return fail('请选择标准或重点字幕样式。');
        const channel = action.channel === undefined ? campaign.channel : action.channel;
        const coverTitle = action.coverTitle === undefined ? campaign.coverTitle : action.coverTitle.trim();
        const aspectRatio = action.aspectRatio === undefined ? campaign.aspectRatio : action.aspectRatio;
        const subtitleStyle = action.subtitleStyle === undefined ? campaign.subtitleStyle : action.subtitleStyle;
        const mediaChanged = aspectRatio !== campaign.aspectRatio || subtitleStyle !== campaign.subtitleStyle;
        if (channel === campaign.channel && coverTitle === campaign.coverTitle && !mediaChanged) return { state: next, message: '包装设置没有变化。' };
        campaign.channel = channel;
        campaign.coverTitle = coverTitle;
        campaign.aspectRatio = aspectRatio;
        campaign.subtitleStyle = subtitleStyle;
        invalidateCampaign(next, '包装或媒体设置已变化，原审批失效。', mediaChanged);
        activity(next, 'content', '包装或媒体设置已更新，等待重新审核');
        message = mediaChanged ? '媒体设置已保存；请按新设置重新预览并审核。' : '包装已保存，脚本预览保留；原审批失效，请重新审核。';
        break;
      }
      case 'ADD_ASSET': {
        if (next.demo !== true) return fail('本地素材添加仅用于演示工作区。');
        const asset = action.asset;
        if (!asset || typeof asset !== 'object' || typeof asset.id !== 'string' || !asset.id.trim() || typeof asset.name !== 'string' || !asset.name.trim() || !['image', 'video'].includes(asset.type) || typeof asset.src !== 'string' || !asset.src.trim()) return fail('本地示例素材缺少编号、名称、类型或预览地址。');
        if (next.assets.some(item => item.id === asset.id)) return fail('这个本地示例素材已存在。');
        next.assets.push({ ...clone(asset), rights: 'pending', localUpload: true, demo: true });
        invalidateCampaign(next, '素材集合已变化，需重新预览和审批。', true);
        activity(next, 'asset', '已添加本地示例素材“' + asset.name + '”，等待用途确认');
        message = '本地示例素材已添加；原审批失效，请确认用途后重新预览。';
        break;
      }
      case 'AUTHORIZE_ASSET': {
        if (next.demo !== true) return fail('用途确认仅用于本地演示工作区。');
        const asset = next.assets.find(item => item.id === action.id);
        if (!asset) return fail('找不到这个本地示例素材。');
        asset.rights = 'authorized';
        asset.demo = true;
        invalidateCampaign(next, '素材演示用途已变化，需重新预览和审批。', true);
        activity(next, 'asset', '“' + asset.name + '”已确认仅用于本地演示');
        message = '已确认本地演示用途；这不代表真实商业授权，请重新预览并审核。';
        break;
      }
      case 'VERIFY_FACT':
      case 'EXPIRE_FACT': {
        const fact = next.facts.find(item => item.id === action.id);
        if (!fact) return fail('找不到需要处理的事实。');
        const verifying = action.type === 'VERIFY_FACT';
        if (verifying) {
          if (!hasValidFactEvidence(fact)) return fail('这项事实仍缺少有效值或证据，请先由资料负责人补充资料。');
        }
        fact.status = verifying ? 'verified' : 'expired';
        fact.updatedAt = '2026-10-11';
        fact.version = (fact.version || 1) + 1;
        fact.verifiedBy = verifying ? '示例资料负责人' : null;
        invalidateCampaign(next, verifying ? '事实核验版本已变化，需重新预览和审批。' : '事实已过期，原审批失效。', true);
        activity(next, 'fact', '“' + fact.label + '”' + (verifying ? '已完成示例人工核验' : '已标为过期'));
        message = verifying ? '已演示人工核验；真实产品需保存核验人和证据。' : '事实已过期；关联审批失效，已发布示例需复核。';
        break;
      }
      case 'UPDATE_FACT': {
        const fact = next.facts.find(item => item.id === action.id);
        if (!fact) return fail('找不到需要补充的事实。');
        if (action.value !== undefined && typeof action.value !== 'string' && typeof action.value !== 'number') return fail('请填写事实值。');
        if (action.source !== undefined && typeof action.source !== 'string') return fail('请填写资料来源。');
        if (action.unit !== undefined && typeof action.unit !== 'string') return fail('请填写正确的计量单位。');
        if (action.value !== undefined) fact.value = String(action.value).trim();
        if (action.source !== undefined) fact.source = action.source.trim();
        if (action.unit !== undefined) fact.unit = action.unit.trim();
        fact.status = 'unverified';
        fact.verifiedBy = null;
        fact.updatedAt = '2026-10-11';
        fact.version = (fact.version || 1) + 1;
        if (fact.id === 'F01') {
          next.property.size = fact.value;
          const areaTopic = next.topics.find(topic => topic.id === 'T04');
          if (areaTopic) areaTopic.title = '面积之外，哪些空间真正为你所用？';
        }
        invalidateCampaign(next, '事实资料已更新，需人工核验后重新预览与审批。', true);
        activity(next, 'fact', '“' + fact.label + '”资料已保存，等待人工核验');
        message = '资料已保存为待核验；请由资料负责人核对值和证据。';
        break;
      }
      case 'RENDER_PREVIEW': {
        if (!campaign.script.trim()) return fail('请先完成脚本。');
        if (!next.assets.length || next.assets.some(asset => !validEvidenceText(asset.src))) return fail('当前项目尚缺可预览的素材，请先上传本项目素材。');
        if (next.assets.some(asset => asset.rights !== 'authorized')) return fail('素材授权尚未确认，不能进入分镜预览。');
        campaign.renderedVersion = campaign.scriptVersion;
        campaign.step = 'visual';
        activity(next, 'content', '脚本 v' + campaign.scriptVersion + ' 已完成示例分镜预览');
        message = '分镜示例已就绪；此 Demo 没有生成 MP4 成片。';
        break;
      }
      case 'APPROVE_RELEASE': {
        const block = approvalBlock(next);
        if (block) return fail(block);
        campaign.release = {
          id: 'REL-' + campaign.id + '-V' + campaign.scriptVersion,
          scriptVersion: campaign.scriptVersion, valid: true, approvedAt: DEMO_TIME,
          factRefs: campaign.factRefs.slice(), assetIds: next.assets.map(asset => asset.id),
          channel: campaign.channel, coverTitle: campaign.coverTitle,
          aspectRatio: campaign.aspectRatio, subtitleStyle: campaign.subtitleStyle, demo: true
        };
        if (typeof action.reviewNote === 'string') {
          campaign.reviewDecision = { note: action.reviewNote.trim(), at: DEMO_TIME, by: '示例内容审核人', scriptVersion: campaign.scriptVersion };
        }
        campaign.exported = false;
        campaign.step = 'release';
        activity(next, 'review', '脚本 v' + campaign.scriptVersion + ' 已获示例人工审批');
        message = '当前版本已批准，可导出示例文案与清单。';
        break;
      }
      case 'EXPORT_RELEASE': {
        const block = approvalBlock(next);
        if (block) return fail(block);
        if (!campaign.release || !campaign.release.valid || campaign.release.scriptVersion !== campaign.scriptVersion) return fail('当前版本没有有效审批，请重新审核。');
        if (campaign.release.channel !== campaign.channel || campaign.release.coverTitle !== campaign.coverTitle) return fail('当前封面或渠道与批准快照不同，请重新审核。');
        if (campaign.release.aspectRatio !== campaign.aspectRatio || campaign.release.subtitleStyle !== campaign.subtitleStyle) return fail('当前媒体设置与批准快照不同，请重新预览和审核。');
        if (campaign.release.assetIds.length !== next.assets.length || next.assets.some(asset => !campaign.release.assetIds.includes(asset.id))) return fail('当前素材集合与批准快照不同，请重新预览和审核。');
        campaign.exported = true;
        activity(next, 'export', '已导出示例交付清单，等待人工发布回执');
        message = '示例交付包已准备：脚本文案与 JSON 清单；不含 MP4，不代表真实发布。';
        break;
      }
      case 'RECORD_PUBLICATION': {
        const url = typeof action.url === 'string' ? action.url.trim() : '';
        const postId = typeof action.postId === 'string' ? action.postId.trim() : '';
        if (!url && !postId) return fail('请填写作品 URL 或平台作品编号。');
        if (url && !/^https?:\/\/[^\s]+$/i.test(url)) return fail('作品 URL 需使用完整的 http 或 https 地址。');
        if (!campaign.release || !campaign.exported) return fail('请先完成审核并导出示例交付包。');
        if (next.publications.some(item => (url && item.url === url) || (postId && item.postId === postId))) return { state: next, message: '这个作品已登记，未重复新增。' };
        if (!['live', 'review_pending'].includes(action.platformState || 'review_pending')) return fail('请明确选择公开或审核中的平台状态。');
        const topic = next.topics.find(item => item.id === campaign.topicId);
        next.publications.push({
          id: 'PUB' + String(next.publications.length + 1).padStart(3, '0'),
          title: action.title || (topic && topic.title) || campaign.name,
          channel: campaign.release.channel, status: 'recorded', platformState: action.platformState || 'review_pending',
          releaseId: campaign.release.id, url: url || null, postId: postId || null,
          views: null, inquiries: null, qualified: null, at: '2026-10-11', window: 'D7', demo: true
        });
        activity(next, 'publication', '已登记示例发布回执，等待人工核对');
        message = '回执已登记为“待核对”；不计入已核验公开发布。';
        break;
      }
      case 'VERIFY_PUBLICATION': {
        const publication = next.publications.find(item => item.id === action.id);
        if (!publication) return fail('找不到需要核对的发布记录。');
        if (action.receiptConfirmed !== true) return fail('请人工确认已核对作品、版本、入口与标识的回执。');
        if (!['live', 'review_pending', 'rejected', 'restricted', 'removed', 'unknown'].includes(action.platformState)) return fail('请填写明确的平台实际状态，无法确认时保留未知。');
        publication.status = action.platformState === 'removed' ? 'removed' : 'verified';
        publication.platformState = action.platformState;
        publication.verifiedAt = DEMO_TIME;
        publication.verifiedBy = '示例内容运营';
        publication.demo = true;
        activity(next, 'publication', '“' + publication.title + '”已完成示例人工回执核对');
        message = action.platformState === 'live' ? '示例回执已核对且平台状态为公开，可计入演示已发布数量。' : '示例回执已核对；当前平台状态不计入公开发布数量。';
        break;
      }
      case 'QUALIFY_INQUIRY': {
        const inquiry = next.inquiries.find(item => item.id === action.id);
        if (!inquiry) return fail('找不到这个咨询。');
        const existing = next.leads.find(lead => lead.inquiryId === inquiry.id);
        if (existing) return { state: next, message: '这条咨询已有关联资格线索，未重复创建。' };
        if (action.contactable !== true || action.consent !== true || action.demandConfirmed !== true || action.fitConfirmed !== true) return fail('请由销售逐项确认：可联系、联系授权、真实需求、楼盘适配或跟进理由。');
        inquiry.contactable = true;
        inquiry.consent = true;
        inquiry.qualified = true;
        inquiry.lastAt = '刚刚';
        if (typeof action.need === 'string' && action.need.trim()) inquiry.need = action.need.trim();
        const owner = action.owner || (inquiry.owner !== '待分配' && inquiry.owner) || (next.property && next.property.salesOwner) || '待分配';
        inquiry.owner = owner;
        next.leads.push({
          id: 'LEAD' + String(next.leads.length + 1).padStart(3, '0'), inquiryId: inquiry.id,
          name: inquiry.name, stage: 'qualified', need: inquiry.need, owner,
          sourceConfidence: inquiry.sourceConfidence, lastAt: '刚刚', nextAction: '通过已授权渠道确认下一步安排',
          notes: [], stageHistory: [{ stage: 'qualified', at: DEMO_TIME }], qualifiedBy: '示例销售负责人', qualifiedAt: DEMO_TIME
        });
        activity(next, 'lead', inquiry.name + ' 已由销售完成示例资格确认');
        message = '已建立一条资格线索；原始来源可信度已保留。';
        break;
      }
      case 'MOVE_LEAD': {
        const lead = next.leads.find(item => item.id === action.id);
        if (!lead) return fail('找不到这条线索。');
        if (!STAGES.includes(action.stage) && !['lost', 'withdrawn'].includes(action.stage)) return fail('这个销售阶段不可用。');
        if (lead.stage === action.stage) return { state: next, message: '销售阶段没有变化。' };
        if (['lost', 'withdrawn', 'won'].includes(lead.stage)) return fail('这条线索已结束；重启或重新授权需独立人工流程。');
        if (!['lost', 'withdrawn'].includes(action.stage) && STAGES.indexOf(action.stage) !== STAGES.indexOf(lead.stage) + 1) return fail('请按实际进展逐步记录，预约不能代替完成带看。');
        lead.stage = action.stage;
        lead.lastAt = '刚刚';
        if (!lead.stageHistory) lead.stageHistory = [];
        lead.stageHistory.push({ stage: action.stage, at: DEMO_TIME });
        if (action.stage === 'withdrawn') {
          lead.nextAction = '已停止联系；真实产品需处理相应用途与删除请求';
          const inquiry = next.inquiries.find(item => item.id === lead.inquiryId);
          if (inquiry) { inquiry.consent = false; inquiry.contactable = false; }
        } else if (action.stage === 'lost' || action.stage === 'won') {
          lead.nextAction = '该阶段已结束，保留最少必要历史';
        } else {
          lead.nextAction = { appointment: '按双方确认的时间安排看房', visit_completed: '收集实际带看后的疑问与反馈', opportunity: '由销售确认后续推进事项' }[action.stage] || lead.nextAction;
        }
        activity(next, 'lead', lead.name + ' 的示例销售阶段已更新');
        message = action.stage === 'withdrawn' ? '联系授权已撤回，后续跟进已停止。' : '已记录实际销售进展，历史阶段已保留。';
        break;
      }
      case 'SAVE_FOLLOWUP': {
        const lead = next.leads.find(item => item.id === action.id);
        if (!lead) return fail('找不到这条线索。');
        if (lead.stage === 'withdrawn') return fail('客户已撤回联系授权，不能继续记录营销跟进。');
        const note = typeof action.note === 'string' ? action.note.trim() : '';
        if (!note) return fail('请先填写实际沟通反馈。');
        lead.notes.push({ id: 'NOTE' + String(lead.notes.length + 1).padStart(3, '0'), text: note, at: DEMO_TIME, by: lead.owner, demo: true });
        if (typeof action.nextAction === 'string' && action.nextAction.trim()) lead.nextAction = action.nextAction.trim();
        lead.lastAt = '刚刚';
        activity(next, 'feedback', lead.name + ' 新增一条示例销售反馈');
        message = '跟进已保存；反馈进入人工复盘，不自动改变客户阶段。';
        break;
      }
      case 'APPROVE_PROPOSAL': {
        const proposal = next.proposals.find(item => item.id === action.id);
        if (!proposal) return fail('找不到这条复盘建议。');
        proposal.status = 'approved';
        proposal.approvedAt = DEMO_TIME;
        activity(next, 'review', '复盘建议“' + proposal.title + '”已获示例人工批准');
        message = '建议已批准，等待运营安排内容任务。';
        break;
      }
      default: return fail('暂不支持这个操作。');
    }
    return { state: next, message };
  }

  function getMetrics(state) {
    const inquiries = Array.from(new Map(state.inquiries.map(inquiry => [inquiry.id, inquiry])).values());
    const leads = Array.from(new Map(state.leads.map(lead => [lead.inquiryId || lead.id, lead])).values());
    const publications = Array.from(new Map(state.publications.map(publication => [publication.id, publication])).values());
    const livePublications = publications.filter(publication => publication.status === 'verified' && publication.platformState === 'live');
    const achieved = (lead, stage) => (lead.stageHistory || []).some(event => event.stage === stage) ||
      (STAGES.indexOf(lead.stage) >= STAGES.indexOf(stage) && STAGES.indexOf(lead.stage) !== -1);
    return {
      published: livePublications.length,
      countsPublished: livePublications.length,
      qualified: leads.length,
      appointments: leads.filter(lead => achieved(lead, 'appointment')).length,
      visits: leads.filter(lead => achieved(lead, 'visit_completed')).length,
      inquiries: inquiries.length,
      views: livePublications.reduce((sum, publication) => sum + (Number.isFinite(publication.views) ? publication.views : 0), 0),
      unknownRate: inquiries.length ? Math.round(inquiries.filter(inquiry => inquiry.sourceConfidence === 'unknown').length / inquiries.length * 1000) / 10 : null
    };
  }
  return { createInitialState, transition, getMetrics, hasValidFactEvidence };
});
