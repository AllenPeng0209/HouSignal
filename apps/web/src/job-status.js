export function jobProgressPercent(progress) {
  const value=Number(progress);return Number.isFinite(value)?Math.round(Math.max(0,Math.min(1,value))*100):0;
}
/** @param {any} job @param {number} now */
export function jobStatusText(job,now=Date.now()) {
  if(!job)return '正在提交任务…';
  const finished={succeeded:'已完成',failed:'任务未完成',cancelled:'已取消'};
  if(finished[job.status])return job.phase||finished[job.status];
  const start=Date.parse(job.status==='queued'?job.created_at:(job.attempt_started_at||job.started_at||job.created_at));
  const seconds=Number.isFinite(start)?Math.max(0,Math.floor((now-start)/1000)):0;
  if(job.status==='queued')return `${job.phase||'等待调度'} · ${seconds}秒`;
  const phases={chat:'正在生成回答',generate_content:'正在生成脚本',render_content:'正在制作成片',extract_asset:'正在识别素材'};
  return `${job.phase||phases[job.kind]||'正在处理任务'} · 已用${seconds}秒`;
}
