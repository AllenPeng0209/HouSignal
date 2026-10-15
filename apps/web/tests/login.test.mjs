import {before,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const logins={};
before(async()=>{
 const appPath=resolve('src/App.tsx');
 for(const [mode,dev] of [['development',true],['production',false]]){
  const bundled=await build({
   stdin:{contents:`export {Login as TestedLogin} from ${JSON.stringify(appPath)};`,resolveDir:process.cwd(),loader:'tsx'},
   write:false,bundle:true,format:'esm',platform:'node',jsx:'automatic',treeShaking:true,
   external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},
   define:{'import.meta.env.DEV':JSON.stringify(dev)},
   plugins:[{name:'in-memory-login-export',setup(builder){
    builder.onResolve({filter:/^\.\/conversation-drafts$/},({resolveDir})=>({path:resolve(resolveDir,'conversation-drafts.ts'),sideEffects:false}));
    builder.onLoad({filter:/\/src\/App\.tsx$/},async({path})=>({loader:'tsx',contents:await readFile(path,'utf8')+'\nexport {Login};',resolveDir:resolve('src')}));
   }}],
  });
  const source=bundled.outputFiles[0].text
   .replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`)
   .replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);
  logins[mode]=(await import('data:text/javascript;base64,'+Buffer.from(source+`\n//# sourceURL=login-render-${mode}.mjs`).toString('base64'))).TestedLogin;
 }
});

// Catches development-only credential shortcuts being shipped in the real login form.
test('development login requires manual credentials and offers no prefilled account action',()=>{
 const html=renderToStaticMarkup(React.createElement(logins.development,{onLogin(){}}));
 assert.equal(html.includes('dev-account'),false,'development login must not expose a credential shortcut');
 assert.equal(html.includes('填入示例账号'),false,'development login must not offer account prefilling');
 assert.equal((html.match(/<button\b/g)||[]).length,1,'only the normal login submit action is available');
});

// Catches removal of the manual login fields or a credential value being prefilled in either build mode.
test('both login modes retain empty required password and username fields with normal submission',()=>{
 for(const mode of ['development','production']){
  const html=renderToStaticMarkup(React.createElement(logins[mode],{onLogin(){}}));
  for(const [type,autocomplete] of [['email','username'],['password','current-password']]){
   const input=html.match(new RegExp(`<input\\b[^>]*type="${type}"[^>]*>`))?.[0];
   assert.ok(input,`${mode} exposes the manual ${type} field`);
   assert.match(input,/required=""/);assert.match(input,/value=""/);
   assert.ok(input.includes(`autoComplete="${autocomplete}"`));
  }
  assert.match(html,/<form\b[^>]*class="login-form"/);
  assert.equal((html.match(/type="submit"/g)||[]).length,1);
  assert.match(html,/登录工作区/);
 }
});
