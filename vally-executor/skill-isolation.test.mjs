import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveSkillIsolation, VALLY_AMBIENT_CONFIG_ARGS } from './skill-isolation.mjs';

async function stagedSkill() {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-skill-isolation-'));
  const workDir = join(root, 'workspace');
  const skillPath = join(workDir, '.agents', 'skills', 'selected-skill', 'SKILL.md');
  const rawContent = '---\nname: selected-skill\ndescription: Selected fixture skill\n---\nUse only this skill.\n';
  await mkdir(join(workDir, '.agents', 'skills', 'selected-skill'), { recursive: true });
  await writeFile(skillPath, rawContent);
  return { root, workDir, skillPath, skills: [{ name: 'selected-skill', rawContent }] };
}

function skill(name, path, scope = 'user', enabled = true) {
  return { name, path, scope, enabled };
}

function fakeClientFactory({ cwd, inventory, onRequest, onMcpRequest, clientRecords }) {
  return (_binary, args, options) => {
    assert.equal(options.cwd, cwd);
    const clientIndex = clientRecords.args.length;
    clientRecords.envs.push(options.env);
    const client = {
      initialized: false,
      async initialize() { this.initialized = true; },
      async request(method, params) {
        assert.equal(this.initialized, true);
        clientRecords.requests.push({ method, params, args: [...args], clientIndex });
        if (method === 'skills/list') return onRequest ? onRequest({ method, params, args, inventory, cwd }) : {
          data: [{ cwd, errors: [], skills: inventory }],
        };
        if (method === 'mcpServerStatus/list') return onMcpRequest ?
          onMcpRequest({ method, params, args, cwd, clientIndex }) : { data: [] };
        throw new Error(`Unexpected request ${method}`);
      },
      async shutdown() { clientRecords.closed++; },
    };
    clientRecords.args.push([...args]);
    return client;
  };
}

