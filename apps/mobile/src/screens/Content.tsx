import { useEffect, useMemo, useState } from 'react';
import { useIsFocused } from 'expo-router';
import { ActivityIndicator, AppState, Platform, StyleSheet, Text, View } from 'react-native';
import { useEvent } from 'expo';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useApp } from '../state/AppContext';
import type { Params } from '../lib/contracts';
import type { Asset, Content, Fact, Release, Shot } from '../api/types';
import { ApiError } from '../api/client';
import { Badge, Button, Card, Empty, Field, Heading, Notice, Page, Row, Select, Sheet, Toggle, colors, formatDate } from '../components/ui';
import { findApprovedArtifacts, normalizeShots, reviewFingerprint, validateRenderDraft } from '../domain/content-workflow';
import { createForegroundPoller } from '../domain/jobs-poller';
import { ContentDraftStore } from '../domain/content-drafts';
import { useAlive } from '../domain/project-resource';
import { PublicationEntrySheet } from './Publications';

type Draft = { script: string; cover_title: string; channel: string; aspect_ratio: string; subtitle_style: string; storyboard: Shot[] };
type ExportPackage = { content: Content; release: Release; render_url: string; snapshot: { render: { metadata: { content_snapshot_sha256: string } } }; [key: string]: unknown };
const stages = [{ label: '脚本与分镜', value: 'script' }, { label: '成片预览', value: 'render' }, { label: '人工审核', value: 'review' }, { label: '导出与回执', value: 'export' }];
const reviewLabels = { script: '脚本文字与封面表达符合项目事实', facts: '引用事实有效，数值、单位和来源准确', rights: '素材来源、使用授权和项目营销范围已核对', video: '我已播放并核对最终画面、字幕与音频' };
const blankReview = () => ({ script: false, facts: false, rights: false, video: false });
const drafts = new ContentDraftStore<Draft>();
export function clearContentDrafts() { drafts.clear(); }
function draftFrom(content: Content): Draft { return { script: content.script, cover_title: content.cover_title || '', channel: content.channel, aspect_ratio: content.aspect_ratio || '9:16', subtitle_style: content.subtitle_style || 'minimal', storyboard: normalizeShots(content.storyboard) }; }

