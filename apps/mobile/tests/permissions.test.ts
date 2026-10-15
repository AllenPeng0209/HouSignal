import { test } from 'node:test';
import assert from 'node:assert/strict';
import { permissionsFor } from '../src/domain/permissions';
test('editor can maintain facts but cannot produce content', () => {
  assert.deepEqual(permissionsFor({role:'editor',project_ids:null}), {canManage:false,canEdit:false,canEditData:true,canReview:false,canSales:false});
});
test('reviewer can upload and review but cannot generate content', () => {
  assert.deepEqual(permissionsFor({role:'reviewer',project_ids:['p1']}), {canManage:false,canEdit:false,canEditData:true,canReview:true,canSales:false});
});
test('project-scoped administrator does not see organization management', () => {
  assert.equal(permissionsFor({role:'admin',project_ids:['p1']}).canManage,false);
  assert.equal(permissionsFor({role:'admin',project_ids:null}).canManage,true);
  assert.equal(permissionsFor({role:'admin'}).canManage,false);
});