test('skill isolation uses bounded read-only catalog probes and verifies exact enabled paths', async () => {
  const fixture = await stagedSkill();
  const external = join(fixture.root, 'outside', 'GLOBAL.md');
  const inventory = [
    skill('selected-skill', fixture.skillPath, 'repo', false),
    skill('host-skill', external, 'user', true),
  ];
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir, inventory, clientRecords: clients,
    onRequest({ method, args }) {
      assert.equal(method, 'skills/list');
      if (!args.some(value => value.startsWith('skills.config='))) return { data: [{ cwd: fixture.workDir, errors: [], skills: inventory }] };
      assert.equal(args[0], '-c');
      assert.ok(args[1].startsWith('skills.config=['));
      const normalize = value => value.replaceAll('\\', '/').toLowerCase();
      const overrides = new Map([...args[1].matchAll(/\{path="([^"]+)",enabled=(true|false)\}/g)]
        .map(match => [normalize(match[1]), match[2] === 'true']));
      return { data: [{ cwd: fixture.workDir, errors: [], skills: inventory.map(value => ({
        ...value, enabled: overrides.get(normalize(value.path)) ?? false,
      })) }] };
    } });
  try {
    const env = { CODEX_TEST_SENTINEL: 'isolated-probe' };
    const result = await resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, env, clientFactory });
    assert.deepEqual(result.expectedPaths, [fixture.skillPath]);
    assert.deepEqual(result.enabledPaths, [fixture.skillPath]);
    assert.equal(result.disabledSkills, 1);
    assert.equal(clients.closed, 2);
    assert.deepEqual(clients.envs, [env, env]);
    assert.deepEqual(clients.requests.map(request => request.method), [
      'skills/list', 'mcpServerStatus/list', 'skills/list', 'mcpServerStatus/list',
    ]);
    assert.deepEqual(clients.requests.filter(request => request.method === 'skills/list').map(request => request.params), [
      { cwds: [fixture.workDir], forceReload: true }, { cwds: [fixture.workDir], forceReload: true },
    ]);
    assert.deepEqual(clients.requests.filter(request => request.method === 'mcpServerStatus/list').map(request => request.params), [
      { limit: 64, detail: 'toolsAndAuthOnly' }, { limit: 64, detail: 'toolsAndAuthOnly' },
    ]);
    assert.ok(clients.args[1][1].includes('enabled=true'));
    assert.ok(clients.args[1][1].includes('enabled=false'));
    assert.deepEqual(clients.args[0], [...VALLY_AMBIENT_CONFIG_ARGS]);
    assert.deepEqual(clients.args[1].slice(2), ['-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS]);
    assert.deepEqual(result.mcpIsolation, { verified: true, disabledServerNames: [], advertisedToolCount: 0 });
    assert.equal(result.exclusiveToolInventoryVerified, false);
    assert.ok(!clients.requests.some(request => /config\/write|thread\/start|turn\/start/.test(request.method)));
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('skill isolation rejects a selected skill absent from native discovery and still closes its probe', async () => {
  const fixture = await stagedSkill();
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir, inventory: [], clientRecords: clients });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, clientFactory }), /not discovered/);
    assert.equal(clients.closed, 1);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('skill isolation rejects config application that leaves an unexpected skill enabled', async () => {
  const fixture = await stagedSkill();
  const inventory = [skill('selected-skill', fixture.skillPath, 'repo'),
    skill('host-skill', join(fixture.root, 'host', 'SKILL.md'), 'system')];
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir, inventory, clientRecords: clients });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, clientFactory }), /expected enabled=false, found true/);
    assert.equal(clients.closed, 2);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('skill isolation hard deadline aborts a hung catalog request and waits for cleanup', async () => {
  const fixture = await stagedSkill();
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const inventory = [skill('selected-skill', fixture.skillPath, 'repo')];
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir, inventory, clientRecords: clients,
    onRequest({ method }) {
      assert.equal(method, 'skills/list');
      return new Promise(() => {});
    } });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 25, clientFactory }), /hard deadline exhausted/);
    assert.equal(clients.closed, 1);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('expired setup budget creates no orphan initialize request during cleanup', async () => {
  const fixture = await stagedSkill();
  let initializeCalls = 0;
  let closed = 0;
  let rejectPending;
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 25,
      clientFactory() {
        // A slow binary verification/spawn can exhaust the budget before the
        // caller attaches its first RPC deadline handler.
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50);
        return {
          initialize() {
            initializeCalls += 1;
            return new Promise((_, reject) => { rejectPending = reject; });
          },
          async request() { throw new Error('An expired probe cannot request catalogs'); },
          async shutdown() {
            closed += 1;
            rejectPending?.(new Error('App-server transport shut down'));
          },
        };
      },
    }), /hard deadline exhausted during Initial skill discovery initialize/);
    assert.equal(initializeCalls, 0);
    assert.equal(closed, 1);
    await new Promise(resolve => setImmediate(resolve));
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('skill isolation rejects Vally skill content that does not match staged bytes', async () => {
  const fixture = await stagedSkill();
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir, inventory: [], clientRecords: clients });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir,
      skills: [{ ...fixture.skills[0], rawContent: 'different' }], timeoutMs: 2_000, clientFactory }), /disagrees with Vally/);
    assert.equal(clients.args.length, 0);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('skill isolation rejects a discovery root junction that escapes the Vally workspace', async t => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-skill-junction-'));
  const workDir = join(root, 'workspace');
  const outsideSkills = join(root, 'outside-skills');
  const skillPath = join(outsideSkills, 'selected-skill', 'SKILL.md');
  const content = '---\nname: selected-skill\ndescription: Fixture\n---\nKeep within workspace.\n';
  await mkdir(join(workDir, '.agents'), { recursive: true });
  await mkdir(join(outsideSkills, 'selected-skill'), { recursive: true });
  await writeFile(skillPath, content);
  try {
    await symlink(outsideSkills, join(workDir, '.agents', 'skills'), 'junction');
  } catch (error) {
    await rm(root, { recursive: true, force: true });
    t.skip(`directory junction creation unavailable: ${error.code ?? error.message}`);
    return;
  }
  let launches = 0;
  try {
    await assert.rejects(resolveSkillIsolation({ workDir,
      skills: [{ name: 'selected-skill', rawContent: content }], timeoutMs: 2_000,
      clientFactory() { launches++; throw new Error('probe must not start'); } }), /outside its workspace/);
    assert.equal(launches, 0);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('MCP isolation paginates both read-only catalogs and appends verified disable overrides', async () => {
  const fixture = await stagedSkill();
  const inventory = [skill('selected-skill', fixture.skillPath, 'repo', false)];
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const onMcpRequest = ({ params, clientIndex }) => {
    const verified = clientIndex === 1;
    if (!params.cursor) return {
      data: [{ name: verified ? 'server-b' : 'server_a', tools: verified ? {} : { first: {} } }],
      nextCursor: 'next-page',
    };
    assert.equal(params.cursor, 'next-page');
    return { data: [{ name: verified ? 'server_a' : 'server-b', tools: verified ? {} : { second: {} } }] };
  };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir, inventory,
    onRequest({ args }) {
      const isVerification = args.some(value => value.startsWith('skills.config=['));
      return { data: [{ cwd: fixture.workDir, errors: [], skills: inventory.map(value => ({
        ...value, enabled: isVerification,
      })) }] };
    }, onMcpRequest, clientRecords: clients });
  try {
    const result = await resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, clientFactory });
    assert.deepEqual(result.mcpIsolation, {
      verified: true, disabledServerNames: ['server_a', 'server-b'], advertisedToolCount: 0,
    });
    assert.equal(result.exclusiveToolInventoryVerified, false);
    assert.deepEqual(clients.args[1].slice(10), [
      '-c', 'mcp_servers.server_a.enabled=false', '-c', 'mcp_servers.server-b.enabled=false',
    ]);
    assert.deepEqual(clients.requests.filter(request => request.method === 'mcpServerStatus/list').map(request => request.params), [
      { limit: 64, detail: 'toolsAndAuthOnly' },
      { limit: 64, detail: 'toolsAndAuthOnly', cursor: 'next-page' },
      { limit: 64, detail: 'toolsAndAuthOnly' },
      { limit: 64, detail: 'toolsAndAuthOnly', cursor: 'next-page' },
    ]);
    assert.equal(clients.closed, 2);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('MCP catalog records are strictly validated', async t => {
  const malformedCatalogs = [
    { label: 'missing data', response: {} },
    { label: 'missing catalog response', response: undefined },
    { label: 'invalid server name', response: { data: [{ name: 'bad/name', tools: {} }] } },
    { label: 'missing tools dictionary', response: { data: [{ name: 'server', description: 'missing tools' }] } },
    { label: 'array tools', response: { data: [{ name: 'server', tools: [] }] } },
    { label: 'duplicate server names', response: { data: [
      { name: 'server', tools: {} }, { name: 'server', tools: {} },
    ] } },
    { label: 'malformed cursor', response: { data: [], nextCursor: '' } },
  ];
  for (const { label, response } of malformedCatalogs) {
    await t.test(label, async () => {
      const fixture = await stagedSkill();
      const clients = { args: [], requests: [], envs: [], closed: 0 };
      const clientFactory = fakeClientFactory({ cwd: fixture.workDir,
        inventory: [skill('selected-skill', fixture.skillPath, 'repo')],
        onMcpRequest() { return response; }, clientRecords: clients });
      try {
        await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
          timeoutMs: 2_000, clientFactory }), /mcp/i);
        assert.equal(clients.closed, 1);
      } finally { await rm(fixture.root, { recursive: true, force: true }); }
    });
  }
});