export function ContentScreen({ params }: { params: Params }) {
  const { api, project, session, refreshKey, canEdit, canReview, navigate, back, reload, notify, trackJob, shareFile, shareText, openCitation, pendingJobs } = useApp();
  const [contents, setContents] = useState<Content[]>([]), [assets, setAssets] = useState<Asset[]>([]), [facts, setFacts] = useState<Fact[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [loadError, setLoadError] = useState(''), [busy, setBusy] = useState(''), [refresh, setRefresh] = useState(0);
  const [stage, setStage] = useState('script'), [draft, setDraft] = useState<Draft | null>(null), [review, setReview] = useState(blankReview), [note, setNote] = useState('');
  const [publication, setPublication] = useState(false), [leave, setLeave] = useState(false);
  const [reviewedSnapshot, setReviewedSnapshot] = useState('');
  const alive = useAlive(), base = project ? `/api/projects/${project.id}` : '';
  const focused = useIsFocused();
  const content = contents.find(item => item.id === params.id) || null;
  const snapshot = content ? reviewFingerprint(content, facts, assets, project || undefined) : '';
  if (content && reviewedSnapshot !== snapshot) { setReviewedSnapshot(snapshot); setReview(blankReview()); }
  const productionBusy = pendingJobs.some(job => job.property_id === project?.id && ['generate_content', 'render_content'].includes(job.kind));
  const scope = useMemo(() => ({ organizationId: session.organization.id, userId: session.user.id, projectId: project?.id || '', contentId: content?.id || '' }), [session.organization.id, session.user.id, project?.id, content?.id]);
  const [initializedScope, setInitializedScope] = useState('');
  const scopeIdentity = JSON.stringify(scope);
  if (content && initializedScope !== scopeIdentity) {
    setInitializedScope(scopeIdentity); setDraft(drafts.get(scope) || draftFrom(content)); setReview(blankReview()); setNote(''); setStage('script');
  }
  function refreshData() { setLoading(true); setLoadError(''); setError(''); setRefresh(value => value + 1); }
  useEffect(() => {
    if (!base || !focused) return;
    const poller = createForegroundPoller({ interval: 7000, read: async signal => {
      const [versions, materials, evidence] = await Promise.all([api<Content[]>(`${base}/contents`, { signal }), api<Asset[]>(`${base}/assets`, { signal }), api<Fact[]>(`${base}/facts`, { signal })]);
      return { versions, materials, evidence };
    }, onData: value => { setContents(value.versions); setAssets(value.materials); setFacts(value.evidence); setLoading(false); setLoadError(''); }, onError: value => { if (value instanceof ApiError && [401, 403, 404].includes(value.status)) { setContents([]); setAssets([]); setFacts([]); } setLoadError(value instanceof Error ? value.message : '内容版本加载失败'); setLoading(false); } });
    if (!AppState.currentState || AppState.currentState === 'active') poller.resume();
    const listener = AppState.addEventListener('change', state => state === 'active' ? poller.resume() : poller.pause());
    return () => { listener.remove(); poller.dispose(); };
  }, [api, base, focused, refresh, refreshKey]);
  const dirty = Boolean(content && draft && JSON.stringify(draft) !== JSON.stringify(draftFrom(content)));
  const compatibleAssets = assets.filter(asset => ['image', 'video'].includes(asset.kind) && asset.rights === 'authorized' && asset.rights_evidence?.trim() && asset.origin_type !== 'ai_generated' && (project?.is_test || asset.origin_type === 'actual'));
  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    if (canEdit && !busy) setDraft(current => { if (!current) return current; const next = { ...current, [key]: value }; drafts.set(scope, next); return next; });
  }
  function changeShot(index: number, key: string, value: unknown) {
    if (!draft || !canEdit || busy) return;
    change('storyboard', draft.storyboard.map((shot, position) => position === index ? { ...shot, [key]: value } : shot));
  }
  function moveShot(index: number, delta: number) {
    if (!draft || !canEdit || busy) return;
    const shots = [...draft.storyboard], other = index + delta;
    if (other < 0 || other >= shots.length) return;
    [shots[index], shots[other]] = [shots[other], shots[index]]; change('storyboard', shots);
  }
  async function save() {
    if (!content || !draft || !canEdit || busy) return;
    if (!draft.script.trim()) { setError('请填写脚本内容。'); return; }
    setBusy('save'); setError('');
    try {
      const value = await api<Content>(`${base}/contents/${content.id}/revise`, { method: 'POST', body: draft });
      drafts.forget(scope);
      if (!alive.current) return;
      await reload(); if (!alive.current) return;
      notify(`已保存 v${value.version}，新版本需要重新成片与审核。`); navigate('content', { id: value.id });
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '内容版本保存失败'); }
    finally { if (alive.current) setBusy(''); }
  }
  async function render() {
    if (!content || !draft || !project || !canEdit || busy || productionBusy || dirty) return;
    const validation = validateRenderDraft(draft.storyboard, assets, project.is_test);
    if (validation) { setError(validation); return; }
    setBusy('render'); setError('');
    try {
      const value = await api<{ job_id: string }>(`${base}/contents/${content.id}/render`, { method: 'POST' });
      if (!alive.current) return;
      trackJob(value.job_id); notify('成片任务已提交，后台将再次校验事实与素材授权。'); navigate('jobs');
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '成片任务提交失败'); }
    finally { if (alive.current) setBusy(''); }
  }
  async function approve() {
    if (!content || !canReview || !Object.values(review).every(Boolean) || dirty || busy || productionBusy || !content.render_asset_id) return;
    setBusy('approve'); setError('');
    try {
      await api<Release>(`${base}/contents/${content.id}/approve`, { method: 'POST', body: { confirmed: true, note, expected_render_asset_id: content.render_asset_id } });
      if (!alive.current) return;
      await reload(); if (!alive.current) return;
      setRefresh(value => value + 1); setStage('export'); notify('当前版本已审核并冻结，可导出核验发布包。');
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '当前版本审核失败'); }
    finally { if (alive.current) setBusy(''); }
  }
  async function exportApproved(kind: 'package' | 'video' | 'subtitle' | 'cover') {
    if (!content || busy || productionBusy || dirty || !content.release_id) return;
    setBusy(kind); setError('');
    try {
      // Every export repeats server validation; cached UI approval alone is insufficient.
      const bundle = await api<ExportPackage>(`${base}/contents/${content.id}/export`);
      if (!alive.current) return;
      const filename = `${project!.name}_内容v${bundle.content.version}`;
      if (kind === 'package') await shareText(JSON.stringify(bundle, null, 2), `${filename}_发布包.json`);
      else if (kind === 'video') await shareFile(bundle.render_url, `${filename}.mp4`, 'video/mp4');
      else {
        const latestAssets = await api<Asset[]>(`${base}/assets`);
        if (!alive.current) return;
        const artifacts = findApprovedArtifacts(latestAssets, content.id, bundle.snapshot.render.metadata.content_snapshot_sha256);
        const artifact = kind === 'subtitle' ? artifacts.subtitle : artifacts.cover;
        if (!artifact) throw new Error(kind === 'subtitle' ? '当前审核成片的字幕文件不存在，请联系内容负责人重新成片。' : '当前审核成片的封面文件不存在，请联系内容负责人重新成片。');
        await shareFile(`${base}/assets/${artifact.id}/file`, `${filename}.${kind === 'subtitle' ? 'srt' : 'jpg'}`, kind === 'subtitle' ? 'application/x-subrip' : 'image/jpeg');
      }
      if (alive.current) notify('已打开系统导出与分享，发布后请登记真实平台回执。');
    } catch (value) { if (alive.current) setError(value instanceof Error ? value.message : '核验导出失败'); }
    finally { if (alive.current) setBusy(''); }
  }
  if (!project) return <Page><Heading title="内容版本" /><Empty title="先选择一个项目" action={<Button title="选择项目" onPress={() => navigate('projects')} />} /></Page>;
  if (!content || !draft) return <Page><Heading title="内容版本" action={<Button title="返回" tone="ghost" onPress={back} />} />{error || loadError ? <Notice message={error || loadError} onRetry={refreshData} /> : loading ? <ActivityIndicator accessibilityLabel="正在加载内容版本" /> : <Empty title="内容版本不可用" description="该内容可能已移除，或不属于当前项目。" action={<Button title="查看项目内容" onPress={() => navigate('studio')} />} />}</Page>;
  const renderPath = content.render_asset_id ? `${base}/assets/${content.render_asset_id}/file` : '';
  const versionHistory = contents.filter(item => item.series_id === content.series_id);
  const referencedFacts = content.fact_refs.map(ref => ({ ref, fact: facts.find(fact => fact.id === (typeof ref === 'string' ? ref : ref.id || ref.fact_id)) }));
  return <Page refreshing={loading} onRefresh={refreshData} testID="content-screen">
    <Heading title={content.cover_title || content.topic} subtitle={`${project.name} · v${content.version} · ${formatDate(content.created_at)}`} action={<Button title="返回" tone="ghost" small disabled={!!(busy)} onPress={() => dirty ? setLeave(true) : back()} />} />
    <Row><Badge label={content.release_id ? '审核有效' : content.render_asset_id ? '成片待审核' : '脚本草稿'} tone={content.release_id ? 'green' : 'amber'} /><Badge label={project.is_test ? '测试项目' : '真实项目'} tone="muted" /></Row>
    {versionHistory.length > 1 ? <Select label="历史版本" value={content.id} options={versionHistory.map(item => ({ label: `v${item.version}${item.release_id ? ' · 已审核' : ''}`, value: item.id }))} onChange={id => { if (!busy) { if (dirty) { setError('先保存当前编辑，再切换历史版本。'); return; } navigate('content', { id }); } }} /> : null}
    <Select label="处理阶段" value={stage} options={stages} onChange={setStage} />
    {error || loadError ? <Notice message={error || loadError} onRetry={refreshData} /> : null}
    {dirty ? <Card><Text style={s.warning}>当前编辑尚未保存。成片、审核和导出使用已保存版本，请先保存新版本。</Text><Button title="保存新版本" onPress={() => void save()} loading={busy === 'save'} disabled={!canEdit || Boolean(busy)} /></Card> : null}
    {stage === 'script' ? <>
      <Card><Field label="脚本内容" value={draft.script} onChangeText={value => change('script', value)} multiline editable={canEdit && !busy} /><Field label="封面标题" value={draft.cover_title} onChangeText={value => change('cover_title', value)} editable={canEdit && !busy} />
        <Select label="内容渠道" value={draft.channel} options={['抖音', '视频号', '小红书', ...(!['抖音', '视频号', '小红书'].includes(draft.channel) ? [draft.channel] : [])].map(value => ({ label: value, value }))} onChange={value => change('channel', value)} />
        <Select label="画面比例" value={draft.aspect_ratio} options={['9:16', '16:9', '1:1', '4:5'].map(value => ({ value, label: value }))} onChange={value => change('aspect_ratio', value)} />
        <Select label="字幕样式" value={draft.subtitle_style} options={[{ label: '极简白字', value: 'minimal' }, { label: '深色底字幕', value: 'dark' }]} onChange={value => change('subtitle_style', value)} />
        <Text style={s.hint}>保存会创建新版本并保留历史。原版本的成片与审核不会迁移到新版本。</Text>
      </Card>
      <Row><Text style={s.title}>分镜与实拍素材</Text><Button title="素材库" tone="ghost" small onPress={() => navigate('assets')} /></Row>
      {!compatibleAssets.length ? <Empty title="还没有可用于成片的授权素材" description="在素材库上传项目实拍，并记录使用授权依据。" action={<Button title="上传素材" onPress={() => navigate('assets')} />} /> : null}
      {draft.storyboard.map((shot, index) => <Card key={`${content.id}-${index}`}>
        <Row><Text style={s.title}>镜头 {index + 1}</Text><Text style={s.hint}>{assets.find(asset => asset.id === shot.asset_id)?.name || '尚未选择素材'}</Text></Row>
        <Select label={`镜头 ${index + 1} 素材`} value={shot.asset_id || ''} options={[{ label: '选择素材', value: '' }, ...compatibleAssets.map(asset => ({ label: asset.name, value: asset.id }))]} onChange={value => changeShot(index, 'asset_id', value)} />
        <Field label={`镜头 ${index + 1} 时长（秒）`} value={String(shot.duration ?? 5)} keyboardType="decimal-pad" onChangeText={value => changeShot(index, 'duration', value === '' ? '' : value)} editable={canEdit && !busy} />
        <Field label={`镜头 ${index + 1} 素材起始位置（秒）`} value={String(shot.start ?? 0)} keyboardType="decimal-pad" onChangeText={value => changeShot(index, 'start', value)} editable={canEdit && !busy} />
        <Field label={`镜头 ${index + 1} 字幕`} value={String(shot.caption || '')} onChangeText={value => changeShot(index, 'caption', value)} multiline editable={canEdit && !busy} />
        <Field label={`镜头 ${index + 1} 画面说明`} value={String(shot.description || '')} onChangeText={value => changeShot(index, 'description', value)} editable={canEdit && !busy} />
        <Row><Button title="上移" tone="secondary" small disabled={!canEdit || Boolean(busy) || index === 0} onPress={() => moveShot(index, -1)} /><Button title="下移" tone="secondary" small disabled={!canEdit || Boolean(busy) || index === draft.storyboard.length - 1} onPress={() => moveShot(index, 1)} /><Button title="移除镜头" tone="danger" small disabled={!canEdit || Boolean(busy)} onPress={() => change('storyboard', draft.storyboard.filter((_, position) => position !== index))} /></Row>
      </Card>)}
      <Text style={s.hint}>1 至 20 个镜头，每个 1 至 60 秒，总时长 15 至 60 秒。素材起始位置适用于视频片段。</Text>
      <Button title="添加镜头" tone="secondary" disabled={!canEdit || Boolean(busy) || draft.storyboard.length >= 20} onPress={() => change('storyboard', [...draft.storyboard, { asset_id: '', duration: 5, start: 0, caption: '' }])} />
      <Button title="保存新版本" onPress={() => void save()} loading={busy === 'save'} disabled={!canEdit || Boolean(busy) || !dirty} />
      {!canEdit ? <Text style={s.hint}>当前角色可以查看；内容负责人可编辑与制作。</Text> : null}
    </> : stage === 'render' ? <>
      <Card><Text style={s.title}>当前版本的实际成片</Text>{renderPath ? <ContentVideo path={renderPath} ratio={content.aspect_ratio} /> : <Empty title="此版本尚未制作成片" description="使用已授权素材、脚本与字幕渲染实际 MP4。" />}<Text style={s.body}>{content.aspect_ratio} · {normalizeShots(content.storyboard).length} 个分镜</Text><Text style={s.hint}>后台再次校验有效事实、数字与单位、营销授权、素材授权及来源。</Text>{productionBusy ? <Button title="项目已有制作任务，查看进度" tone="secondary" onPress={() => navigate('jobs')} /> : null}<Button title={content.render_asset_id ? '重新制作此版本成片' : '制作成片'} onPress={() => void render()} loading={busy === 'render'} disabled={!canEdit || dirty || Boolean(busy) || productionBusy} /><Button title="进入人工审核" tone="secondary" disabled={!content.render_asset_id || dirty || productionBusy} onPress={() => setStage('review')} /></Card>
    </> : stage === 'review' ? <>
      <Card><Text style={s.title}>逐项核对当前版本</Text><Text selectable style={s.body}>{content.script}</Text><Text style={s.hint}>封面：{content.cover_title || '无封面标题'} · {content.channel}</Text>{renderPath ? <ContentVideo path={renderPath} ratio={content.aspect_ratio} /> : <Text style={s.warning}>先完成此版本的真实成片，再提交人工审核。</Text>}</Card>
      <Card><Text style={s.title}>当前引用的事实</Text>{referencedFacts.length ? referencedFacts.map(({ ref, fact }, index) => <View key={fact?.id || index} style={s.fact}>
        <Row><Text style={s.label}>{fact?.label || '引用事实不可用'}</Text><Badge label={fact && fact.valid && (typeof ref === 'string' || ref.revision === fact.revision) ? '有效且版本匹配' : '需要重新核验'} tone={fact && fact.valid && (typeof ref === 'string' || ref.revision === fact.revision) ? 'green' : 'amber'} /></Row>
        <Text style={s.body}>{fact ? `${fact.value} ${fact.unit} · 来源：${fact.source}` : '该事实已移除或无权访问。'}</Text>
        {fact ? <Button title="查看事实依据" tone="ghost" small onPress={() => openCitation({ kind: 'fact', id: fact.id, label: fact.label, revision: fact.revision })} /> : null}
      </View>) : <Text style={s.warning}>此内容缺少事实引用。</Text>}</Card>
      <Card><Text style={s.title}>审核确认</Text>{Object.entries(reviewLabels).map(([key, label]) => <Toggle key={key} label={label} value={review[key as keyof typeof review]} onChange={value => { if (canReview && !busy && !dirty) setReview(current => ({ ...current, [key]: value })); }} />)}<Field label="审核备注" value={note} onChangeText={setNote} multiline editable={canReview && !busy} placeholder="记录确认范围及注意事项" /><Text style={s.hint}>确认人为 {session.user.name}。审批绑定当前脚本、事实版本、素材授权与实际成片。</Text><Button title={content.release_id ? '当前版本审核已有效' : '审核并冻结当前版本'} onPress={() => void approve()} loading={busy === 'approve'} disabled={!canReview || !Object.values(review).every(Boolean) || !content.render_asset_id || dirty || Boolean(busy) || Boolean(content.release_id)} />{!canReview ? <Text style={s.hint}>需要审核负责人或组织管理员完成确认。</Text> : null}</Card>
    </> : <>
      <Card><Text style={s.title}>核验导出</Text><Text style={s.body}>每次导出都由服务端重新核对审核版本。资料或授权改变后，请重新制作并审核。</Text>{!content.release_id ? <Text style={s.warning}>当前版本没有有效审核，请先完成成片与审核。</Text> : null}
        <Button title="导出核验发布包 JSON" onPress={() => void exportApproved('package')} loading={busy === 'package'} disabled={!content.release_id || dirty || Boolean(busy) || productionBusy} />
        <Button title="导出实际成片 MP4" tone="secondary" onPress={() => void exportApproved('video')} loading={busy === 'video'} disabled={!content.release_id || dirty || Boolean(busy) || productionBusy} />
        <Row><Button title="导出字幕 SRT" tone="secondary" onPress={() => void exportApproved('subtitle')} loading={busy === 'subtitle'} disabled={!content.release_id || dirty || Boolean(busy) || productionBusy} /><Button title="导出封面 JPG" tone="secondary" onPress={() => void exportApproved('cover')} loading={busy === 'cover'} disabled={!content.release_id || dirty || Boolean(busy) || productionBusy} /></Row>
      </Card>
      <Card><Text style={s.title}>发布后，登记真实回执</Text><Text style={s.body}>在目标平台发布后填写原链接，并由审核负责人核实平台可见状态。</Text><Button title="登记平台发布链接" disabled={!canEdit || !content.release_id || dirty || Boolean(busy)} onPress={() => setPublication(true)} /><Button title="查看项目发布回执" tone="secondary" onPress={() => navigate('publications')} /></Card>
    </>}
    <Sheet visible={leave} title="当前编辑尚未保存" onClose={() => setLeave(false)}><Text style={s.body}>可以继续编辑或先保存为新版本。切换页面会暂时保留编辑；关闭 App 前请保存。</Text><Button title="继续编辑" onPress={() => setLeave(false)} /><Button title="保存新版本" tone="secondary" loading={busy === 'save'} disabled={!!(busy)} onPress={() => { setLeave(false); void save(); }} /><Button title="丢弃编辑并返回" tone="danger" onPress={() => { drafts.forget(scope); setLeave(false); back(); }} /></Sheet>
    {publication && content.release_id ? <PublicationEntrySheet releaseId={content.release_id} onClose={() => setPublication(false)} onSaved={() => { setPublication(false); navigate('publications'); }} /> : null}
  </Page>;
}

