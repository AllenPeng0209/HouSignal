/** Controlled HTTP/AI fixture for the actual built React UI. No real credentials or business data. */
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
const dist=resolve(dirname(fileURLToPath(import.meta.url)),'../../dist');
const readiness={percent:71,completed:5,total:7,items:[]};
const project=id=>({id,name:`前端性能测试项目${id==='p1'?'A':'B'}`,city:'上海',property_type:'大平层',channel:'抖音',owner_name:'验收负责人',audience:'示例客户',sales_owner_name:'验收销售',marketing_confirmed:true,marketing_evidence:'仅为前端性能测试',is_test:true,completeness:readiness});
const session={user:{id:'u1',name:'前端验收',email:'qa@example.invalid'},organization:{id:'t1',name:'性能验收工作区'},role:'owner'};
const conversations=new Map([['c1',{id:'c1',title:'前端性能验收对话',draft:'',created_at:new Date().toISOString()}],['c2',{id:'c2',title:'另一项目对话',draft:'',created_at:new Date().toISOString()}]]);
const messages=new Map([['c1',[]],['c2',[]]]);const events=[],jobs=new Map();
const topic={id:'topic-1',title:'空间与生活',angle:'只测试前端交互',eligible:true,missing:[],fact_refs:[]};
const content={id:'content-1',series_id:'series-1',version:1,topic:topic.title,script:'性能验收示例脚本',status:'draft',channel:'抖音',cover_title:'验收示例',aspect_ratio:'9:16',subtitle_style:'minimal',storyboard:[],fact_refs:[],created_at:new Date().toISOString()};
function json(res,body,status=200){res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(body));}
function addJob(id,kind){const at=new Date().toISOString();jobs.set(id,{id,kind,status:'running',progress:.05,phase:kind==='chat'?'查询项目记录':'生成脚本',result:{},created_at:at,updated_at:at,started_at:at,attempt_started_at:at});}
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost'),path=url.pathname;const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):{};
 events.push({method:req.method,path,at:Date.now()});
 if(path==='/__qa/records')return json(res,{events,jobs:[...jobs.values()],conversations:[...conversations.values()]});
 if(path==='/__qa/reset'){events.length=0;messages.set('c1',[]);messages.set('c2',[]);jobs.clear();for(const c of conversations.values())c.draft='';return json(res,{ok:true});}
 if(path==='/__qa/start-studio'){addJob('g1','generate_content');return json(res,{ok:true});}
 if(path==='/api/auth/me')return json(res,session);
 if(path==='/api/projects')return json(res,[project('p1'),project('p2')]);
 if(path==='/api/model/status')return json(res,{provider:'fixture',model:'controlled',configured:true});
 if(path.match(/^\/api\/projects\/p[12]\/conversations$/))return json(res,[conversations.get(path.includes('/p1/')?'c1':'c2')]);
 if(path.endsWith('/messages')){
  const cid=path.split('/')[3];
  if(req.method==='POST'){const rows=messages.get(cid);rows.push({id:'m-user',role:'user',content:body.text,status:'completed',citations:[]},{id:'m-assistant',role:'assistant',content:'',status:'queued',citations:[],job_id:'j1'});addJob('j1','chat');conversations.get(cid).draft='';return json(res,{job_id:'j1'},202);}
  return json(res,messages.get(cid));
 }
 if(path.endsWith('/draft')){const row=conversations.get(path.split('/')[3]);row.draft=body.draft;return json(res,row);}
 if(path.match(/^\/api\/jobs\/[^/]+\/events$/)){
  const id=path.split('/')[3],job=jobs.get(id);if(!job)return json(res,{detail:'未找到任务'},404);
  res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache'});
  const emit=()=>res.write(`event: status\ndata: ${JSON.stringify(job)}\n\n`);emit();
  if(job.status==='succeeded'){res.end();return;}
  const preview=setTimeout(()=>{job.phase=id==='j1'?'正在组织回答':'生成脚本';job.progress=.45;job.result=id==='j1'?{preview_text:'正在流式输出的项目结论'}:{};job.updated_at=new Date().toISOString();if(!res.destroyed)emit();},150);
  const complete=setTimeout(()=>{job.status='succeeded';job.phase='已完成';job.progress=1;job.completed_at=new Date().toISOString();job.updated_at=job.completed_at;job.result=id==='j1'?{preview_text:'最终回答附上核对依据'}:{content_version_id:content.id};if(id==='j1'){for(const rows of messages.values()){const assistant=rows.find(m=>m.job_id===id);if(assistant){assistant.status='completed';assistant.content='最终回答附上核对依据';assistant.citations=[];}}}if(!res.destroyed){emit();res.end();}},id==='j1'?1400:2400);
  res.on('close',()=>{events.push({method:'CLOSE',path,at:Date.now()});clearTimeout(preview);/* Durable task completion intentionally survives closing this socket. */});
  return;
 }
 if(path.match(/^\/api\/jobs\/[^/]+$/))return json(res,jobs.get(path.split('/')[3]));
 if(path.endsWith('/jobs'))return json(res,[...jobs.values()].reverse());
 if(path.endsWith('/topics'))return json(res,[topic]);
 if(path.endsWith('/contents'))return json(res,[content]);
 if(path.endsWith('/assets')||path.endsWith('/publications'))return json(res,[]);
 if(path.endsWith('/contents/generate')){addJob('g1','generate_content');return json(res,{job_id:'g1'},202);}
 if(path.endsWith('/overview'))return json(res,{project:project('p1'),metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
 if(path.startsWith('/api/'))return json(res,{detail:'此验收仅覆盖指定前端流程'},404);
 try{const file=resolve(dist,path==='/'?'index.html':path.slice(1));if(!file.startsWith(dist+'/'))return json(res,{},404);const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.jpg':'image/jpeg'};const bytes=await readFile(file);res.writeHead(200,{'content-type':types[extname(file)]||'application/octet-stream'});res.end(bytes);}catch{json(res,{},404);}
});
server.listen(Number(process.env.HS_UI_QA_PORT||8029),'127.0.0.1',()=>process.stdout.write('Controlled frontend performance fixture: http://127.0.0.1:8029/\n'));
