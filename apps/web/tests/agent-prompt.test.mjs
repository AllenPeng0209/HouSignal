import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const path=resolve('src/agent-prompt.js');
const helpers=existsSync(path)?await import(pathToFileURL(path).href):{stageAgentPrompt:()=>false,consumeAgentPrompt:(_s,_p,d='')=>d};
function storage(){const map=new Map();return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};}
test('a staged account task reaches only its project and is consumed once',()=>{
 const s=storage();helpers.stageAgentPrompt(s,'p1','请校验小红书账号');
 assert.equal(helpers.consumeAgentPrompt(s,'p2'),'');
 assert.equal(helpers.consumeAgentPrompt(s,'p1'),'请校验小红书账号');
 assert.equal(helpers.consumeAgentPrompt(s,'p1'),'');
});
test('account handoff preserves a conversation draft and rejects oversized prompts',()=>{
 const s=storage();helpers.stageAgentPrompt(s,'p1','账号登录情况');
 assert.equal(helpers.consumeAgentPrompt(s,'p1','原来的草稿'),'原来的草稿\n\n账号登录情况');
 assert.equal(helpers.stageAgentPrompt(s,'p1','x'.repeat(2001)),false);
 assert.equal(helpers.consumeAgentPrompt(s,'p1'),'');
});
