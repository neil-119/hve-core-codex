import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, readFile, readdir, realpath, stat } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, parse, relative, resolve, sep, win32 } from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildBundlePlan } from './plan.mjs';
import { captureBundleResources } from './bundle-resource-integrity.mjs';
import { parseConfig } from './index.mjs';

const PROVENANCE_SCHEMA = 'hve.vally-run-provenance';
const PROVENANCE_VERSION = 2;
const MAX_JSON_BYTES = 64 * 1024 * 1024;
const MAX_JSONL_BYTES = 512 * 1024 * 1024;
const MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024;
const MAX_JSONL_RECORDS = 100_000;
const ADAPTER_FILES = [
  'run.mjs', 'plan.mjs', 'index.mjs', 'app-server.mjs', 'app-server-execution.mjs',
  'app-server-events.mjs', 'turn.mjs', 'settlement.mjs', 'skill-isolation.mjs',
  'grader-capabilities.mjs', 'shell-normalization.mjs', 'mcp-config-translator.mjs',
  'bundle-resource-integrity.mjs', 'judge.mjs', 'judge-evidence.mjs',
];
const RUNTIME_MANIFESTS = ['package.json', 'package-lock.json', 'navigation.json'];
const VALLY_PACKAGE_PATH = 'node_modules/@microsoft/vally/package.json';
const VALLY_JS_PREFIX = 'node_modules/@microsoft/vally/dist/';
const MAX_VALLY_JS_FILES = 4096;
const MAX_VALLY_JS_TREE_ENTRIES = 50_000;
const MAX_VALLY_JS_TOTAL_BYTES = 64 * 1024 * 1024;
const MAX_VALLY_JS_FILE_BYTES = 16 * 1024 * 1024;

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object')
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}

function canonicalJson(value) {
  return JSON.stringify(canonical(value));
}

function isWithin(parent, candidate) {
  const rel = relative(parent, candidate);
  if (!rel) return true;
  return !isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`);
}

function overlaps(left, right) {
  const a = process.platform === 'win32' ? left.toLowerCase() : left;
  const b = process.platform === 'win32' ? right.toLowerCase() : right;
  return isWithin(a, b) || isWithin(b, a);
}

function shaValid(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
}

function normalizedRelativePath(root, absolutePath, label) {
  if (typeof absolutePath !== 'string' || !absolutePath) throw new Error(`${label} is missing`);
  const absolute = resolve(absolutePath);
  const rel = relative(root, absolute);
  if (!rel || !isWithin(root, absolute)) throw new Error(`${label} is not inside its declared root`);
  return rel.split(sep).join('/');
}

function sourceRootRelativePath(sourceRoot, absolutePath, label) {
  if (win32.isAbsolute(String(sourceRoot ?? ''))) {
    if (!win32.isAbsolute(String(absolutePath ?? ''))) throw new Error(`${label} is not an absolute Windows path`);
    const rel = win32.relative(win32.resolve(sourceRoot), win32.resolve(absolutePath));
    const normalized = rel.replaceAll('\\', '/');
    if (!normalized || win32.isAbsolute(rel) || normalized === '..' || normalized.startsWith('../'))
      throw new Error(`${label} is not inside its captured source root`);
    return normalized;
  }
  return normalizedRelativePath(resolve(sourceRoot), resolve(String(absolutePath ?? '')), label);
}

function validateRelativePath(path, label) {
  if (typeof path !== 'string' || !path || isAbsolute(path) || /^[a-z]:/i.test(path) || path.includes('\\'))
    throw new Error(`${label} is not a safe normalized relative path`);
  const parts = path.split('/');
  if (parts.some(part => !part || part === '.' || part === '..'))
    throw new Error(`${label} is not a safe normalized relative path`);
  return parts;
}

async function canonicalDirectory(path, label) {
  if (typeof path !== 'string' || !path.trim()) throw new Error(`${label} path is required`);
  const absolute = resolve(path);
  const volume = parse(absolute).root;
  let current = volume;
  for (const component of absolute.slice(volume.length).split(sep).filter(Boolean)) {
    current = join(current, component);
    const info = await lstat(current);
    if (info.isSymbolicLink()) throw new Error(`${label} traverses a symbolic link: ${current}`);
    if (!info.isDirectory()) throw new Error(`${label} contains a non-directory path component: ${current}`);
  }
  const info = await lstat(absolute);
  if (info.isSymbolicLink() || !info.isDirectory()) throw new Error(`${label} must be a regular directory`);
  return await realpath(absolute);
}

async function safeFile(root, relativePath, label, maxBytes = MAX_JSON_BYTES) {
  const parts = validateRelativePath(relativePath, label);
  let candidate = root;
  for (let index = 0; index < parts.length; index++) {
    candidate = join(candidate, parts[index]);
    const info = await lstat(candidate);
    if (info.isSymbolicLink()) throw new Error(`${label} traverses a symbolic link: ${relativePath}`);
    if (index < parts.length - 1 && !info.isDirectory())
      throw new Error(`${label} has a non-directory path component: ${relativePath}`);
    if (index === parts.length - 1 && !info.isFile()) throw new Error(`${label} is not a regular file: ${relativePath}`);
  }
  const canonical = await realpath(candidate);
  if (!isWithin(root, canonical)) throw new Error(`${label} resolves outside its declared root: ${relativePath}`);
  const info = await stat(canonical);
  if (info.size > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte read limit`);
  const bytes = await readFile(canonical);
  if (bytes.length > maxBytes) throw new Error(`${label} exceeded the ${maxBytes}-byte read limit`);
  return { path: relativePath, absolutePath: canonical, bytes: bytes.length, sha256: sha256(bytes), data: bytes };
}

async function safeJson(root, relativePath, label, maxBytes = MAX_JSON_BYTES) {
  const record = await safeFile(root, relativePath, label, maxBytes);
  try {
    record.value = JSON.parse(record.data.toString('utf8'));
  } catch (error) {
    throw new Error(`${label} is not valid JSON: ${error.message}`);
  }
  if (!record.value || typeof record.value !== 'object' || Array.isArray(record.value))
    throw new Error(`${label} must contain a JSON object`);
  return record;
}

function samePath(left, right) {
  if (process.platform === 'win32') return String(left).toLowerCase() === String(right).toLowerCase();
  return left === right;
}

function idKey(value) {
  return process.platform === 'win32' ? value.toLowerCase() : value;
}

function remapStoredItemId(itemId, sourceBundleRoot, currentBundleRoot, label) {
  if (typeof itemId !== 'string' || !itemId) throw new Error(`${label} has no item identity`);
  const separator = itemId.indexOf('::');
  if (separator <= 0) throw new Error(`${label} identity has no Vally item delimiter`);
  const sourcePath = itemId.slice(0, separator);
  const specPath = sourceRootRelativePath(sourceBundleRoot, sourcePath, `${label} source path`);
  const parts = validateRelativePath(specPath, `${label} source path`);
  return `${join(currentBundleRoot, ...parts)}${itemId.slice(separator)}`;
}

