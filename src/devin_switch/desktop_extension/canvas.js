'use strict';
// Whiteboard tabs: one per board, so showing one chat's board never hides another chat's.

const { randomBytes } = require('node:crypto');

function canvasHtml(url) {
  const nonce = randomBytes(16).toString('base64');
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src http://127.0.0.1:* http://localhost:*; style-src 'nonce-${nonce}';">
<style nonce="${nonce}">html,body,iframe{margin:0;padding:0;border:0;width:100%;height:100%;overflow:hidden;display:block}</style>
</head><body><iframe src="${url.replace(/"/g, '&quot;')}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads" allow="clipboard-read; clipboard-write"></iframe></body></html>`;
}

class CanvasPanels {
  /** @param {typeof import('vscode')} vscode */
  constructor(vscode) {
    this.vscode = vscode;
    /** @type {Map<string, { panel: import('vscode').WebviewPanel, src: string }>} board id → its tab */
    this.tabs = new Map();
  }

  /** Adds the panel theme and embed flags to a board URL from the whiteboard daemon. */
  embedUrl(raw) {
    const url = new URL(raw);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error('Only local canvas URLs can be shown.');
    url.searchParams.set('embed', '1');
    const { kind } = this.vscode.window.activeColorTheme;
    const { Light, HighContrastLight } = this.vscode.ColorThemeKind;
    url.searchParams.set('theme', kind === Light || kind === HighContrastLight ? 'light' : 'dark');
    return url.toString();
  }

  /** Reveals the board's own tab, else opens a new tab beside the chat, next to the other boards. */
  show(canvasId, url, title = '') {
    const src = this.embedUrl(url);
    const label = title && title !== canvasId ? title : 'Whiteboard';
    const tab = this.tabs.get(canvasId);
    if (tab) {
      tab.panel.title = label;
      tab.panel.reveal(undefined, true);
      if (tab.src !== src) this.load(tab, src);
      return;
    }
    const column = [...this.tabs.values()].map((t) => t.panel.viewColumn).filter(Boolean).at(-1) ?? this.vscode.ViewColumn.Beside;
    const panel = this.vscode.window.createWebviewPanel('switchPanels.canvas', label, { viewColumn: column, preserveFocus: true }, {
      enableScripts: true,
      retainContextWhenHidden: true,
    });
    panel.iconPath = new this.vscode.ThemeIcon('edit');
    const entry = { panel, src: '' };
    const closed = panel.onDidDispose(() => {
      closed.dispose();
      if (this.tabs.get(canvasId) === entry) this.tabs.delete(canvasId);
    });
    this.tabs.set(canvasId, entry);
    this.load(entry, src);
  }

  load(tab, src) {
    tab.src = src;
    tab.panel.webview.html = canvasHtml(src);
  }

  dispose() {
    for (const { panel } of [...this.tabs.values()]) panel.dispose();
  }
}

module.exports = { CanvasPanels };
