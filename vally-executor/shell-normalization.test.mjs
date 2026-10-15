import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyNativeCommandExecution,
  commandLineToArgvW,
} from './shell-normalization.mjs';

const retainedCommandSubset = [
  {
    id: 'retained-valid-get-content',
    command: String.raw`"C:\Runtime\pwsh.exe" -Command "Get-Content -LiteralPath 'fixture.txt'"`,
    status: 'verified',
    payload: String.raw`Get-Content -LiteralPath 'fixture.txt'`,
    argvCount: 3,
  },
  {
    id: 'retained-valid-rg',
    command: String.raw`"C:\Runtime\pwsh.exe" -Command "rg -n -i -C 2 \"project|src/app\\.py|HVE Core|scoped.rule\" \"fixture.txt\""`,
    status: 'verified',
    payload: String.raw`rg -n -i -C 2 "project|src/app\\.py|HVE Core|scoped.rule" "fixture.txt"`,
    argvCount: 3,
  },
  {
    // Sanitized to remove the original per-user runtime path while preserving
    // the retained third command's exact argv shape.
    id: 'retained-rpi-third-powershell-prefix',
    command: String.raw`"C:\Runtime\pwsh.exe" -NoProfile -Command pwd`,
    status: 'verified',
    payload: 'pwd',
    argvCount: 4,
    prefixFlags: ['-NoProfile'],
  },
  {
    id: 'retained-multi-argv-numbered-read',
    command: String.raw`"C:\Runtime\pwsh.exe" -Command '$n=0; Get-Content -LiteralPath '"'fixture.txt' | ForEach-Object { "$n++; '"'{0}:{1}' -f "$n, $_ }'`,
    status: 'unverified',
    argvCount: 9,
  },
];

test('the supported grammar decodes backslash-escaped quotes and rejects native quote quirks', () => {
  const commandLine = String.raw`"C:\Tools\PowerShell 7\pwsh.exe" -Command "Write-Output \"two words\""`;
  const parsed = commandLineToArgvW(commandLine);
  assert.deepEqual(parsed, {
    ok: true,
    argv: [
      String.raw`C:\Tools\PowerShell 7\pwsh.exe`,
      '-Command',
      'Write-Output "two words"',
    ],
  });

  // Native CommandLineToArgvW treats repeated quote runs specially. For
  // example, its results for a""b, a""""b, and a"""""b differ. We only
  // alias the simpler grammar used by observed wrappers.
  for (const quoteCount of [2, 3, 4, 5, 8]) {
    const commandLine = String.raw`"C:\Tools\pwsh.exe" -Command "a` + '"'.repeat(quoteCount) + 'b"';
    assert.deepEqual(commandLineToArgvW(commandLine), {
      ok: false,
      reason: 'unsupported-repeated-quotes',
    }, `quote run length ${quoteCount}`);
  }

  // Differential cases checked against native CommandLineToArgvW: odd
  // backslash counts escape the quote and preserve floor(n / 2) slashes.
  for (const slashCount of [1, 3, 5]) {
    const commandLine = String.raw`"C:\Tools\pwsh.exe" -Command "a` + '\\'.repeat(slashCount) + '"b"';
    const parsed = commandLineToArgvW(commandLine);
    assert.equal(parsed.ok, true, `slash run length ${slashCount}`);
    assert.deepEqual(parsed.argv, [
      String.raw`C:\Tools\pwsh.exe`,
      '-Command',
      'a' + '\\'.repeat(Math.floor(slashCount / 2)) + '"b',
    ]);
  }

  for (const slashCount of [2, 4, 6]) {
    const commandLine = String.raw`"C:\Tools\pwsh.exe" -Command "a` + '\\'.repeat(slashCount) + '"b"';
    const parsed = commandLineToArgvW(commandLine);
    assert.equal(parsed.ok, false, `even slash run length ${slashCount} leaves wrapper quoting unsupported`);
  }

  assert.deepEqual(commandLineToArgvW('  "C:\\Tools\\pwsh.exe" -Command "x"'), {
    ok: false,
    reason: 'leading-whitespace',
  });
});

test('sanitized retained wrappers distinguish a single payload from multiple argv fragments', () => {
  const results = retainedCommandSubset.map(example => {
    const split = commandLineToArgvW(example.command);
    assert.equal(split.ok, true);
    assert.equal(split.argv.length, example.argvCount);
    const classified = classifyNativeCommandExecution({
      id: example.id,
      type: 'commandExecution',
      command: example.command,
    }, { platform: 'win32' });
    assert.equal(classified.status, example.status);
    assert.equal(classified.native.itemId, example.id);
    assert.equal(classified.native.command, example.command);
    if (example.status === 'verified') {
      assert.equal(classified.logical.toolName, 'powershell');
      assert.equal(classified.logical.arguments.command, example.payload);
      assert.equal(classified.evidence.argumentCount, example.argvCount);
      assert.deepEqual(classified.evidence.prefixFlags, example.prefixFlags ?? []);
    } else {
      assert.equal('logical' in classified, false);
    }
    return classified.status;
  });
  assert.deepEqual(results, ['verified', 'verified', 'verified', 'unverified']);
});