function describePlanItem(item, root) {
  const graderConfigs = item.run.graderConfigs ?? [];
  const graderConfigSha256 = sha256(Buffer.from(canonicalJson(graderConfigs)));
  return {
    itemId: item.id,
    evalName: item.evalName,
    specPath: normalizedRelativePath(root, item.evalFilePath, 'planned eval spec path'),
    variant: item.variant,
    model: item.model ?? null,
    stimulusName: item.stimulus.name,
    trialIndex: item.trialIndex ?? 0,
    totalTrials: item.totalTrials,
    grading: {
      graderConfigSha256,
      graderConfigs,
      skipGrade: item.run.skipGrade === true,
      judgeModel: item.run.judgeModel ?? null,
      judgeReasoningEffort: item.run.judgeReasoningEffort ?? null,
      judgeProvider: item.run.judgeProvider ?? null,
      scoringWeights: item.run.scoringWeights ?? null,
    },
  };
}

function normalizedCapturedTrial(trial, sourceBundleRoot, currentBundleRoot) {
  const specPath = sourceRootRelativePath(sourceBundleRoot, String(trial.evalFilePath ?? ''), 'captured trial spec path');
  const mappedId = remapStoredItemId(trial.itemId, sourceBundleRoot, currentBundleRoot, 'captured trial');
  return {
    itemId: mappedId,
    evalName: trial.evalName,
    specPath,
    variant: trial.variant,
    model: trial.model ?? null,
    stimulusName: trial.stimulusName,
    trialIndex: trial.trialIndex ?? 0,
    totalTrials: trial.totalTrials,
    grading: {
      graderConfigSha256: trial.grading?.graderConfigSha256,
      skipGrade: trial.grading?.skipGrade === true,
      judgeModel: trial.grading?.judgeModel ?? null,
      judgeReasoningEffort: trial.grading?.judgeReasoningEffort ?? null,
      judgeProvider: trial.grading?.judgeProvider ?? null,
      scoringWeights: trial.grading?.scoringWeights ?? null,
    },
  };
}

function sameSorted(left, right) {
  return canonicalJson([...left].sort((a, b) => canonicalJson(a).localeCompare(canonicalJson(b)))) ===
    canonicalJson([...right].sort((a, b) => canonicalJson(a).localeCompare(canonicalJson(b))));
}

function check(condition, message, blockers) {
  if (!condition) blockers.push(message);
  return condition;
}

function asComparableEval(evaluation, bundleRoot, sourceBundleRoot) {
  const pathValue = evaluation.evalFilePath;
  let specPath;
  if (sourceBundleRoot && pathValue) {
    specPath = sourceRootRelativePath(sourceBundleRoot, pathValue, 'reported summary eval path');
  } else {
    specPath = normalizedRelativePath(bundleRoot, pathValue, 'reported summary eval path');
  }
  return {
    evalName: evaluation.evalName ?? evaluation.name,
    specPath,
    variant: evaluation.variant,
    model: evaluation.model ?? null,
    passed: evaluation.passed === true,
    hadExecutionErrors: evaluation.hadExecutionErrors ?? false,
    overallScore: evaluation.overallScore ?? null,
    threshold: evaluation.threshold ?? null,
    scoringApplied: evaluation.scoringApplied === true,
    stimuliRun: evaluation.stimuliRun ?? 0,
    stimuliTotal: evaluation.stimuliTotal ?? 0,
    stimuliSkipped: evaluation.stimuliSkipped ?? 0,
    skippedStimuli: evaluation.skippedStimuli ?? [],
  };
}

function compareSummaryEval(left, right) {
  const fields = ['evalName', 'specPath', 'variant', 'model', 'passed', 'hadExecutionErrors', 'threshold',
    'scoringApplied', 'stimuliRun', 'stimuliTotal', 'stimuliSkipped', 'skippedStimuli'];
  for (const field of fields) if (canonicalJson(left[field]) !== canonicalJson(right[field])) return false;
  if (left.overallScore === null || right.overallScore === null) return left.overallScore === right.overallScore;
  return Number.isFinite(left.overallScore) && Number.isFinite(right.overallScore) &&
    Math.abs(left.overallScore - right.overallScore) <= 1e-12;
}

function summarizeCompare(actual, expected, blockers, label, bundleRoot, sourceBundleRoot) {
  check(actual && typeof actual === 'object', `${label} is missing`, blockers);
  if (!actual || !expected) return;
  check(actual.passed === expected.passed, `${label} pass verdict disagrees with Vally's recomputed score`, blockers);
  check(actual.hadExecutionErrors === expected.hadExecutionErrors,
    `${label} execution-error verdict disagrees with Vally's recomputed results`, blockers);
  const actualEvals = actual.evals ?? [];
  const expectedEvals = expected.evals ?? [];
  check(actualEvals.length === expectedEvals.length, `${label} eval count disagrees with Vally's recomputed score`, blockers);
  const actualNormalized = actualEvals.map(evaluation => asComparableEval(evaluation, bundleRoot, sourceBundleRoot));
  const expectedNormalized = expectedEvals.map(evaluation => asComparableEval(evaluation, bundleRoot, null));
  const findByKey = values => new Map(values.map(value => [`${value.evalName}\0${value.specPath}\0${value.variant}\0${value.model}`, value]));
  const left = findByKey(actualNormalized);
  const right = findByKey(expectedNormalized);
  check(left.size === actualNormalized.length && right.size === expectedNormalized.length,
    `${label} contains duplicate eval summaries`, blockers);
  for (const [key, expectedEval] of right) {
    const actualEval = left.get(key);
    check(!!actualEval, `${label} is missing eval summary ${expectedEval.evalName}`, blockers);
    if (actualEval) check(compareSummaryEval(actualEval, expectedEval),
      `${label} scoring fields disagree for ${expectedEval.evalName}`, blockers);
  }
}

async function *jsonlRecords(root, relativePath) {
  const parts = validateRelativePath(relativePath, 'Vally results JSONL');
  let candidate = root;
  for (let index = 0; index < parts.length; index++) {
    candidate = join(candidate, parts[index]);
    const info = await lstat(candidate);
    if (info.isSymbolicLink()) throw new Error('Vally results JSONL traverses a symbolic link');
    if (index < parts.length - 1 && !info.isDirectory()) throw new Error('Vally results JSONL path has a non-directory component');
    if (index === parts.length - 1 && !info.isFile()) throw new Error('Vally results JSONL is not a regular file');
  }
  const canonical = await realpath(candidate);
  if (!isWithin(root, canonical)) throw new Error('Vally results JSONL resolves outside the run directory');
  const size = (await stat(canonical)).size;
  if (size > MAX_JSONL_BYTES) throw new Error(`Vally results JSONL exceeds ${MAX_JSONL_BYTES} bytes`);
  let total = 0;
  let recordCount = 0;
  let pending = Buffer.alloc(0);
  const parseLine = line => {
    if (!line.length) return undefined;
    if (line.length > MAX_JSONL_LINE_BYTES) throw new Error(`Vally results JSONL line exceeds ${MAX_JSONL_LINE_BYTES} bytes`);
    let row;
    try { row = JSON.parse(line.toString('utf8')); }
    catch (error) { throw new Error(`Vally results JSONL contains malformed JSON: ${error.message}`); }
    if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('Vally results JSONL record must be an object');
    recordCount++;
    if (recordCount > MAX_JSONL_RECORDS) throw new Error(`Vally results JSONL exceeds ${MAX_JSONL_RECORDS} records`);
    return row;
  };
  for await (const chunk of createReadStream(canonical)) {
    total += chunk.length;
    if (total > MAX_JSONL_BYTES) throw new Error(`Vally results JSONL exceeds ${MAX_JSONL_BYTES} bytes`);
    const combined = pending.length ? Buffer.concat([pending, chunk]) : chunk;
    let offset = 0;
    while (true) {
      const newline = combined.indexOf(0x0a, offset);
      if (newline < 0) break;
      const row = parseLine(combined.subarray(offset, newline));
      if (row !== undefined) yield row;
      offset = newline + 1;
    }
    pending = Buffer.from(combined.subarray(offset));
    if (pending.length > MAX_JSONL_LINE_BYTES) throw new Error(`Vally results JSONL line exceeds ${MAX_JSONL_LINE_BYTES} bytes`);
  }
  const final = parseLine(pending);
  if (final !== undefined) yield final;
}

