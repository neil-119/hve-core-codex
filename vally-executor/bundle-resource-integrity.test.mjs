import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { captureBundleResources } from './bundle-resource-integrity.mjs';
const hash=value=>createHash('sha256').update(value).digest('hex');
async function fixture(run) {
  const root=await mkdtemp(join(tmpdir(),'vally-resource-integrity-'));
  try {
    await mkdir(join(root,'fixture')); await writeFile(join(root,'fixture','input.txt'),'original');
    await run(root,{fixture:{outputs:[{path:'fixture',status:'manifest-resource-directory'},{path:'fixture/input.txt',status:'manifest-resource-copy',sha256:hash('original')}]}});
  } finally {await rm(root,{recursive:true,force:true});}
}
test('resource hashes cover exact fixture contents and reject altered bytes',async()=>fixture(async(root,map)=>{
  const records=await captureBundleResources(root,map);
  assert.deepEqual(records,[{path:'fixture/input.txt',bytes:8,sha256:hash('original')}]);
  await writeFile(join(root,'fixture','input.txt'),'modified');
  await assert.rejects(captureBundleResources(root,map),/hash differs/);
}));
test('extra files inside copied directories fail integrity',async()=>fixture(async(root,map)=>{
  await writeFile(join(root,'fixture','injected.txt'),'unexpected');
  await assert.rejects(captureBundleResources(root,map),/undeclared file/);
}));
test('unsafe, conflicting, and unhashed output declarations are rejected',async()=>fixture(async(root,map)=>{
  for(const output of [{path:'../escape',sha256:hash('x')},{path:'fixture/input.txt'},{path:'fixture/input.txt',sha256:hash('conflict')}])
    await assert.rejects(captureBundleResources(root,{...map,extra:{outputs:[output]}}));
}));
test('an injected empty directory is rejected too',async()=>fixture(async(root,map)=>{
  await mkdir(join(root,'fixture','injected'));
  await assert.rejects(captureBundleResources(root,map),/undeclared directory/);
}));
