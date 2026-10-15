import assert from 'node:assert/strict';
import test from 'node:test';
import {CaptureFiles} from '../src/domain/project-capture';
test('discard deletes only registered app copies and preserves camera/gallery originals',async()=>{
 const deleted:string[]=[];const registry=new CaptureFiles(async uri=>{deleted.push(uri);});registry.add('file:///app/cache/copied.pdf');registry.add('file:///app/cache/voice.m4a');
 await registry.release('file:///gallery/original.jpg');await registry.release('file:///camera/photo.jpg');await registry.release('file:///app/cache/copied.pdf');await registry.clear();
 assert.deepEqual(deleted,['file:///app/cache/copied.pdf','file:///app/cache/voice.m4a']);
});
test('failed deletion remains registered for logout retry',async()=>{
 let failed=true;const deleted:string[]=[];const registry=new CaptureFiles(async uri=>{if(failed)throw new Error('busy');deleted.push(uri);});registry.add('file:///app/cache/voice.m4a');await assert.rejects(registry.clear());failed=false;await registry.clear();assert.deepEqual(deleted,['file:///app/cache/voice.m4a']);
});
test('repeated discard and logout cleanup delete owned copies only once',async()=>{
 const deleted:string[]=[];const registry=new CaptureFiles(async uri=>{deleted.push(uri);});registry.add('file:///app/cache/voice.m4a');await registry.release('file:///app/cache/voice.m4a');await registry.release('file:///app/cache/voice.m4a');await registry.clear();assert.deepEqual(deleted,['file:///app/cache/voice.m4a']);
});
test('simultaneous upload completion and screen cleanup do not race deletion',async()=>{
 let finish!:()=>void;const pending=new Promise<void>(resolve=>{finish=resolve;});const deleted:string[]=[];const registry=new CaptureFiles(async uri=>{deleted.push(uri);await pending;});registry.add('file:///app/cache/voice.m4a');const a=registry.release('file:///app/cache/voice.m4a'),b=registry.clear();finish();await Promise.all([a,b]);assert.deepEqual(deleted,['file:///app/cache/voice.m4a']);
});