async function parseJsonlResults(runRoot) {
  const trials = [];
  const summaries = [];
  for await (const row of jsonlRecords(runRoot, 'results.jsonl')) {
    if (row.type === 'trial-result') {
      const trajectory = row.trajectory && typeof row.trajectory === 'object' ? {
        id: row.trajectory.id,
        metadata: { completedAt: row.trajectory.metadata?.completedAt },
        metrics: row.trajectory.metrics,
      } : undefined;
      trials.push({
        itemId: row.itemId,
        evalName: row.evalName,
        evalFilePath: row.evalFilePath,
        variant: row.variant,
        model: row.model,
        stimulus: row.stimulus,
        trialIndex: row.trialIndex ?? 0,
        totalTrials: row.totalTrials ?? 1,
        status: row.status,
        gradeResult: row.gradeResult ?? null,
        trajectory,
        durationMs: row.durationMs ?? 0,
        error: row.error,
        skipReason: row.skipReason,
      });
    } else if (row.type === 'run-summary') summaries.push(row);
  }
  return { trials, summaries };
}

async function verifyBundleSources(bundleRoot, provenance, expectedUpstreamSha, blockers) {
  const source = provenance.source;
  const bundleReport = await safeJson(bundleRoot, 'generated/vally-bundle-report.json', 'converted bundle report');
  check(source.bundleReport?.path === 'generated/vally-bundle-report.json', 'provenance bundle report path is unexpected', blockers);
  check(source.bundleReport?.sha256 === bundleReport.sha256, 'actual bundle report hash disagrees with run provenance', blockers);
  check(source.bundleReport?.bytes === bundleReport.bytes, 'actual bundle report byte count disagrees with run provenance', blockers);
  const reportValue = bundleReport.value;
  check(reportValue.schema_version === 1 && reportValue.status === 'complete' && reportValue.success === true,
    'actual converted bundle report is not complete and successful', blockers);
  const pin = reportValue.source ?? {};
  const actualPin = source.declaredSourcePin;
  check(actualPin && actualPin.manifest === pin.manifest && actualPin.manifestSha256 === pin.manifest_sha256,
    'run provenance source manifest declaration differs from the actual bundle report', blockers);
  check(actualPin?.sourceInventory?.path === pin.source_inventory?.path &&
    actualPin?.sourceInventory?.sha256 === pin.source_inventory?.sha256 &&
    actualPin?.sourceInventory?.upstreamSha === pin.source_inventory?.upstream_sha &&
    actualPin?.sourceInventory?.upstreamRef === pin.source_inventory?.upstream_ref &&
    actualPin?.sourceInventory?.upstreamUrl === pin.source_inventory?.upstream_url &&
    actualPin?.sourceInventory?.channel === pin.source_inventory?.channel,
    'run provenance source inventory declaration differs from the actual bundle report', blockers);
  check(actualPin?.sourceToOutput === reportValue.source_to_output,
    'run provenance source-map path differs from the actual bundle report', blockers);
  check(actualPin?.customDeclaration?.root_name === pin.custom?.root_name || (!actualPin?.customDeclaration && !pin.custom),
    'run provenance custom source declaration differs from the actual bundle report', blockers);
  if (actualPin?.customDeclaration || pin.custom)
    check(canonicalJson(actualPin?.customDeclaration) === canonicalJson(pin.custom),
      'run provenance custom file declarations differ from the actual bundle report', blockers);

  for (const [label, declaration, captured] of [
    ['source manifest', pin.manifest, source.manifest],
    ['source inventory', pin.source_inventory?.path, source.sourceInventory],
    ['source-to-output map', reportValue.source_to_output, source.sourceToOutputMap],
  ]) {
    if (!declaration || !captured) {
      blockers.push(`${label} declaration or captured hash is missing`);
      continue;
    }
    const actual = await safeFile(bundleRoot, declaration, label);
    check(captured.path === declaration, `${label} path disagrees with run provenance`, blockers);
    check(captured.sha256 === actual.sha256, `actual ${label} hash disagrees with run provenance`, blockers);
    check(captured.bytes === actual.bytes, `actual ${label} byte count disagrees with run provenance`, blockers);
    if (captured.declaredSha256 !== undefined)
      check(captured.declaredSha256 === actual.sha256, `${label} does not match its captured declared hash`, blockers);
    if (label === 'source manifest') {
      check(pin.manifest_sha256 === actual.sha256, 'actual source manifest hash disagrees with bundle report declaration', blockers);
      try {
        const parsed = JSON.parse(actual.data.toString('utf8'));
        check(parsed && typeof parsed === 'object' && !Array.isArray(parsed), 'source manifest is not a JSON object', blockers);
      } catch (error) { blockers.push(`source manifest is not valid JSON: ${error.message}`); }
    }
    if (label === 'source inventory') {
      check(pin.source_inventory?.sha256 === actual.sha256, 'actual source inventory hash disagrees with bundle report declaration', blockers);
      let inventory;
      try { inventory = JSON.parse(actual.data.toString('utf8')); }
      catch (error) { blockers.push(`source inventory is not valid JSON: ${error.message}`); }
      if (inventory && typeof inventory === 'object' && !Array.isArray(inventory)) {
        for (const key of ['upstream_sha', 'upstream_ref', 'upstream_url', 'channel'])
          check(inventory[key] === pin.source_inventory?.[key], `source inventory ${key} differs from bundle report`, blockers);
        check(String(inventory.upstream_sha ?? '').toLowerCase() === String(expectedUpstreamSha ?? '').toLowerCase(),
          'actual source inventory upstream SHA does not match the approved source SHA', blockers);
      }
    }
    if (label === 'source-to-output map') {
      check(captured.parsed === true, 'captured source-to-output map was not parsed at run time', blockers);
      try {
        const parsed = JSON.parse(actual.data.toString('utf8'));
        check(parsed && typeof parsed === 'object' && !Array.isArray(parsed), 'source-to-output map is not a JSON object', blockers);
      } catch (error) { blockers.push(`source-to-output map is not valid JSON: ${error.message}`); }
    }
  }

  check(String(pin.source_inventory?.upstream_sha ?? '').toLowerCase() === String(expectedUpstreamSha ?? '').toLowerCase(),
    'bundle report upstream SHA does not match the approved source SHA', blockers);
  check(source.verification?.sourcePinValid === true && source.verification?.afterExecutionValid === true,
    'run provenance did not record successful source-pin verification before and after execution', blockers);
  check(source.integrity?.unchanged === true && source.integrity?.changedPaths?.length === 0 && source.integrity?.errors?.length === 0,
    'run provenance reports source changes or an unverified integrity snapshot', blockers);
  const after = source.integrity?.afterExecution;
  check(after && typeof after === 'object', 'run provenance has no after-execution source snapshot', blockers);
  if (after && typeof after === 'object') {
    check(after.bundleReport === bundleReport.sha256, 'after-execution bundle report hash differs', blockers);
    for (const key of ['manifest', 'sourceInventory', 'sourceToOutputMap']) {
      const actualPath = key === 'manifest' ? pin.manifest : key === 'sourceInventory' ? pin.source_inventory?.path : reportValue.source_to_output;
      const actualRecord = key === 'manifest' ? source.manifest : key === 'sourceInventory' ? source.sourceInventory : source.sourceToOutputMap;
      check(after[key]?.path === actualPath && after[key]?.sha256 === actualRecord?.sha256 && after[key]?.bytes === actualRecord?.bytes,
        `after-execution ${key} snapshot differs from the independently read source`, blockers);
    }
  }

  const sourceMapRecord = await safeJson(bundleRoot, reportValue.source_to_output, 'Vally source map');
  const sourceMap = sourceMapRecord.value;
  try {
    const resources = await captureBundleResources(bundleRoot, sourceMap);
    check(Array.isArray(source.bundleResources) && canonicalJson(source.bundleResources) === canonicalJson(resources),
      'bundle resource files differ from run provenance', blockers);
    check(Array.isArray(after?.bundleResources) && canonicalJson(after.bundleResources) === canonicalJson(resources),
      'after-execution bundle resource files differ from independently verified files', blockers);
  } catch (error) { blockers.push(`bundle resource integrity verification failed: ${error.message}`); }
  const capturedSpecs = source.specFiles;
  check(Array.isArray(capturedSpecs) && capturedSpecs.length > 0, 'provenance has no full-bundle converted eval spec hashes', blockers);
  const capturedMapEntries = Array.isArray(source.sourceMapEntries) ? source.sourceMapEntries : [];
  check(capturedMapEntries.length === capturedSpecs?.length, 'provenance does not capture every eval source-map record', blockers);
  const sourceArchives = new Map((source.sourceArchives ?? []).map(record => [record.path, record]));
  const capturedMapByPath = new Map(capturedMapEntries.map(record => [record.sourcePath, record]));
  const specPaths = new Set();
  for (const spec of capturedSpecs ?? []) {
    const logicalPath = spec.path;
    if (typeof logicalPath !== 'string' || specPaths.has(logicalPath)) {
      blockers.push('provenance eval spec paths are missing or duplicated');
      continue;
    }
    specPaths.add(logicalPath);
    const actualSpec = await safeFile(bundleRoot, logicalPath, 'converted eval spec');
    check(actualSpec.sha256 === spec.sha256 && actualSpec.bytes === spec.bytes,
      `converted eval spec hash/size differs from run provenance: ${logicalPath}`, blockers);
    const mapping = sourceMap[logicalPath];
    const capturedMapping = capturedMapByPath.get(logicalPath);
    check(mapping && Array.isArray(mapping.outputs), `actual Vally source map is missing eval spec ${logicalPath}`, blockers);
    if (!mapping || !Array.isArray(mapping.outputs)) continue;
    const origin = mapping.origin === 'codex-custom' ? 'codex-custom' : 'upstream';
    check(spec.origin === origin, `eval source origin differs from actual source map: ${logicalPath}`, blockers);
    const mapProjection = {
      sourcePath: logicalPath,
      origin: mapping.origin ?? 'upstream',
      outputs: mapping.outputs.map(output => ({
        path: output.path,
        status: output.status,
        ...(output.sha256 ? { sha256: output.sha256 } : {}),
      })),
    };
    check(canonicalJson(capturedMapping) === canonicalJson(mapProjection),
      `captured Vally source-map entry differs from actual map: ${logicalPath}`, blockers);
    const converted = mapping.outputs.filter(output => output.path === logicalPath && output.status === 'converted-eval-spec');
    check(converted.length === 1 && converted[0].sha256 === actualSpec.sha256,
      `source map does not uniquely bind converted spec bytes: ${logicalPath}`, blockers);
    const originalPrefix = origin === 'codex-custom' ? 'custom/' : 'upstream/';
    const originals = mapping.outputs.filter(output => output.status === 'preserved-original' &&
      typeof output.path === 'string' && output.path.startsWith(originalPrefix));
    check(originals.length === 1, `source map does not uniquely bind the preserved original: ${logicalPath}`, blockers);
    if (originals.length === 1) {
      const original = originals[0];
      const archive = await safeFile(bundleRoot, original.path, 'preserved original eval source');
      const capturedArchive = sourceArchives.get(original.path);
      check(original.sha256 === archive.sha256 && capturedArchive?.sha256 === archive.sha256 &&
        capturedArchive?.bytes === archive.bytes && capturedArchive?.sourcePath === logicalPath &&
        capturedArchive?.origin === origin && capturedArchive?.verified === true,
        `preserved original eval source is not bound by source-map and provenance hashes: ${logicalPath}`, blockers);
      if (capturedArchive?.declaredSha256 !== undefined)
        check(capturedArchive.declaredSha256 === archive.sha256, `captured original source hash differs: ${logicalPath}`, blockers);
    }
  }
  const customFiles = Array.isArray(pin.custom?.files) ? pin.custom.files : [];
  const customArchives = source.customArchives ?? [];
  const customCapturedByPath = new Map(customArchives.map(record => [record.path, record]));
  for (const declaration of customFiles) {
    const identity = declaration.path;
    const archivePath = `custom/${identity}`;
    const actual = await safeFile(bundleRoot, archivePath, 'custom original source archive');
    const captured = customCapturedByPath.get(archivePath);
    check(actual.sha256 === declaration.sha256 && captured?.sha256 === actual.sha256 &&
      captured?.verified === true && captured?.customSourceDeclaration === true,
      `custom original source archive is not bound to its declaration: ${identity}`, blockers);
  }
  return {
    upstreamSha: pin.source_inventory?.upstream_sha ?? null,
    bundleReportSha256: bundleReport.sha256,
    sourceInventorySha256: source.sourceInventory?.sha256 ?? null,
    manifestSha256: source.manifest?.sha256 ?? null,
    sourceMapSha256: source.sourceToOutputMap?.sha256 ?? null,
    sourceSpecs: capturedSpecs?.length ?? 0,
  };
}

