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

exports.run = async () => {
  const results = {};
  try {
    await vscode.extensions.getExtension('devin-switch.panels').activate();
    const requests = path.join(env.DS_HOME, 'panels', 'requests');
    fs.mkdirSync(requests, { recursive: true });
    const request = path.join(requests, `1-test.json`);
    fs.writeFileSync(request, JSON.stringify({ url: env.PANELS_CANVAS_URL, cwd: vscode.workspace.workspaceFolders[0].uri.fsPath, at: Date.now() }));
    results.claimed = await waitFor('the request to be claimed', () => !fs.existsSync(request), 10_000);
    results.canvasTab = await waitFor('the Canvas tab', () => tabLabels().includes('Canvas'));

    await vscode.commands.executeCommand('switchPanels.openBrowser');
    results.browserTab = await waitFor('the Agent Browser tab', () => tabLabels().includes('Agent Browser'));
    results.browser = await waitFor('the panel to attach to the agent browser', async () => {
      const state = await attachedPages();
      return state.attached > 0 && state;
    });
  } catch (error) {
    results.error = String(error && error.stack || error);
  }
  fs.writeFileSync(env.PANELS_RESULT, JSON.stringify(results));
};
