// Authorized synthetic Git history for Vally's disposable branch-review fixture.
import { execFileSync } from 'node:child_process';
import { basename, dirname, join } from 'node:path';
import { lstatSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';

const root = realpathSync(process.cwd());
if (basename(dirname(root)) !== 'workspaces' || !/^[a-f0-9]{64}$/.test(basename(root)))
  throw new Error('Git setup requires a disposable Vally trial workspace');
try { lstatSync(join(root, '.git')); throw new Error('Refusing an existing Git repository'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const source = join(root, 'src', 'profile_preferences.py');
if (!lstatSync(source).isFile() || lstatSync(source).isSymbolicLink() || realpathSync(source) !== source)
  throw new Error('Review source must be a regular file inside the trial workspace');
const original = readFileSync(source);
const emptyConfig = join(root, '.synthetic-fixture-gitconfig');
writeFileSync(emptyConfig, '', { flag: 'wx' });
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('GIT_')));
Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: emptyConfig,
  GIT_TERMINAL_PROMPT: '0' });
function git(...args) {
  return execFileSync('git', ['-c', 'user.name=HVE Synthetic Fixture', '-c', 'user.email=fixture@example.invalid',
    '-c', 'commit.gpgSign=false', '-c', 'core.fsmonitor=false',
    '-c', `core.hooksPath=${join(root, '.git', 'disabled-hooks')}`, ...args],
  { cwd: root, env, timeout: 15000, encoding: 'utf8' });
}
try {
  writeFileSync(source, 'def update_preferences(current, submitted):\n    return {**current, **submitted}\n');
  git('init', '--initial-branch=main');
  git('add', '--all');
  git('commit', '-m', 'Synthetic review fixture baseline');
  git('update-ref', 'refs/remotes/origin/main', 'HEAD');
  git('checkout', '-b', 'fixture-review');
  writeFileSync(source, original);
  git('add', 'src/profile_preferences.py');
  git('commit', '-m', 'Synthetic review candidate');
  if (git('status', '--porcelain').trim()) throw new Error('Synthetic review fixture is not clean');
  if (git('rev-list', '--count', 'HEAD').trim() !== '2') throw new Error('Expected two synthetic commits');
  if (git('remote').trim()) throw new Error('Synthetic fixture must have no remotes');
} finally {
  // Preserve the authored fixture even if a local Git operation fails.
  writeFileSync(source, original);
}
