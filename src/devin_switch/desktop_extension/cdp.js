'use strict';
// Minimal Chrome DevTools Protocol client over Node's built-in WebSocket (flattened sessions).

const CALL_TIMEOUT_MS = 10_000;

class Cdp {
  /** @param {WebSocket} socket */
  constructor(socket) {
    this.socket = socket;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Set();
    this.closed = false;
    this.closeListeners = new Set();
    socket.addEventListener('message', (event) => this.receive(event.data));
    socket.addEventListener('close', () => this.shutdown('Chrome closed the DevTools connection.'));
    socket.addEventListener('error', () => this.shutdown('The DevTools connection failed.'));
  }

  static async connect(port, { fetchImpl = fetch, WebSocketImpl = WebSocket, timeoutMs = 5000 } = {}) {
    const response = await fetchImpl(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(timeoutMs) });
    const { webSocketDebuggerUrl } = await response.json();
    const url = new URL(webSocketDebuggerUrl);
    if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') throw new Error('Refusing a non-local DevTools endpoint.');
    const socket = new WebSocketImpl(webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('DevTools connection timed out.')), timeoutMs);
      socket.addEventListener('open', () => (clearTimeout(timer), resolve()), { once: true });
      socket.addEventListener('error', () => (clearTimeout(timer), reject(new Error('DevTools connection failed.'))), { once: true });
    });
    return new Cdp(socket);
  }

  send(method, params = {}, sessionId) {
    if (this.closed) return Promise.reject(new Error('DevTools connection is closed.'));
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out.`));
      }, CALL_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
    });
  }

  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onClose(listener) {
    this.closeListeners.add(listener);
    return () => this.closeListeners.delete(listener);
  }

  receive(data) {
    let message;
    try {
      message = JSON.parse(String(data));
    } catch {
      return;
    }
    if (message.id !== undefined) {
      const call = this.pending.get(message.id);
      if (!call) return;
      this.pending.delete(message.id);
      clearTimeout(call.timer);
      if (message.error) call.reject(new Error(`${call.method}: ${message.error.message}`));
      else call.resolve(message.result ?? {});
      return;
    }
    for (const listener of [...this.listeners]) listener(message);
  }

  shutdown(reason) {
    if (this.closed) return;
    this.closed = true;
    for (const call of this.pending.values()) {
      clearTimeout(call.timer);
      call.reject(new Error(reason));
    }
    this.pending.clear();
    this.listeners.clear();
    for (const listener of [...this.closeListeners]) listener(reason);
    this.closeListeners.clear();
  }

  close() {
    this.shutdown('Closed.');
    try {
      this.socket.close();
    } catch {
      // already closing
    }
  }
}

module.exports = { Cdp, CALL_TIMEOUT_MS };
