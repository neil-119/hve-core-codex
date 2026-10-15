const MAX_WINDOWS_COMMAND_LINE_CODE_UNITS = 32767;
const POWERSHELL_EXECUTABLES = new Set(['pwsh.exe', 'powershell.exe']);
const POWERSHELL_NO_ARGUMENT_PREFIX_FLAGS = new Set([
  '-noprofile',
  '-noninteractive',
  '-nologo',
]);

function isCommandLineWhitespace(character) {
  return character === ' ' || character === '\t';
}

/**
 * Split a Windows command line using CommandLineToArgvW backslash/quote rules.
 * The result is evidence only; this function never invokes a process.
 */
export function commandLineToArgvW(commandLine) {
  if (typeof commandLine !== 'string' || commandLine.length === 0 ||
      commandLine.length > MAX_WINDOWS_COMMAND_LINE_CODE_UNITS || commandLine.includes('\0') ||
      commandLine.includes('\r') || commandLine.includes('\n')) {
    return { ok: false, reason: 'invalid-command-line' };
  }
  // CommandLineToArgvW creates an empty argv[0] when the input begins with
  // whitespace. The accepted Codex wrapper shape requires a proven executable
  // in argv[0], so reject this unusual form instead of normalizing it away.
  if (isCommandLineWhitespace(commandLine[0])) {
    return { ok: false, reason: 'leading-whitespace' };
  }

  const argv = [];
  let cursor = 0;
  let malformed = false;
  while (cursor < commandLine.length) {
    while (cursor < commandLine.length && isCommandLineWhitespace(commandLine[cursor])) cursor += 1;
    if (cursor >= commandLine.length) break;

    let argument = '';
    let inQuotes = false;
    let started = false;
    while (cursor < commandLine.length) {
      const character = commandLine[cursor];
      if (isCommandLineWhitespace(character) && !inQuotes) break;

      if (character === '\\') {
        const slashStart = cursor;
        while (commandLine[cursor] === '\\') cursor += 1;
        const slashCount = cursor - slashStart;
        if (commandLine[cursor] === '"') {
          argument += '\\'.repeat(Math.floor(slashCount / 2));
          if (slashCount % 2 === 1) {
            argument += '"';
            cursor += 1;
          } else {
            if (commandLine[cursor + 1] === '"') {
              // CommandLineToArgvW has special repeated-quote behavior that
              // differs from the common CRT parsing algorithm. This wrapper
              // recognizer deliberately declines that grammar rather than
              // risking a false payload reconstruction.
              return { ok: false, reason: 'unsupported-repeated-quotes' };
            }
            inQuotes = !inQuotes;
            cursor += 1;
          }
          started = true;
          continue;
        }
        argument += '\\'.repeat(slashCount);
        started = true;
        continue;
      }

      if (character === '"') {
        if (commandLine[cursor + 1] === '"') {
          return { ok: false, reason: 'unsupported-repeated-quotes' };
        }
        inQuotes = !inQuotes;
        cursor += 1;
        started = true;
        continue;
      }

      argument += character;
      cursor += 1;
      started = true;
    }

    if (inQuotes) malformed = true;
    if (started) argv.push(argument);
    while (cursor < commandLine.length && isCommandLineWhitespace(commandLine[cursor])) cursor += 1;
  }

  return malformed ? { ok: false, reason: 'unmatched-windows-quote' } : { ok: true, argv };
}

function isAbsolutePowerShellExecutable(value) {
  if (typeof value !== 'string' || !/^(?:[a-z]:\\|\\\\[^\\]+\\[^\\]+\\)/i.test(value)) return false;
  const basename = value.slice(value.lastIndexOf('\\') + 1).toLowerCase();
  return POWERSHELL_EXECUTABLES.has(basename);
}

function unverified(item, reason) {
  return {
    status: 'unverified',
    reason,
    native: {
      type: 'commandExecution',
      itemId: typeof item?.id === 'string' ? item.id : null,
      command: typeof item?.command === 'string' ? item.command : null,
    },
  };
}

/**
 * Derive a PowerShell view only when one explicit Windows argv payload is
 * proven. Unknown commands remain native observations and are not aliased.
 */
export function classifyNativeCommandExecution(item, { platform } = {}) {
  if (!item || typeof item !== 'object' ||
      !['commandExecution', 'command_execution'].includes(item.type)) {
    return unverified(item, 'not-a-native-command-execution-item');
  }
  if (typeof item.id !== 'string' || item.id.length === 0) {
    return unverified(item, 'missing-native-item-id');
  }
  if (typeof item.command !== 'string') return unverified(item, 'native-command-missing');
  if (platform !== 'win32') return unverified(item, 'windows-platform-not-proven');
  const parsed = commandLineToArgvW(item.command);
  if (!parsed.ok) return unverified(item, parsed.reason);
  const { argv } = parsed;

  if (!isAbsolutePowerShellExecutable(argv[0])) {
    return unverified(item, 'executable-is-not-an-absolute-powershell-path');
  }

  // Accept only these documented, no-argument switches before one explicit
  // -Command. Preserve the exact native tokens in evidence. Aliases,
  // parameterized switches, duplicate flags, and anything after the payload
  // remain unverified; argv fragments are never concatenated.
  const prefixFlags = [];
  const seenPrefixFlags = new Set();
  let invocationSwitchIndex = 1;
  while (invocationSwitchIndex < argv.length &&
      argv[invocationSwitchIndex].toLowerCase() !== '-command') {
    const flag = argv[invocationSwitchIndex];
    const normalizedFlag = flag.toLowerCase();
    if (!POWERSHELL_NO_ARGUMENT_PREFIX_FLAGS.has(normalizedFlag)) {
      return unverified(item, 'unsupported-powershell-prefix-switch');
    }
    if (seenPrefixFlags.has(normalizedFlag)) {
      return unverified(item, 'duplicate-powershell-prefix-switch');
    }
    seenPrefixFlags.add(normalizedFlag);
    prefixFlags.push(flag);
    invocationSwitchIndex += 1;
  }

  if (invocationSwitchIndex >= argv.length ||
      argv[invocationSwitchIndex].toLowerCase() !== '-command') {
    return unverified(item, 'powershell-switch-is-not-explicit-command');
  }
  const payloadIndex = invocationSwitchIndex + 1;
  if (argv.length !== payloadIndex + 1) {
    return unverified(item, 'not-one-executable-switch-and-payload');
  }
  if (argv[payloadIndex].length === 0) {
    return unverified(item, 'empty-powershell-command-payload');
  }

  return {
    status: 'verified',
    native: {
      type: 'commandExecution',
      itemId: item.id,
      command: item.command,
    },
    logical: {
      toolName: 'powershell',
      arguments: { command: argv[payloadIndex] },
    },
    evidence: {
      parser: 'CommandLineToArgvW-compatible-v1',
      platform,
      executablePath: argv[0],
      invocationSwitch: argv[invocationSwitchIndex],
      prefixFlags,
      argumentCount: argv.length,
    },
  };
}
