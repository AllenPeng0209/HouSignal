const active=job=>['queued','running'].includes(job.status);
export function activeContentJob(jobs,contentId){return jobs.find(job=>active(job)&&['render_content','illustrate_content'].includes(job.kind)&&job.production_request?.content_version_id===contentId)||null;}
export function resumableProductionJob(jobs,contentId){return activeContentJob(jobs,contentId)||jobs.find(job=>active(job)&&job.kind==='generate_content')||null;}