test('MCP isolation fails closed when server names change after overrides', async () => {
  const fixture = await stagedSkill();
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir,
    inventory: [skill('selected-skill', fixture.skillPath, 'repo')],
    onMcpRequest({ clientIndex }) { return { data: [{ name: clientIndex ? 'different' : 'original', tools: {} }] }; },
    clientRecords: clients });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, clientFactory }), /catalog changed/);
    assert.equal(clients.closed, 2);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('MCP isolation rejects any advertised tool left after the process-local overrides', async () => {
  const fixture = await stagedSkill();
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir,
    inventory: [skill('selected-skill', fixture.skillPath, 'repo')],
    onMcpRequest({ clientIndex }) { return { data: [{ name: 'server', tools: clientIndex ? { stillEnabled: {} } : { tool: {} } }] }; },
    clientRecords: clients });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, clientFactory }), /left 1 advertised tool/);
    assert.equal(clients.closed, 2);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('MCP isolation rejects a repeated cursor and closes the active probe', async () => {
  const fixture = await stagedSkill();
  const clients = { args: [], requests: [], envs: [], closed: 0 };
  const clientFactory = fakeClientFactory({ cwd: fixture.workDir,
    inventory: [skill('selected-skill', fixture.skillPath, 'repo')],
    onMcpRequest({ params }) { return { data: [], nextCursor: params.cursor ?? 'loop' }; },
    clientRecords: clients });
  try {
    await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
      timeoutMs: 2_000, clientFactory }), /repeated a pagination cursor/);
    assert.equal(clients.requests.filter(request => request.method === 'mcpServerStatus/list').length, 2);
    assert.equal(clients.closed, 1);
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('MCP isolation enforces the server and page caps', async t => {
  await t.test('server count', async () => {
    const fixture = await stagedSkill();
    const clients = { args: [], requests: [], envs: [], closed: 0 };
    const tooMany = Array.from({ length: 257 }, (_, index) => ({ name: `server_${index}`, tools: {} }));
    const clientFactory = fakeClientFactory({ cwd: fixture.workDir,
      inventory: [skill('selected-skill', fixture.skillPath, 'repo')],
      onMcpRequest() { return { data: tooMany }; }, clientRecords: clients });
    try {
      await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
        timeoutMs: 2_000, clientFactory }), /exceeds 256/);
      assert.equal(clients.closed, 1);
    } finally { await rm(fixture.root, { recursive: true, force: true }); }
  });

  await t.test('page count', async () => {
    const fixture = await stagedSkill();
    const clients = { args: [], requests: [], envs: [], closed: 0 };
    const clientFactory = fakeClientFactory({ cwd: fixture.workDir,
      inventory: [skill('selected-skill', fixture.skillPath, 'repo')],
      onMcpRequest({ params }) {
        const current = Number((params.cursor ?? 'page-0').replace('page-', ''));
        return { data: [], nextCursor: `page-${current + 1}` };
      }, clientRecords: clients });
    try {
      await assert.rejects(resolveSkillIsolation({ workDir: fixture.workDir, skills: fixture.skills,
        timeoutMs: 2_000, clientFactory }), /exceeds 16 pages/);
      assert.equal(clients.requests.filter(request => request.method === 'mcpServerStatus/list').length, 16);
      assert.equal(clients.closed, 1);
    } finally { await rm(fixture.root, { recursive: true, force: true }); }
  });
});
