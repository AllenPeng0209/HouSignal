/** Exercises the actual built React UI; external Easel/model calls stay in this fixture. */
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
const dist=resolve(dirname(fileURLToPath(import.meta.url)),'../../dist');
const at=new Date().toISOString();
const project={id:'skills-qa',name:'技能交互验收项目',city:'上海',property_type:'住宅',channel:'抖音',owner_name:'验收负责人',audience:'测试受众',sales_owner_name:'验收销售',marketing_confirmed:true,marketing_evidence:'前端验收',is_test:true,completeness:{percent:100,completed:7,total:7,items:[]}};
const session={user:{id:'qa',name:'技能验收',email:'qa@example.invalid'},organization:{id:'qa-org',name:'技能验收工作区'},role:'owner'};
const skills=[
 {id:'trending-topics',title:'全网热搜聚合',layer:'discover',description:'研究当前赛道热点并列出来源',easel_skill:'skill-trending-topics',mode:'easel',execution_engine:'easel',easel_url:'http://127.0.0.1:8030',target:'studio',status:'ready',reason:'测试服务已就绪'},
 {id:'topic-evaluation',title:'选题评分',layer:'plan',description:'按受众和制作成本评估选题',easel_skill:'skill-topic-evaluator',mode:'easel',target:'studio',status:'ready',reason:'测试服务已就绪'},
 {id:'social-copywriting',title:'社媒文案',layer:'text_visual',description:'按目标受众与平台生成文案',easel_skill:'social-content',mode:'easel',execution_engine:'housignal_text',target:'studio',status:'ready',reason:'楼响文字模型已就绪，参考 Easel 方法'},
 {id:'ai-video',title:'AI 视频',layer:'audio_video',description:'按明确画幅生成新的视频',easel_skill:'ai-video-gen',mode:'easel',target:'studio',status:'setup',reason:'尚未配置视频生成服务'},
 {id:'native-editing',title:'视频剪辑',layer:'audio_video',description:'使用项目素材编排并渲染',easel_skill:'video-editing',mode:'native',target:'studio',status:'ready',reason:'使用内容制作流程'},
 {id:'douyin-publish',title:'抖音发布',layer:'publish_attribute',description:'人工登录平台并提交已审核内容',easel_skill:'skill-douyin-upload',mode:'manual',target:'accounts',status:'manual',reason:'需人工登录和发布确认'},
];
const requests=[],jobs=new Map(),subscribers=new Map();
function json(res,body,status=200){res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(body));}
const server=createServer(async(req,res)=>{
 const path=new URL(req.url,'http://localhost').pathname,chunks=[];
 for await(const chunk of req)chunks.push(chunk);
 const body=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):{};
 if(path==='/__qa/reset'){requests.length=0;jobs.clear();return json(res,{ok:true});}
 if(path==='/__qa/records')return json(res,{requests,jobs:[...jobs.values()]});
 if(path==='/__qa/seed-history'){
  const time=new Date().toISOString();
  jobs.set('history-success',{id:'history-success',kind:'content_skill',skill_id:'social-copywriting',status:'succeeded',phase:'已完成',progress:1,result:{skill_id:'social-copywriting',execution_engine:'housignal_text',response:'历史成品待核对',artifacts:[{name:'历史成品文件',url:'/api/projects/skills-qa/operations/skills/artifacts/history-success/result.txt'}]},created_at:time,updated_at:time});
  jobs.set('history-running',{id:'history-running',kind:'content_skill',skill_id:'trending-topics',status:'running',phase:'执行技能',progress:.2,result:{},created_at:time,updated_at:time});
  jobs.set('unrelated-chat',{id:'unrelated-chat',kind:'chat',status:'succeeded',progress:1,result:{preview_text:'不应展示的聊天内容'},created_at:time,updated_at:time});
  return json(res,{ok:true});
 }
 requests.push({method:req.method,path,body});
 if(path==='/api/auth/me')return json(res,{...session,role:req.headers.cookie?.includes('skills-qa-role=editor')?'editor':'owner'});
 if(path==='/api/projects')return json(res,[project]);
 if(path==='/api/model/status')return json(res,{provider:'fixture',model:'fixture',configured:true});
 if(path.endsWith('/operations/skills'))return json(res,skills);
 if(path.endsWith('/operations/accounts'))return json(res,[{id:'account-qa',name:'验收抖音账号',platform:'抖音',status:'managed',positioning:'真实验收定位',audience:'测试受众',voice:'专业',red_lines:'不虚构事实',homepage:'',profile:{}},{id:'account-paused',name:'暂停的账号',platform:'小红书',status:'paused',positioning:'暂停',audience:'测试',voice:'专业',red_lines:'',homepage:'',profile:{}}]);
 if(path.endsWith('/operations/service-status'))return json(res,{configured:true,available:true,skills:6});
 if(path.match(/^\/api\/projects\/skills-qa\/jobs$/))return json(res,[...jobs.values()].reverse());
 if(path.endsWith('/run')){
  const id=`skill-${jobs.size+1}`,time=new Date().toISOString(),job={id,kind:'content_skill',skill_id:path.split('/')[6],status:'running',phase:'执行技能',progress:.2,result:{},created_at:time,updated_at:time};
  jobs.set(id,job);if(body.input==='延迟提交验收'){setTimeout(()=>json(res,{job_id:id,job},202),700);return;}return json(res,{job_id:id,job},202);
 }
 if(path.endsWith('/cancel')){const job=jobs.get(path.split('/')[3]);job.status='cancelled';job.error='用户已取消任务';subscribers.get(job.id)?.();return json(res,job);}
 if(path.match(/^\/api\/jobs\/[^/]+\/events$/)){
  const job=jobs.get(path.split('/')[3]);res.writeHead(200,{'content-type':'text/event-stream'});
  const emit=()=>res.write(`event: status\ndata: ${JSON.stringify(job)}\n\n`);subscribers.set(job.id,()=>{emit();res.end();});emit();
  const finish=setTimeout(()=>{if(job.status==='running'){job.status='succeeded';job.progress=1;job.phase='已完成';job.result={response:'这是一份待人工核对的文案结果。',artifacts:[{name:'安全产物',url:`/api/projects/skills-qa/operations/skills/artifacts/${job.id}/result.txt`,mime_type:'text/plain'},{name:'危险链接',url:'javascript:alert(1)'},{name:'外站协议相对链接',url:'//example.invalid/asset.png'},{name:'另一个项目产物',url:`/api/projects/another-project/operations/skills/artifacts/${job.id}/result.txt`},{name:'另一个任务产物',url:'/api/projects/skills-qa/operations/skills/artifacts/another-job/result.txt'}]};}emit();res.end();},900);
  if(job.id==='history-running')clearTimeout(finish);
  res.on('close',()=>{clearTimeout(finish);subscribers.delete(job.id);});return;
 }
 if(path.match(/^\/api\/jobs\/[^/]+$/))return json(res,jobs.get(path.split('/')[3]));
 if(path.match(/^\/api\/projects\/skills-qa\/operations\/skills\/artifacts\/(skill-\d+|history-success)\/result\.txt$/)){
  const id=path.split('/')[7],job=jobs.get(id);
  if(!job||job.status!=='succeeded')return json(res,{detail:'产物尚不可用'},404);
  res.writeHead(200,{'content-type':'text/plain; charset=utf-8','content-disposition':'attachment; filename="result.txt"'});res.end('Skill UI verified artifact');return;
 }
 if(path.endsWith('/conversations'))return json(res,[{id:'qa-conversation',title:'验收对话',draft:'',created_at:at}]);
 if(path.endsWith('/overview'))return json(res,{project,metrics:{published:0,inquiries:0,qualified:0,appointments:0,visits:0,views:null,unknown_rate:null},pending_inquiries:[],content_versions:[],publications:[],proposals:[]});
 if(path.startsWith('/api/'))return json(res,[]);
 try{const file=resolve(dist,path==='/'?'index.html':path.slice(1));if(!file.startsWith(dist+'/'))return json(res,{},404);const bytes=await readFile(file),types={'.html':'text/html','.js':'text/javascript','.css':'text/css'};res.writeHead(200,{'content-type':types[extname(file)]||'application/octet-stream'});res.end(bytes);}catch{json(res,{},404);}
});
server.listen(Number(process.env.HS_SKILLS_QA_PORT||8030),'127.0.0.1',()=>process.stdout.write('Skills UI fixture ready on 8030\n'));