function ContentVideo({ path, ratio }: { path: string; ratio: string }) {
  const { baseUrl, authHeaders } = useApp();
  const focused = useIsFocused();
  const alive = useAlive(), [browserSource, setBrowserSource] = useState<{ path: string; url: string } | null>(null), [mediaError, setMediaError] = useState<{ path: string; message: string } | null>(null), [attempt, setAttempt] = useState(0);
  const browserUrl = browserSource?.path === path ? browserSource.url : '', loadError = mediaError?.path === path ? mediaError.message : '';
  // Browser video elements cannot send Authorization headers; keep the authenticated
  // response transient in a Blob URL, while native playback uses Range + Bearer.
  useEffect(() => {
    if (Platform.OS !== 'web' || !focused) return;
    const controller = new AbortController(); let objectUrl = '';
    const timeout = setTimeout(() => { controller.abort(); if (alive.current) setMediaError({ path, message: '成片加载超时，请检查网络后重试。' }); }, 60000);
    void fetch(`${baseUrl}${path}`, { headers: authHeaders(), credentials: 'omit', signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('当前成片无权访问或已不可用，请刷新内容版本。');
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setBrowserSource({ path, url: objectUrl }); setMediaError(null);
    }).catch(value => { if (!controller.signal.aborted) setMediaError({ path, message: value instanceof Error ? value.message : '视频加载失败' }); }).finally(() => clearTimeout(timeout));
    return () => { controller.abort(); clearTimeout(timeout); if (objectUrl) URL.revokeObjectURL(objectUrl); setBrowserSource(current => current?.url === objectUrl ? null : current); };
  }, [baseUrl, path, authHeaders, focused, attempt, alive]);
  const source = useMemo(() => Platform.OS === 'web' ? browserUrl ? { uri: browserUrl, useCaching: false } : null : { uri: `${baseUrl}${path}`, headers: authHeaders(), useCaching: false }, [baseUrl, path, authHeaders, browserUrl]);
  const player = useVideoPlayer(source);
  const { status, error } = useEvent(player, 'statusChange', { status: player.status });
  const [retry, setRetry] = useState(false);
  useEffect(() => {
    if (!focused) { try { player.pause(); } catch {} return; }
    const listener = AppState.addEventListener('change', state => { if (state !== 'active') { try { player.pause(); } catch {} } });
    return () => { listener.remove(); try { player.pause(); } catch {} };
  }, [player, focused]);
  return <View style={{ gap: 10 }}><VideoView player={player} nativeControls contentFit="contain" fullscreenOptions={{ enable: true }} style={[s.video, { aspectRatio: ratio === '16:9' ? 16 / 9 : ratio === '1:1' ? 1 : ratio === '4:5' ? 4 / 5 : 9 / 16 }]} accessibilityLabel="当前内容实际成片播放器" />
    {status === 'loading' || (Platform.OS === 'web' && !browserUrl && !loadError) ? <ActivityIndicator accessibilityLabel="正在加载视频" /> : null}
    {status === 'error' || loadError ? <Notice message={loadError || error?.message || '视频加载失败，请检查连接后重试。'} onRetry={() => {
      if (Platform.OS === 'web') { setBrowserSource(null); setMediaError(null); setAttempt(value => value + 1); return; }
      setRetry(true); setMediaError(null); void player.replaceAsync(source).catch(value => { if (alive.current) setMediaError({ path, message: value instanceof Error ? value.message : '视频加载失败' }); }).finally(() => { if (alive.current) setRetry(false); });
    }} /> : null}
    {retry ? <Text style={s.hint}>正在重新加载成片…</Text> : null}
  </View>;
}
const s = StyleSheet.create({ title: { fontSize: 18, fontWeight: '600', color: colors.navy, flexShrink: 1 }, label: { fontSize: 15, fontWeight: '600', color: colors.text, flexShrink: 1 }, body: { fontSize: 14, lineHeight: 23, color: colors.text, marginVertical: 8 }, hint: { fontSize: 12, lineHeight: 20, color: colors.muted, marginVertical: 5 }, warning: { fontSize: 14, lineHeight: 23, color: colors.amber }, fact: { gap: 4, borderBottomWidth: 1, borderBottomColor: colors.border, paddingBottom: 12 }, video: { width: '100%', maxHeight: 520, backgroundColor: '#101B2D', borderRadius: 14 } });
