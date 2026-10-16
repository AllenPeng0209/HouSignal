import {test} from 'node:test';import assert from 'node:assert/strict';
const feature=await import('../src/studio-production.js').catch(()=>({activeContentJob:()=>null,resumableProductionJob:()=>null}));
const render={id:'render-v1',kind:'render_content',status:'running',production_request:{kind:'render_content',content_version_id:'v1'}};
// Catches project-wide rendering locks preventing editing an unrelated content version.
test('a running render locks only its own content version',()=>{assert.equal(feature.activeContentJob([render],'v2'),null);assert.equal(feature.activeContentJob([render],'v1')?.id,'render-v1');assert.equal(feature.activeContentJob([{...render,status:'succeeded'}],'v1'),null);});
// Catches resuming an unrelated version and unexpectedly pulling users away from their work.
test('recovery follows the selected version before an ongoing script generation',()=>{const generating={id:'generate',kind:'generate_content',status:'queued',production_request:{kind:'generate_content',topic_id:'topic'}};assert.equal(feature.resumableProductionJob([render],'v2'),null);assert.equal(feature.resumableProductionJob([generating,render],'v1')?.id,'render-v1');assert.equal(feature.resumableProductionJob([generating,render],'v2')?.id,'generate');});
