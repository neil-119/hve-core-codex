import { isAbsolute, resolve, join, relative, sep } from 'node:path';
import { createHash } from 'node:crypto';
import { readFile, realpath } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { ProjectContext, discoverAndLoadEvals, planRun, createDefaultGraderRegistry, needsLlmClient } from '@microsoft/vally';
import { CodexExecutor } from './index.mjs';
import { auditGraderCapabilities } from './grader-capabilities.mjs';

function bundlePath(root, value, label) {
  if (typeof value !== 'string' || !value || isAbsolute(value) || /^[a-z]:/i.test(value) ||
      value.includes('\\') || value.includes('\0'))
    throw new Error(`${label} path is not a safe bundle-relative path`);
  const parts = value.split('/');
  if (parts.some(part => !part || part === '.' || part === '..'))
    throw new Error(`${label} path is not normalized`);
  const rootPath = resolve(root);
  const target = resolve(rootPath, ...parts);
  const fromRoot = relative(rootPath, target);
  if (isAbsolute(fromRoot) || fromRoot === '..' || fromRoot.startsWith(`..${sep}`))
    throw new Error(`${label} path escapes the converted bundle`);
  return target;
}

function pathKey(path) {
  const resolved = resolve(path);
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

async function restrictDiscoveryToDeclaredSpecs(root, preparation, discovered) {
  const mapPathValue = preparation?.source_to_output;
  if (typeof mapPathValue !== 'string' || !mapPathValue) return discovered;
  const mapPath = bundlePath(root, mapPathValue, 'source-to-output map');
  const [canonicalRoot, canonicalMapPath] = await Promise.all([realpath(root), realpath(mapPath)]);
  const mapRelativePath = relative(canonicalRoot, canonicalMapPath);
  if (isAbsolute(mapRelativePath) || mapRelativePath === '..' || mapRelativePath.startsWith(`..${sep}`))
    throw new Error('Vally source-to-output map resolves outside the converted bundle');
  const sourceMap = JSON.parse(await readFile(canonicalMapPath, 'utf8'));
  if (!sourceMap || typeof sourceMap !== 'object' || Array.isArray(sourceMap))
    throw new Error('Vally source-to-output map must be a JSON object');

  // Small hand-built evaluator fixtures predate source maps. Real converted
  // bundles use their exact converted-eval records as the discovery allowlist.
  if (Object.keys(sourceMap).length === 0 &&
      !Number.isSafeInteger(preparation?.summary?.eval_specs_converted)) return discovered;

  const expected = new Map();
  for (const [identity, record] of Object.entries(sourceMap)) {
    const converted = Array.isArray(record?.outputs)
      ? record.outputs.filter(output => output?.status === 'converted-eval-spec')
      : [];
    if (!converted.length) continue;
    if (converted.length !== 1 || converted[0].path !== identity)
      throw new Error(`Vally source map has an invalid converted eval declaration: ${identity}`);
    if (!identity.startsWith('evals/'))
      throw new Error(`Vally converted eval is outside the evals directory: ${identity}`);
    const path = bundlePath(root, identity, 'converted eval spec');
    const key = pathKey(path);
    if (expected.has(key)) throw new Error(`Vally source map repeats a converted eval path: ${identity}`);
    expected.set(key, { path, identity });
  }

  const declaredCount = preparation?.summary?.eval_specs_converted;
  if (Number.isSafeInteger(declaredCount) && declaredCount >= 0 && declaredCount !== expected.size)
    throw new Error(`Vally source map declares ${expected.size} converted eval specs, bundle report declares ${declaredCount}`);

  const loaded = new Map(discovered.evals.map(entry => [pathKey(entry.filePath), entry]));
  const evals = discovered.evals.filter(entry => expected.has(pathKey(entry.filePath)));
  const errors = discovered.errors.filter(error =>
    typeof error?.path === 'string' && expected.has(pathKey(error.path)));
  for (const [key, entry] of expected) {
    if (!loaded.has(key)) errors.push({
      path: entry.identity,
      reason: 'Declared converted eval spec was not discovered or could not be loaded by Vally',
    });
  }
  return { ...discovered, evals, errors };
}

// Use Vally's planner, schema validation and graders; never execute a trial.
export async function buildBundlePlan(root, {
  executor = new CodexExecutor(), graderRegistry, evalNames = [], stimulusNames = [], workspaceRoot,
} = {}) {
  const preparation = await readFile(join(resolve(root), 'generated/vally-bundle-report.json'), 'utf8')
    .then(content => JSON.parse(content)).catch(error => {
      if (error.code === 'ENOENT') return undefined;
      throw error;
    });
  const projectCtx = await ProjectContext.load(resolve(root));
  const rawDiscovery = await discoverAndLoadEvals(projectCtx);
  const discovered = await restrictDiscoveryToDeclaredSpecs(resolve(root), preparation, rawDiscovery);
  const offlineJudge = {
    supportsWorkspaceDelivery: true,
    async judge() { throw new Error('Offline planning must never invoke a judge'); },
    async shutdown() {},
  };
  graderRegistry ??= createDefaultGraderRegistry(offlineJudge);
  for (const [description, names] of [['eval', evalNames], ['stimulus', stimulusNames]]) {
    if (!Array.isArray(names) || names.some(name => typeof name !== 'string' || !name.trim()))
      throw new Error(`Invalid ${description} name selectors`);
  }
  let specs = discovered.evals;
  for (const name of evalNames) {
    const count = specs.filter(entry => entry.spec.name === name).length;
    if (count !== 1) throw new Error(`Eval selector must match exactly one spec: ${name} (${count} matches)`);
  }
  if (evalNames.length) specs = specs.filter(entry => evalNames.includes(entry.spec.name));
  for (const name of stimulusNames) {
    if (!specs.some(entry => entry.spec.stimuli.some(stimulus => stimulus.name === name)))
      throw new Error(`Stimulus selector matched no selected eval: ${name}`);
  }
  if (stimulusNames.length) specs = specs.map(entry => ({ ...entry, spec: { ...entry.spec,
    stimuli: entry.spec.stimuli.filter(stimulus => stimulusNames.includes(stimulus.name)) } }))
    .filter(entry => entry.spec.stimuli.length);
  const excludedStimuli = [];
  specs = specs.map(entry => ({ ...entry, spec: { ...entry.spec,
    stimuli: entry.spec.stimuli.filter(stimulus => {
      const excluded = stimulus.name.startsWith('skill-copilot-otel-metrics-');
      if (excluded) excludedStimuli.push({ eval: entry.spec.name, stimulus: stimulus.name, reason: 'VS Code/Copilot telemetry is outside Codex acceptance scope' });
      return !excluded;
    }) } })).filter(entry => entry.spec.stimuli.length);
  if (!specs.length) throw new Error('Selection contains only out-of-scope VS Code/Copilot telemetry tests');
  const plan = await planRun({ variants: [{ name: 'codex', specs }], projectCtx,
    getExecutor: () => executor, models: ['gpt-6-luna'], judgeModel: 'gpt-6-luna', graderRegistry });
  // Vally's default workspace layout omits eval identity and collides when
  // separate upstream specs reuse stimulus names. Its public trialWorkspace
  // field selects only the host export destination; retain all trial criteria.
  if (workspaceRoot) {
    const identities = new Set();
    for (const item of plan.items) {
      const identity = createHash('sha256').update(item.id).digest('hex');
      if (identities.has(identity)) throw new Error(`Duplicate Vally trial identity: ${item.id}`);
      identities.add(identity);
      item.run.trialWorkspace = join(resolve(workspaceRoot), identity);
    }
  }
  const audits = new Map();
  for (const item of plan.items) {
    const key = JSON.stringify([item.evalFilePath, item.variant, item.stimulus.name]);
    if (!audits.has(key)) audits.set(key, auditGraderCapabilities({ stimuli: [{
      name: item.stimulus.name, graders: item.run.graderConfigs,
    }] }, { evalName: item.evalName, sourcePath: item.evalFilePath }));
  }
  const graderCapabilityAudits = [...audits.values()].filter(audit => audit.findings.length);
  return { projectCtx, discovered, plan, preparation, graderRegistry, graderCapabilityAudits, excludedStimuli };
}

// Use Vally's planner, schema validation and graders; never execute a trial.
export async function inspectBundle(root) {
  const { discovered, plan, preparation, graderRegistry, graderCapabilityAudits, excludedStimuli } = await buildBundlePlan(root);
  const unsupported = [];
  let plannedLlmGraderApplications = 0;
  for (const item of plan.items) {
    if (!item.run.skip) plannedLlmGraderApplications += item.run.graderConfigs.filter(g => needsLlmClient([g], graderRegistry)).length;
    const features = [];
    if (item.stimulus.simulation) features.push('simulation');
    if (item.stimulus.attachments?.length) features.push('attachments');
    if (item.run.maxAgentDurationMs) features.push('recoverable-agent-time-limit');
    const rootEnvironment = plan.evals.find(e => e.evalFilePath === item.evalFilePath && e.variant === item.variant)?.environment;
    if (Object.keys(rootEnvironment?.mcpServers ?? {}).length || Object.keys(item.stimulus.environment?.mcpServers ?? {}).length)
      features.push('MCP-configuration');
    if (features.length) unsupported.push({ eval: item.evalName, stimulus: item.stimulus.name, trial: item.trialIndex, features });
  }
  return { mode: 'vally-offline-plan', modelCalls: 0, excludedStimuli, scannedCount: discovered.scannedCount,
    preparationSucceeded: preparation?.success ?? null,
    discoveryErrors: discovered.errors, diagnostics: plan.diagnostics, evals: plan.evals,
    plannedTrials: plan.totalItems, plannedLlmGraderApplications, unsupported, graderCapabilityAudits, runtimeReady: false,
    runtimeBlockers: ['Offline planning does not establish runtime acceptance; use vally-run --dry-run with the intended judge backend to check executable readiness.'],
    valid: preparation?.success !== false && discovered.errors.length === 0 && !plan.evals.some(e => e.failure) &&
      unsupported.length === 0 && graderCapabilityAudits.length === 0 && plan.totalItems > 0 };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    if (process.argv.length !== 3) throw new Error('Usage: node plan.mjs <converted-bundle>');
    const report = await inspectBundle(process.argv[2]);
    process.stdout.write(JSON.stringify(report, null, 2) + '\n');
    if (!report.valid) process.exitCode = 1;
  } catch (error) { process.stderr.write(error.message + '\n'); process.exitCode = 1; }
}