test('accepts only exact documented no-argument prefix flags before -Command', () => {
  const executable = String.raw`"C:\Runtime\pwsh.exe"`;
  const accepted = [
    { flags: ['-NoProfile'], payload: 'pwd' },
    { flags: ['-NonInteractive'], payload: 'Write-Output OK' },
    { flags: ['-NoLogo'], payload: 'pwd' },
    { flags: ['-NoProfile', '-NonInteractive', '-NoLogo'], payload: 'Write-Output OK' },
  ];

  for (const [index, example] of accepted.entries()) {
    const command = `${executable} ${example.flags.join(' ')} -Command "${example.payload}"`;
    const id = `allowed-prefix-${index}`;
    const classified = classifyNativeCommandExecution({
      id,
      type: 'commandExecution',
      command,
    }, { platform: 'win32' });
    assert.equal(classified.status, 'verified', command);
    assert.equal(classified.native.itemId, id);
    assert.equal(classified.native.command, command);
    assert.equal(classified.logical.arguments.command, example.payload);
    assert.deepEqual(classified.evidence.prefixFlags, example.flags);
  }

  // PowerShell switch names are case-insensitive, but the full documented
  // token must be present and its original spelling is retained as evidence.
  const mixedCase = `${executable} -noprofile -Command pwd`;
  const mixedCaseResult = classifyNativeCommandExecution({
    id: 'allowed-prefix-mixed-case',
    type: 'commandExecution',
    command: mixedCase,
  }, { platform: 'win32' });
  assert.equal(mixedCaseResult.status, 'verified');
  assert.deepEqual(mixedCaseResult.evidence.prefixFlags, ['-noprofile']);

  const rejected = [
    `${executable} -nop -Command pwd`,
    `${executable} -noni -Command pwd`,
    `${executable} -nol -Command pwd`,
    `${executable} -NoProfile:$true -Command pwd`,
    `${executable} -NoProfile=true -Command pwd`,
    `${executable} -NoProfile-Command pwd`,
    `${executable} -NoProfile -NoProfile -Command pwd`,
    `${executable} -NoProfile`,
    `${executable} -Command pwd -NoLogo`,
  ];
  for (const [index, command] of rejected.entries()) {
    const classified = classifyNativeCommandExecution({
      id: `rejected-prefix-${index}`,
      type: 'commandExecution',
      command,
    }, { platform: 'win32' });
    assert.equal(classified.status, 'unverified', command);
    assert.equal(classified.native.command, command);
    assert.equal('logical' in classified, false, command);
  }
});

test('rejects ambiguous quote structure, extra payloads, encoded commands, and script files', () => {
  const cases = [
    {
      name: 'unmatched outer quote',
      command: String.raw`"C:\Runtime\pwsh.exe" -Command "Get-Content -LiteralPath fixture.txt`,
    },
    {
      name: 'PowerShell single quotes do not group Windows argv',
      command: String.raw`"C:\Runtime\pwsh.exe" -Command 'Get-Content 'fixture.txt'`,
    },
    {
      name: 'tail argument after one command payload',
      command: String.raw`"C:\Runtime\pwsh.exe" -Command "Get-Content fixture.txt" trailing`,
    },
    {
      name: 'multiple command payloads',
      command: String.raw`"C:\Runtime\pwsh.exe" -Command "Get-Content fixture.txt" -Command "Get-Content other.txt"`,
    },
    {
      name: 'encoded command',
      command: String.raw`"C:\Runtime\pwsh.exe" -EncodedCommand RwBlAHQALQBDAG8AbgB0AGUAbgB0AA==`,
    },
    {
      name: 'file mode',
      command: String.raw`"C:\Runtime\pwsh.exe" -File check.ps1`,
    },
    {
      name: 'non-PowerShell executable',
      command: String.raw`"C:\Runtime\bash.exe" -Command "Get-Content fixture.txt"`,
    },
  ];

  for (const [index, item] of cases.entries()) {
    const classified = classifyNativeCommandExecution({
      id: `ambiguous-${index}`,
      type: 'commandExecution',
      command: item.command,
    }, { platform: 'win32' });
    assert.equal(classified.status, 'unverified', item.name);
    assert.equal(classified.native.itemId, `ambiguous-${index}`);
    assert.equal(classified.native.command, item.command);
    assert.equal('logical' in classified, false, item.name);
  }
});

test('classification retains native identity and emits no synthetic event activations', () => {
  const command = retainedCommandSubset[0].command;
  const started = classifyNativeCommandExecution({ id: 'native-item-7', type: 'commandExecution', command }, { platform: 'win32' });
  const completed = classifyNativeCommandExecution({ id: 'native-item-7', type: 'commandExecution', command }, { platform: 'win32' });

  assert.equal(started.status, 'verified');
  assert.equal(completed.status, 'verified');
  assert.equal(started.native.itemId, completed.native.itemId);
  assert.equal(started.logical.toolName, 'powershell');
  assert.equal(completed.logical.toolName, 'powershell');
  assert.equal('events' in started, false);
  assert.equal('events' in completed, false);
  assert.equal(started.evidence.platform, 'win32');
});

test('requires explicit proof that commandExecution came from a Windows runtime', () => {
  const command = retainedCommandSubset[0].command;
  const item = { id: 'native-item-platform', type: 'commandExecution', command };

  for (const options of [undefined, {}, { platform: 'linux' }, { platform: 'darwin' }]) {
    const classified = classifyNativeCommandExecution(item, options);
    assert.equal(classified.status, 'unverified');
    assert.equal(classified.reason, 'windows-platform-not-proven');
    assert.equal('logical' in classified, false);
  }

  assert.equal(classifyNativeCommandExecution(item, { platform: 'win32' }).status, 'verified');
});
