import test from 'node:test';
import assert from 'node:assert/strict';
import { parseDuration } from '@microsoft/vally';
import { translateMcpServers, vallyMcpToolName } from './mcp-config-translator.mjs';

test('translates Vally stdio config to Codex MCP config and preserves timeout units', () => {
  const timeout = '10s';
  const translated = translateMcpServers({
    figma: {
      type: 'stdio',
      command: 'node',
      args: ['.eval-assets/figma-mcp/server.mjs'],
      env: { FIGMA_MCP_SCENARIO: 'success' },
      cwd: 'C:/eval/workspace',
      timeout,
    },
  });

  assert.deepEqual(translated.config, {
    mcp_servers: {
      figma: {
        command: 'node',
        args: ['.eval-assets/figma-mcp/server.mjs'],
        env: { FIGMA_MCP_SCENARIO: 'success' },
        cwd: 'C:/eval/workspace',
        startup_timeout_sec: parseDuration(timeout) / 1_000,
        tool_timeout_sec: parseDuration(timeout) / 1_000,
      },
    },
  });
});

test('translates Vally Streamable HTTP config and headers to native Codex fields', () => {
  const translated = translateMcpServers({
    remote: {
      type: 'http',
      url: 'https://mcp.example.test/v1',
      headers: { Authorization: 'Bearer test-only' },
      timeout: '2.5s',
    },
  });

  assert.deepEqual(translated.config, {
    mcp_servers: {
      remote: {
        url: 'https://mcp.example.test/v1',
        http_headers: { Authorization: 'Bearer test-only' },
        startup_timeout_sec: 2.5,
        tool_timeout_sec: 2.5,
      },
    },
  });
});

test('rejects SSE because Codex does not provide a native SSE MCP transport', () => {
  assert.throws(() => translateMcpServers({
    events: {
      type: 'sse',
      url: 'https://mcp.example.test/events',
      headers: { 'X-Test': 'value' },
      timeout: '750ms',
    },
  }), /does not support Vally SSE MCP server 'events'/);
});

test('rejects mixed maps that include unsupported SSE servers', () => {
  assert.throws(() => translateMcpServers({
    local: { type: 'stdio', command: 'node' },
    events: { type: 'sse', url: 'https://mcp.example.test/events' },
  }), /does not support Vally SSE MCP server 'events'/);
});

test('normalizes Codex server/tool identity to Vally trajectory names', () => {
  assert.equal(vallyMcpToolName('figma', 'create_new_file'), 'figma-create_new_file');
  assert.equal(vallyMcpToolName('local-server', 'get_metadata'), 'local-server-get_metadata');
});

test('rejects invalid configs instead of silently changing their meaning', () => {
  assert.throws(() => translateMcpServers([]), /Vally mcpServers must be an object/);
  assert.throws(() => translateMcpServers({ bad: { type: 'unknown' } }), /type must be/);
  assert.throws(() => translateMcpServers({ bad: { type: 'stdio', command: '' } }), /command must be/);
  assert.throws(() => translateMcpServers({ bad: { type: 'http', url: 'file:///tmp/mcp' } }), /HTTP or HTTPS/);
  assert.throws(() => translateMcpServers({ bad: { type: 'stdio', command: 'node', env: { TOKEN: 1 } } }), /map nonempty names to strings/);
});
