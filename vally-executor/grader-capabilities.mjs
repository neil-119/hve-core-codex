/**
 * Report a small, explicit set of upstream grader predicates whose event
 * vocabulary is not verified by the Codex app-server adapter.
 *
 * This is diagnostic only: it never edits the evaluation, rewrites a grader,
 * or claims that unreported predicates are compatible.
 */

const NATIVE_COMMAND_NAME = 'exec_command';
const SHELL_TOOL_NAMES = new Set([
  'bash',
  'powershell',
  'shell',
  'terminal',
  'run_in_terminal',
]);

function cloneData(value) {
  if (Array.isArray(value)) return value.map(cloneData);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneData(item)]));
  }
  return value;
}

/** Parse only simple, fully anchored alternatives; do not execute arbitrary regexes. */
function anchoredAlternatives(pattern) {
  if (typeof pattern !== 'string' || !pattern.startsWith('^') || !pattern.endsWith('$')) return null;
  let body = pattern.slice(1, -1);
  if (body.startsWith('(?:') && body.endsWith(')')) body = body.slice(3, -1);
  else if (body.startsWith('(') && body.endsWith(')')) body = body.slice(1, -1);
  if (!body || /[()[\]{}.*+?\\]/.test(body)) return null;
  const alternatives = body.split('|');
  return alternatives.every(value => /^[A-Za-z0-9_-]+$/.test(value)) ? alternatives : null;
}

function shellNameAlternatives(pattern) {
  const alternatives = anchoredAlternatives(pattern);
  return alternatives?.filter(name => SHELL_TOOL_NAMES.has(name.toLowerCase())) ?? [];
}

function graderIdentity(stimulus, stimulusIndex, grader, graderIndex) {
  return {
    stimulus: stimulus?.name ?? stimulus?.id ?? `stimuli[${stimulusIndex}]`,
    grader: grader?.name ?? `graders[${graderIndex}]`,
  };
}

function toolCallFindings(stimulus, stimulusIndex, grader, graderIndex) {
  if (!grader.config || typeof grader.config !== 'object' || Array.isArray(grader.config)) return [];
  const findings = [];
  const identity = graderIdentity(stimulus, stimulusIndex, grader, graderIndex);
  for (const listName of ['required', 'sequence', 'parallel']) {
    const entries = grader.config[listName];
    if (!Array.isArray(entries)) continue;
    for (const [entryIndex, entry] of entries.entries()) {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
      const shellNames = shellNameAlternatives(entry.name);
      if (!shellNames.length || anchoredAlternatives(entry.name).includes(NATIVE_COMMAND_NAME)) continue;
      findings.push({
        ...identity,
        category: 'anchored-shell-tool-name',
        path: `stimuli[${stimulusIndex}].graders[${graderIndex}].config.${listName}[${entryIndex}].name`,
        predicatePath: 'trajectory.events[].data.toolName',
        nativeEvidencePath: 'commandExecution -> proven Windows PowerShell wrapper or unverified exec_command',
        reason: `The exact anchored alternatives ${shellNames.join(', ')} omit ${NATIVE_COMMAND_NAME}. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain ${NATIVE_COMMAND_NAME}. This predicate is not verified across every execution path.`,
        originalRegex: entry.name,
        originalMatcher: cloneData(entry),
        originalConfig: cloneData(grader.config),
      });
    }
  }

  const disallowed = grader.config.disallowed;
  if (Array.isArray(disallowed)) {
    for (const [entryIndex, entry] of disallowed.entries()) {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry) || entry.write_only !== true) continue;
      const shellNames = shellNameAlternatives(entry.name);
      if (!shellNames.length) continue;
      findings.push({
        ...identity,
        category: 'write-only-shell-disallow',
        path: `stimuli[${stimulusIndex}].graders[${graderIndex}].config.disallowed[${entryIndex}]`,
        predicatePath: 'tool-call name plus extracted write target',
        nativeEvidencePath: 'commandExecution -> proven Windows PowerShell wrapper or unverified exec_command; Vally write_only does not recognize exec_command',
        reason: `The original write_only disallow rule names ${shellNames.join(', ')}. Commands without a proven Windows PowerShell wrapper remain ${NATIVE_COMMAND_NAME}, whose write targets the current Vally matcher does not extract. Enforcement across all native command paths remains unverified.`,
        originalRegex: entry.name,
        originalMatcher: cloneData(entry),
        originalConfig: cloneData(grader.config),
      });
    }
  }
  return findings;
}

