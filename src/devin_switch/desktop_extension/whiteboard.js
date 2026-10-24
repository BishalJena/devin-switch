'use strict';
// Panel host for whiteboard-mcp: the daemon asks this window to show a chat's board beside the chat.

const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const RECONNECT_MS = 3000;
const HTTP_TIMEOUT_MS = 5000;

function whiteboardHome(configured) {
  const value = configured || process.env.WHITEBOARD_HOME || path.join(os.homedir(), '.whiteboard-mcp');
  return value.startsWith('~') ? path.join(os.homedir(), value.slice(1)) : value;
}

class WhiteboardHost {
  /**
   * `hostId` names this window for the daemon, which routes a chat's board to the window whose id matches the chat's.
   * @param {{ home: string, hostId: string, folders: () => string[], focused: () => boolean,
   *           onShow: (board: { canvasId: string, url: string, title: string }) => Promise<void> | void,
   *           WebSocketImpl?: typeof WebSocket, fetchImpl?: typeof fetch, readFile?: (file: string) => Promise<string>, reconnectMs?: number }} options
   */
  constructor({ home, hostId, folders, focused, onShow, WebSocketImpl = WebSocket, fetchImpl = fetch, readFile = (f) => fs.readFile(f, 'utf8'), reconnectMs = RECONNECT_MS }) {
    Object.assign(this, { home, hostId, folders, focused, onShow, WebSocketImpl, fetchImpl, readFile, reconnectMs });
    this.socket = null;
    this.daemon = null;
    this.timer = null;
    this.disposed = false;
  }

  start() {
    void this.connect();
  }

  /** Where the daemon listens and its token, or null while it isn't running. */
  async locate() {
    try {
      const { port } = JSON.parse(await this.readFile(path.join(this.home, 'daemon.json')));
      const token = (await this.readFile(path.join(this.home, 'token'))).trim();
      return Number.isInteger(port) && token ? { base: `http://127.0.0.1:${port}`, token } : null;
    } catch {
      return null;
    }
  }

  async connect() {
    if (this.disposed || this.socket) return;
    const daemon = await this.locate();
    if (this.disposed || this.socket) return;
    if (!daemon) return this.retry();
    const socket = new this.WebSocketImpl(`${daemon.base.replace('http', 'ws')}/ws/host?token=${encodeURIComponent(daemon.token)}`);
    this.socket = socket;
    this.daemon = daemon;
    socket.addEventListener('open', () => this.send({ t: 'hello', hostId: this.hostId, folders: this.folders(), focused: this.focused() }));
    socket.addEventListener('message', (event) => void this.receive(event.data));
    socket.addEventListener('close', () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.retry();
    });
    socket.addEventListener('error', () => {});
  }

  retry() {
    if (this.disposed || this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.connect();
    }, this.reconnectMs);
  }

  send(message) {
    if (this.socket?.readyState === 1) this.socket.send(JSON.stringify(message));
  }

  /** Call when the window's folders or focus change. */
  update() {
    this.send({ t: 'state', folders: this.folders(), focused: this.focused() });
  }

  async receive(data) {
    let message;
    try {
      message = JSON.parse(String(data));
    } catch {
      return;
    }
    if (message.t !== 'show' || typeof message.reqId !== 'string' || typeof message.canvasId !== 'string' || !this.daemon) return;
    try {
      await this.onShow({ canvasId: message.canvasId, url: this.viewerUrl(message.canvasId), title: String(message.title ?? '') });
      this.send({ t: 'shown', reqId: message.reqId, ok: true });
    } catch (error) {
      this.send({ t: 'shown', reqId: message.reqId, ok: false, error: String(error?.message ?? error) });
    }
  }

  viewerUrl(canvasId) {
    return `${this.daemon.base}/c/${encodeURIComponent(canvasId)}?token=${encodeURIComponent(this.daemon.token)}`;
  }

  /** Boards of this window's folders, newest first; empty while the daemon isn't running. */
  async boards() {
    const daemon = this.daemon ?? (await this.locate());
    if (!daemon) return [];
    this.daemon = daemon;
    const response = await this.fetchImpl(`${daemon.base}/api/canvases`, {
      headers: { Authorization: `Bearer ${daemon.token}` },
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
    if (!response.ok) return [];
    const folders = this.folders();
    const inFolder = (workspace) => typeof workspace === 'string' && folders.some((folder) => {
      const relative = path.relative(folder, workspace);
      return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
    });
    return (await response.json()).filter((board) => inFolder(board.workspace)).sort((a, b) => b.updatedAt - a.updatedAt);
  }

  dispose() {
    this.disposed = true;
    clearTimeout(this.timer);
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }
}

module.exports = { WhiteboardHost, whiteboardHome };