async function verifyRuntimeAdapters(adapterRoot, source, blockers) {
  const expectedAdapterRecords = source.runtimeAdapters;
  const expectedManifestRecords = source.runtimeManifests;
  check(Array.isArray(expectedAdapterRecords), 'run provenance runtime adapter hashes are missing', blockers);
  check(Array.isArray(expectedManifestRecords), 'run provenance runtime manifest hashes are missing', blockers);
  const adapterPaths = new Set((expectedAdapterRecords ?? []).map(record => record.path));
  const manifestPaths = new Set((expectedManifestRecords ?? []).map(record => record.path));
  check(adapterPaths.size === ADAPTER_FILES.length && ADAPTER_FILES.every(name => adapterPaths.has(`adapter/${name}`)),
    'run provenance does not contain the exact approved runtime adapter source set', blockers);
  check(manifestPaths.size === RUNTIME_MANIFESTS.length && RUNTIME_MANIFESTS.every(name => manifestPaths.has(`runtime/${name}`)),
    'run provenance does not contain the exact approved runtime manifest set', blockers);
  const captured = [];
  for (const [prefix, names, records] of [
    ['adapter', ADAPTER_FILES, expectedAdapterRecords ?? []],
    ['runtime', RUNTIME_MANIFESTS, expectedManifestRecords ?? []],
  ]) {
    for (const name of names) {
      const path = `${prefix}/${name}`;
      const record = records.find(entry => entry.path === path);
      if (!record) continue;
      const actual = await safeFile(adapterRoot, name, `approved runtime ${name}`);
      check(record.sha256 === actual.sha256 && record.bytes === actual.bytes,
        `approved runtime adapter changed since the run: ${path}`, blockers);
      captured.push({ path, bytes: actual.bytes, sha256: actual.sha256 });
    }
  }
  const dependency = source.runtimeDependency;
  check(dependency?.path === VALLY_PACKAGE_PATH, 'run provenance Vally runtime package path is unexpected', blockers);
  if (dependency?.path === VALLY_PACKAGE_PATH) {
    const actual = await safeJson(adapterRoot, VALLY_PACKAGE_PATH, 'approved Vally runtime package');
    check(actual.sha256 === dependency.sha256 && actual.bytes === dependency.bytes &&
      actual.value.version === dependency.version,
      'approved Vally runtime package differs from the run provenance', blockers);
    captured.push({ path: VALLY_PACKAGE_PATH, bytes: actual.bytes, sha256: actual.sha256, version: actual.value.version });
  }
  const expectedVallyJavaScript = source.runtimeDependencyFiles;
  check(Array.isArray(expectedVallyJavaScript) && expectedVallyJavaScript.length > 0 &&
    expectedVallyJavaScript.length <= MAX_VALLY_JS_FILES,
    'run provenance installed Vally JavaScript hashes are missing or exceed the safe limit', blockers);
  if (Array.isArray(expectedVallyJavaScript) && expectedVallyJavaScript.length > 0 &&
      expectedVallyJavaScript.length <= MAX_VALLY_JS_FILES) {
    const actualFiles = await enumerateVallyRuntimeJavaScript(adapterRoot);
    const expectedPaths = expectedVallyJavaScript.map(record => record.path);
    const orderedPaths = [...expectedPaths].sort((left, right) => String(left) < String(right) ? -1 : String(left) > String(right) ? 1 : 0);
    check(new Set(expectedPaths).size === expectedPaths.length && expectedPaths.every(path =>
      typeof path === 'string' && path.startsWith(VALLY_JS_PREFIX)) &&
      canonicalJson(expectedPaths) === canonicalJson(orderedPaths),
      'run provenance contains duplicate, unsorted, or out-of-scope Vally JavaScript paths', blockers);
    check(sameSorted(expectedVallyJavaScript.map(({ path, bytes }) => ({ path, bytes })), actualFiles),
      'approved Vally runtime JavaScript file set differs from run provenance', blockers);
    for (const record of expectedVallyJavaScript) {
      const actual = await safeFile(adapterRoot, record.path, 'approved Vally runtime JavaScript', MAX_VALLY_JS_FILE_BYTES);
      check(actual.bytes <= MAX_VALLY_JS_FILE_BYTES && actual.sha256 === record.sha256 && actual.bytes === record.bytes,
        `approved Vally runtime JavaScript changed since the run: ${record.path}`, blockers);
      captured.push({ path: record.path, bytes: actual.bytes, sha256: actual.sha256 });
    }
  }
  const after = source.integrity?.afterExecution;
  if (after) {
    for (const field of ['runtimeAdapters', 'runtimeManifests', 'runtimeDependencyFiles']) {
      const values = after[field];
      const current = field === 'runtimeAdapters'
        ? captured.filter(record => record.path.startsWith('adapter/'))
        : field === 'runtimeManifests'
          ? captured.filter(record => record.path.startsWith('runtime/'))
          : captured.filter(record => record.path.startsWith(VALLY_JS_PREFIX));
      check(Array.isArray(values) && sameSorted(values.map(({ path, bytes, sha256: digest }) => ({ path, bytes, sha256: digest })), current.map(({ path, bytes, sha256: digest }) => ({ path, bytes, sha256: digest }))),
        `after-execution ${field} hashes differ from approved runtime files`, blockers);
    }
    check(after.runtimeDependency?.path === VALLY_PACKAGE_PATH && after.runtimeDependency?.sha256 === dependency?.sha256 &&
      after.runtimeDependency?.bytes === dependency?.bytes,
      'after-execution Vally runtime package hash differs from approved runtime', blockers);
  }
  return captured;
}

