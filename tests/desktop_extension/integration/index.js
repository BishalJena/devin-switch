'use strict';
// Runs inside a real Desktop extension host (--extensionTestsPath). Driven by tests/test_desktop_extension.py.
const vscode = require('vscode');
const fs = require('node:fs');
const path = require('node:path');

const env = process.env;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(what, check, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch {}
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${what}`);
}

const tabLabels = () => vscode.window.tabGroups.all.flatMap((g) => g.tabs.map((t) => t.label));

async function attachedPages() {
  const list = await (await fetch(`http://127.0.0.1:${env.PANELS_CHROME_PORT}/json/list`)).json();
  // /json/list has no "attached" flag; ask the browser target instead.
  const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${env.PANELS_CHROME_PORT}/json/version`)).json();
  const socket = new WebSocket(webSocketDebuggerUrl);
  await new Promise((resolve, reject) => ((socket.onopen = resolve), (socket.onerror = reject)));
  const reply = await new Promise((resolve) => {
    socket.onmessage = (event) => resolve(JSON.parse(event.data));
    socket.send(JSON.stringify({ id: 1, method: 'Target.getTargets' }));
  });
  socket.close();
  return { pages: list.filter((t) => t.type === 'page').length, attached: reply.result.targetInfos.filter((t) => t.type === 'page' && t.attached).length };
}

async function whiteboard(method, route, body) {
  const res = await fetch(`${env.PANELS_WB_URL}${route}`, {
    method,
    headers: { Authorization: `Bearer ${env.PANELS_WB_TOKEN}`, 'Content-Type': 'application/json' },
    body: body && JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

exports.run = async () => {
  const results = {};
  try {
    await vscode.extensions.getExtension('devin-switch.panels').activate();
    const workspace = vscode.workspace.workspaceFolders[0].uri.fsPath;
    await whiteboard('PUT', '/api/canvases/panel-test', { title: 'How MCP works', workspace, chat: 'chat-1' });
    // The extension connects to the daemon as a panel host within a few seconds.
    results.shown = await waitFor('the daemon to reach the panel host', async () => (await whiteboard('POST', '/api/canvases/panel-test/show', { workspace })).status === 200);
    results.canvasTab = await waitFor('the board tab titled after the topic', () => tabLabels().includes('How MCP works'));
    results.viewers = await waitFor('the embedded viewer to connect live', async () => {
      const { body } = await whiteboard('GET', '/api/canvases');
      return body.find((b) => b.id === 'panel-test')?.viewers || 0;
    });

    await vscode.commands.executeCommand('switchPanels.openBrowser');
    results.browserTab = await waitFor('the Agent Browser tab', () => tabLabels().includes('Agent Browser'));
    results.browser = await waitFor('the panel to attach to the agent browser', async () => {
      const state = await attachedPages();
      return state.attached > 0 && state;
    });
  } catch (error) {
    results.error = String((error && error.stack) || error);
  }
  fs.writeFileSync(env.PANELS_RESULT, JSON.stringify(results));
};
