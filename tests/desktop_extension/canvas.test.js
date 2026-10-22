'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { CanvasPanels } = require('../../src/devin_switch/desktop_extension/canvas');

/** Just enough of the vscode API for webview tabs, recording what the extension does. */
function fakeVscode({ theme = 2 } = {}) {
  const created = [];
  const vscode = {
    ViewColumn: { Beside: -2 },
    ColorThemeKind: { Light: 1, Dark: 2, HighContrast: 3, HighContrastLight: 4 },
    ThemeIcon: class {
      constructor(id) {
        this.id = id;
      }
    },
    window: {
      activeColorTheme: { kind: theme },
      createWebviewPanel(viewType, title, showOptions, options) {
        const disposeListeners = new Set();
        const panel = {
          viewType,
          title,
          showOptions,
          options,
          viewColumn: showOptions.viewColumn === -2 ? 2 : showOptions.viewColumn,
          reveals: [],
          webview: { html: '' },
          reveal(column, preserveFocus) {
            panel.reveals.push({ column, preserveFocus });
          },
          onDidDispose(fn) {
            disposeListeners.add(fn);
            return { dispose: () => disposeListeners.delete(fn) };
          },
          dispose() {
            panel.disposed = true;
            for (const fn of [...disposeListeners]) fn();
          },
          listeners: () => disposeListeners.size,
        };
        created.push(panel);
        return panel;
      },
    },
  };
  return { vscode, created };
}

const board = (id, port = 47428) => `http://127.0.0.1:${port}/c/${id}?token=tok`;
const iframeSrc = (panel) => /<iframe src="([^"]+)"/.exec(panel.webview.html)[1].replace(/&amp;/g, '&');

test('opens one tab per board beside the chat, titled after the board, and never retargets another board\'s tab', () => {
  const { vscode, created } = fakeVscode();
  const panels = new CanvasPanels(vscode);
  panels.show('app-111111', board('app-111111'), 'How MCP works');
  panels.show('app-222222', board('app-222222'), 'Auth flow');
  assert.equal(created.length, 2);
  assert.deepEqual(created.map((p) => p.title), ['How MCP works', 'Auth flow']);
  assert.deepEqual(created[0].showOptions, { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true });
  // Later boards join the first board's group as tabs instead of splitting the editor again.
  assert.deepEqual(created[1].showOptions, { viewColumn: created[0].viewColumn, preserveFocus: true });
  assert.equal(iframeSrc(created[0]), 'http://127.0.0.1:47428/c/app-111111?token=tok&embed=1&theme=dark');
  assert.equal(iframeSrc(created[1]), 'http://127.0.0.1:47428/c/app-222222?token=tok&embed=1&theme=dark');
  assert.deepEqual(created[1].options, { enableScripts: true, retainContextWhenHidden: true });
  assert.equal(created[0].iconPath.id, 'edit');
});

test('showing a board again reveals its own tab in place without reloading it or opening another', () => {
  const { vscode, created } = fakeVscode();
  const panels = new CanvasPanels(vscode);
  panels.show('app-111111', board('app-111111'), 'How MCP works');
  panels.show('app-222222', board('app-222222'), 'Auth flow');
  const html = created[0].webview.html;
  panels.show('app-111111', board('app-111111'), 'How MCP works (renamed)');
  assert.equal(created.length, 2);
  assert.deepEqual(created[0].reveals, [{ column: undefined, preserveFocus: true }]);
  assert.deepEqual(created[1].reveals, []);
  assert.equal(created[0].title, 'How MCP works (renamed)');
  assert.equal(created[0].webview.html, html, 'a live board is not reloaded');
  assert.equal(iframeSrc(created[1]), 'http://127.0.0.1:47428/c/app-222222?token=tok&embed=1&theme=dark');
  // The daemon moved to another port: the same board's tab follows it.
  panels.show('app-111111', board('app-111111', 50000), 'How MCP works');
  assert.equal(iframeSrc(created[0]), 'http://127.0.0.1:50000/c/app-111111?token=tok&embed=1&theme=dark');
  assert.equal(created.length, 2);
});

test('closing a tab forgets it, so the board opens in a new tab next time', () => {
  const { vscode, created } = fakeVscode();
  const panels = new CanvasPanels(vscode);
  panels.show('app-111111', board('app-111111'), 'How MCP works');
  panels.show('app-222222', board('app-222222'), 'Auth flow');
  created[0].dispose();
  assert.equal(created[0].listeners(), 0, 'the dispose listener is removed');
  assert.deepEqual([...panels.tabs.keys()], ['app-222222']);
  panels.show('app-111111', board('app-111111'), 'How MCP works');
  assert.equal(created.length, 3);
  assert.equal(created[2].showOptions.viewColumn, created[1].viewColumn);
  panels.dispose();
  assert.ok(created[1].disposed && created[2].disposed);
  assert.equal(panels.tabs.size, 0);
});

test('untitled boards get a generic title, light themes embed light, and only local boards are shown', () => {
  const { vscode, created } = fakeVscode({ theme: 4 });
  const panels = new CanvasPanels(vscode);
  panels.show('app-333333', board('app-333333'), 'app-333333');
  panels.show('app-444444', board('app-444444'), '');
  assert.deepEqual(created.map((p) => p.title), ['Whiteboard', 'Whiteboard']);
  assert.match(iframeSrc(created[0]), /theme=light$/);
  assert.throws(() => panels.show('x', 'https://evil.example/c/x', 'X'), /Only local/);
  assert.throws(() => panels.show('y', 'http://192.168.1.2:1/c/y', 'Y'), /Only local/);
  assert.equal(created.length, 2);
  assert.equal(panels.tabs.size, 2);
});