async function enumerateVallyRuntimeJavaScript(adapterRoot) {
  let distRoot = adapterRoot;
  for (const component of ['node_modules', '@microsoft', 'vally', 'dist']) {
    distRoot = join(distRoot, component);
    const info = await lstat(distRoot);
    if (info.isSymbolicLink() || !info.isDirectory())
      throw new Error(`approved Vally runtime path is not a regular directory: ${distRoot}`);
  }
  const canonicalDistRoot = await realpath(distRoot);
  if (!isWithin(adapterRoot, canonicalDistRoot)) throw new Error('approved Vally dist directory resolves outside the adapter root');
  const files = [];
  let treeEntries = 0;
  let totalBytes = 0;
  const compareText = (left, right) => left < right ? -1 : left > right ? 1 : 0;

  async function visit(directory, depth) {
    if (depth > 64) throw new Error('approved Vally runtime JS directory nesting exceeds the safe limit');
    const entries = await readdir(directory);
    if (entries.length > MAX_VALLY_JS_TREE_ENTRIES || treeEntries + entries.length > MAX_VALLY_JS_TREE_ENTRIES)
      throw new Error(`approved Vally runtime tree exceeds ${MAX_VALLY_JS_TREE_ENTRIES} entries`);
    entries.sort(compareText);
    for (const name of entries) {
      treeEntries++;
      const path = join(directory, name);
      const info = await lstat(path);
      if (info.isSymbolicLink()) throw new Error(`approved Vally runtime tree contains a symbolic link: ${path}`);
      if (info.isDirectory()) {
        await visit(path, depth + 1);
      } else if (name.endsWith('.js')) {
        if (!info.isFile()) throw new Error(`approved Vally runtime JavaScript is not a regular file: ${path}`);
        if (info.size > MAX_VALLY_JS_FILE_BYTES)
          throw new Error(`approved Vally runtime JavaScript exceeds ${MAX_VALLY_JS_FILE_BYTES} bytes: ${path}`);
        if (files.length >= MAX_VALLY_JS_FILES)
          throw new Error(`approved Vally runtime exceeds ${MAX_VALLY_JS_FILES} JavaScript files`);
        totalBytes += info.size;
        if (totalBytes > MAX_VALLY_JS_TOTAL_BYTES)
          throw new Error(`approved Vally runtime JavaScript exceeds ${MAX_VALLY_JS_TOTAL_BYTES} total bytes`);
        const canonicalPath = await realpath(path);
        if (!isWithin(canonicalDistRoot, canonicalPath))
          throw new Error(`approved Vally runtime JavaScript resolves outside dist: ${path}`);
        files.push({
          path: `${VALLY_JS_PREFIX}${relative(canonicalDistRoot, canonicalPath).split(sep).join('/')}`,
          bytes: info.size,
        });
      }
    }
  }

  await visit(canonicalDistRoot, 0);
  files.sort((left, right) => compareText(left.path, right.path));
  if (!files.length) throw new Error('approved Vally runtime contains no dist JavaScript files');
  return files;
}

