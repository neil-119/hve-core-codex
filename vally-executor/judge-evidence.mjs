import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import { isAbsolute, join, parse, relative, resolve, sep } from 'node:path';
import { Worker } from 'node:worker_threads';

const MAX_TOOL_CALLS = 12;
const MAX_RESULT_CHARS = 16_000;
const MAX_TOTAL_CHARS = 64_000;
const MAX_EVIDENCE_FILES = 64;
const MAX_PATH_CHARS = 4_096;
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const MAX_PATTERN_CHARS = 256;
const MAX_GREP_MATCHES = 256;
const MAX_GREP_LINE_CHARS = 2_000;
const REGEX_TIMEOUT_MS = 250;

const REGEX_WORKER_SOURCE = `
const { parentPort, workerData } = require('node:worker_threads');
try {
  const regex = new RegExp(workerData.pattern, 'u');
  const content = workerData.content;
  const lines = content.length === 0 ? [] : content.split(/\\r\\n|\\r|\\n/);
  if (lines.length && /(?:\\r\\n|\\r|\\n)$/.test(content)) lines.pop();
  const matches = [];
  let truncated = false;
  for (let index = 0; index < lines.length; index += 1) {
    const text = lines[index];
    const match = regex.exec(text);
    if (!match) continue;
    if (matches.length >= workerData.maxMatches) {
      truncated = true;
      break;
    }
    // Large serialized tool outputs can put a relevant fact well beyond the
    // line prefix. Return bounded source context around the observed match.
    let start = Math.max(0, Math.min(Math.max(0, text.length - workerData.maxLineChars),
      match.index - Math.floor(workerData.maxLineChars / 4)));
    if (start > 0 && /[\\uDC00-\\uDFFF]/.test(text[start]) && /[\\uD800-\\uDBFF]/.test(text[start - 1])) start -= 1;
    let end = Math.min(text.length, start + workerData.maxLineChars);
    // Preserve a bounded match when its context consumes most of the window,
    // including a Unicode boundary adjustment immediately before EOF.
    if (match[0].length <= workerData.maxLineChars && end < match.index + match[0].length) {
      start = Math.max(0, match.index + match[0].length - workerData.maxLineChars);
      if (start > 0 && /[\\uDC00-\\uDFFF]/.test(text[start]) && /[\\uD800-\\uDBFF]/.test(text[start - 1])) start += 1;
      end = Math.min(text.length, start + workerData.maxLineChars);
    }
    if (end < text.length && /[\\uD800-\\uDBFF]/.test(text[end - 1]) && /[\\uDC00-\\uDFFF]/.test(text[end])) end -= 1;
    matches.push({
      line: index + 1,
      text: text.slice(start, end),
      startColumn: start + 1, endColumn: end, totalChars: text.length, matchColumn: match.index + 1,
      matchEndColumn: match.index + match[0].length,
      matchTruncated: match.index < start || match.index + match[0].length > end,
      lineTruncated: text.length > workerData.maxLineChars,
    });
  }
  parentPort.postMessage({ matches, truncated });
} catch (error) {
  parentPort.postMessage({ error: error && error.name === 'SyntaxError' ? 'Invalid regular expression.' : 'Regex worker failed.' });
}
`;

