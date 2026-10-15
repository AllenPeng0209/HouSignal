import {Button,Icon} from './components';

export type ContentTypeId='professional'|'traffic'|'trust'|'acquisition'|'conversion';
export interface ContentPlanDraft {title:string;angle:string;brief:string;topic_id:'tour'|'space'|'light'|'layout'|'kitchen';content_type?:ContentTypeId;subject?:string;reference_id?:string;start_mode?:'reference'|'category'|'idea'}
interface Subject {label:string;query:string;angle:string;outline:string;topic_id?:ContentPlanDraft['topic_id']}
interface Direction {id:ContentTypeId;label:string;goal:string;purpose:string;icon:string;subjects:Subject[]}

export const contentDirections:Direction[]=[
 {id:'professional',label:'专业内容',goal:'建立专业认知',purpose:'用清楚、有依据的分析，帮助用户理解房产。',icon:'file',subjects:[
  {label:'政策解读',query:'购房政策',angle:'从购房者关心的问题出发，解释政策的适用范围与实际影响。',outline:'政策原文与日期 → 适用人群 → 购房流程中的影响 → 需要进一步核对的问题'},
  {label:'板块分析',query:'板块分析',angle:'围绕目标家庭的通勤与生活需求，比较板块条件。',outline:'目标客群 → 交通与生活配套 → 板块差异 → 适合什么需求'},
  {label:'户型点评',query:'户型分析',angle:'从日常使用场景看户型，解释动线与空间安排。',outline:'户型图 → 回家与家务动线 → 空间优点与限制 → 适合的家庭',topic_id:'layout'},
  {label:'买房知识',query:'买房知识',angle:'回答首次买房最常遇到的一个具体问题。',outline:'常见问题 → 概念解释 → 实际看房怎么核对 → 下一步准备'},
 ]},
 {id:'traffic',label:'流量内容',goal:'吸引目标客群',purpose:'用热点、特色和反差，让潜在客户愿意点开。',icon:'sparkle',subjects:[
  {label:'楼市热点',query:'楼市热点',angle:'从近期楼市话题切入，讲清它与目标客群的关系。',outline:'热点与来源 → 用户为什么关注 → 不同需求的影响 → 留下讨论问题'},
  {label:'特色房源',query:'特色房源',angle:'从一个有辨识度的空间细节开始，带用户看真实房源。',outline:'特色镜头 → 实际体验 → 产品条件 → 哪些人会喜欢',topic_id:'space'},
  {label:'居住反差',query:'居住体验',angle:'对比看房时的想象与日常居住体验。',outline:'预期与现实 → 实拍场景 → 差异原因 → 看房时如何核对'},
  {label:'热门话题',query:'买房话题',angle:'围绕买房与居住中的常见分歧，邀请用户表达自己的选择。',outline:'一个有讨论度的问题 → 两种需求 → 各自的考虑 → 互动提问'},
 ]},
 {id:'trust',label:'信任内容',goal:'增强服务信任',purpose:'让用户看到真实过程、服务能力和可核对的反馈。',icon:'shield',subjects:[
  {label:'真实服务过程',query:'看房服务',angle:'记录一次真实服务中的需求沟通、核对与解决过程。',outline:'客户需求 → 服务步骤 → 发现的问题 → 如何处理与跟进'},
  {label:'客户反馈',query:'买房经历',angle:'通过获得同意的客户反馈，呈现实际服务体验。',outline:'客户原有顾虑 → 实际服务经历 → 客户反馈 → 后续改进'},
  {label:'交易案例复盘',query:'成交案例',angle:'复盘真实交易中的关键问题和团队如何应对。',outline:'需求与条件 → 关键难点 → 解决过程 → 可以复用的经验'},
  {label:'个人从业经历',query:'房产经纪人日常',angle:'讲述一个能体现专业判断与服务态度的真实经历。',outline:'具体经历 → 当时的选择 → 判断依据 → 形成的服务习惯'},
 ]},
 {id:'acquisition',label:'获客内容',goal:'促成主动咨询',purpose:'讲清能提供什么房源与服务，让有需求的人找到你。',icon:'users',subjects:[
  {label:'房源展示',query:'房源推荐',angle:'按需求展示房源的实际条件与适配人群。',outline:'房源条件 → 实拍亮点与限制 → 适合的需求 → 看房咨询方式'},
  {label:'不同预算的需求匹配',query:'预算选房',angle:'围绕预算、区域与家庭需求，展示选房思路。',outline:'预算与需求 → 可比较的条件 → 选择取舍 → 邀请补充具体需求'},
  {label:'买卖租服务介绍',query:'房产服务',angle:'明确说明服务对象、服务范围与沟通方式。',outline:'适合哪些需求 → 能提供的服务 → 服务过程 → 如何开始沟通'},
 ]},
 {id:'conversion',label:'转化内容',goal:'帮助成交决策',purpose:'针对已有意向客户的疑问，帮助其推进选择。',icon:'target',subjects:[
  {label:'具体房源对比',query:'房源对比',angle:'根据同一客户的需求，用一致标准比较候选房源。',outline:'客户优先级 → 同标准对比 → 优点与限制 → 明确下一步核对项'},
  {label:'购房疑虑解答',query:'买房顾虑',angle:'回应意向客户在选择具体房源时最关心的一个问题。',outline:'具体疑问 → 可核对的依据 → 仍需确认的条件 → 后续沟通'},
  {label:'看房后分析',query:'看房复盘',angle:'结合实际看房反馈，梳理房源与客户需求的匹配程度。',outline:'看房反馈 → 符合与不符合的需求 → 需要补充的信息 → 下一步安排'},
  {label:'成交过程拆解',query:'成交过程',angle:'复盘真实案例中，客户如何消除疑虑并完成决策。',outline:'最初顾虑 → 核对与比较 → 决策依据 → 可参考的经验'},
 ]},
];