function makeOfflineExecutor() {
  return {
    name: 'codex',
    supportsPreparedWorkspace: true,
    supportsMultiTurn: true,
    supportsTurnCompletion: true,
    supportsEnvVars: true,
    validateConfig(config) { parseConfig(config); },
    async execute() { throw new Error('Evidence verification cannot execute a trial'); },
    async shutdown() {},
  };
}

async function verifyPlans(bundleRoot, report, provenance, blockers) {
  const executor = makeOfflineExecutor();
  const full = await buildBundlePlan(bundleRoot, { executor });
  check(full.graderCapabilityAudits.length === 0,
    'actual full-bundle graders contain known unverified native event predicates', blockers);
  check(full.preparation?.success === true, 'actual bundle preparation is not successful', blockers);
  check(Array.isArray(full.discovered.errors) && full.discovered.errors.length === 0,
    'actual full-bundle eval discovery reported errors', blockers);
  check(full.plan.evals.every(evaluation => !evaluation.failure) &&
    full.plan.diagnostics.every(diagnostic => diagnostic.severity !== 'error'),
    'actual full-bundle Vally plan contains planning errors', blockers);
  check(full.plan.totalItems > 0 && full.plan.items.length === full.plan.totalItems,
    'actual full-bundle plan is empty or incomplete', blockers);

  const sourceRoot = provenance.source.bundleRoot;
  check(typeof sourceRoot === 'string' && (isAbsolute(sourceRoot) || win32.isAbsolute(sourceRoot)),
    'run provenance source bundle root is missing or not absolute', blockers);
  const bundleCount = provenance.suite?.fullBundle?.plannedTrialCount;
  check(provenance.suite?.fullBundle?.available === true && provenance.suite?.fullBundle?.valid === true &&
    bundleCount === full.plan.totalItems, 'captured full-bundle plan count/validity differs from the actual plan', blockers);
  const expectedFullIds = provenance.suite?.fullBundle?.expectedItemIds;
  check(Array.isArray(expectedFullIds) && expectedFullIds.length === full.plan.totalItems,
    'run provenance does not contain every full-bundle trial identity', blockers);
  if (Array.isArray(expectedFullIds) && typeof sourceRoot === 'string') {
    const remapped = expectedFullIds.map(id => remapStoredItemId(id, sourceRoot, bundleRoot, 'full-bundle trial'));
    check(new Set(remapped.map(idKey)).size === remapped.length, 'captured full-bundle trial identities are duplicated', blockers);
    const actualIds = full.plan.items.map(item => item.id);
    check(sameSorted(remapped.map(idKey), actualIds.map(idKey)),
      'captured full-bundle trial identities differ from Vally re-planning', blockers);
  }

  const evalNames = report.selectedEvals ?? [];
  const stimulusNames = report.selectedStimuli ?? [];
  check(Array.isArray(evalNames) && Array.isArray(stimulusNames), 'run report selectors are malformed', blockers);
  const selected = await buildBundlePlan(bundleRoot, {
    executor,
    graderRegistry: full.graderRegistry,
    evalNames,
    stimulusNames,
  });
  const expectedSelected = provenance.suite?.selected;
  check(expectedSelected?.plannedTrialCount === selected.plan.totalItems &&
    Array.isArray(expectedSelected?.expectedTrials) && expectedSelected.expectedTrials.length === selected.plan.totalItems,
    'captured selected-plan count differs from Vally re-planning', blockers);
  if (Array.isArray(expectedSelected?.expectedTrials) && typeof sourceRoot === 'string') {
    const captured = expectedSelected.expectedTrials.map(trial => normalizedCapturedTrial(trial, sourceRoot, bundleRoot));
    const actual = selected.plan.items.map(item => {
      const descriptor = describePlanItem(item, bundleRoot);
      delete descriptor.grading.graderConfigs;
      return descriptor;
    });
    check(sameSorted(captured, actual), 'captured selected trial identities or grader policies differ from Vally re-planning', blockers);

    // Full-bundle provenance currently stores full-plan item IDs and detailed
    // descriptors for the selected plan. When the selection is the full plan,
    // bind every full-plan identity and grading policy to those descriptors too.
    const fullIds = full.plan.items.map(item => idKey(item.id));
    const selectedIds = selected.plan.items.map(item => idKey(item.id));
    const selectedIsFull = fullIds.length === selectedIds.length &&
      new Set(fullIds).size === fullIds.length && fullIds.every(id => selectedIds.includes(id));
    if (selectedIsFull) {
      const fullDescriptors = full.plan.items.map(item => {
        const descriptor = describePlanItem(item, bundleRoot);
        delete descriptor.grading.graderConfigs;
        return descriptor;
      });
      check(sameSorted(captured, fullDescriptors),
        'captured full-bundle trial identities or grader policies differ from Vally re-planning', blockers);
    }
  }

  const expectedConfigMap = new Map();
  for (const item of selected.plan.items) {
    const descriptor = describePlanItem(item, bundleRoot);
    const digest = descriptor.grading.graderConfigSha256;
    expectedConfigMap.set(digest, { sha256: digest, configs: descriptor.grading.graderConfigs });
    if (item.run.skip || item.run.skipGrade)
      blockers.push(`Vally plan skips execution or grading for ${item.evalName}/${item.stimulus.name}`);
  }
  const actualConfigRows = expectedSelected?.graderConfigurations ?? [];
  check(sameSorted(actualConfigRows, [...expectedConfigMap.values()]),
    'captured grader configurations differ from the original eval specs', blockers);
  const sourceSpecByPath = new Map((provenance.source.specFiles ?? []).map(spec => [spec.path, spec]));
  const discoveredPaths = full.discovered.evals.map(entry => normalizedRelativePath(bundleRoot, entry.filePath ?? entry.spec?.evalFilePath, 'discovered eval spec path'));
  check(new Set(discoveredPaths).size === discoveredPaths.length && sourceSpecByPath.size === discoveredPaths.length &&
    discoveredPaths.every(path => sourceSpecByPath.has(path)),
    'captured full-bundle eval spec identities differ from actual discovery', blockers);
  const selectedByPath = new Map();
  for (const item of selected.plan.items) {
    const specPath = normalizedRelativePath(bundleRoot, item.evalFilePath, 'selected eval spec path');
    const names = selectedByPath.get(specPath) ?? new Set();
    names.add(item.stimulus.name);
    selectedByPath.set(specPath, names);
  }
  for (const [path, spec] of sourceSpecByPath) {
    const discovered = full.discovered.evals.find(entry =>
      normalizedRelativePath(bundleRoot, entry.filePath ?? entry.spec?.evalFilePath, 'discovered eval spec path') === path);
    check(discovered && spec.evalName === discovered.spec.name,
      `captured eval spec name differs from actual discovery: ${path}`, blockers);
    const shouldBeSelected = selectedByPath.has(path);
    check(spec.selected === shouldBeSelected && canonicalJson(spec.selectedStimuli ?? []) ===
      canonicalJson([...(selectedByPath.get(path) ?? [])].sort()),
      `captured eval selection differs from Vally selected plan: ${path}`, blockers);
  }
  return { full, selected };
}