function programSnippet(config) {
  if (typeof config?.code === 'string') return config.code;
  if (Array.isArray(config?.args)) {
    const candidate = [...config.args].reverse().find(value => typeof value === 'string' && value.includes('trajectory'));
    if (candidate !== undefined) return candidate;
  }
  if (typeof config?.program === 'string' && config.program.includes('trajectory')) return config.program;
  return null;
}

function hasSkillAndReferenceReadAlternative(source) {
  if (typeof source !== 'string') return false;
  const hasSkillBranch = /event\.type\s*===\s*['"]skill_activation['"]/.test(source) &&
    /\^skill\$/.test(source);
  const hasReferenceRead = /event\.type\s*===\s*['"]tool_call['"]/.test(source) &&
    /\^\(\?:view\|read\|read_file\)\$/.test(source) &&
    /\breference\s*=\s*false\b/.test(source) &&
    /\bcoaching\s*&&\s*reference\b/.test(source);
  return hasSkillBranch && hasReferenceRead;
}

function programFindings(stimulus, stimulusIndex, grader, graderIndex) {
  const snippet = programSnippet(grader.config);
  if (typeof snippet === 'string' && snippet.includes('simpleRead') && snippet.includes('mural') && snippet.includes('command')) {
    return [{ ...graderIdentity(stimulus, stimulusIndex, grader, graderIndex), category: 'shell-wrapper-sensitive-program',
      reason: 'Program uses anchored simpleRead command matching; native shell wrapper equivalence is unverified. Preserve the grader and block this contract pending compatible evidence.',
      originalProgramSnippet: snippet, originalConfig: cloneData(grader.config) }];
  }
  if (!hasSkillAndReferenceReadAlternative(snippet)) return [];
  const identity = graderIdentity(stimulus, stimulusIndex, grader, graderIndex);
  return [{
    ...identity,
    category: 'skill-activation-and-reference-read-alternative',
    path: `stimuli[${stimulusIndex}].graders[${graderIndex}].config.args`,
    predicatePath: 'trajectory.events[].type + data.toolName + data.arguments.path/filePath',
    nativeEvidencePath: 'skill activation is not emitted; native file reads through commandExecution normalize as exec_command',
    reason: 'This program has an alternate pass path requiring both skill_activation (or a skill tool call) and a view/read/read_file tool call naming the reference. The current adapter does not emit those event identities for Codex skill loading or command reads. Any separate output-based pass branch is preserved and not evaluated by this audit.',
    originalProgramSnippet: snippet,
    originalConfig: cloneData(grader.config),
  }];
}

/**
 * Return only known unverified event predicates for a parsed Vally spec.
 * Other graders are deliberately left unclassified.
 */
export function auditGraderCapabilities(spec, { evalName = null, sourcePath = null } = {}) {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
    throw new TypeError('Vally grader capability audit requires a spec object.');
  }
  if (spec.stimuli !== undefined && !Array.isArray(spec.stimuli)) {
    throw new TypeError('Vally spec stimuli must be an array when present.');
  }

  const findings = [];
  for (const [stimulusIndex, stimulus] of (spec.stimuli ?? []).entries()) {
    if (!stimulus || typeof stimulus !== 'object' || Array.isArray(stimulus) || !Array.isArray(stimulus.graders)) continue;
    for (const [graderIndex, grader] of stimulus.graders.entries()) {
      if (!grader || typeof grader !== 'object' || Array.isArray(grader)) continue;
      if (grader.type === 'tool-calls') findings.push(...toolCallFindings(stimulus, stimulusIndex, grader, graderIndex));
      if (grader.type === 'program') findings.push(...programFindings(stimulus, stimulusIndex, grader, graderIndex));
    }
  }

  return {
    schemaVersion: 1,
    evalName,
    sourcePath,
    status: findings.length ? 'known-unverified-predicates' : 'no-targeted-unverified-predicate-found',
    acceptanceClaim: false,
    overallCompatibility: 'not-assessed',
    auditedScope: [
      'anchored shell-name tool-call predicates',
      'write_only disallowed shell predicates',
      'skill-activation plus reference-read program alternatives',
    ],
    otherPredicatesAssessed: false,
    unverifiedPredicateCount: findings.length,
    findings,
  };
}