function rawPathKey(value) {
  const normalized = value.replaceAll('\\', '/').replace(/\/+$/, '') || '/';
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function hasDotSegment(value) {
  const root = parse(value).root;
  return value.slice(root.length).split(/[\\/]+/).some(segment => segment === '.' || segment === '..');
}

function isCanonicalAbsolutePath(value, label) {
  if (typeof value !== 'string' || !value.length || value.length > MAX_PATH_CHARS || value.includes('\0') || !isAbsolute(value))
    throw new Error(`${label} must be an absolute path`);
  if (hasDotSegment(value)) throw new Error(`${label} contains a traversal segment`);
  const normalized = value.replaceAll('\\', '/').replace(/\/+$/, '') || '/';
  const resolved = resolve(value).replaceAll('\\', '/').replace(/\/+$/, '') || '/';
  const equal = process.platform === 'win32' ? normalized.toLowerCase() === resolved.toLowerCase() : normalized === resolved;
  if (!equal) throw new Error(`${label} must be canonical`);
  return value;
}

function isWithin(parent, candidate) {
  const rel = relative(parent, candidate);
  return rel === '' || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`));
}

function samePath(left, right) {
  return rawPathKey(left) === rawPathKey(right);
}

export function sameFileIdentity(left, right) {
  if (left.size !== right.size || left.mtimeMs !== right.mtimeMs || left.ctimeMs !== right.ctimeMs)
    return false;
  const identityAvailable = Number.isFinite(left.dev) && Number.isFinite(left.ino) &&
    Number.isFinite(right.dev) && Number.isFinite(right.ino) &&
    (left.dev !== 0 || left.ino !== 0 || right.dev !== 0 || right.ino !== 0);
  return !identityAvailable || (left.dev === right.dev && left.ino === right.ino);
}

async function inspectPathChain(path, expectedKind, workspaceRoot) {
  isCanonicalAbsolutePath(path, 'Evidence path');
  if (workspaceRoot && (!isWithin(workspaceRoot, path) || samePath(workspaceRoot, path)))
    throw new Error('Evidence path is outside the supplied workspace');

  const root = parse(path).root;
  const components = path.slice(root.length).split(/[\\/]+/).filter(Boolean);
  let current = root;
  let finalStat;
  for (let index = 0; index < components.length; index += 1) {
    current = join(current, components[index]);
    let info;
    try { info = await lstat(current); }
    catch { throw new Error('Evidence path is unavailable'); }
    if (info.isSymbolicLink()) throw new Error('Evidence path contains a symbolic link or junction');
    if (index < components.length - 1 && !info.isDirectory())
      throw new Error('Evidence path contains a non-directory component');
    if (index === components.length - 1) finalStat = info;
  }

  if (!finalStat) throw new Error('Evidence path is unavailable');
  if (expectedKind === 'directory' && !finalStat.isDirectory())
    throw new Error('Evidence workspace must be a directory');
  if (expectedKind === 'file' && !finalStat.isFile())
    throw new Error('Evidence path must be a regular file');

  let resolvedPath;
  try { resolvedPath = await realpath(path); }
  catch { throw new Error('Evidence path is unavailable'); }
  if (!samePath(resolvedPath, path)) throw new Error('Evidence path resolves through an alias or symbolic link');
  return finalStat;
}

async function readEvidenceText(path, workspaceRoot) {
  const beforePath = await inspectPathChain(path, 'file', workspaceRoot);
  const noFollow = constants.O_NOFOLLOW ?? 0;
  let handle;
  try {
    handle = await open(path, constants.O_RDONLY | noFollow);
    const before = await handle.stat();
    if (!before.isFile()) throw new Error('Evidence path must be a regular file');
    if (!sameFileIdentity(beforePath, before)) throw new Error('Evidence file changed while it was being opened');
    if (before.size > MAX_FILE_BYTES) throw new Error(`Evidence file exceeds ${MAX_FILE_BYTES} bytes`);

    const buffer = Buffer.alloc(Math.max(1, before.size));
    let bytesRead = 0;
    while (bytesRead < before.size) {
      const result = await handle.read(buffer, bytesRead, before.size - bytesRead, bytesRead);
      if (!result.bytesRead) break;
      bytesRead += result.bytesRead;
    }
    const after = await handle.stat();
    if (bytesRead !== before.size || !sameFileIdentity(before, after))
      throw new Error('Evidence file changed while it was being read');
    if (after.size > MAX_FILE_BYTES) throw new Error(`Evidence file exceeds ${MAX_FILE_BYTES} bytes`);

    const afterPath = await inspectPathChain(path, 'file', workspaceRoot);
    if (!sameFileIdentity(after, afterPath)) throw new Error('Evidence path changed while it was being read');
    let content;
    try { content = new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, bytesRead)); }
    catch { throw new Error('Evidence file is not valid UTF-8 text'); }
    return content;
  } finally {
    if (handle) await handle.close();
  }
}

function splitLines(content) {
  if (content.length === 0) return [];
  const lines = content.split(/\r\n|\r|\n/);
  if (/(?:\r\n|\r|\n)$/.test(content)) lines.pop();
  return lines;
}

function printableLine(line) {
  return line.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,
    character => `\\x${character.charCodeAt(0).toString(16).padStart(2, '0')}`);
}

function truncateResult(value, limit) {
  if (value.length <= limit) return value;
  const marker = '\n[truncated at evidence character limit]';
  if (limit <= marker.length) return value.slice(0, limit);
  let end = limit - marker.length;
  if (end > 0 && /[\uD800-\uDBFF]/.test(value[end - 1])) end -= 1;
  return value.slice(0, end) + marker;
}

function toolSpec(name, description, properties, required, paths) {
  return Object.freeze({
    type: 'function',
    name,
    description,
    inputSchema: Object.freeze({
      type: 'object',
      properties: Object.freeze(properties(paths)),
      required: Object.freeze(required),
      additionalProperties: false,
    }),
  });
}

function createToolSpecs(paths) {
  if (!paths.length) return [];
  const pathSchema = { type: 'string', enum: paths };
  return [
    toolSpec('view', 'Read one exact listed UTF-8 evidence file. Optional view_range is [startLine,endLine], inclusive; use -1 as endLine to read through EOF.', paths => ({
      path: { ...pathSchema },
      view_range: { type: 'array', minItems: 2, maxItems: 2, items: { type: 'integer', minimum: -1 } },
    }), ['path'], paths),
    toolSpec('grep', 'Search the listed UTF-8 evidence files with a bounded regular expression. Long lines return context near the match start with character locations; a long matched span may be omitted. Refine broad patterns to locate specific text when a match is truncated. Always provide explicit paths from the supplied evidence list.', paths => ({
      pattern: { type: 'string', minLength: 1, maxLength: MAX_PATTERN_CHARS },
      paths: { type: 'array', minItems: 1, maxItems: paths.length, uniqueItems: true, items: { ...pathSchema } },
    }), ['pattern', 'paths'], paths),
  ];
}

function errorText(error) {
  const message = error instanceof Error ? error.message : 'Evidence request failed';
  return `ERROR: ${message.slice(0, 500)}`;
}

function plainRecord(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function readDataProperty(record, key) {
  const descriptor = Object.getOwnPropertyDescriptor(record, key);
  if (!descriptor || !Object.hasOwn(descriptor, 'value')) throw new Error(`Missing or invalid ${key}`);
  return descriptor.value;
}

function validateKeys(record, allowed, required) {
  if (!plainRecord(record)) throw new Error('Arguments must be a plain object');
  for (const key of Object.keys(record)) {
    if (!allowed.includes(key)) throw new Error(`Unexpected argument ${key}`);
    readDataProperty(record, key);
  }
  for (const key of required) {
    if (!Object.hasOwn(record, key)) throw new Error(`Missing argument ${key}`);
  }
}

function validatePattern(pattern) {
  if (typeof pattern !== 'string' || !pattern.length || pattern.length > MAX_PATTERN_CHARS)
    throw new Error(`pattern must contain 1 to ${MAX_PATTERN_CHARS} characters`);
  if (/[\u0000-\u001f\u007f]/.test(pattern)) throw new Error('pattern contains control characters');
  try { new RegExp(pattern, 'u'); }
  catch { throw new Error('pattern is not a valid regular expression'); }
}

async function boundedRegexSearch(pattern, content) {
  const worker = new Worker(REGEX_WORKER_SOURCE, {
    eval: true,
    workerData: { pattern, content, maxMatches: MAX_GREP_MATCHES, maxLineChars: MAX_GREP_LINE_CHARS },
  });

  return new Promise((resolveResult, rejectResult) => {
    let settled = false;
    let timer;
    const finish = async (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      worker.removeAllListeners();
      try { await worker.terminate(); }
      catch (cleanupError) {
        rejectResult(new Error(`Regex worker cleanup failed: ${cleanupError.message}`));
        return;
      }
      if (error) rejectResult(error);
      else resolveResult(result);
    };
    worker.once('message', result => {
      if (result?.error) finish(new Error(result.error));
      else finish(null, result);
    });
    worker.once('error', error => finish(new Error(`Regex worker failed: ${error.message}`)));
    worker.once('exit', code => {
      if (!settled) finish(new Error(`Regex worker exited before returning a result (code ${code})`));
    });
    timer = setTimeout(() => finish(new Error(`Regex search exceeded ${REGEX_TIMEOUT_MS} ms`)), REGEX_TIMEOUT_MS);
  });
}

function formatView(path, lines, start, end) {
  if (!lines.length) return `${path}: empty file (0 lines)`;
  const last = end === -1 ? lines.length : end;
  const selected = lines.slice(start - 1, last);
  return [
    `${path}: lines ${start}-${last} of ${lines.length}`,
    ...selected.map((line, index) => `${start + index}: ${printableLine(line)}`),
  ].join('\n');
}

function formatGrep(pattern, results) {
  const lines = [];
  for (const result of results) {
    if (!result.matches.length) {
      lines.push(`${result.path}: no matches`);
      continue;
    }
    for (const match of result.matches) {
      const suffix = (match.lineTruncated
        ? ` [line truncated; chars ${match.startColumn}-${match.endColumn} of ${match.totalChars}; match at char ${match.matchColumn}]`
        : '') + (match.matchTruncated
          ? ` [matched span through char ${match.matchEndColumn} omitted in part; refine the pattern to locate specific text]`
          : '');
      lines.push(`${result.path}:${match.line}: ${printableLine(match.text)}${suffix}`);
    }
    if (result.truncated) lines.push(`${result.path}: [additional matching lines omitted]`);
  }
  return lines.length ? lines.join('\n') : `No matches for /${pattern}/ in the listed evidence files.`;
}

export async function createJudgeEvidence(workspace) {
  if (workspace === undefined || workspace === null) {
    return {
      tools: [],
      async call() { return 'ERROR: Evidence reading is unavailable without a workspace.'; },
      usage() { return { toolCalls: 0, chars: 0, budgetExhausted: false }; },
    };
  }
  if (!plainRecord(workspace)) throw new Error('Judge evidence workspace must be a plain object');
  const workingDirectory = isCanonicalAbsolutePath(readDataProperty(workspace, 'workingDirectory'), 'workingDirectory');
  const evidenceFiles = readDataProperty(workspace, 'evidenceFiles');
  if (!Array.isArray(evidenceFiles) || evidenceFiles.length > MAX_EVIDENCE_FILES)
    throw new Error(`evidenceFiles must be an array with at most ${MAX_EVIDENCE_FILES} paths`);
  await inspectPathChain(workingDirectory, 'directory');

  const allowed = new Map();
  const canonicalPaths = [];
  for (const path of evidenceFiles) {
    isCanonicalAbsolutePath(path, 'Evidence file path');
    if (!isWithin(workingDirectory, path) || samePath(workingDirectory, path))
      throw new Error('Evidence file path is outside the supplied workspace');
    const key = rawPathKey(path);
    if (allowed.has(key)) throw new Error('evidenceFiles contains a duplicate path');
    await inspectPathChain(path, 'file', workingDirectory);
    allowed.set(key, path);
    canonicalPaths.push(path);
  }

  const tools = Object.freeze(createToolSpecs(canonicalPaths));
  let toolCalls = 0;
  let chars = 0;
  let budgetExhausted = false;
  let queue = Promise.resolve();

  const serialized = operation => {
    const current = queue.then(operation, operation);
    queue = current.catch(() => {});
    return current;
  };

  const selectPath = value => {
    if (typeof value !== 'string' || !value.length || value.length > MAX_PATH_CHARS || value.includes('\0'))
      throw new Error('path must be an exact listed absolute path');
    if (!isAbsolute(value) || hasDotSegment(value)) throw new Error('path is not a canonical listed path');
    const path = allowed.get(rawPathKey(value));
    if (!path) throw new Error('path is not listed in the evidence workspace');
    if (value !== path) throw new Error('path must exactly match its canonical listed path');
    return path;
  };

  const accountResult = output => {
    const remaining = MAX_TOTAL_CHARS - chars;
    if (remaining <= 0) {
      budgetExhausted = true;
      throw new Error('Evidence character budget is exhausted');
    }
    const result = truncateResult(output, Math.min(MAX_RESULT_CHARS, remaining));
    chars += result.length;
    return result;
  };

  const call = (name, args) => serialized(async () => {
    try {
      if (!tools.length) throw new Error('Evidence reading is unavailable without listed workspace files');
      if (name === 'view') {
        validateKeys(args, ['path', 'view_range'], ['path']);
        const path = selectPath(readDataProperty(args, 'path'));
        const rangeValue = Object.hasOwn(args, 'view_range') ? readDataProperty(args, 'view_range') : undefined;
        if (rangeValue !== undefined && (!Array.isArray(rangeValue) || rangeValue.length !== 2 ||
            !Number.isSafeInteger(rangeValue[0]) || rangeValue[0] < 1 ||
            !Number.isSafeInteger(rangeValue[1]) || (rangeValue[1] < 1 && rangeValue[1] !== -1) ||
            (rangeValue[1] !== -1 && rangeValue[1] < rangeValue[0])))
          throw new Error('view_range must be [positiveStart,positiveEnd] or [positiveStart,-1]');
        if (toolCalls >= MAX_TOOL_CALLS) throw new Error(`Evidence tool call budget of ${MAX_TOOL_CALLS} is exhausted`);
        if (chars >= MAX_TOTAL_CHARS) {
          budgetExhausted = true;
          throw new Error('Evidence character budget is exhausted');
        }
        const content = await readEvidenceText(path, workingDirectory);
        toolCalls += 1;
        const lines = splitLines(content);
        const start = rangeValue?.[0] ?? 1;
        const end = rangeValue?.[1] ?? -1;
        if (lines.length && start > lines.length) throw new Error('view_range starts after the end of the file');
        if (!lines.length && rangeValue) throw new Error('view_range cannot select lines from an empty file');
        if (lines.length && end !== -1 && end > lines.length) throw new Error('view_range ends after the end of the file');
        return accountResult(formatView(path, lines, start, end));
      }

      if (name === 'grep') {
        validateKeys(args, ['pattern', 'paths'], ['pattern', 'paths']);
        const pattern = readDataProperty(args, 'pattern');
        validatePattern(pattern);
        const requestedPaths = readDataProperty(args, 'paths');
        if (!Array.isArray(requestedPaths) || !requestedPaths.length || requestedPaths.length > MAX_EVIDENCE_FILES)
          throw new Error(`paths must be a non-empty explicit array of at most ${MAX_EVIDENCE_FILES} listed files`);
        const paths = requestedPaths.map(selectPath);
        if (new Set(paths.map(rawPathKey)).size !== paths.length) throw new Error('paths contains a duplicate evidence file');
        if (toolCalls >= MAX_TOOL_CALLS) throw new Error(`Evidence tool call budget of ${MAX_TOOL_CALLS} is exhausted`);
        if (chars >= MAX_TOTAL_CHARS) {
          budgetExhausted = true;
          throw new Error('Evidence character budget is exhausted');
        }
        const results = [];
        let countedCall = false;
        for (const path of paths) {
          const content = await readEvidenceText(path, workingDirectory);
          if (!countedCall) {
            toolCalls += 1;
            countedCall = true;
          }
          const matches = await boundedRegexSearch(pattern, content);
          results.push({ path, ...matches });
        }
        return accountResult(formatGrep(pattern, results));
      }

      throw new Error(`Unknown evidence tool: ${String(name).slice(0, 80)}`);
    } catch (error) {
      return errorText(error);
    }
  });

  return {
    tools,
    call,
    usage() { return { toolCalls, chars, budgetExhausted }; },
  };
}
