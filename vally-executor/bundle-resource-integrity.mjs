import { createHash } from 'node:crypto';
import { lstat, readFile, readdir } from 'node:fs/promises';
import { isAbsolute, resolve, join } from 'node:path';

// Repository-contract fixtures include generated guidance across multiple isolated specs.
const MAX_FILES = 40000;
const MAX_FILE_BYTES = 32 * 1024 * 1024;
const MAX_TOTAL_BYTES = 1024 * 1024 * 1024;
function parts(path) {
  if (typeof path !== 'string' || !path || isAbsolute(path) || /[:\\\0]/.test(path) ||
      path.split('/').some(part => !part || part === '.' || part === '..'))
    throw new Error('Bundle resource path is unsafe');
  return path.split('/');
}
async function checkedPath(root, path) {
  let target = resolve(root);
  for (const part of parts(path)) {
    target = join(target, part);
    if ((await lstat(target)).isSymbolicLink()) throw new Error(`Bundle resource is symlinked: ${path}`);
  }
  return target;
}
export async function captureBundleResources(root, sourceMap) {
  if (!sourceMap || typeof sourceMap !== 'object' || Array.isArray(sourceMap))
    throw new Error('Bundle resource source map is malformed');
  const declared = new Map(), directories = new Set();
  let outputs = 0;
  for (const mapping of Object.values(sourceMap)) {
    if (!Array.isArray(mapping?.outputs)) throw new Error('Bundle resource output map is malformed');
    for (const output of mapping.outputs) {
      if (++outputs > MAX_FILES * 4) throw new Error('Bundle resource map exceeds output limit');
      parts(output?.path);
      if (output.status?.endsWith('-directory')) { directories.add(output.path); continue; }
      if (!/^[a-f0-9]{64}$/i.test(output.sha256 ?? '')) throw new Error(`Bundle resource hash is missing: ${output.path}`);
      const previous = declared.get(output.path);
      if (previous && previous !== output.sha256) throw new Error(`Bundle resource hashes conflict: ${output.path}`);
      declared.set(output.path, output.sha256);
      if (declared.size > MAX_FILES) throw new Error('Bundle resource map exceeds file limit');
    }
  }
  let traversed = 0;
  async function walk(path) {
    if (++traversed > MAX_FILES * 2) throw new Error('Bundle resource directory exceeds entry limit');
    const target = await checkedPath(root, path);
    const info = await lstat(target);
    if (info.isDirectory()) {
      if (!directories.has(path) && ![...declared.keys()].some(file => file.startsWith(`${path}/`)))
        throw new Error(`Bundle resource directory contains an undeclared directory: ${path}`);
      for (const name of (await readdir(target)).sort()) await walk(`${path}/${name}`);
    } else if (!info.isFile() || !declared.has(path)) throw new Error(`Bundle resource directory contains an undeclared file: ${path}`);
  }
  for (const directory of [...directories].sort()) await walk(directory);
  const records = []; let total = 0;
  for (const [path, expected] of [...declared].sort(([a],[b]) => a < b ? -1 : a > b ? 1 : 0)) {
    const target = await checkedPath(root, path), info = await lstat(target);
    if (!info.isFile() || info.size > MAX_FILE_BYTES || (total += info.size) > MAX_TOTAL_BYTES)
      throw new Error(`Bundle resource is not a bounded regular file: ${path}`);
    const bytes = await readFile(target);
    if (bytes.length !== info.size) throw new Error(`Bundle resource changed while reading: ${path}`);
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    if (sha256 !== expected) throw new Error(`Bundle resource hash differs from source map: ${path}`);
    records.push({path, bytes: bytes.length, sha256});
  }
  return records;
}
