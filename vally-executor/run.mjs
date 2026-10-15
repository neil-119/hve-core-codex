import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, lstat, realpath, readFile, readdir, writeFile } from 'node:fs/promises';
import { finished } from 'node:stream/promises';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import {
  CompositeEvalReporter,
  createDefaultBackendRegistry,
  createDefaultGraderRegistry,
  createPartialRunSummary,
  discoverSkills,
  EvalJsonlReporter,
  EvalJunitReporter,
  EvalMarkdownReporter,
  needsLlmClient,
  parseSkill,
  runTrialItem,
} from '@microsoft/vally';
import { buildBundlePlan } from './plan.mjs';
import { captureBundleResources } from './bundle-resource-integrity.mjs';
import { CodexJudgeClient } from './judge.mjs';
import { CodexExecutor, parseConfig } from './index.mjs';

const REPORT_PATH = 'run-report.json';
const RESULTS_PATH = 'results.jsonl';
const MARKDOWN_PATH = 'report.md';
const JUNIT_PATH = 'junit.xml';
const PROVENANCE_SCHEMA = 'hve.vally-run-provenance';
const PROVENANCE_VERSION = 2;
const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const MAX_VALLY_JS_FILES = 4096;
const MAX_VALLY_JS_TREE_ENTRIES = 50_000;
const MAX_VALLY_JS_TOTAL_BYTES = 64 * 1024 * 1024;
const MAX_VALLY_JS_FILE_BYTES = 16 * 1024 * 1024;
const JUDGE_COMPATIBILITY = Object.freeze({ toolIsolation: 'unverified', acceptedLimitation: true });
const JUDGE_COMPATIBILITY_WARNING = 'Codex judge tool isolation is unverified; the accepted limitation is recorded.';
const RUNTIME_ADAPTERS = [
  'run.mjs', 'plan.mjs', 'index.mjs', 'app-server.mjs', 'app-server-execution.mjs',
  'app-server-events.mjs', 'turn.mjs', 'settlement.mjs', 'skill-isolation.mjs',
  'grader-capabilities.mjs', 'shell-normalization.mjs', 'mcp-config-translator.mjs',
  'bundle-resource-integrity.mjs', 'judge.mjs', 'judge-evidence.mjs',
];
const RUNTIME_MANIFESTS = ['package.json', 'package-lock.json', 'navigation.json'];

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

// Keep the launcher wrapper small and safe for stdout. Vally's source reports
// retain the full trajectories; this report is only the run verdict and counts.
function compactSummary(summary) {
  return {
    passed: summary?.passed === true,
    hadExecutionErrors: summary?.hadExecutionErrors === true,
    evals: (summary?.evals ?? []).map(evaluation => ({
      evalName: evaluation.evalName,
      evalFilePath: evaluation.evalFilePath,
      variant: evaluation.variant,
      model: evaluation.model,
      passed: evaluation.passed === true,
      hadExecutionErrors: evaluation.hadExecutionErrors === true,
      ...(evaluation.error ? { error: evaluation.error } : {}),
      scoringApplied: evaluation.scoringApplied === true,
      ...(evaluation.overallScore !== undefined ? { overallScore: evaluation.overallScore } : {}),
      ...(evaluation.threshold !== undefined ? { threshold: evaluation.threshold } : {}),
      durationMs: evaluation.durationMs ?? 0,
      stimuliRun: evaluation.stimuliRun ?? 0,
      stimuliTotal: evaluation.stimuliTotal ?? 0,
      stimuliSkipped: evaluation.stimuliSkipped ?? 0,
      skippedStimuli: (evaluation.skippedStimuli ?? []).map(skipped => ({
        stimulusName: skipped.stimulusName,
        reason: skipped.reason,
      })),
    })),
  };
}

// Keep the process interface small even when the saved provenance contains the
// full 1,500+ trial plan and actual outcome ledger. The saved report remains the
// authoritative, complete artifact; stdout is only a bounded locator/verdict.
export function compactCliSummary(report, savedReportSha256 = null) {
  const provenance = report?.runProvenance;
  const source = provenance?.source;
  const fullBundle = provenance?.suite?.fullBundle;
  const selected = provenance?.suite?.selected;
  return {
    mode: report?.mode ?? 'vally-run',
    status: report?.status ?? 'failed',
    exitCode: report?.exitCode ?? 1,
    dryRun: report?.dryRun === true,
    runtimeInvoked: report?.runtimeInvoked === true,
    plannedTrials: report?.plannedTrials ?? selected?.plannedTrialCount ?? 0,
    completedTrials: report?.completedTrials ?? provenance?.execution?.resultsRecorded ?? 0,
    remainingTrials: report?.remainingTrials ?? 0,
    summary: report?.summary ? {
      passed: report.summary.passed === true,
      hadExecutionErrors: report.summary.hadExecutionErrors === true,
    } : null,
    runProvenance: provenance ? {
      schema: provenance.schema ?? null,
      version: provenance.version ?? null,
      source: {
        upstreamSha: source?.declaredSourcePin?.sourceInventory?.upstreamSha ?? null,
        sourceInventorySha256: source?.sourceInventory?.sha256 ?? null,
        sourceManifestSha256: source?.manifest?.sha256 ?? null,
        bundleReportSha256: source?.bundleReport?.sha256 ?? null,
        sourcePinValid: source?.verification?.sourcePinValid === true,
        integrity: source?.integrity ? {
          unchanged: source.integrity.unchanged ?? null,
          changedPathCount: source.integrity.changedPaths?.length ?? 0,
          errorCount: source.integrity.errors?.length ?? 0,
        } : null,
      },
      suite: {
        selectedTrialCount: selected?.plannedTrialCount ?? 0,
        fullBundleTrialCount: fullBundle?.plannedTrialCount ?? null,
        selectionIsFullBundle: selected?.selectionIsFullBundle === true,
        omittedTrialCount: selected?.omittedItemIds?.length ?? 0,
      },
      execution: {
        fullSuiteExecution: provenance.execution?.fullSuiteExecution === true,
      },
      judge: provenance.judge ? {
        requestedBackend: provenance.judge.requestedBackend ?? 'none',
        backendSelected: provenance.judge.backendSelected === true,
        actualBackendSelected: provenance.judge.actualBackendSelected === true,
        clientKind: provenance.judge.clientKind ?? 'none',
        compatibility: provenance.judge.compatibility ?? null,
      } : null,
    } : null,
    judgeCompatibilityWarning: report?.judgeCompatibilityWarning ?? JUDGE_COMPATIBILITY_WARNING,
    savedReport: {
      path: report?.files?.runReport ?? null,
      sha256: savedReportSha256,
    },
  };
}

function stableId(value) {
  return createHash('sha256').update(String(value)).digest('hex').slice(0, 24);
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalValue(value[key])]));
  }
  return value;
}

function canonicalJson(value) {
  return JSON.stringify(canonicalValue(value));
}

async function readHashedFile(path, logicalPath = path) {
  const bytes = await readFile(path);
  return { path: logicalPath, absolutePath: resolve(path), bytes: bytes.length, sha256: sha256(bytes) };
}

