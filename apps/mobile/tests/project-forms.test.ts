import assert from 'node:assert/strict';
import test from 'node:test';
import { projectPayload, rightsPayload, factPayload, validatePickedFile } from '../src/domain/project-forms';

const draft = {name:' 云境 ',city:' 上海 ',property_type:'大平层',owner_name:'',audience:'',sales_owner_name:'',channel:'抖音',marketing_confirmed:true,marketing_evidence:' 开发商授权 '};
test('editor metadata changes omit review-only authorization fields', () => {
  const body = projectPayload(draft, false);
  assert.equal(body.name,'云境'); assert.equal('marketing_confirmed' in body,false); assert.equal('marketing_evidence' in body,false);
});
test('marketing authorization cannot be confirmed without evidence', () => {
  assert.throws(()=>projectPayload({...draft,marketing_evidence:' '},true),/授权依据/);
});
test('revoking rights always removes model-processing permission',()=>{
  assert.deepEqual(rightsPayload('revoked',' 授权书 ',true,false),{rights:'revoked',evidence:'授权书',model_processing_authorized:false});
});
test('authorizing file requires explicit evidence and confirmation',()=>{
  assert.throws(()=>rightsPayload('authorized','',false,true),/依据/);
  assert.throws(()=>rightsPayload('authorized','授权书',false,false),/确认/);
});
test('fact validity is explicit China time and never silently normalizes impossible dates',()=>{
  assert.equal(factPayload({value:'180',unit:'㎡',source:'楼书 8 页',valid_until:'2026-10-20 18:00'}).valid_until,'2026-10-20T10:00:00.000Z');
  assert.throws(()=>factPayload({value:'180',unit:'㎡',source:'楼书',valid_until:'2026-02-30 18:00'}),/有效期/);
});
test('asset validation rejects empty and oversized files before upload',()=>{
  assert.throws(()=>validatePickedFile({uri:'file:///x',name:'x',mimeType:'image/jpeg',size:0}),/空文件/);
  assert.throws(()=>validatePickedFile({uri:'file:///x',name:'x',mimeType:'image/jpeg',size:104857601}),/100MB/);
  assert.doesNotThrow(()=>validatePickedFile({uri:'file:///x',name:'x',mimeType:'image/jpeg',size:104857600}));
});
