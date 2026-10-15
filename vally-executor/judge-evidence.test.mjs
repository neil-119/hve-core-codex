import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { join, sep } from 'node:path';
import { createJudgeEvidence, sameFileIdentity } from './judge-evidence.mjs';

async function fixture(content = 'first line\nsecond line\nthird line\n') {
  const cache = join(import.meta.dirname, '.cache');
  await mkdir(cache, { recursive: true });
  const root = await mkdtemp(join(cache, 'hve-judge-evidence-'));
  const workingDirectory = join(root, 'workspace');
  const evidenceDirectory = join(workingDirectory, 'evidence');
  const file = join(evidenceDirectory, 'sample.txt');
  await mkdir(evidenceDirectory, { recursive: true });
  await writeFile(file, content, 'utf8');
  const workspace = { workingDirectory, evidenceFiles: [file] };
  return { root, workingDirectory, evidenceDirectory, file, workspace };
}

test('grep exposes a match beyond a long JSON line prefix without enlarging read budgets', async () => {
  const needle = 'The supplied state records analysis as the active job';
  // The desired excerpt starts between this emoji's UTF-16 code units.
  const fixtureData = await fixture('x'.repeat(9500) + '😀' + 'x'.repeat(499) + needle + 'z'.repeat(5000) + '\n');
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    const output = await evidence.call('grep', { pattern: needle, paths: [fixtureData.file] });
    assert.ok(output.includes(needle), 'the bounded literal match must be visible in this excerpt');
    assert.match(output, /match at char 10002/);
    assert.ok(output.includes('😀'), 'excerpt boundaries must preserve the complete Unicode character');
    assert.ok(output.length < 4000);
    assert.equal(evidence.usage().toolCalls, 1);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('no workspace exposes no evidence tools and denies every read', async () => {
  const evidence = await createJudgeEvidence(undefined);
  assert.deepEqual(evidence.tools, []);
  assert.match(await evidence.call('view', { path: 'C:\\outside.txt' }), /unavailable without a workspace/);
  assert.deepEqual(evidence.usage(), { toolCalls: 0, chars: 0, budgetExhausted: false });
});

test('grep discloses truncated greedy matches and a refined pattern retrieves the late fact', async () => {
  const fixtureData = await fixture('x'.repeat(20000) + 'needle' + 'z'.repeat(4000) + '\n');
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    const broad = await evidence.call('grep', { pattern: '.*needle', paths: [fixtureData.file] });
    assert.match(broad, /matched span through char 20006 omitted in part; refine the pattern/);
    const precise = await evidence.call('grep', { pattern: 'needle', paths: [fixtureData.file] });
    assert.ok(precise.includes('needle'));
    assert.match(precise, /match at char 20001/);
    assert.ok(broad.length < 4000 && precise.length < 4000);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('grep preserves a short match at EOF when its context begins inside a surrogate pair', async () => {
  const fixtureData = await fixture('x'.repeat(10000) + '😀' + 'x'.repeat(1993) + 'needle');
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    const output = await evidence.call('grep', { pattern: 'needle', paths: [fixtureData.file] });
    assert.ok(output.includes('needle'), 'Unicode context adjustment must not cut off the matched fact');
    assert.ok(!output.includes('matched span through'));
    assert.ok(output.length < 4000);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('file snapshots must preserve metadata even when the inode stays the same', () => {
  const snapshot = { dev: 3, ino: 19, size: 100, mtimeMs: 11, ctimeMs: 17 };
  assert.equal(sameFileIdentity(snapshot, { ...snapshot }), true);
  assert.equal(sameFileIdentity(snapshot, { ...snapshot, size: 101 }), false);
  assert.equal(sameFileIdentity(snapshot, { ...snapshot, mtimeMs: 12 }), false);
  assert.equal(sameFileIdentity(snapshot, { ...snapshot, ctimeMs: 18 }), false);
  assert.equal(sameFileIdentity(snapshot, { ...snapshot, ino: 20 }), false);
  assert.equal(sameFileIdentity({ ...snapshot, dev: 0, ino: 0 }, {
    ...snapshot, dev: 0, ino: 0,
  }), true);
});

test('tools expose only exact evidence paths and view returns numbered requested lines', async () => {
  const fixtureData = await fixture();
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    assert.deepEqual(evidence.tools.map(tool => tool.name), ['view', 'grep']);
    assert.deepEqual(evidence.tools[0].inputSchema.properties.path.enum, [fixtureData.file]);
    assert.deepEqual(evidence.tools[1].inputSchema.properties.paths.items.enum, [fixtureData.file]);

    const output = await evidence.call('view', { path: fixtureData.file, view_range: [2, -1] });
    assert.equal(output, `${fixtureData.file}: lines 2-3 of 3\n2: second line\n3: third line`);
    assert.deepEqual(evidence.usage(), { toolCalls: 1, chars: output.length, budgetExhausted: false });
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('unlisted paths, traversal, extra arguments, and malformed ranges are denied before reading', async () => {
  const fixtureData = await fixture();
  const unlisted = join(fixtureData.workingDirectory, 'unlisted.txt');
  const traversal = `${fixtureData.workingDirectory}${sep}evidence${sep}..${sep}evidence${sep}sample.txt`;
  await writeFile(unlisted, 'not evidence', 'utf8');
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    assert.match(await evidence.call('view', { path: unlisted }), /not listed/);
    assert.match(await evidence.call('view', { path: traversal }), /traversal|canonical/);
    assert.match(await evidence.call('view', { path: `${fixtureData.file}${sep}` }), /exactly match/);
    assert.match(await evidence.call('view', { path: fixtureData.file, extra: true }), /Unexpected argument/);
    assert.match(await evidence.call('view', { path: fixtureData.file, view_range: [0, -1] }), /view_range/);
    assert.match(await evidence.call('view', { path: fixtureData.file, view_range: [3, 2] }), /view_range/);
    assert.deepEqual(evidence.usage(), { toolCalls: 0, chars: 0, budgetExhausted: false });

    assert.match(await evidence.call('view', { path: fixtureData.file, view_range: [1, 99] }), /ends after the end/);
    assert.deepEqual(evidence.usage(), { toolCalls: 1, chars: 0, budgetExhausted: false });

    await assert.rejects(createJudgeEvidence({
      workingDirectory: fixtureData.workingDirectory,
      evidenceFiles: [fixtureData.evidenceDirectory],
    }), /regular file/);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('path-chain symlinks introduced after setup are rejected before reading', async t => {
  const fixtureData = await fixture();
  const outside = join(fixtureData.root, 'outside');
  await mkdir(outside, { recursive: true });
  await writeFile(join(outside, 'sample.txt'), 'outside secret', 'utf8');
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    await rm(fixtureData.evidenceDirectory, { recursive: true, force: true });
    try {
      await symlink(outside, fixtureData.evidenceDirectory, process.platform === 'win32' ? 'junction' : 'dir');
    } catch (error) {
      t.skip(`directory symlink creation unavailable: ${error.code ?? error.message}`);
      return;
    }

    assert.match(await evidence.call('view', { path: fixtureData.file }), /symbolic link|junction|alias/);
    assert.deepEqual(evidence.usage(), { toolCalls: 0, chars: 0, budgetExhausted: false });
    await assert.rejects(createJudgeEvidence({
      workingDirectory: fixtureData.workingDirectory,
      evidenceFiles: [fixtureData.file],
    }), /symbolic link|junction|alias/);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('grep requires explicit listed paths and bounds pattern and result sizes', async () => {
  const hugeLine = `match:${'x'.repeat(20_000)}`;
  const fixtureData = await fixture(`${hugeLine}\nother line\n`);
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    assert.match(await evidence.call('grep', { pattern: 'match', paths: [] }), /explicit array/);
    assert.match(await evidence.call('grep', { pattern: 'match', paths: [join(fixtureData.root, 'outside.txt')] }), /not listed/);
    assert.match(await evidence.call('grep', { pattern: 'x'.repeat(257), paths: [fixtureData.file] }), /1 to 256/);
    assert.match(await evidence.call('grep', { pattern: '(', paths: [fixtureData.file] }), /valid regular expression/);
    assert.deepEqual(evidence.usage(), { toolCalls: 0, chars: 0, budgetExhausted: false });

    const output = await evidence.call('grep', { pattern: '^match:', paths: [fixtureData.file] });
    assert.ok(output.length <= 16_000);
    assert.match(output, /line truncated/);
    assert.equal(evidence.usage().toolCalls, 1);
    assert.equal(evidence.usage().chars, output.length);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('total character budget is shared across calls and sets exhaustion only when no evidence fits', async () => {
  const fixtureData = await fixture(`line:${'x'.repeat(20_000)}\n`);
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    const outputs = [];
    for (let index = 0; index < 4; index += 1)
      outputs.push(await evidence.call('view', { path: fixtureData.file }));
    assert.ok(outputs.every(output => output.length === 16_000));
    assert.equal(evidence.usage().chars, 64_000);

    const denied = await evidence.call('view', { path: fixtureData.file });
    assert.match(denied, /character budget is exhausted/);
    assert.deepEqual(evidence.usage(), { toolCalls: 4, chars: 64_000, budgetExhausted: true });
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('concurrent calls share the 12-call budget without exceeding the total character cap', async () => {
  const fixtureData = await fixture('evidence line\n');
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    const outputs = await Promise.all(Array.from({ length: 24 }, () =>
      evidence.call('view', { path: fixtureData.file })));
    assert.equal(outputs.filter(output => output.includes(': lines 1-1 of 1')).length, 12);
    assert.equal(outputs.filter(output => output.includes('call budget of 12')).length, 12);
    assert.deepEqual(evidence.usage(), {
      toolCalls: 12,
      chars: outputs.filter(output => output.includes(': lines 1-1 of 1')).reduce((sum, output) => sum + output.length, 0),
      budgetExhausted: false,
    });
    assert.ok(evidence.usage().chars <= 64_000);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});

test('catastrophic regex search is terminated in a worker and releases the queue', async () => {
  const fixtureData = await fixture(`${'a'.repeat(100_000)}!\nready\n`);
  try {
    const evidence = await createJudgeEvidence(fixtureData.workspace);
    const result = await evidence.call('grep', { pattern: '(a+)+$', paths: [fixtureData.file] });
    assert.match(result, /Regex search exceeded 250 ms/);
    assert.deepEqual(evidence.usage(), { toolCalls: 1, chars: 0, budgetExhausted: false });

    const view = await evidence.call('view', { path: fixtureData.file, view_range: [2, -1] });
    assert.match(view, /2: ready/);
    assert.equal(evidence.usage().toolCalls, 2);
  } finally { await rm(fixtureData.root, { recursive: true, force: true }); }
});