function compareText(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

async function captureVallyRuntimeJavaScript() {
  const packageParts = ['node_modules', '@microsoft', 'vally', 'dist'];
  let distRoot = MODULE_DIR;
  for (const part of packageParts) {
    distRoot = join(distRoot, part);
    const info = await lstat(distRoot);
    if (info.isSymbolicLink() || !info.isDirectory())
      throw new Error(`Installed Vally runtime path is not a regular directory: ${distRoot}`);
  }
  const canonicalDistRoot = await realpath(distRoot);
  const records = [];
  let treeEntries = 0;
  let totalBytes = 0;

  async function visit(directory, depth) {
    if (depth > 64) throw new Error('Installed Vally runtime JS directory nesting exceeds the safe limit');
    const entries = await readdir(directory);
    if (entries.length > MAX_VALLY_JS_TREE_ENTRIES || treeEntries + entries.length > MAX_VALLY_JS_TREE_ENTRIES)
      throw new Error(`Installed Vally runtime tree exceeds ${MAX_VALLY_JS_TREE_ENTRIES} entries`);
    entries.sort(compareText);
    for (const name of entries) {
      treeEntries++;
      const path = join(directory, name);
      const info = await lstat(path);
      if (info.isSymbolicLink()) throw new Error(`Installed Vally runtime tree contains a symbolic link: ${path}`);
      if (info.isDirectory()) {
        await visit(path, depth + 1);
        continue;
      }
      if (!name.endsWith('.js')) continue;
      if (!info.isFile()) throw new Error(`Installed Vally runtime JS is not a regular file: ${path}`);
      if (info.size > MAX_VALLY_JS_FILE_BYTES)
        throw new Error(`Installed Vally runtime JS exceeds ${MAX_VALLY_JS_FILE_BYTES} bytes: ${path}`);
      if (records.length >= MAX_VALLY_JS_FILES)
        throw new Error(`Installed Vally runtime exceeds ${MAX_VALLY_JS_FILES} JavaScript files`);
      totalBytes += info.size;
      if (totalBytes > MAX_VALLY_JS_TOTAL_BYTES)
        throw new Error(`Installed Vally runtime JS exceeds ${MAX_VALLY_JS_TOTAL_BYTES} total bytes`);
      const canonicalPath = await realpath(path);
      if (!isWithin(canonicalDistRoot, canonicalPath))
        throw new Error(`Installed Vally runtime JS resolves outside dist: ${path}`);
      const bytes = await readFile(canonicalPath);
      const after = await lstat(path);
      if (after.isSymbolicLink() || !after.isFile() || bytes.length !== info.size || after.size !== info.size || after.mtimeMs !== info.mtimeMs)
        throw new Error(`Installed Vally runtime JS changed while being captured: ${path}`);
      records.push({
        path: `node_modules/@microsoft/vally/dist/${relative(canonicalDistRoot, canonicalPath).split(sep).join('/')}`,
        bytes: bytes.length,
        sha256: sha256(bytes),
      });
    }
  }

  await visit(canonicalDistRoot, 0);
  records.sort((left, right) => compareText(left.path, right.path));
  if (!records.length) throw new Error('Installed Vally runtime contains no dist JavaScript files');
  return records;
}

function validSha256(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
}

function sourcePinFromReport(bundleReport) {
  const source = bundleReport?.source ?? {};
  const inventory = source.source_inventory ?? {};
  return {
    distributionRootName: source.distribution_root_name ?? null,
    upstreamRootName: source.upstream_root_name ?? null,
    manifest: source.manifest ?? null,
    manifestSha256: source.manifest_sha256 ?? null,
    sourceInventory: {
      path: inventory.path ?? null,
      sha256: inventory.sha256 ?? null,
      channel: inventory.channel ?? null,
      upstreamRef: inventory.upstream_ref ?? null,
      upstreamSha: inventory.upstream_sha ?? null,
      upstreamUrl: inventory.upstream_url ?? null,
    },
    sourceToOutput: bundleReport?.source_to_output ?? null,
    customDeclaration: source.custom ?? null,
  };
}

function sourcePinErrors(bundleReport, reportRecord) {
  const errors = [];
  if (!reportRecord) errors.push('converted bundle report is missing');
  if (bundleReport?.schema_version !== 1) errors.push('converted bundle report schema_version is not 1');
  if (bundleReport?.status !== 'complete' || bundleReport?.success !== true)
    errors.push('converted bundle report is not complete and successful');
  const pin = sourcePinFromReport(bundleReport);
  if (typeof pin.manifest !== 'string' || !pin.manifest) errors.push('bundle source manifest path is missing');
  if (!validSha256(pin.manifestSha256)) errors.push('bundle source manifest SHA-256 is missing or invalid');
  if (typeof pin.sourceInventory.path !== 'string' || !pin.sourceInventory.path)
    errors.push('bundle source inventory path is missing');
  if (!validSha256(pin.sourceInventory.sha256)) errors.push('bundle source inventory SHA-256 is missing or invalid');
  if (typeof pin.sourceInventory.upstreamSha !== 'string' || !pin.sourceInventory.upstreamSha)
    errors.push('bundle upstream source SHA is missing');
  if (typeof pin.sourceToOutput !== 'string' || !pin.sourceToOutput)
    errors.push('bundle source-to-output path is missing');
  if (pin.customDeclaration !== null) {
    if (!pin.customDeclaration || typeof pin.customDeclaration !== 'object' ||
        typeof pin.customDeclaration.root_name !== 'string' || !pin.customDeclaration.root_name ||
        !Array.isArray(pin.customDeclaration.files)) {
      errors.push('bundle custom source declaration is malformed');
    } else {
      for (const entry of pin.customDeclaration.files) {
        if (!entry || typeof entry.path !== 'string' || !entry.path ||
            isAbsolute(entry.path) || entry.path.includes('\\') || entry.path.split('/').some(part => part === '..' || part === ''))
          errors.push('bundle custom source declaration contains an unsafe or invalid path');
        if (!validSha256(entry.sha256)) errors.push(`bundle custom source hash is missing or invalid: ${entry.path ?? '(unknown path)'}`);
      }
    }
  }
  return errors;
}

async function readDeclaredBundleFile(root, declaredPath, label) {
  if (typeof declaredPath !== 'string' || !declaredPath || isAbsolute(declaredPath) ||
      /^[a-z]:/i.test(declaredPath) || declaredPath.includes('\\'))
    throw new Error(`${label} path is not a safe bundle-relative path`);
  const parts = declaredPath.split('/');
  if (parts.some(part => !part || part === '.' || part === '..'))
    throw new Error(`${label} path is not a normalized bundle-relative path`);
  const candidate = resolve(root, ...parts);
  if (!isWithin(root, candidate)) throw new Error(`${label} path escapes the converted bundle`);
  const canonicalPath = await realpath(candidate);
  if (!isWithin(root, canonicalPath)) throw new Error(`${label} resolves outside the converted bundle`);
  return { ...(await readHashedFile(canonicalPath, declaredPath)), present: true };
}

async function captureRunInputs(bundleRoot, discoveredEvals) {
  const root = await realpath(bundleRoot);
  const bundleReportPath = join(root, 'generated', 'vally-bundle-report.json');
  let bundleReportRecord;
  let bundleReport;
  let bundleReportError;
  try {
    const raw = await readFile(bundleReportPath);
    bundleReportRecord = {
      path: 'generated/vally-bundle-report.json',
      absolutePath: resolve(bundleReportPath),
      bytes: raw.length,
      sha256: sha256(raw),
    };
    bundleReport = JSON.parse(raw.toString('utf8'));
  } catch (error) {
    bundleReportError = errorText(error);
  }

  const sourcePin = sourcePinFromReport(bundleReport);
  const sourceArtifactErrors = [];
  const sourceArtifacts = {};
  for (const [key, declaredPath, expectedSha] of [
    ['manifest', sourcePin.manifest, sourcePin.manifestSha256],
    ['sourceInventory', sourcePin.sourceInventory.path, sourcePin.sourceInventory.sha256],
  ]) {
    try {
      const record = await readDeclaredBundleFile(root, declaredPath, key);
      record.declaredSha256 = expectedSha ?? null;
      record.verified = record.sha256 === expectedSha;
      if (!record.verified) sourceArtifactErrors.push(`bundle ${key} file hash does not match report declaration: ${declaredPath}`);
      sourceArtifacts[key] = record;
    } catch (error) {
      sourceArtifactErrors.push(`could not verify bundle ${key} file: ${errorText(error)}`);
      sourceArtifacts[key] = { path: declaredPath ?? null, present: false, verified: false, error: errorText(error) };
    }
  }
  let sourceMapRecord;
  let sourceMap;
  try {
    sourceMapRecord = await readDeclaredBundleFile(root, sourcePin.sourceToOutput, 'source-to-output map');
    const sourceMapRaw = await readFile(sourceMapRecord.absolutePath);
    if (sha256(sourceMapRaw) !== sourceMapRecord.sha256) throw new Error('source-to-output map changed while it was being read');
    sourceMap = JSON.parse(sourceMapRaw.toString('utf8'));
    if (!sourceMap || typeof sourceMap !== 'object' || Array.isArray(sourceMap))
      throw new Error('source-to-output map root is not an object');
    sourceMapRecord.parsed = true;
  } catch (error) {
    sourceArtifactErrors.push(`could not verify source-to-output map: ${errorText(error)}`);
    sourceMapRecord ??= { path: sourcePin.sourceToOutput ?? null, present: false, error: errorText(error) };
  }

  let bundleResources = [];
  try { bundleResources = await captureBundleResources(root, sourceMap); }
  catch (error) { sourceArtifactErrors.push(`could not verify bundle resources: ${errorText(error)}`); }
  // discoveredEvals is kept as the full original spec set for byte-level source binding.
  const specFiles = [];
  const sourceMapEntries = [];
  const sourceArchivesByPath = new Map();
  const seen = new Set();
  const customDeclaration = bundleReport?.source?.custom;
  const customIdentities = new Set((Array.isArray(customDeclaration?.files) ? customDeclaration.files : [])
    .filter(entry => typeof entry?.path === 'string')
    .map(entry => entry.path.replaceAll('\\', '/').replace(/^\.\//, '').toLowerCase()));
  for (const entry of discoveredEvals ?? []) {
    const rawPath = entry.filePath ?? entry.spec?.evalFilePath;
    if (typeof rawPath !== 'string') throw new Error('Vally discovery entry has no source file path');
    const canonicalPath = await realpath(rawPath);
    if (!isWithin(root, canonicalPath)) throw new Error(`Eval source resolves outside the converted bundle: ${rawPath}`);
    const logicalPath = relative(root, canonicalPath).split(sep).join('/');
    if (seen.has(logicalPath)) continue;
    seen.add(logicalPath);
    const record = await readHashedFile(canonicalPath, logicalPath);
    record.evalName = entry.spec?.name ?? null;
    const mapping = sourceMap?.[logicalPath];
    const declaredCustom = customIdentities.has(logicalPath.toLowerCase());
    record.origin = mapping?.origin === 'codex-custom' || declaredCustom ? 'codex-custom' : 'upstream';
    record.selected = false;
    record.selectedStimuli = [];
    if (!mapping || !Array.isArray(mapping.outputs)) {
      sourceArtifactErrors.push(`source map has no output records for eval spec: ${logicalPath}`);
    } else {
      if (declaredCustom && mapping.origin !== 'codex-custom')
        sourceArtifactErrors.push(`custom eval origin disagrees with source map: ${logicalPath}`);
      const converted = mapping.outputs.find(output => output.path === logicalPath && output.status === 'converted-eval-spec');
      if (!converted) sourceArtifactErrors.push(`source map does not identify the converted eval output: ${logicalPath}`);
      else if (converted.sha256 !== record.sha256)
        sourceArtifactErrors.push(`converted eval hash does not match source map: ${logicalPath}`);
      const prefix = record.origin === 'codex-custom' ? 'custom/' : 'upstream/';
      const original = mapping.outputs.find(output => output.status === 'preserved-original' &&
        typeof output.path === 'string' && output.path.startsWith(prefix));
      if (!original) {
        sourceArtifactErrors.push(`source map has no preserved original archive for eval spec: ${logicalPath}`);
      } else {
        try {
          const archive = await readDeclaredBundleFile(root, original.path, 'preserved original source archive');
          archive.sourcePath = logicalPath;
          archive.origin = record.origin;
          archive.declaredSha256 = original.sha256 ?? null;
          archive.verified = validSha256(original.sha256) && archive.sha256 === original.sha256;
          if (!archive.verified) sourceArtifactErrors.push(`preserved original archive hash does not match source map: ${original.path}`);
          sourceArchivesByPath.set(archive.path, archive);
        } catch (error) {
          sourceArtifactErrors.push(`could not verify preserved original archive ${original.path}: ${errorText(error)}`);
        }
      }
      sourceMapEntries.push({
        sourcePath: logicalPath,
        origin: mapping.origin ?? 'upstream',
        outputs: mapping.outputs.map(output => ({
          path: output.path,
          status: output.status,
          ...(output.sha256 ? { sha256: output.sha256 } : {}),
        })),
      });
    }
    specFiles.push(record);
  }

  for (const declaration of Array.isArray(customDeclaration?.files) ? customDeclaration.files : []) {
    if (typeof declaration?.path !== 'string') continue;
    const identity = declaration.path.replaceAll('\\', '/').replace(/^\.\//, '');
    const archiveLogicalPath = `custom/${identity}`;
    if (sourceArchivesByPath.has(archiveLogicalPath)) {
      const prior = sourceArchivesByPath.get(archiveLogicalPath);
      prior.customSourceDeclaration = true;
      if (prior.sha256 !== declaration.sha256) {
        prior.verified = false;
        sourceArtifactErrors.push(`custom source archive hash does not match bundle declaration: ${identity}`);
      }
      continue;
    }
    try {
      const record = await readDeclaredBundleFile(root, archiveLogicalPath, 'custom source archive');
      record.sourcePath = identity;
      record.declaredSha256 = declaration.sha256 ?? null;
      record.verified = record.sha256 === declaration.sha256;
      record.origin = 'codex-custom';
      record.customSourceDeclaration = true;
      if (!record.verified) sourceArtifactErrors.push(`custom source archive hash does not match bundle declaration: ${identity}`);
      sourceArchivesByPath.set(archiveLogicalPath, record);
    } catch (error) {
      sourceArtifactErrors.push(`could not verify custom source archive ${identity}: ${errorText(error)}`);
      sourceArchivesByPath.set(archiveLogicalPath, { path: archiveLogicalPath, sourcePath: identity,
        origin: 'codex-custom', customSourceDeclaration: true, present: false, verified: false, error: errorText(error) });
    }
  }

  const runtimeAdapters = await Promise.all(RUNTIME_ADAPTERS.map(name =>
    readHashedFile(join(MODULE_DIR, name), `adapter/${name}`)));
  const runtimeManifests = await Promise.all(RUNTIME_MANIFESTS.map(name =>
    readHashedFile(join(MODULE_DIR, name), `runtime/${name}`)));
  const vallyPackagePath = join(MODULE_DIR, 'node_modules', '@microsoft', 'vally', 'package.json');
  const vallyPackage = JSON.parse((await readFile(vallyPackagePath)).toString('utf8'));
  const vallyPackageRecord = await readHashedFile(vallyPackagePath, 'node_modules/@microsoft/vally/package.json');
  const runtimeDependencyFiles = await captureVallyRuntimeJavaScript();
  const sourceArchives = [...sourceArchivesByPath.values()];
  const sourcePinErrorsList = [...sourcePinErrors(bundleReport, bundleReportRecord), ...sourceArtifactErrors];
  const bundleSource = bundleReportRecord ? {
    bundleReport: {
      ...bundleReportRecord,
      schemaVersion: bundleReport?.schema_version ?? null,
      status: bundleReport?.status ?? null,
      success: bundleReport?.success === true,
    },
    declaredSourcePin: sourcePinFromReport(bundleReport),
  } : {
    bundleReport: { path: 'generated/vally-bundle-report.json', present: false, error: bundleReportError ?? 'bundle report unavailable' },
    declaredSourcePin: null,
  };
  return {
    snapshot: {
      bundleRoot: root,
      ...bundleSource,
      ...sourceArtifacts,
      sourceToOutputMap: sourceMapRecord,
      bundleResources,
      specFiles: specFiles.sort((a, b) => a.path.localeCompare(b.path)),
      sourceMapEntries: sourceMapEntries.sort((a, b) => a.sourcePath.localeCompare(b.sourcePath)),
      sourceArchives: sourceArchives.sort((a, b) => a.path.localeCompare(b.path)),
      customArchives: sourceArchives.filter(record => record.origin === 'codex-custom')
        .sort((a, b) => a.path.localeCompare(b.path)),
      runtimeAdapters,
      runtimeManifests,
      runtimeDependencyFiles,
      runtimeDependency: {
        name: '@microsoft/vally',
        version: vallyPackage.version ?? null,
        ...vallyPackageRecord,
      },
    },
    bundleReport,
    bundleReportError,
    sourcePinErrors: sourcePinErrorsList,
    sourceVerification: {
      sourcePinValid: sourcePinErrorsList.length === 0,
      manifestHashVerified: sourceArtifacts.manifest?.verified === true,
      sourceInventoryHashVerified: sourceArtifacts.sourceInventory?.verified === true,
      sourceMapParsed: sourceMapRecord?.parsed === true,
      convertedSpecMappingsVerified: !sourceArtifactErrors.some(error => error.startsWith('source map') || error.startsWith('converted eval')),
      sourceArchiveHashesVerified: sourceArchives.length > 0 && sourceArchives.every(record => record.verified === true),
    },
  };
}

function trialGrading(item) {
  const graderConfigs = item.run.graderConfigs ?? [];
  const graderConfigSha256 = sha256(canonicalJson(graderConfigs));
  return {
    graderConfigSha256,
    graderConfigs,
    skipGrade: item.run.skipGrade === true,
    judgeModel: item.run.judgeModel ?? null,
    judgeReasoningEffort: item.run.judgeReasoningEffort ?? null,
    judgeProvider: item.run.judgeProvider ?? null,
    scoringWeights: item.run.scoringWeights ?? null,
  };
}

function describePlannedTrial(item) {
  const grading = trialGrading(item);
  return {
    itemId: item.id,
    evalName: item.evalName,
    evalFilePath: item.evalFilePath,
    variant: item.variant,
    model: item.model ?? null,
    stimulusName: item.stimulus.name,
    trialIndex: item.trialIndex ?? 0,
    totalTrials: item.totalTrials,
    grading: {
      graderConfigSha256: grading.graderConfigSha256,
      skipGrade: grading.skipGrade,
      judgeModel: grading.judgeModel,
      judgeReasoningEffort: grading.judgeReasoningEffort,
      judgeProvider: grading.judgeProvider,
      scoringWeights: grading.scoringWeights,
    },
  };
}

function makeRunProvenance({ bundleRoot, selectedPlan, fullPlan, fullPlanError, inputCapture }) {
  const selected = selectedPlan.plan.items.map(describePlannedTrial);
  const fullIds = fullPlan?.plan.items.map(item => item.id) ?? null;
  const selectedIds = selected.map(item => item.itemId);
  const fullSet = fullIds ? new Set(fullIds) : null;
  const selectedSet = new Set(selectedIds);
  const selectionIsFullBundle = !!fullSet && fullIds.length === selectedIds.length &&
    selectedSet.size === selectedIds.length && selectedIds.every(id => fullSet.has(id));
  const omittedItemIds = fullIds ? fullIds.filter(id => !selectedSet.has(id)) : null;
  const selectedSpecPaths = new Set(selectedPlan.plan.evals.map(entry => resolve(entry.evalFilePath)));
  const selectedStimuliByPath = new Map();
  for (const item of selectedPlan.plan.items) {
    const path = resolve(item.evalFilePath);
    const names = selectedStimuliByPath.get(path) ?? new Set();
    names.add(item.stimulus.name);
    selectedStimuliByPath.set(path, names);
  }
  for (const spec of inputCapture.snapshot.specFiles) {
    const absolutePath = resolve(spec.absolutePath);
    spec.selected = selectedSpecPaths.has(absolutePath);
    spec.selectedStimuli = [...(selectedStimuliByPath.get(absolutePath) ?? [])].sort();
  }
  const graderMap = new Map();
  for (const item of selectedPlan.plan.items) {
    const grading = trialGrading(item);
    if (!graderMap.has(grading.graderConfigSha256)) {
      graderMap.set(grading.graderConfigSha256, {
        sha256: grading.graderConfigSha256,
        configs: grading.graderConfigs,
      });
    }
  }
  const fullPlanningErrors = [
    ...(fullPlanError ? [fullPlanError] : []),
    ...(fullPlan?.discovered.errors ?? []).map(errorText),
    ...(fullPlan?.plan.evals ?? []).filter(entry => entry.failure).map(entry => `${entry.evalName}: ${entry.failure}`),
    ...(fullPlan?.plan.diagnostics ?? []).filter(diagnostic => diagnostic.severity === 'error')
      .map(diagnostic => diagnostic.message ?? diagnostic.code ?? 'unknown Vally planning error'),
  ];
  return {
    schema: PROVENANCE_SCHEMA,
    version: PROVENANCE_VERSION,
    source: {
      ...inputCapture.snapshot,
      verification: { ...inputCapture.sourceVerification, sourcePinErrors: inputCapture.sourcePinErrors },
      integrity: {
        afterExecution: null,
        unchanged: null,
        changedPaths: [],
        errors: [],
      },
    },
    suite: {
      fullBundle: {
        available: !!fullPlan,
        valid: !!fullPlan && fullPlanningErrors.length === 0,
        plannedTrialCount: fullIds?.length ?? null,
        expectedItemIds: fullIds,
        planningErrors: fullPlanningErrors,
      },
      selected: {
        plannedTrialCount: selected.length,
        expectedTrials: selected,
        graderConfigurations: [...graderMap.values()].sort((a, b) => a.sha256.localeCompare(b.sha256)),
        selectionIsFullBundle,
        omittedItemIds,
      },
    },
    execution: {
      state: 'not-run',
      expectedResults: selectedIds.length,
      resultsRecorded: 0,
      successfulResults: 0,
      failedResults: 0,
      actualTrials: [],
      missingItemIds: selectedIds,
      duplicateItemIds: [],
      unexpectedItemIds: [],
      exactCoverage: false,
      allExecutionStatusesSuccessful: false,
      allGradesPassed: false,
      finalScoringPassed: null,
      fullSuiteExecution: false,
    },
  };
}

function recordExecutionProvenance(provenance, results, summary, runtimeInvoked, runStatus) {
  const expectedIds = provenance.suite.selected.expectedTrials.map(trial => trial.itemId);
  const expectedSet = new Set(expectedIds);
  const counts = new Map();
  const actualTrials = results.map(result => {
    const itemId = typeof result?.itemId === 'string' ? result.itemId : null;
    if (itemId !== null) counts.set(itemId, (counts.get(itemId) ?? 0) + 1);
    return {
      itemId,
      status: result?.status ?? 'unknown',
      gradePassed: typeof result?.grade?.passed === 'boolean' ? result.grade.passed : null,
      ...(result?.error ? { error: errorText(result.error) } : {}),
      ...(Number.isFinite(result?.durationMs) ? { durationMs: result.durationMs } : {}),
    };
  });
  const observedIds = new Set(counts.keys());
  const duplicates = [...counts.entries()].filter(([, count]) => count > 1).map(([id]) => id).sort();
  const missing = expectedIds.filter(id => !observedIds.has(id));
  const unexpected = [...observedIds].filter(id => !expectedSet.has(id)).sort();
  const exactCoverage = missing.length === 0 && duplicates.length === 0 && unexpected.length === 0 &&
    actualTrials.length === expectedIds.length;
  const allExecutionStatusesSuccessful = actualTrials.length > 0 && actualTrials.every(trial => trial.status === 'success');
  const allGradesPassed = actualTrials.length > 0 && actualTrials.every(trial => trial.gradePassed === true);
  const finalScoringPassed = summary?.passed === true;
  const unchanged = provenance.source.integrity.unchanged === true;
  const fullSuiteExecution = runtimeInvoked && runStatus === 'completed' && provenance.suite.fullBundle.available &&
    provenance.suite.fullBundle.valid &&
    provenance.suite.selected.selectionIsFullBundle && exactCoverage && allExecutionStatusesSuccessful &&
    finalScoringPassed && unchanged;
  provenance.execution = {
    state: runtimeInvoked ? 'recorded' : 'not-run',
    expectedResults: expectedIds.length,
    resultsRecorded: actualTrials.length,
    successfulResults: actualTrials.filter(trial => trial.status === 'success').length,
    failedResults: actualTrials.filter(trial => trial.status !== 'success').length,
    actualTrials,
    missingItemIds: missing,
    duplicateItemIds: duplicates,
    unexpectedItemIds: unexpected,
    exactCoverage,
    allExecutionStatusesSuccessful,
    allGradesPassed,
    finalScoringPassed,
    fullSuiteExecution,
  };
}

function compareInputSnapshots(before, after) {
  const records = snapshot => [
    snapshot.bundleReport,
    snapshot.manifest,
    snapshot.sourceInventory,
    snapshot.sourceToOutputMap,
    ...(snapshot.specFiles ?? []),
    ...(snapshot.sourceArchives ?? []),
    ...(snapshot.customArchives ?? []),
    ...(snapshot.runtimeAdapters ?? []),
    ...(snapshot.runtimeManifests ?? []),
    ...(snapshot.runtimeDependencyFiles ?? []),
    snapshot.runtimeDependency,
  ].filter(record => record && typeof record.path === 'string');
  const beforeMap = new Map(records(before).map(record => [record.path, record]));
  const afterMap = new Map(records(after).map(record => [record.path, record]));
  const paths = new Set([...beforeMap.keys(), ...afterMap.keys()]);
  const changedPaths = [...paths].filter(path => {
    const left = beforeMap.get(path);
    const right = afterMap.get(path);
    return !left || !right || left.sha256 !== right.sha256 || left.bytes !== right.bytes;
  }).sort();
  return { unchanged: changedPaths.length === 0, changedPaths };
}

function compactInputSnapshot(snapshot) {
  return {
    bundleReport: snapshot.bundleReport?.sha256 ?? null,
    manifest: snapshot.manifest ? { path: snapshot.manifest.path, bytes: snapshot.manifest.bytes,
      sha256: snapshot.manifest.sha256, declaredSha256: snapshot.manifest.declaredSha256, verified: snapshot.manifest.verified } : null,
    sourceInventory: snapshot.sourceInventory ? { path: snapshot.sourceInventory.path, bytes: snapshot.sourceInventory.bytes,
      sha256: snapshot.sourceInventory.sha256, declaredSha256: snapshot.sourceInventory.declaredSha256, verified: snapshot.sourceInventory.verified } : null,
    sourceToOutputMap: snapshot.sourceToOutputMap ? { path: snapshot.sourceToOutputMap.path, bytes: snapshot.sourceToOutputMap.bytes,
      sha256: snapshot.sourceToOutputMap.sha256 } : null,
    specFiles: (snapshot.specFiles ?? []).map(({ path, bytes, sha256: digest }) => ({ path, bytes, sha256: digest })),
    bundleResources: snapshot.bundleResources ?? [],
    sourceArchives: (snapshot.sourceArchives ?? []).map(({ path, sourcePath, origin, bytes, sha256: digest, declaredSha256, verified }) =>
      ({ path, sourcePath, origin, bytes, sha256: digest, declaredSha256, verified })),
    customArchives: (snapshot.customArchives ?? []).map(({ path, sourcePath, bytes, sha256: digest, declaredSha256, verified }) =>
      ({ path, sourcePath, bytes, sha256: digest, declaredSha256, verified })),
    runtimeAdapters: (snapshot.runtimeAdapters ?? []).map(({ path, bytes, sha256: digest }) => ({ path, bytes, sha256: digest })),
    runtimeManifests: (snapshot.runtimeManifests ?? []).map(({ path, bytes, sha256: digest }) => ({ path, bytes, sha256: digest })),
    runtimeDependencyFiles: (snapshot.runtimeDependencyFiles ?? []).map(({ path, bytes, sha256: digest }) => ({ path, bytes, sha256: digest })),
    runtimeDependency: snapshot.runtimeDependency ? {
      name: snapshot.runtimeDependency.name,
      version: snapshot.runtimeDependency.version,
      path: snapshot.runtimeDependency.path,
      bytes: snapshot.runtimeDependency.bytes,
      sha256: snapshot.runtimeDependency.sha256,
    } : null,
  };
}

function isWithin(parent, candidate) {
  const rel = relative(parent, candidate);
  if (rel === '') return true;
  if (isAbsolute(rel) || rel === '..' || rel.startsWith(`..${sep}`)) return false;
  return true;
}

function pathsOverlap(left, right) {
  const a = process.platform === 'win32' ? left.toLowerCase() : left;
  const b = process.platform === 'win32' ? right.toLowerCase() : right;
  return isWithin(a, b) || isWithin(b, a);
}

async function resolveFreshOutput(bundleRoot, output) {
  if (typeof output !== 'string' || !output.trim()) throw new Error('output must be a nonempty directory path');
  const root = await realpath(resolve(bundleRoot));
  const requested = resolve(output);
  const parent = await realpath(dirname(requested));
  const target = join(parent, basename(requested));
  if (pathsOverlap(root, target)) throw new Error('output must be disjoint from the converted bundle directory');
  try {
    await lstat(target);
    throw new Error(`output directory already exists: ${target}`);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  return { root, output: target };
}

function makePlanningExecutor() {
  return {
    name: 'codex',
    supportsPreparedWorkspace: true,
    supportsMultiTurn: true,
    supportsTurnCompletion: true,
    supportsEnvVars: true,
    validateConfig(config) { parseConfig(config); },
    async execute() { throw new Error('The dry-run planning executor cannot execute'); },
    async shutdown() {},
  };
}

function makePlanningJudgeClient() {
  return {
    supportsWorkspaceDelivery: true,
    async judge() { throw new Error('Dry-run planning must never invoke a judge'); },
    async shutdown() {},
  };
}

function judgeProvenance({ requestedBackend, dryRun, judgeClientKind }) {
  return {
    requestedBackend,
    backendSelected: requestedBackend === 'codex',
    actualBackendSelected: requestedBackend === 'codex' && !dryRun,
    clientKind: judgeClientKind,
    evidenceDirectory: requestedBackend === 'codex' ? 'judge-evidence' : null,
    compatibility: { ...JUDGE_COMPATIBILITY },
    compatibilityWarning: JUDGE_COMPATIBILITY_WARNING,
  };
}

function mcpDeclarationBlockers(mcpServers, label) {
  if (mcpServers === undefined || mcpServers === null) return [];
  if (typeof mcpServers !== 'object' || Array.isArray(mcpServers))
    return [`${label}: invalid MCP configuration`];
  return Object.keys(mcpServers).length
    ? [`${label}: MCP servers are unsupported by this runner`]
    : [];
}

export function planBlockers({ discovered, plan, preparation, graderRegistry, judgeClient, output,
  skillDiscovery, graderCapabilityAudits = [] }) {
  const blockers = [];
  if (preparation !== undefined && preparation?.success !== true) blockers.push('bundle preparation report is not successful');
  if (!Array.isArray(discovered?.errors) || discovered.errors.length) blockers.push('eval discovery reported errors');
  if (skillDiscovery?.errors?.length) blockers.push('skill discovery reported errors');
  if (plan.evals.some(evaluation => evaluation.failure)) blockers.push('one or more selected evals failed planning');
  if (plan.diagnostics.some(diagnostic => diagnostic.severity === 'error')) blockers.push('Vally plan contains error diagnostics');
  if (plan.totalItems < 1 || plan.items.length !== plan.totalItems) blockers.push('selected plan has no complete trial items');

  let llmGraderApplications = 0;
  for (const item of plan.items) {
    const label = `${item.evalName}/${item.stimulus.name}`;
    if (item.model !== 'gpt-6-luna') blockers.push(`${label}: selected model is not gpt-6-luna`);
    if (item.run.skip) blockers.push(`${label}: Vally would skip this stimulus (${item.run.skip.reason})`);
    if (item.run.skipGrade) blockers.push(`${label}: grading is disabled`);
    if (item.stimulus.simulation) blockers.push(`${label}: tool-call simulation is unsupported`);
    if (item.stimulus.attachments?.length) blockers.push(`${label}: prompt attachments are unsupported`);
    blockers.push(...mcpDeclarationBlockers(item.stimulus.environment?.mcpServers,
      `${label} stimulus`));
    if (item.run.maxAgentDurationMs) blockers.push(`${label}: recoverable agent-duration limit awaits full delegated/background runtime acceptance`);
    for (const config of item.run.graderConfigs) {
      if (!needsLlmClient([config], graderRegistry)) continue;
      llmGraderApplications++;
    }
    if (item.run.trialWorkspace && !isWithin(join(output, 'workspaces'), resolve(item.run.trialWorkspace)))
      blockers.push(`${label}: planned workspace is outside the owned output directory`);
  }

  for (const evaluation of plan.evals) {
    blockers.push(...mcpDeclarationBlockers(evaluation.environment?.mcpServers,
      `${evaluation.evalName} eval`));
  }
  if (llmGraderApplications > 0 && !judgeClient)
    blockers.push('selected evals require LLM grading, but standard Codex judge support is unavailable');
  for (const audit of graderCapabilityAudits) for (const finding of audit.findings)
    blockers.push(`${audit.evalName}/${finding.stimulus}/${finding.grader}: ${finding.reason}`);

  return { blockers: [...new Set(blockers)], llmGraderApplications };
}

function makePreflightReport({ output, evalNames, stimulusNames, dryRun, plan, discovery, preparation, blockers,
  llmGraderApplications, judgeClient, judgePreparationStatus, judgeBackend = 'none',
  actualJudgeBackendSelected = false, judgeClientKind = 'none', runProvenance }) {
  const status = blockers.length ? 'preflight-failed' : dryRun ? 'dry-run' : 'ready';
  return {
    mode: 'vally-run',
    status,
    output,
    dryRun,
    runtimeInvoked: false,
    plannedTrials: plan?.totalItems ?? 0,
    completedTrials: 0,
    remainingTrials: plan?.totalItems ?? 0,
    selectedEvals: evalNames,
    selectedStimuli: stimulusNames,
    ...(runProvenance ? { runProvenance } : {}),
    judgeBackend,
    judgeBackendSelected: judgeBackend === 'codex',
    actualJudgeBackendSelected,
    judgeClientKind,
    judgeCompatibility: { ...JUDGE_COMPATIBILITY },
    judgeCompatibilityWarning: JUDGE_COMPATIBILITY_WARNING,
    ...(judgeBackend === 'codex' && output ? { judgeEvidenceDirectory: join(output, 'judge-evidence') } : {}),
    preparationSucceeded: preparation?.success ?? null,
    discoveredEvals: discovery?.evals?.length ?? 0,
    discoveredSkills: 0,
    llmGraderApplications,
    judgeClientProvided: !!judgeClient,
    judgePreparationStatus: judgePreparationStatus ?? (llmGraderApplications > 0
      ? judgeClientKind === 'planning-fake' ? 'planning-only'
        : judgeClientKind === 'codex' ? 'codex-selected'
          : judgeClient ? 'injected' : 'standard-codex-judge-unavailable'
      : 'not-required'),
    blockers,
    ...(output ? { files: { runReport: join(output, REPORT_PATH) } } : {}),
    exitCode: status === 'dry-run' ? 0 : status === 'ready' ? null : 1,
  };
}

function runArtifacts(output) {
  return {
    jsonl: join(output, RESULTS_PATH),
    markdown: join(output, MARKDOWN_PATH),
    junit: join(output, JUNIT_PATH),
    sessionLogsDir: join(output, 'session-logs'),
  };
}

function ensureReporterOk(reporter, method, args) {
  return (async () => {
    const before = reporter.erroredReporterCount;
    await reporter[method](...args);
    if (reporter.erroredReporterCount !== before)
      throw new Error(`Vally reporter failed during ${method}`);
  })();
}

function sessionLogAllocator(output) {
  const base = join(output, 'session-logs');
  return async (item, attemptIndex) => {
    const attempt = attemptIndex === undefined ? 'attempt-single' : `attempt-${attemptIndex}`;
    const directory = join(base, stableId(item.id), attempt);
    const artifactsDirectory = join(directory, 'executor-artifacts');
    if (!isWithin(base, directory) || !isWithin(directory, artifactsDirectory))
      throw new Error('session log path escaped the owned output directory');
    await mkdir(directory, { recursive: true });
    return {
      directory,
      executorOptions: { rootDir: directory, executorArtifactsDir: artifactsDirectory },
      async complete(update) {
        await writeFile(join(directory, 'lifecycle.json'), JSON.stringify({ status: 'completed', ...update }, null, 2) + '\n', { flag: 'wx' });
      },
      async fail(error) {
        await writeFile(join(directory, 'lifecycle.json'), JSON.stringify({ status: 'failed', error: errorText(error) }, null, 2) + '\n', { flag: 'wx' });
      },
    };
  };
}

function scopedOutputPath(output, family, info) {
  const id = stableId(`${info.trajectoryId}\0${info.trialIndex ?? ''}\0${info.stimulus?.name ?? ''}`);
  return join(output, family, id);
}

function restoreEnvValue(name, previous) {
  if (previous === undefined) delete process.env[name];
  else process.env[name] = previous;
}

async function closeStream(stream) {
  const done = finished(stream);
  stream.end();
  await done;
}

async function persistPreflightReport(report, output) {
  if (!output) return report;
  try {
    await mkdir(output);
    report.files = { runReport: join(output, REPORT_PATH) };
    await writeFile(join(output, REPORT_PATH), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  } catch (error) {
    report.status = 'preflight-failed';
    report.exitCode = 1;
    report.blockers = [...report.blockers, `Could not persist preflight report: ${errorText(error)}`];
  }
  return report;
}

async function discoverSelectedSkills(bundleRoot, planned) {
  const projectRoot = planned.projectCtx.dir || bundleRoot;
  const discovery = await discoverSkills(projectRoot, { skillsDirs: planned.projectCtx.config.paths.skills });
  const explicitSkills = new Map();
  for (const item of planned.plan.items) {
    for (const reference of item.stimulus.environment?.skills ?? []) {
      const sourceDir = isAbsolute(reference) ? resolve(reference) : resolve(item.run.baseDir, reference);
      const skill = await parseSkill(join(sourceDir, 'SKILL.md'));
      const skillPath = resolve(skill.path ?? join(sourceDir, 'SKILL.md'));
      explicitSkills.set(skillPath, { name: skill.name, path: skillPath });
    }
  }
  return { ...discovery, explicitSkills: [...explicitSkills.values()] };
}

/** Execute one fully-preflighted selection through Vally's plan, runner, backend and reporters. */
export async function runBundle(bundleRoot, {
  output,
  evalNames = [],
  stimulusNames = [],
  dryRun = false,
  codexBinary,
  judgeBackend = 'none',
  reasoningEffort = 'max',
  navigationContext = false,
} = {}, {
  executor: suppliedExecutor,
  judgeClient: suppliedJudgeClient,
  backend: suppliedBackend,
  appServerFactory,
  skillIsolationResolver,
} = {}) {
  let resolved;
  let planned;
  let fullPlanned;
  let fullPlanError;
  let inputCapture;
  let runProvenance;
  let skillDiscovery;
  let executor;
  let judgeClient = suppliedJudgeClient;
  let judgeClientKind = suppliedJudgeClient ? 'injected' : 'none';
  let actualJudgeBackendSelected = false;
  let graders;
  let preflight;
  try {
    if (!Array.isArray(evalNames) || evalNames.some(name => typeof name !== 'string' || !name.trim()))
      throw new Error('evalNames must contain nonempty strings');
    if (!Array.isArray(stimulusNames) || stimulusNames.some(name => typeof name !== 'string' || !name.trim()))
      throw new Error('stimulusNames must contain nonempty strings');
    if (!['low', 'medium', 'high', 'xhigh', 'max'].includes(reasoningEffort)) throw new Error('Unsupported reasoningEffort');
    if (typeof navigationContext !== 'boolean') throw new Error('navigationContext must be boolean');
    if (typeof dryRun !== 'boolean') throw new Error('dryRun must be a boolean');
    if (!['none', 'codex'].includes(judgeBackend)) throw new Error('judgeBackend must be none or codex');
    if (codexBinary !== undefined && (typeof codexBinary !== 'string' || !codexBinary.trim()))
      throw new Error('codexBinary must be a nonempty executable path');
    resolved = await resolveFreshOutput(bundleRoot, output);
    if (judgeBackend === 'codex' && suppliedJudgeClient)
      throw new Error('judgeClient injection cannot be combined with judgeBackend selection');
    executor = dryRun ? makePlanningExecutor() : (suppliedExecutor ?? new CodexExecutor({ binary: codexBinary, reasoningEffort, navigationContext }));
    if (judgeBackend === 'codex') {
      if (dryRun) {
        judgeClient = makePlanningJudgeClient();
        judgeClientKind = 'planning-fake';
      } else {
        judgeClient = new CodexJudgeClient({
          binary: codexBinary ?? 'codex',
          model: 'gpt-6-luna',
          reasoningEffort,
          auditDir: join(resolved.output, 'judge-evidence'),
          ...(appServerFactory ? { appServerFactory } : {}),
          ...(skillIsolationResolver ? { skillIsolationResolver } : {}),
        });
        judgeClientKind = 'codex';
        actualJudgeBackendSelected = true;
      }
    }
    const planningJudgeClient = judgeClient;
    graders = planningJudgeClient ? createDefaultGraderRegistry(planningJudgeClient) : undefined;
    planned = await buildBundlePlan(resolved.root, {
      executor,
      graderRegistry: graders,
      evalNames,
      stimulusNames,
      workspaceRoot: join(resolved.output, 'workspaces'),
    });
    if (evalNames.length || stimulusNames.length) {
      try {
        fullPlanned = await buildBundlePlan(resolved.root, {
          executor,
          graderRegistry: planned.graderRegistry,
          workspaceRoot: join(resolved.output, 'workspaces'),
        });
      } catch (error) {
        fullPlanError = errorText(error);
      }
    } else {
      fullPlanned = planned;
    }
    inputCapture = await captureRunInputs(resolved.root, (fullPlanned ?? planned).discovered.evals);
    runProvenance = makeRunProvenance({ bundleRoot: resolved.root, selectedPlan: planned,
      fullPlan: fullPlanned, fullPlanError, inputCapture });
    runProvenance.judge = judgeProvenance({ requestedBackend: judgeBackend, dryRun, judgeClientKind });
    skillDiscovery = await discoverSelectedSkills(resolved.root, planned);
    const checked = planBlockers({ ...planned, graderRegistry: planned.graderRegistry, judgeClient: planningJudgeClient,
      output: resolved.output, skillDiscovery });
    checked.blockers.push(...inputCapture.sourcePinErrors);
    if (fullPlanError && !evalNames.length && !stimulusNames.length)
      checked.blockers.push(`Could not prove the unfiltered Vally suite plan: ${fullPlanError}`);
    if (fullPlanned) {
      const fullIds = new Set(fullPlanned.plan.items.map(item => item.id));
      if (planned.plan.items.some(item => !fullIds.has(item.id)))
        checked.blockers.push('selected Vally trial identities do not match the unfiltered bundle plan');
    }
    preflight = makePreflightReport({ output: resolved.output, evalNames, stimulusNames, dryRun,
      plan: planned.plan, discovery: planned.discovered, preparation: planned.preparation,
      blockers: [...new Set(checked.blockers)], llmGraderApplications: checked.llmGraderApplications,
      judgeClient, judgeBackend, actualJudgeBackendSelected, judgeClientKind, runProvenance });
    preflight.discoveredSkills = skillDiscovery.skills.length + skillDiscovery.explicitSkills.length;
    preflight.selectedEnvironmentSkills = skillDiscovery.explicitSkills.length;
    preflight.graderCapabilityAudits = planned.graderCapabilityAudits;
    if (preflight.blockers.length || dryRun) {
      return await persistPreflightReport(preflight, resolved.output);
    }
  } catch (error) {
    const report = makePreflightReport({ output: resolved?.output ?? output, evalNames, stimulusNames, dryRun,
      plan: planned?.plan, discovery: planned?.discovered, preparation: planned?.preparation,
      blockers: [errorText(error)], llmGraderApplications: 0, judgeClient, judgeBackend,
      actualJudgeBackendSelected, judgeClientKind, runProvenance });
    report.status = 'preflight-failed';
    report.exitCode = 1;
    return await persistPreflightReport(report, resolved?.output);
  }

  const outputDir = resolved.output;
  const files = runArtifacts(outputDir);
  let outputCreated = false;
  let jsonlStream;
  let reporters;
  let runtimeStarted = false;
  let reportStarted = false;
  let reportCompleted = false;
  let status = 'failed';
  let failure;
  let summary;
  let hadExecutionErrors = false;
  const results = [];
  let backend;
  const oldTemp = process.env.TEMP;
  const oldTmp = process.env.TMP;
  let tempOverridesApplied = false;

  try {
    await mkdir(outputDir);
    outputCreated = true;
    const runtimeTmp = join(outputDir, 'runtime-tmp');
    await mkdir(runtimeTmp);
    process.env.TEMP = runtimeTmp;
    process.env.TMP = runtimeTmp;
    tempOverridesApplied = true;

    jsonlStream = createWriteStream(join(outputDir, RESULTS_PATH), { flags: 'wx' });
    reporters = new CompositeEvalReporter([
      new EvalJsonlReporter({ stream: jsonlStream }),
      new EvalMarkdownReporter({ outputPath: join(outputDir, MARKDOWN_PATH) }),
      new EvalJunitReporter({ outputPath: join(outputDir, JUNIT_PATH) }),
    ]);
    backend = suppliedBackend ?? createDefaultBackendRegistry().get('local')?.create();
    if (!backend) throw new Error('Vally local backend is unavailable');

    runtimeStarted = true;
    reportStarted = true;
    await ensureReporterOk(reporters, 'onRunStart', [{ evals: planned.plan.evals, totalItems: planned.plan.totalItems, workers: 1,
      source: { name: 'hve-codex-vally-runner', version: '0.1.0' } }]);
    if (planned.plan.diagnostics.length)
      await ensureReporterOk(reporters, 'onDiagnostics', [planned.plan.diagnostics]);
    await backend.prepare?.({}, { plugins: { cwd: planned.projectCtx.dir || resolved.root } });

    const acquireSessionLog = sessionLogAllocator(outputDir);
    for (const item of planned.plan.items) {
      await ensureReporterOk(reporters, 'onTrialStart', [item]);
      let result;
      const trialBegan = Date.now();
      try {
        result = await runTrialItem(item, backend, {
          skills: skillDiscovery.skills,
          acquireSessionLog: attemptIndex => acquireSessionLog(item, attemptIndex),
          resolveArtifactsDir: info => scopedOutputPath(outputDir, 'artifacts', info),
          resolveExecutorArtifactMaterializeDir: info => scopedOutputPath(outputDir, 'executor-artifacts', info),
          resolveWorkspacePatchPath: info => join(outputDir, 'workspace-patches', `${stableId(info.trajectoryId)}.patch`),
          onDiagnostic: diagnostics => reporters.onDiagnostics([{ ...diagnostics, scope: {
            evalName: item.evalName, evalFilePath: item.evalFilePath, variant: item.variant, model: item.model,
          } }]),
          onPhase: (phase, detail) => reporters.onTrialPhase(item.id, phase, detail),
        });
      } catch (error) {
        result = { itemId: item.id, status: 'error', trajectory: null, grade: null,
          error: errorText(error), durationMs: Date.now() - trialBegan };
        hadExecutionErrors = true;
      }
      result.graderInputErrors = (result.grade?.details ?? []).filter(detail => detail.status === 'error').map(detail => ({ name: detail.name, evidence: detail.evidence }));
      results.push(result);
      await ensureReporterOk(reporters, 'onTrialResult', [{ item, result }]);
      if (result.status === 'error') {
        hadExecutionErrors = true;
        failure ??= result.error ?? 'Vally trial failed';
        break;
      }
      if (result.status !== 'success') {
        failure ??= `Unexpected Vally trial status: ${result.status}`;
        break;
      }
      if (reporters.erroredReporterCount > 0) throw new Error('Vally reporter failure; stopping after the current trial');
    }
    status = results.length === planned.plan.totalItems && !hadExecutionErrors && !failure ? 'completed' : 'failed';
  } catch (error) {
    failure ??= errorText(error);
    status = 'failed';
  } finally {
    const cleanupErrors = [];
    if (runtimeStarted && backend) {
      try { await backend.shutdown(); } catch (error) { cleanupErrors.push(`Vally backend shutdown failed: ${errorText(error)}`); }
    }
    if (runtimeStarted && executor) {
      try { await executor.shutdown?.(); } catch (error) { cleanupErrors.push(`Codex executor shutdown failed: ${errorText(error)}`); }
    }
    if (runtimeStarted && judgeClient && preflight.llmGraderApplications > 0) {
      try { await judgeClient.shutdown(); } catch (error) { cleanupErrors.push(`LLM judge shutdown failed: ${errorText(error)}`); }
    }
    if (cleanupErrors.length) {
      failure ??= cleanupErrors.join('; ');
      status = 'failed';
    }

    const allItemsHaveResults = results.length === planned.plan.totalItems;
    try {
      summary = allItemsHaveResults
        ? planned.plan.summarize(results)
        : createPartialRunSummary(hadExecutionErrors);
      if (reportStarted && reporters) {
        if (allItemsHaveResults) {
          for (const evaluation of summary.evals ?? [])
            await ensureReporterOk(reporters, 'onEvalComplete', [evaluation]);
        }
        await ensureReporterOk(reporters, 'onRunComplete', [summary, files, status]);
        reportCompleted = true;
      }
    } catch (error) {
      failure ??= errorText(error);
      status = 'failed';
    }

    if (jsonlStream) {
      try { await closeStream(jsonlStream); }
      catch (error) { failure ??= `Could not finalize Vally JSONL report: ${errorText(error)}`; status = 'failed'; }
    }
    if (tempOverridesApplied) {
      restoreEnvValue('TEMP', oldTemp);
      restoreEnvValue('TMP', oldTmp);
    }
  }

  if (runProvenance && inputCapture) {
    try {
      const afterCapture = await captureRunInputs(resolved.root, (fullPlanned ?? planned).discovered.evals);
      const integrity = compareInputSnapshots(inputCapture.snapshot, afterCapture.snapshot);
      runProvenance.source.integrity.afterExecution = compactInputSnapshot(afterCapture.snapshot);
      runProvenance.source.integrity.unchanged = integrity.unchanged;
      runProvenance.source.integrity.changedPaths = integrity.changedPaths;
      runProvenance.source.integrity.errors = afterCapture.sourcePinErrors;
      runProvenance.source.verification.afterExecutionValid = afterCapture.sourcePinErrors.length === 0;
      runProvenance.source.verification.afterExecutionErrors = afterCapture.sourcePinErrors;
      if (!integrity.unchanged) {
        failure ??= `Bundle, eval, or runtime source files changed during execution: ${integrity.changedPaths.join(', ')}`;
        status = 'failed';
      }
      if (afterCapture.sourcePinErrors.length) {
        failure ??= `Could not verify source pin after execution: ${afterCapture.sourcePinErrors.join('; ')}`;
        status = 'failed';
      }
    } catch (error) {
      runProvenance.source.integrity.afterExecution = { captureError: errorText(error) };
      runProvenance.source.integrity.unchanged = false;
      runProvenance.source.integrity.changedPaths = ['<unverifiable>'];
      runProvenance.source.integrity.errors = [errorText(error)];
      runProvenance.source.verification.afterExecutionValid = false;
      runProvenance.source.verification.afterExecutionErrors = [errorText(error)];
      failure ??= `Could not verify source inputs after execution: ${errorText(error)}`;
      status = 'failed';
    }
  }

  const finalSummary = summary ?? createPartialRunSummary(hadExecutionErrors);
  const complete = status === 'completed' && results.length === planned.plan.totalItems;
  const exitCode = complete && finalSummary.passed ? 0 : 1;
  if (runProvenance) recordExecutionProvenance(runProvenance, results, finalSummary, runtimeStarted, status);
  const report = {
    ...preflight,
    status,
    runtimeInvoked: runtimeStarted,
    completedTrials: results.length,
    graderInputErrors: results.flatMap(result => result.graderInputErrors ?? []),
    remainingTrials: Math.max(0, planned.plan.totalItems - results.length),
    executionErrors: hadExecutionErrors,
    reportsFinalized: reportCompleted,
    summary: compactSummary(finalSummary),
    ...(failure ? { failure } : {}),
    files: { ...files, runReport: join(outputDir, REPORT_PATH) },
    exitCode,
  };
  if (outputCreated) {
    try { await writeFile(join(outputDir, REPORT_PATH), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' }); }
    catch (error) {
      report.status = 'failed';
      report.exitCode = 1;
      report.failure = [report.failure, `Could not write run report: ${errorText(error)}`].filter(Boolean).join('; ');
    }
  }
  return report;
}

function parseArgs(args) {
  const parsed = { evalNames: [], stimulusNames: [], dryRun: false, judgeBackend: 'none' };
  let haveBundle = false;
  let haveOutput = false;
  let haveBinary = false;
  let haveJudgeBackend = false;
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === '--navigation-context') {
      if (parsed.navigationContext) throw new Error('duplicate --navigation-context');
      parsed.navigationContext = true; continue;
    }
    if (flag === '--dry-run') {
      if (parsed.dryRun) throw new Error('duplicate --dry-run');
      parsed.dryRun = true;
      continue;
    }
    if (!['--bundle', '--output', '--eval', '--stimulus', '--codex-binary', '--judge-backend', '--reasoning-effort'].includes(flag))
      throw new Error(`unsupported argument: ${flag}`);
    const value = args[++index];
    if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value`);
    if (flag === '--bundle') {
      if (haveBundle) throw new Error('duplicate --bundle');
      parsed.bundle = value; haveBundle = true;
    } else if (flag === '--output') {
      if (haveOutput) throw new Error('duplicate --output');
      parsed.output = value; haveOutput = true;
    } else if (flag === '--codex-binary') {
      if (haveBinary) throw new Error('duplicate --codex-binary');
      if (!value.trim()) throw new Error('--codex-binary requires a nonempty executable path');
      parsed.codexBinary = value; haveBinary = true;
    } else if (flag === '--judge-backend') {
      if (haveJudgeBackend) throw new Error('duplicate --judge-backend');
      if (!['none', 'codex'].includes(value)) throw new Error('--judge-backend must be none or codex');
      parsed.judgeBackend = value; haveJudgeBackend = true;
    } else if (flag === '--reasoning-effort') {
      if (parsed.reasoningEffort !== undefined) throw new Error('duplicate --reasoning-effort');
      if (!['low', 'medium', 'high', 'xhigh', 'max'].includes(value)) throw new Error('Unsupported reasoning effort');
      parsed.reasoningEffort = value;
    } else if (flag === '--eval') parsed.evalNames.push(value);
    else if (flag === '--stimulus') parsed.stimulusNames.push(value);
  }
  if (!haveBundle || !haveOutput) throw new Error('Usage: node run.mjs --bundle PATH --output FRESH_DIR [--eval NAME] [--stimulus NAME] [--codex-binary PATH] [--judge-backend none|codex] [--dry-run]');
  return parsed;
}

export { parseArgs as parseRunnerArgs };

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const { bundle, ...options } = parseArgs(process.argv.slice(2));
    const report = await runBundle(bundle, options);
    let savedReportSha256 = null;
    if (report.files?.runReport) {
      try { savedReportSha256 = sha256(await readFile(report.files.runReport)); }
      catch { /* The compact interface reports a null hash if persistence failed. */ }
    }
    process.stdout.write(JSON.stringify(compactCliSummary(report, savedReportSha256)) + '\n');
    process.exitCode = report.exitCode ?? (report.status === 'dry-run' ? 0 : report.status === 'completed' ? 0 : 1);
  } catch (error) {
    process.stderr.write(`${errorText(error)}\n`);
    process.exitCode = 1;
  }
}