async function verifyRows(runRoot, report, provenance, plans, blockers) {
  const { trials, summaries } = await parseJsonlResults(runRoot);
  const selected = plans.selected.plan.items;
  const expectedById = new Map(selected.map(item => [idKey(item.id), item]));
  const oldRoot = provenance.source.bundleRoot;
  const actualById = new Map();
  const normalizedRows = [];
  for (const row of trials) {
    let normalizedId;
    try { normalizedId = remapStoredItemId(row.itemId, oldRoot, plans.bundleRoot, 'JSONL trial'); }
    catch (error) { blockers.push(error.message); continue; }
    const key = idKey(normalizedId);
    if (actualById.has(key)) blockers.push(`Vally JSONL duplicates trial identity: ${row.itemId}`);
    actualById.set(key, row);
    const item = expectedById.get(key);
    check(!!item, `Vally JSONL contains an unexpected trial identity: ${row.itemId}`, blockers);
    if (item) {
      const expected = describePlanItem(item, plans.bundleRoot);
      const rowDescriptor = {
        itemId: normalizedId,
        evalName: row.evalName,
        specPath: sourceRootRelativePath(oldRoot, row.evalFilePath, 'JSONL eval spec path'),
        variant: row.variant,
        model: row.model ?? null,
        stimulusName: row.stimulus,
        trialIndex: row.trialIndex ?? 0,
        totalTrials: row.totalTrials ?? 1,
      };
      check(canonicalJson({ ...expected, grading: undefined }) === canonicalJson({ ...rowDescriptor, grading: undefined }),
        `Vally JSONL trial identity fields differ from the re-planned source: ${row.itemId}`, blockers);
      const gradePresent = item.run.graderConfigs?.length > 0;
      if (row.status === 'success' && gradePresent)
        check(row.gradeResult && typeof row.gradeResult.passed === 'boolean' && Number.isFinite(row.gradeResult.score),
          `Vally JSONL trial has no valid grade result: ${row.itemId}`, blockers);
      check(row.status === 'success', `Vally JSONL trial did not complete successfully: ${row.itemId} (${row.status})`, blockers);
      check(!item.run.skipGrade && !item.run.skip, `Vally planned skipped grading or execution: ${row.itemId}`, blockers);
    }
    normalizedRows.push({
      ...row,
      normalizedItemId: normalizedId,
      summaryResult: {
        itemId: normalizedId,
        status: row.status,
        grade: row.gradeResult,
        trajectory: row.trajectory,
        durationMs: row.durationMs,
        error: row.error,
        skipReason: row.skipReason,
      },
    });
  }
  const expectedIds = selected.map(item => idKey(item.id));
  const missing = expectedIds.filter(id => !actualById.has(id));
  check(missing.length === 0, `Vally JSONL is missing ${missing.length} selected trial results`, blockers);
  check(trials.length === selected.length, 'Vally JSONL trial-result row count differs from the selected Vally plan', blockers);
  check(summaries.length === 1, 'Vally JSONL must contain exactly one original run-summary record', blockers);
  const runSummary = summaries[0];
  check(runSummary?.passed === true && runSummary?.hadExecutionErrors === false,
    'original Vally run-summary did not pass or reported execution errors', blockers);

  const provenanceTrials = provenance.execution?.actualTrials;
  check(Array.isArray(provenanceTrials), 'run provenance actual trial ledger is missing', blockers);
  if (Array.isArray(provenanceTrials)) {
    const provenanceById = new Map();
    for (const trial of provenanceTrials) {
      const normalized = remapStoredItemId(trial.itemId, oldRoot, plans.bundleRoot, 'provenance trial');
      const key = idKey(normalized);
      if (provenanceById.has(key)) blockers.push(`run provenance duplicates trial identity: ${trial.itemId}`);
      provenanceById.set(key, trial);
    }
    check(provenanceById.size === actualById.size, 'run provenance and Vally JSONL result counts differ', blockers);
    for (const [key, row] of actualById) {
      const saved = provenanceById.get(key);
      check(!!saved, `run provenance is missing Vally JSONL trial ${row.itemId}`, blockers);
      if (!saved) continue;
      const gradePassed = typeof row.gradeResult?.passed === 'boolean' ? row.gradeResult.passed : null;
      check(saved.status === row.status && saved.gradePassed === gradePassed,
        `run provenance status/grade result differs from Vally JSONL: ${row.itemId}`, blockers);
      if (row.error !== undefined) check(saved.error === row.error, `run provenance error differs from Vally JSONL: ${row.itemId}`, blockers);
    }
  }
  const execution = provenance.execution ?? {};
  const successful = trials.filter(trial => trial.status === 'success').length;
  check(execution.expectedResults === selected.length && execution.resultsRecorded === trials.length &&
    execution.successfulResults === successful && execution.failedResults === trials.length - successful,
    'run provenance execution counters differ from Vally JSONL results', blockers);
  check(execution.exactCoverage === (missing.length === 0 && trials.length === selected.length) &&
    execution.allExecutionStatusesSuccessful === (trials.length > 0 && successful === trials.length),
    'run provenance coverage/status flags disagree with actual Vally JSONL rows', blockers);
  check(report.plannedTrials === selected.length && report.completedTrials === trials.length &&
    report.remainingTrials === Math.max(0, selected.length - trials.length),
    'wrapper trial counts differ from actual Vally plan/results', blockers);
  check(report.runtimeInvoked === true && report.status === 'completed' && report.exitCode === 0 && report.reportsFinalized === true,
    'wrapper does not report a completed, finalized, successful runtime execution', blockers);

  const summaryResults = normalizedRows.map(row => row.summaryResult);
  const recomputed = plans.selected.plan.summarize(summaryResults);
  summarizeCompare(runSummary, recomputed, blockers, 'original Vally run-summary', plans.bundleRoot, oldRoot);
  summarizeCompare(report.summary, recomputed, blockers, 'wrapper summary', plans.bundleRoot, oldRoot);
  check(recomputed.passed === true && recomputed.hadExecutionErrors === false,
    'Vally-recomputed scoring does not pass its original thresholds', blockers);
  check(recomputed.evals.every(evaluation => evaluation.passed === true && evaluation.hadExecutionErrors === false &&
    evaluation.stimuliSkipped === 0 && evaluation.stimuliRun === evaluation.stimuliTotal),
    'Vally-recomputed scoring contains a failed or skipped eval/stimulus', blockers);
  return {
    trials,
    normalizedRows,
    runSummary,
    recomputed,
    exactSelectedCoverage: missing.length === 0 && trials.length === selected.length && actualById.size === selected.length,
    allSuccessful: trials.length > 0 && successful === trials.length,
  };
}

