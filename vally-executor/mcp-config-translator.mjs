import { parseDuration } from '@microsoft/vally';

const STDIO_KEYS = new Set(['type', 'command', 'args', 'env', 'cwd', 'timeout']);
const REMOTE_KEYS = new Set(['type', 'url', 'headers', 'timeout']);

function isRecord(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function assertRecord(value, label) {
  if (!isRecord(value)) throw new TypeError(`${label} must be an object`);
}

function assertString(value, label) {
  if (typeof value !== 'string' || value.length === 0)
    throw new TypeError(`${label} must be a nonempty string`);
}

function assertStringArray(value, label) {
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string'))
    throw new TypeError(`${label} must be an array of strings`);
}

function assertStringRecord(value, label) {
  assertRecord(value, label);
  for (const [key, entry] of Object.entries(value)) {
    if (!key || typeof entry !== 'string')
      throw new TypeError(`${label} must map nonempty names to strings`);
  }
}

function assertAllowedKeys(value, allowed, label) {
  const unknown = Object.keys(value).find(key => !allowed.has(key));
  if (unknown) throw new TypeError(`${label} has unsupported field '${unknown}'`);
}

function timeoutConfig(timeout) {
  if (timeout === undefined) return {};
  const timeoutMs = parseDuration(timeout);
  const seconds = timeoutMs / 1_000;
  return {
    startup_timeout_sec: seconds,
    tool_timeout_sec: seconds,
  };
}

function validateRemoteUrl(url, label) {
  assertString(url, label);
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new TypeError(`${label} must be a valid HTTP URL`);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
    throw new TypeError(`${label} must use HTTP or HTTPS`);
}

function translateServer(name, server) {
  assertString(name, 'MCP server name');
  assertRecord(server, `MCP server '${name}'`);

  if (server.type === 'stdio') {
    assertAllowedKeys(server, STDIO_KEYS, `MCP server '${name}'`);
    assertString(server.command, `MCP server '${name}' command`);
    if (server.args !== undefined) assertStringArray(server.args, `MCP server '${name}' args`);
    if (server.env !== undefined) assertStringRecord(server.env, `MCP server '${name}' env`);
    if (server.cwd !== undefined && typeof server.cwd !== 'string')
      throw new TypeError(`MCP server '${name}' cwd must be a string`);
    return {
      kind: 'native',
      config: {
        command: server.command,
        args: [...(server.args ?? [])],
        ...(server.env !== undefined ? { env: { ...server.env } } : {}),
        ...(server.cwd !== undefined ? { cwd: server.cwd } : {}),
        ...timeoutConfig(server.timeout),
      },
    };
  }

  if (server.type === 'sse')
    throw new TypeError(`Codex does not support Vally SSE MCP server '${name}'`);

  if (server.type === 'http') {
    assertAllowedKeys(server, REMOTE_KEYS, `MCP server '${name}'`);
    validateRemoteUrl(server.url, `MCP server '${name}' url`);
    if (server.headers !== undefined) assertStringRecord(server.headers, `MCP server '${name}' headers`);
    return {
      kind: 'native',
      config: {
        url: server.url,
        ...(server.headers !== undefined ? { http_headers: { ...server.headers } } : {}),
        ...timeoutConfig(server.timeout),
      },
    };
  }

  throw new TypeError(`MCP server '${name}' type must be 'stdio', 'http', or 'sse'`);
}

/**
 * Translate Vally's eval-level MCP declarations into Codex thread config.
 *
 * Native stdio and Streamable HTTP entries are returned under `config.mcp_servers`.
 * SSE is rejected because Codex does not provide a native SSE transport.
 */
export function translateMcpServers(mcpServers) {
  if (mcpServers === undefined) return { config: undefined };
  assertRecord(mcpServers, 'Vally mcpServers');

  const nativeEntries = [];
  for (const [name, server] of Object.entries(mcpServers)) {
    const translated = translateServer(name, server);
    nativeEntries.push([name, translated.config]);
  }

  return { config: { mcp_servers: Object.fromEntries(nativeEntries) } };
}

/** Return the public Vally trajectory/grader name for a Codex MCP item. */
export function vallyMcpToolName(serverName, toolName) {
  assertString(serverName, 'MCP server name');
  assertString(toolName, 'MCP tool name');
  return `${serverName}-${toolName}`;
}
