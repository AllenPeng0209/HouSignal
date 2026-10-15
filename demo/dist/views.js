(function () {
  'use strict';
  const esc = value => window.HSUI && window.HSUI.escape ? window.HSUI.escape(value) : String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name, size = 18) => window.HSUI && window.HSUI.icon ? window.HSUI.icon(name, size) : '';
  const badge = (label, type = 'gray') => `<span class="pill ${esc(type)}">${esc(label)}</span>`;
  const av = name => window.HSUI && window.HSUI.avatar ? window.HSUI.avatar(name) : `<span class="avatar">${esc((name || '未').slice(0, 1))}</span>`;
  const arr = (state, key) => Array.isArray(state[key]) ? state[key] : [];
  const num = value => value == null ? '待回填' : Number(value).toLocaleString('zh-CN');
  const channels = value => ({ douyin: '抖音', xiaohongshu: '小红书', wechat: '微信' }[value] || value || '未记录');
  const sources = { confirmed: ['已确认来源', 'green'], self_reported: ['客户自述', 'blue'], inferred: ['关联推测', 'amber'], unknown: ['来源未知', 'gray'] };
  const factStates = { verified: ['已核验', 'green'], unverified: ['待核验', 'amber'], disputed: ['有争议', 'red'], expired: ['已失效', 'red'] };
  const stages = { qualified: ['需求已确认', 'blue'], appointment: ['已预约', 'blue'], visit_completed: ['已完成带看', 'green'], opportunity: ['方案沟通', 'blue'], won: ['已成交', 'green'], lost: ['已结束', 'gray'], withdrawn: ['已退出', 'gray'] };
  const sourceBadge = value => badge(...(sources[value] || sources.unknown));
  const stageBadge = value => badge(...(stages[value] || ['待确认', 'gray']));
  const action = (name, label, extra = '', cls = 'btn') => `<button class="${cls}" data-action="${esc(name)}" ${extra}>${label}</button>`;
  const heading = (eyebrow, title, subtitle, buttons = '') => `<header class="page-heading"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p class="muted">${esc(subtitle)}</p></div><div class="row">${buttons}</div></header>`;
  const tabs = (items, current, name) => `<div class="tab-bar">${items.map(([value, label]) => action(name, esc(label), `data-value="${esc(value)}" aria-pressed="${current === value}"`, `tab ${current === value ? 'active' : ''}`)).join('')}</div>`;
  const smallStat = (label, value, note, iconName) => `<div class="stat-card"><div class="row" style="justify-content:space-between"><span class="muted">${esc(label)}</span>${icon(iconName || 'chart')}</div><div class="stat-value">${esc(value)}</div><div class="muted" style="font-size:12px">${esc(note)}</div></div>`;
  const empty = text => `<div class="empty-state">${icon('inbox', 28)}<p>${esc(text)}</p></div>`;

  function facts(state) {
    const property = state.property || {};
    const list = arr(state, 'facts');
    const filter = state.factFilter || 'all';
    const visible = list.filter(f => filter === 'all' || f.status === filter);
    const verified = list.filter(f => f.status === 'verified').length;
    const heroSrc = state.heroSrc || property.heroSrc;
    const complete = state.projectCompleteness || { completed: 0, total: 7 };
    const metadata = [property.city, property.propertyType, property.unit].filter(Boolean).join(' · ') || property.subtitle || '项目信息待完善';
    const hero = heroSrc
      ? `<section class="property-hero card"><img src="${esc(heroSrc)}" alt="${esc(property.name || '当前项目')} · ${esc(property.visualLabel || '项目素材预览')}"><div class="property-hero-overlay"></div><div class="property-hero-content"><div class="row">${badge(property.status || '项目资料', 'gray')}${property.visualLabel ? badge(property.visualLabel, 'gray') : ''}</div><h2>${esc(property.name || '未命名项目')}</h2><p>${esc(metadata)}</p><div class="hero-caption">先讲清空间如何被使用，再讲清它适合谁。</div></div><div class="property-hero-status">${icon('check-circle')} ${verified} 项事实已核验 / ${list.length} 项记录</div></section>`
      : `<section class="property-hero card property-hero-empty"><div class="property-placeholder"><div class="row">${badge(property.status || '待完善资料', 'amber')}${badge('项目素材待补充', 'gray')}</div><div class="property-placeholder-icon">${icon('building', 40)}</div><h2>${esc(property.name || '未命名项目')}</h2><p class="muted">${esc(metadata)}</p><p class="muted">补充项目资料与授权实景素材，建立属于这个楼盘的内容依据。</p>${action('edit-project-details', `${icon('edit', 16)} 完善项目信息`, '', 'btn small')}</div></section>`;
    return heading('PROPERTY INTELLIGENCE', '楼盘资料', '每一个对外判断，都从可追溯的事实出发。', action('project-completeness', `${icon('check-circle', 16)} 资料完整度 ${badge(`${complete.completed}/${complete.total} 项`, 'blue')}`, '', 'btn') + action('upload-assets', `${icon('plus')} 上传资料`)) +
      hero +
      `<div class="stat-grid" style="margin-top:24px">${smallStat('核验完成', `${verified}/${list.length}`, '仅已核验且有效的事实可对外引用', 'shield')}${smallStat('待补依据', list.filter(f => f.status === 'unverified').length, '核验人补充来源后可确认', 'file')}${smallStat('需重新复核', list.filter(f => ['expired', 'disputed'].includes(f.status)).length, '失效或争议事实会阻塞相关发布', 'clock')}</div>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">事实簿</h2><p class="muted">按事实逐条核验，保留来源与口径。</p></div>${badge('人工核验', 'blue')}</div><div class="toolbar">${tabs([['all', '全部事实'], ['verified', '已核验'], ['unverified', '待核验'], ['expired', '已失效']], filter, 'fact-filter')}<span class="muted">${visible.length} 条记录</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>房屋事实</th><th>值与口径</th><th>证据来源</th><th>状态</th><th>最近复核</th><th></th></tr></thead><tbody>${visible.map(f => `<tr><td><strong>${esc(f.label)}</strong>${f.required ? '<span class="muted" style="margin-left:8px;font-size:11px">必需</span>' : ''}</td><td><div class="fact-cell">${esc(f.value === '' || f.value == null ? '待补资料' : f.value)} <span class="muted">${esc(f.value === '' || f.value == null ? '' : f.unit || '')}</span></div></td><td>${action(f.source ? 'show-fact' : 'verify-fact', `${icon('file', 14)} ${esc(f.source || '补依据')}`, `data-id="${esc(f.id)}"`, 'btn ghost small')}</td><td>${badge(...(factStates[f.status] || factStates.unverified))}</td><td class="muted">${esc(f.updatedAt || '待复核')}</td><td>${action(f.status === 'verified' ? 'show-fact' : 'verify-fact', f.status === 'verified' ? '查看依据' : '核验事实', `data-id="${esc(f.id)}"`, 'btn ghost small')}</td></tr>`).join('')}</tbody></table>${visible.length ? '' : empty('当前筛选下没有事实记录')}</div><div class="info-strip">${icon('shield', 16)} 核验价格、面积、朝向与景观时，请明确适用房源、数据口径和有效时间。</div></section>`;
  }

  function assets(state) {
    const all = arr(state, 'assets');
    const filter = state.assetFilter || 'all';
    const list = all.filter(a => filter === 'all' || a.type === filter);
    const available = all.filter(a => a.rights === 'authorized').length;
    const hasDemo = all.some(a => a.previewType === 'template_photo');
    const assetImage = a => a.src ? `<img loading="lazy" src="${esc(a.src)}" alt="${esc(a.space || a.name || '项目素材')}预览">${a.type === 'video' ? `<span class="asset-play">${icon('play', 20)}</span>` : ''}<span class="asset-duration">${a.type === 'video' ? esc(a.duration || '视频素材') : '照片'}</span><span class="asset-image-caption">${esc(a.previewType === 'template_photo' ? '演示画面' : '项目素材')}</span>` : `<div class="asset-placeholder">${icon(a.type === 'video' ? 'play' : 'image', 30)}<span>待上传原始素材</span></div>`;
    return heading('ASSET LIBRARY', '素材库', '将真实空间与拍摄依据，变成可持续复用的内容资产。', action('upload-assets', `${icon('plus')} 上传素材`, '', 'btn primary')) +
      `<div class="stat-grid">${smallStat('全部素材', all.length, '当前项目的素材记录', 'image')}${smallStat('授权已登记', available, '发布前核对授权原件、用途与期限', 'shield')}${smallStat('待补授权', all.length - available, '补齐用途、渠道与有效期限', 'clock')}</div><section class="card"><div class="toolbar">${tabs([['all', '全部素材'], ['video', '视频'], ['image', '照片']], filter, 'assets-filter')}<span class="muted">${list.length} 个素材</span></div><div class="card-body"><div class="asset-grid">${list.map(a => `<article class="asset-card"><button class="asset-image" data-action="show-asset" data-id="${esc(a.id)}" aria-label="查看 ${esc(a.name)}">${assetImage(a)}</button><div class="asset-meta"><div class="row" style="justify-content:space-between"><h3>${esc(a.name)}</h3>${action('show-asset', icon('more'), `data-id="${esc(a.id)}" aria-label="查看素材详情"`, 'icon-btn')}</div><p class="muted">${esc(a.space || '未标记空间')} · ${a.type === 'video' ? '视频素材' : '空间照片'}${a.previewType === 'template_photo' ? ' · 演示' : ''}</p><div class="row" style="justify-content:space-between">${badge(a.rights === 'authorized' ? `授权已登记${a.previewType === 'template_photo' ? ' · 示例' : ''}` : '待补授权', a.rights === 'authorized' ? 'green' : 'amber')}<span class="muted" style="font-size:11px">${icon('link', 12)} ${a.src ? '素材记录' : '原件待补'}</span></div></div></article>`).join('')}</div>${list.length ? '' : `<div class="empty-state">${icon('image', 28)}<h3>${all.length ? '暂无这类素材' : '为这个项目建立素材库'}</h3><p>上传实景照片与视频，并登记来源、用途与授权依据。</p>${action('upload-assets', `${icon('plus', 16)} 上传项目素材`, '', 'btn primary small')}</div>`}</div></section><div class="info-strip">${icon('info', 16)} ${hasDemo ? '当前包含演示画面，不代表真实房源。' : ''}正式制作只引用当前项目已核实、已授权的实景素材。</div>`;
  }

  function leadRow(l) {
    return `<tr data-action="open-lead" data-id="${esc(l.id)}" tabindex="0" role="button" aria-label="查看 ${esc(l.name)} 客户详情"><td><div class="row">${av(l.name)}<div><strong>${esc(l.name)}</strong><div class="muted" style="font-size:12px">${esc(l.need)}</div></div></div></td><td>${stageBadge(l.stage)}</td><td>${sourceBadge(l.sourceConfidence)}</td><td>${esc(l.owner || '待分配')}</td><td><span>${esc(l.nextAction || '待记录')}</span><div class="muted" style="font-size:12px">${esc(l.lastAt || '')}</div></td><td>${icon('arrow-right')}</td></tr>`;
  }
  function leadBoard(leads) {
    const groups = ['qualified', 'appointment', 'visit_completed', 'opportunity', ...['won', 'lost', 'withdrawn'].filter(stage => leads.some(l => l.stage === stage))];
    return `<div class="crm-board">${groups.map(stage => { const entries = leads.filter(l => l.stage === stage); return `<section class="crm-column"><div class="row" style="justify-content:space-between;margin-bottom:16px"><strong>${esc(stages[stage][0])}</strong>${badge(entries.length, 'gray')}</div>${entries.map(l => `<button class="lead-card" data-action="open-lead" data-id="${esc(l.id)}"><div class="row">${av(l.name)}<strong>${esc(l.name)}</strong></div><p>${esc(l.need)}</p>${sourceBadge(l.sourceConfidence)}<div class="lead-card-footer"><span>${esc(l.owner || '待分配')}</span><span>${esc(l.nextAction || '待跟进')}</span></div></button>`).join('')}${entries.length ? '' : '<div class="empty-state" style="padding:36px 8px;font-size:12px">暂无客户</div>'}</section>`; }).join('')}</div>`;
  }
  function leads(state) {
    const candidates = arr(state, 'inquiries');
    const clients = arr(state, 'leads');
    const query = String(state.leadSearch || '').trim().toLowerCase();
    const visible = clients.filter(l => [l.name, l.need, l.owner, l.nextAction].join(' ').toLowerCase().includes(query));
    const pending = candidates.filter(i => !i.qualified);
    const mode = state.leadView || 'table';
    const unknown = candidates.filter(i => i.sourceConfidence === 'unknown').length;
    return heading('CLIENT RELATIONSHIPS', '咨询与客户', '先确认真实需求，再安排每一步跟进。', action('new-inquiry', `${icon('plus')} 录入咨询`, '', 'btn primary')) +
      `<div class="stat-grid">${smallStat('主动咨询', candidates.length, '当前项目的主动咨询记录', 'message')}${smallStat('需求已确认', clients.length, '经销售确认后进入客户跟进', 'users')}${smallStat('待销售确认', pending.length, `${unknown} 条咨询的内容来源未知`, 'clock')}</div>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">待确认咨询</h2><p class="muted">可联系、需求真实、房源匹配或有明确后续理由。</p></div>${badge(`${pending.length} 条待确认`, pending.length ? 'amber' : 'green')}</div>${pending.length ? `<div class="card-body">${pending.map(i => `<div class="inquiry-row"><div class="row">${av(i.name)}<div><strong>${esc(i.name)}</strong><p class="muted">${esc(i.need || '需求待沟通')}</p></div></div><div class="stack" style="gap:6px">${sourceBadge(i.sourceConfidence)}<span class="muted" style="font-size:12px">${esc(channels(i.channel))} · ${esc(i.sourceText || '未确认关联内容')}</span></div><div><span class="muted">${i.consent ? '已获联系同意' : '联系同意待记录'}</span><div class="muted" style="font-size:12px">${esc(i.lastAt)}</div></div>${action('open-inquiry', '确认需求', `data-id="${esc(i.id)}"`, 'btn small')}</div>`).join('')}</div>` : empty(candidates.length ? '待确认咨询已处理完毕。' : '暂无咨询。收到客户主动咨询后，录入需求与联系同意。')}</section>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">销售跟进</h2><p class="muted">记录客户需求，明确下一步动作。</p></div>${tabs([['table', '列表'], ['board', '看板']], mode, 'lead-view')}</div><div class="toolbar"><label class="search-field">${icon('search', 16)}<input id="lead-search" type="search" value="${esc(state.leadSearch || '')}" placeholder="搜索客户、需求或负责人" aria-label="搜索客户"></label><span class="muted">${visible.length} 位客户 · 当前项目</span></div>${mode === 'board' ? `<div class="card-body">${leadBoard(visible)}</div>` : `<div class="table-wrap"><table class="data-table"><thead><tr><th>客户与需求</th><th>跟进阶段</th><th>来源依据</th><th>负责人</th><th>下一步</th><th></th></tr></thead><tbody>${visible.map(leadRow).join('')}</tbody></table>${visible.length ? '' : empty(clients.length ? '没有匹配的客户，请调整搜索关键词。' : '暂无客户。销售确认真实需求后，咨询将进入跟进队列。')}</div>`}</section>`;
  }

  function effectSummary(state) {
    const pubs = arr(state, 'publications').filter(p => p.window === 'D7');
    const live = pubs.filter(p => p.status === 'verified' && p.platformState === 'live');
    const measured = live.filter(p => Number.isFinite(p.views));
    const views = measured.reduce((sum, p) => sum + p.views, 0);
    const missing = live.length - measured.length;
    const pending = pubs.length - live.length;
    const unknown = arr(state, 'inquiries').filter(i => i.sourceConfidence === 'unknown').length;
    const metric = measured.length ? num(views) : live.length ? '待回填' : '待观察';
    const channelList = [...new Set(live.map(p => channels(p.channel)))].join('、');
    const note = measured.length ? `${measured.length} 条已回填${missing ? ` · ${missing} 条待回填` : ''}` : live.length ? `${missing} 条待回填平台指标` : '登记发布回执后，持续观察内容表现';
    return `<section class="card effect-summary" aria-label="效果概览"><div class="card-header"><div><h3>效果概览</h3><p class="muted">发布后 D7 · ${esc(channelList || '当前项目')}</p></div>${action('navigate', `效果分析${icon('arrow-right', 13)}`, 'data-page="reports" data-window="D7"', 'btn text')}</div><div class="card-body"><div class="effect-summary-metric"><strong>${esc(metric)}</strong><span>${measured.length ? '次累计播放' : '平台播放指标'}</span>${badge(pubs.some(p => p.demo) ? '演示指标' : '回执指标', 'gray')}</div><p class="effect-summary-detail">${live.length} 条公开作品 · ${esc(note)}</p><div class="effect-summary-notes"><span>${pending} 条回执或平台状态待确认</span><span>${unknown} 条咨询来源未知，继续保留待核对</span></div><p class="effect-summary-caption">同一 D7 窗口，按各平台口径累计播放；跨平台合计不去重观看人数。销售进展见上方项目指标。</p></div></section>`;
  }

  function reviewSummary(state) {
    const proposals = arr(state, 'proposals');
    const pending = proposals.filter(p => p.status === 'pending');
    const visible = [...pending, ...proposals.filter(p => p.status === 'approved')].slice(0, 2);
    const approved = proposals.filter(p => p.status === 'approved').length;
    return `<section class="card review-summary" aria-label="复盘与下一步"><div class="card-header"><div><h3>复盘与下一步</h3><p class="muted">${pending.length} 条待采纳${approved ? ` · ${approved} 条已采纳，待创作` : ''}</p></div>${badge('来自项目记录', 'gray')}</div><div class="card-body">${visible.length ? visible.map(p => `<div class="review-summary-item"><strong>${esc(p.title)}</strong><p>${esc(p.reason)}</p></div>`).join('') : '<div class="review-summary-empty"><strong>先建立内容与客户记录</strong><p>登记发布回执并补充销售反馈后，在这里形成下一轮建议。</p></div>'}<div class="review-summary-actions">${action('navigate', '查看全部建议', 'data-page="reports"', 'btn small')}${action('navigate', `${icon('sparkle', 13)}问问楼管家`, 'data-page="assistant"', 'btn ghost small')}</div></div></section>`;
  }

  function reportChart(pubs, metricWindow) {
    const maximum = Math.max(1, ...pubs.map(p => Number(p.views) || 0));
    const lines = [0, .25, .5, .75, 1].map(r => `<line x1="46" y1="${180 - r * 142}" x2="600" y2="${180 - r * 142}" stroke="#e9edf3"/><text x="35" y="${184 - r * 142}" text-anchor="end" fill="#8c95a5" font-size="10">${Math.round(maximum * r / 1000)}k</text>`).join('');
    const step = 510 / Math.max(pubs.length, 1);
    const bars = pubs.map((p, idx) => { const height = Number(p.views || 0) / maximum * 142; const width = Math.min(88, step * .45); const x = 65 + step * idx + (step - width) / 2; return `<g><title>${esc(p.title)}：${num(p.views)} 次累计播放</title><rect x="${x}" y="${180 - height}" width="${width}" height="${height}" rx="7" fill="${idx ? '#99bdfd' : '#3873ee'}"/><text x="${x + width / 2}" y="${170 - height}" text-anchor="middle" fill="#53657d" font-size="11">${num(p.views)}</text><text x="${x + width / 2}" y="204" text-anchor="middle" fill="#8c95a5" font-size="10">作品 ${idx + 1}</text></g>`; }).join('');
    return `<svg class="report-chart" viewBox="0 0 620 220" role="img" aria-label="各作品 ${esc(metricWindow)} 累计播放量比较">${lines}${bars}</svg>`;
  }
  function reports(state) {
    const metricWindow = state.reportWindow || 'D7';
    const pubs = arr(state, 'publications').filter(p => p.window === metricWindow);
    const live = pubs.filter(p => p.status === 'verified' && p.platformState === 'live');
    const inquiries = arr(state, 'inquiries');
    const clients = arr(state, 'leads');
    const eligible = live.filter(p => Number.isFinite(p.views));
    const unknown = inquiries.filter(i => i.sourceConfidence === 'unknown').length;
    const historyCount = target => clients.filter(l => (Array.isArray(l.stageHistory) && l.stageHistory.some(h => (typeof h === 'string' ? h : h.stage) === target)) || l.stage === target).length;
    const proposals = arr(state, 'proposals');
    const describeRef = ref => { const fact = arr(state, 'facts').find(f => f.id === ref); const pub = arr(state, 'publications').find(p => p.id === ref); const inquiry = inquiries.find(i => i.id === ref); return fact ? fact.label : pub ? pub.title : inquiry ? '匿名咨询反馈' : '依据待补充'; };
    const channelGroups = [...new Set(live.map(p => p.channel))];
    const views = eligible.reduce((sum, p) => sum + p.views, 0);
    const channelSections = channelGroups.map(channel => {
      const current = eligible.filter(p => p.channel === channel);
      const missing = live.filter(p => p.channel === channel).length - current.length;
      const total = current.reduce((sum, p) => sum + p.views, 0);
      return `<div class="report-channel-section"><div class="report-channel-heading"><h3>${esc(channels(channel))}</h3><span>${current.length ? `${num(total)} 次播放 · ${current.length} 条已回填` : '播放指标待回填'}${missing ? ` · ${missing} 条待回填` : ''}</span></div>${current.length ? `${reportChart(current, metricWindow)}<div class="chart-meta">${current.map((p, i) => `<div><span class="chart-dot" style="background:${i ? '#99bdfd' : '#3873ee'}"></span><span class="muted">作品 ${i + 1} · ${esc(p.title)}</span></div>`).join('')}</div>` : '<p class="muted report-channel-pending">该平台已公开作品尚待回填指标。</p>'}</div>`;
    }).join('');
    const demoMetrics = pubs.some(p => p.demo);
    const windows = tabs([['D7', '发布后 D7'], ['D30', '发布后 D30']], metricWindow, 'report-window');
    const reportHeader = state.embedded ? `<div class="analysis-toolbar"><div><h2>效果分析</h2><p class="muted">对照内容表现、销售进展与回执依据，安排下一轮内容。</p></div>${windows}</div>` : heading('PERFORMANCE & LEARNING', '效果分析', '看清内容带来了什么，也看清哪些判断还缺依据。', windows);
    return reportHeader +
      `<div class="info-strip" style="margin-bottom:24px">${icon('info', 16)} ${demoMetrics ? '当前包含模拟指标。' : '指标以已确认的回执为依据。'}播放按作品在同一窗口的累计值展示；销售阶段按当前项目实际记录统计。</div>` +
      `<div class="stat-grid">${smallStat('公开发布', live.length, pubs.length ? `${pubs.length - live.length} 条回执或平台状态待确认` : `${metricWindow} 尚无发布回执`, 'send')}${smallStat('主动咨询', inquiries.length, `${unknown} 条来源未知，保留在总咨询中`, 'message')}${smallStat('资格客户', clients.length, '当前项目经销售确认的去重客户', 'users')}${smallStat('已完成带看', historyCount('visit_completed'), '已完成记录，预约不计作带看', 'check-circle')}</div>` +
      `<div class="grid-2"><section class="card"><div class="card-header"><div><h2 class="section-title">内容表现</h2><p class="muted">${esc(metricWindow)} · ${channelGroups.length} 个发布渠道 · 各平台独立回填</p></div>${badge(demoMetrics ? '演示指标' : eligible.length ? '已回填指标' : '待回填指标', demoMetrics || eligible.length ? 'blue' : 'gray')}</div><div class="card-body">${eligible.length ? `<div class="row" style="align-items:baseline;gap:10px"><strong class="stat-value">${num(views)}</strong><span class="muted">次累计播放 / ${eligible.length} 条已回填作品</span></div><p class="report-platform-note muted">按各平台口径累计播放；跨平台合计不去重观看人数。</p>${channelSections}` : empty('该窗口尚无可核验的作品指标。发布后登记回执与观察窗口，再回填平台指标。')}</div></section>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">销售推进</h2><p class="muted">当前项目客户队列的业务进展</p></div>${badge('不同业务事件', 'gray')}</div><div class="card-body"><div class="funnel">${[['主动咨询', inquiries.length, '客户主动发起咨询'], ['资格确认', clients.length, '销售确认真实需求'], ['预约看房', historyCount('appointment'), '预约事件已记录'], ['完成带看', historyCount('visit_completed'), '已实际完成']].map(([label, value, hint], idx) => `<div class="funnel-row"><div class="row" style="justify-content:space-between"><strong>${esc(label)}</strong><strong>${value}</strong></div><div class="funnel-bar"><span style="width:${value ? Math.min(100, Math.max(4, value / Math.max(1, inquiries.length) * 100)) : 0}%;background:${['#386be5','#638af0','#8da9f6','#b0c5f9'][idx]}"></span></div><span class="muted" style="font-size:11px">${esc(hint)}</span></div>`).join('')}</div><p class="muted" style="font-size:12px;margin:20px 0 0">来源未知占比：${inquiries.length ? `${Math.round(unknown / inquiries.length * 100)}%` : 'N/A（暂无咨询）'}。当前队列展示业务事件，不作为内容归因或 D30 转化率。</p></div></section></div>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">发布与指标回执</h2><p class="muted">发布回执状态与平台实际状态分别记录。</p></div>${badge('运营人工回填', 'gray')}</div><div class="table-wrap"><table class="data-table"><thead><tr><th>作品</th><th>渠道</th><th>发布 / 平台状态</th><th>${esc(metricWindow)} 播放</th><th>来源关联咨询（含自述）</th><th>回填日期</th></tr></thead><tbody>${pubs.map(p => `<tr data-action="show-publication" data-id="${esc(p.id)}" role="button" tabindex="0" aria-label="查看 ${esc(p.title)} 发布记录"><td><strong>${esc(p.title)}</strong></td><td>${esc(channels(p.channel))}</td><td><div class="stack" style="gap:5px">${badge(p.status === 'verified' ? '回执已确认' : '回执待核对', p.status === 'verified' ? 'green' : 'amber')}${badge(p.platformState === 'live' ? '公开展示' : '平台审核中', p.platformState === 'live' ? 'blue' : 'gray')}</div></td><td>${num(p.views)}</td><td>${p.inquiries == null ? '待回填' : num(p.inquiries)}</td><td class="muted">${esc(p.at || '待回填')}</td></tr>`).join('')}</tbody></table>${pubs.length ? '' : empty(`${metricWindow} 尚未取得发布或指标回执。`)}</div></section>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">下一轮内容建议</h2><p class="muted">从脱敏销售反馈中提出建议，由内容负责人确认。</p></div>${badge('建议不会自动发布', 'gray')}</div><div class="card-body stack">${proposals.map(p => `<div class="proposal-row"><div class="proposal-icon">${icon('sparkles', 22)}</div><div style="flex:1"><h3>${esc(p.title)}</h3><p class="muted">${esc(p.reason)}</p><div class="muted" style="font-size:11px">依据：${esc(Array.isArray(p.refs) ? p.refs.map(describeRef).join(' · ') : p.refs || '销售反馈待补充')}</div></div>${p.status === 'approved' ? badge('已采纳，待创作', 'green') : action('approve-proposal', '采纳建议', `data-id="${esc(p.id)}"`, 'btn small')}</div>`).join('') || empty('补充销售反馈后，可形成有依据的下一轮建议。')}</div></section>`;
  }

  function settings(state) {
    const role = state.role || 'content';
    const property = state.property || {};
    const hasEntry = Boolean(property.entryMode && property.salesOwner);
    const roles = { content: ['内容编辑', '维护资料、策划选题、编辑文案与提交审核'], sales: ['销售', '确认需求、记录授权、安排预约和跟进'], reviewer: ['事实与发布审核', '核验事实，检查权利与批准冻结版本'] };
    return heading('WORKSPACE SETTINGS', '组织设置', '让每个人知道自己的责任，让每个入口符合当前能力。') +
      `<div class="settings-layout"><section class="card"><div class="card-header"><div><h2 class="section-title">体验角色</h2><p class="muted">在演示中切换角色，查看不同业务责任。</p></div>${badge('演示权限', 'blue')}</div><div class="card-body stack">${Object.entries(roles).map(([value, [label, desc]]) => `<button class="settings-role ${role === value ? 'selected' : ''}" data-action="switch-role" data-value="${value}" aria-pressed="${role === value}"><span class="settings-role-icon">${icon(value === 'sales' ? 'users' : value === 'reviewer' ? 'shield' : 'edit', 20)}</span><span style="flex:1"><strong>${label}</strong><span class="muted" style="display:block;margin-top:6px;font-size:12px">${desc}</span></span>${role === value ? badge('当前角色', 'blue') : icon('arrow-right')}</button>`).join('')}<p class="muted" style="font-size:12px">真实系统由组织管理员授予权限。切换角色仅用于交互演示。</p></div></section>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">团队与当前项目</h2><p class="muted">团队方法共享，项目资料与业务记录独立保存。</p></div>${badge('演示', 'gray')}</div><div class="card-body"><div class="settings-item"><span class="muted">团队</span><strong>${esc(state.teamName || '内容团队')}</strong></div><div class="settings-item"><span class="muted">当前楼盘</span><strong>${esc((state.property || {}).name || '未命名项目')}</strong></div><div class="settings-item"><span class="muted">主渠道</span><strong>${esc((state.campaign || {}).channel ? channels(state.campaign.channel) : '待配置')}</strong></div><div class="settings-item"><span class="muted">内容方法</span><strong>真实需求 → 空间证据 → 适配与取舍</strong></div><div class="settings-item"><span class="muted">项目资料</span>${action('project-completeness', '查看完整度', '', 'btn ghost small')}</div><div class="settings-item"><span class="muted">客户数据</span><strong>${esc((state.property || {}).status === '示例楼盘' ? '脱敏示例，无真实联系方式' : '项目内记录 · 联系同意后录入')}</strong></div></div></section></div>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">咨询承接入口</h2><p class="muted">启用已核验的路径，内容才有可执行的下一步。</p></div>${action('entry-settings', `${icon('settings', 16)} 查看配置`, '', 'btn small')}</div><div class="card-body"><div class="entry-card"><div class="entry-icon">${icon('message', 24)}</div><div style="flex:1"><div class="row"><h3>平台内人工咨询</h3>${badge(hasEntry ? '承接配置已填写' : '承接配置待补', hasEntry ? 'blue' : 'amber')}</div><p class="muted">客户主动在站内咨询，销售回应，并在取得联系同意后录入最少必要信息。负责人：${esc(property.salesOwner || '待分配')}。承接配置填写后，仍需核对平台实际权限。</p><div class="row" style="gap:20px;font-size:12px"><span>${icon('check-circle', 14)} 主动咨询</span><span>${icon('check-circle', 14)} 人工回应</span><span>${icon('check-circle', 14)} 同意后录入</span></div></div></div><div class="grid-2" style="margin-top:16px"><div class="capability-note"><div class="row" style="justify-content:space-between"><strong>官方线索组件</strong>${badge('尚未开通', 'gray')}</div><p class="muted">需要核对试点账号的实际权限与官方能力证明。</p></div><div class="capability-note"><div class="row" style="justify-content:space-between"><strong>自有咨询表单</strong>${badge('尚未启用', 'gray')}</div><p class="muted">需要渠道允许、完整隐私说明及入口审核。</p></div></div></div></section>` +
      `<section class="card"><div class="card-header"><div><h2 class="section-title">工作流控制</h2><p class="muted">AI 协助整理与创作，关键判断由团队确认。</p></div>${badge('人工确认', 'green')}</div><div class="card-body grid-3"><div><div class="row">${icon('shield', 20)}<strong>事实与素材</strong></div><p class="muted">待核验、失效或缺授权的内容，明确提示补资料。</p></div><div><div class="row">${icon('check-circle', 20)}<strong>版本与审核</strong></div><p class="muted">脚本修改会创建新版本，批准结论对应冻结内容。</p></div><div><div class="row">${icon('users', 20)}<strong>客户与反馈</strong></div><p class="muted">销售确认需求与阶段。脱敏反馈用于提出选题建议。</p></div></div></section><section class="card"><div class="card-body row" style="justify-content:space-between"><div><h3>重置交互演示</h3><p class="muted">清除本地演示改动，恢复初始示例数据。</p></div>${action('reset-demo', '恢复初始演示', '', 'btn small')}</div></section>`;
  }

  window.LouxiangViews = { facts, assets, leads, effectSummary, reviewSummary, reports, settings };
})();