/** Read-only, model-free verification of a completed Vally run and its source/runtime pins. */
export async function verifyRunEvidence({ bundle, run, expectedUpstreamSha, approvedAdapterRoot } = {}) {
  const blockers = [];
  const details = {
    mode: 'vally-evidence-verification',
    verified: false,
    fullSuiteExecution: false,
    source: null,
    counts: { fullPlanned: null, selectedPlanned: null, actualResults: null, successfulResults: null },
    scoring: { passed: null, hadExecutionErrors: null, evaluations: 0 },
    blockers,
  };
  let bundleRoot;
  let runRoot;
  let adapterRoot;
  try {
    bundleRoot = await canonicalDirectory(bundle, 'bundle');
    const runArgument = resolve(String(run ?? ''));
    const runInfo = await lstat(runArgument);
    if (runInfo.isSymbolicLink()) throw new Error('run path is a symbolic link');
    if (runInfo.isFile()) {
      if (basename(runArgument) !== 'run-report.json') throw new Error('run file must be named run-report.json');
      runRoot = await canonicalDirectory(dirname(runArgument), 'run directory');
      const reportCanonical = await realpath(runArgument);
      if (!isWithin(runRoot, reportCanonical)) throw new Error('run report resolves outside the run directory');
    } else {
      runRoot = await canonicalDirectory(runArgument, 'run directory');
    }
    adapterRoot = await canonicalDirectory(approvedAdapterRoot, 'approved adapter root');
    if (overlaps(bundleRoot, runRoot)) throw new Error('bundle and run directories must be disjoint');
  } catch (error) {
    blockers.push(error.message);
    return details;
  }

  let report;
  try {
    report = (await safeJson(runRoot, 'run-report.json', 'saved Vally run report')).value;
  } catch (error) {
    blockers.push(error.message);
    return details;
  }
  const provenance = report.runProvenance;
  if (report.mode !== 'vally-run') blockers.push('saved run report is not a Vally run report');
  if (provenance?.schema !== PROVENANCE_SCHEMA || provenance?.version !== PROVENANCE_VERSION)
    blockers.push('saved run report has missing or unsupported source-bound provenance');
  if (!provenance?.source || !provenance?.suite || !provenance?.execution) {
    blockers.push('saved run report provenance is incomplete');
    return details;
  }
  if (!/^[a-f0-9]{40,64}$/i.test(String(expectedUpstreamSha ?? '')))
    blockers.push('an approved 40- or 64-character upstream SHA is required');

  let sourceResult;
  try { sourceResult = await verifyBundleSources(bundleRoot, provenance, expectedUpstreamSha, blockers); }
  catch (error) { blockers.push(`source verification failed: ${error.message}`); }
  details.source = sourceResult ?? {
    upstreamSha: null,
    bundleReportSha256: null,
    sourceInventorySha256: null,
    manifestSha256: null,
    sourceMapSha256: null,
    sourceSpecs: 0,
  };

  try { await verifyRuntimeAdapters(adapterRoot, provenance.source, blockers); }
  catch (error) { blockers.push(`runtime adapter verification failed: ${error.message}`); }

  let plans;
  try {
    plans = await verifyPlans(bundleRoot, report, provenance, blockers);
    plans.bundleRoot = bundleRoot;
  } catch (error) {
    blockers.push(`Vally re-planning failed: ${error.message}`);
  }
  details.counts.fullPlanned = provenance.suite.fullBundle?.plannedTrialCount ?? null;
  details.counts.selectedPlanned = provenance.suite.selected?.plannedTrialCount ?? null;

  let rowResult;
  if (plans) {
    try { rowResult = await verifyRows(runRoot, report, provenance, plans, blockers); }
    catch (error) { blockers.push(`Vally result verification failed: ${error.message}`); }
  }
  if (rowResult) {
    details.counts.actualResults = rowResult.trials.length;
    details.counts.successfulResults = rowResult.trials.filter(trial => trial.status === 'success').length;
    details.scoring = {
      passed: rowResult.recomputed.passed,
      hadExecutionErrors: rowResult.recomputed.hadExecutionErrors,
      evaluations: rowResult.recomputed.evals.length,
    };
  }
  if (plans) {
    const currentFullIds = plans.full.plan.items.map(item => idKey(item.id));
    const currentSelectedIds = plans.selected.plan.items.map(item => idKey(item.id));
    const selectionIsFull = currentFullIds.length === currentSelectedIds.length &&
      new Set(currentFullIds).size === currentFullIds.length && currentFullIds.every(id => currentSelectedIds.includes(id));
    check(selectionIsFull, 'the executed selection is not the complete original bundle suite', blockers);
    check(provenance.suite.selected.selectionIsFullBundle === selectionIsFull,
      'run provenance full-bundle selection flag disagrees with Vally re-planning', blockers);
    const actualFull = !!rowResult && rowResult.exactSelectedCoverage && rowResult.allSuccessful &&
      rowResult.recomputed.passed === true && rowResult.recomputed.hadExecutionErrors === false;
    const computedFull = selectionIsFull && actualFull && provenance.source.integrity?.unchanged === true;
    check(provenance.execution.fullSuiteExecution === computedFull,
      'run provenance fullSuiteExecution flag disagrees with independent verification', blockers);
    if (!computedFull && !blockers.includes('the executed selection is not the complete original bundle suite'))
      blockers.push('run does not independently meet full-suite execution criteria');
  }
  details.verified = blockers.length === 0;
  details.fullSuiteExecution = details.verified;
  return details;
}

function parseArgs(args) {
  const result = {};
  const allowed = new Map([
    ['--bundle', 'bundle'], ['--run', 'run'], ['--expected-upstream-sha', 'expectedUpstreamSha'],
    ['--approved-adapter-root', 'approvedAdapterRoot'],
  ]);
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const key = allowed.get(flag);
    if (!key) throw new Error(`unsupported argument: ${flag}`);
    if (Object.hasOwn(result, key)) throw new Error(`duplicate argument: ${flag}`);
    const value = args[++i];
    if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value`);
    result[key] = value;
  }
  for (const [flag, key] of allowed) if (!result[key]) throw new Error(`missing required argument: ${flag}`);
  return result;
}

export { parseArgs as parseVerifyArgs };
export { sourceRootRelativePath as normalizeCapturedSourcePath, remapStoredItemId as remapCapturedTrialId };

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const result = await verifyRunEvidence(parseArgs(process.argv.slice(2)));
    process.stdout.write(JSON.stringify(result) + '\n');
    if (!result.verified) process.exitCode = 1;
  } catch (error) {
    process.stdout.write(JSON.stringify({ mode: 'vally-evidence-verification', verified: false,
      fullSuiteExecution: false, blockers: [error.message] }) + '\n');
    process.exitCode = 1;
  }
}
