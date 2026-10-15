const key=projectId=>`hs-agent-prompt:${projectId}`;
export function stageAgentPrompt(storage,projectId,prompt){
 if(typeof projectId!=='string'||!projectId||typeof prompt!=='string'||!prompt.trim()||prompt.length>2000)return false;
 try{storage.setItem(key(projectId),JSON.stringify({projectId,prompt:prompt.trim()}));return true;}catch{return false;}
}
export function consumeAgentPrompt(storage,projectId,existingDraft=''){
 try{
  const raw=storage.getItem(key(projectId));if(!raw)return existingDraft;
  const entry=JSON.parse(raw);
  if(entry.projectId!==projectId||typeof entry.prompt!=='string'||!entry.prompt.trim()||entry.prompt.length>2000){storage.removeItem(key(projectId));return existingDraft;}
  const combined=existingDraft.trim()?`${existingDraft}\n\n${entry.prompt}`:entry.prompt;
  if(combined.length>2000)return existingDraft;
  storage.removeItem(key(projectId));return combined;
 }catch{return existingDraft;}
}