export function createContentPlanDraft(type:ContentTypeId|null|undefined,query:string):ContentPlanDraft|null {
 const direction=contentDirections.find(d=>d.id===type),subject=direction?.subjects.find(s=>s.query===query.trim());
 if(!direction||!subject)return null;
 return {title:subject.label,angle:subject.angle,topic_id:subject.topic_id||'tour',content_type:direction.id,subject:subject.query,start_mode:'category',brief:`内容类型：${direction.label}\n核心作用：${direction.goal}。${direction.purpose}\n选题方向：${subject.label}\n内容提纲：${subject.outline}\n结合当前项目资料与目标客群调整表达，使用有依据的信息和真实案例。`};
}

export function ContentDirectionGuide({type,query,disabled=false,editable=true,onSelectType,onSelectSubject,onClear,onPlan}:{
 type?:ContentTypeId|null;query:string;disabled?:boolean;editable?:boolean;
 onSelectType:(type:ContentTypeId)=>void;onSelectSubject:(type:ContentTypeId,query:string)=>void;onClear:()=>void;onPlan?:(draft:ContentPlanDraft)=>void;
}) {
 const direction=contentDirections.find(d=>d.id===type),subject=direction?.subjects.find(s=>s.query===query.trim()),draft=createContentPlanDraft(type,query);
 return <section className="card content-direction-guide" aria-label="按内容类型找选题">
  <div className="content-direction-heading"><div><h3>按内容类型找选题</h3><p>先确定这条内容的目的，再选择具体题目。</p></div>{direction&&<Button onClick={onClear} disabled={disabled}>清除内容类型</Button>}</div>
  <div className="content-direction-types">{contentDirections.map((d,i)=><button key={d.id} type="button" aria-label={d.label} aria-pressed={type===d.id} className={`content-direction-type ${type===d.id?'selected':''}`} disabled={disabled} onClick={()=>onSelectType(d.id)}><span className="content-direction-number">0{i+1}<Icon name={d.icon} size={18}/></span><strong>{d.label}</strong><span>{d.goal}</span></button>)}</div>
  {direction?<div className="content-direction-detail" aria-live="polite"><div className="content-direction-description"><h4>{direction.label} · 可以做的选题</h4><p>{direction.purpose}</p></div><div className="content-direction-subjects">{direction.subjects.map(s=><button key={s.query} type="button" aria-label={s.label} aria-pressed={subject===s} className={subject===s?'selected':''} disabled={disabled} onClick={()=>onSelectSubject(direction.id,s.query)}>{s.label}</button>)}</div>{subject&&<div className="content-direction-preview"><div><strong>{subject.label}</strong><p>{subject.angle}</p><small>搜索关键词：{subject.query}</small></div>{draft&&onPlan&&<Button icon="arrow" onClick={()=>onPlan(draft)} disabled={disabled||!editable}>用此选题策划</Button>}</div>}<p className="content-direction-hint">选题会带入下方关键词；点击「搜索相关内容」开始检索，也可以继续补充关键词。</p></div>:<p className="content-direction-hint">专业展示能力，信任呈现服务；获客促成咨询，转化帮助已有意向的客户做决定。</p>}
 </section>;
}
