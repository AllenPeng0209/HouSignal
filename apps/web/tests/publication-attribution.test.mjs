import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
let feature,dir;
before(async()=>{dir=await mkdtemp(join(tmpdir(),'publication-attribution-'));const result=await build({stdin:{contents:existsSync('src/PublicationAttribution.tsx')?"export * from './PublicationAttribution';":"export const PublicationAttribution=()=>null;",resolveDir:resolve('src'),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react/jsx-runtime']});const text=result.outputFiles[0].text.replaceAll('from "react"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/index.js')).href)}`).replaceAll('from "react/jsx-runtime"',`from ${JSON.stringify(pathToFileURL(resolve('node_modules/react/jsx-runtime.js')).href)}`);await writeFile(join(dir,'feature.mjs'),text);feature=await import(pathToFileURL(join(dir,'feature.mjs')).href);});
after(async()=>{await rm(dir,{recursive:true,force:true});});
const render=props=>renderToStaticMarkup(React.createElement(feature.PublicationAttribution,{publications:[{id:'video-a',title:'客厅实拍'}],onRecord(){},...props}));
// Catches inventing zero CRM outcomes when an old or failed API did not return attribution.
test('missing attribution stays unavailable instead of being shown as zero',()=>{const html=render({});assert.match(html,/尚未取得作品关联记录/);assert.doesNotMatch(html,/<td>0<\/td>/);});
// Catches mixing platform window counts with cumulatively linked, self-reported CRM records.
test('linked CRM counts and source confidence remain explicit and separate from platform metrics',()=>{const html=render({rows:[{publication_id:'video-a',linked_inquiries:3,qualified_customers:1,source_confidence_counts:{self_reported:2,unknown:1}}]});assert.match(html,/客厅实拍/);assert.match(html,/<td>3<\/td>/);assert.match(html,/<td>1<\/td>/);assert.match(html,/客户自报 2/);assert.match(html,/来源待确认 1/);assert.match(html,/累计关联/);assert.match(html,/录入咨询/);});
