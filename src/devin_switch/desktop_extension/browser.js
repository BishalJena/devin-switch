'use strict';
// Streams one agent-browser tab into a panel and forwards the human's input to it.

const { keyCommand, mouseCommand, navigationUrl } = require('./input');

const SCREENCAST = { format: 'jpeg', quality: 70, everyNthFrame: 1 };

class BrowserView {
  /**
   * @param {{ send(method: string, params?: object, sessionId?: string): Promise<any>, on(listener: (m: any) => void): () => void }} cdp
   * @param {(message: object) => void} post  sends a message to the panel
   */
  constructor(cdp, post, { width = 1280, height = 800 } = {}) {
    this.cdp = cdp;
    this.post = post;
    this.size = { width, height };
    this.tabs = new Map();
    this.activeId = null;
    this.sessionId = null;
    this.metadata = null;
    this.follow = true;
    this.queue = Promise.resolve();
    this.disposed = false;
    this.unsubscribe = cdp.on((message) => this.event(message));
  }

  async start() {
    await this.cdp.send('Target.setDiscoverTargets', { discover: true });
    const { targetInfos = [] } = await this.cdp.send('Target.getTargets');
    for (const info of targetInfos) if (info.type === 'page') this.tabs.set(info.targetId, info);
    const last = [...this.tabs.keys()].pop();
    if (last) await this.select(last);
    else this.publishTabs();
  }

  /** Serialises tab switches so frames from a previous session never reach the panel. */
  select(targetId) {
    this.queue = this.queue.then(() => this.attach(targetId)).catch((error) => this.status(error.message));
    return this.queue;
  }

  async attach(targetId) {
    if (this.disposed || targetId === this.activeId || !this.tabs.has(targetId)) return;
    await this.detach();
    this.activeId = targetId;
    this.publishTabs();
    const { sessionId } = await this.cdp.send('Target.attachToTarget', { targetId, flatten: true });
    if (this.disposed || this.activeId !== targetId) {
      await this.cdp.send('Target.detachFromTarget', { sessionId }).catch(() => {});
      return;
    }
    this.sessionId = sessionId;
    await this.cdp.send('Page.enable', {}, sessionId);
    const metrics = await this.cdp.send('Page.getLayoutMetrics', {}, sessionId);
    const viewport = metrics.cssVisualViewport ?? metrics.layoutViewport ?? {};
    this.metadata = { deviceWidth: viewport.clientWidth ?? 1440, deviceHeight: viewport.clientHeight ?? 900 };
    // Headless Chrome only sends screencast frames on paint; show the current page right away.
    const shot = await this.cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: 70 }, sessionId);
    if (this.sessionId === sessionId && shot.data) this.post({ type: 'frame', data: shot.data, metadata: this.metadata });
    await this.screencast();
  }

  screencast() {
    if (!this.sessionId) return Promise.resolve();
    return this.cdp.send('Page.startScreencast', { ...SCREENCAST, maxWidth: this.size.width, maxHeight: this.size.height }, this.sessionId);
  }

  async detach() {
    const sessionId = this.sessionId;
    this.sessionId = null;
    this.metadata = null;
    this.activeId = null;
    if (!sessionId) return;
    await this.cdp.send('Page.stopScreencast', {}, sessionId).catch(() => {});
    await this.cdp.send('Target.detachFromTarget', { sessionId }).catch(() => {});
  }

  event({ method, params = {}, sessionId }) {
    if (this.disposed) return;
    if (method === 'Page.screencastFrame') {
      if (sessionId !== this.sessionId) return;
      this.cdp.send('Page.screencastFrameAck', { sessionId: params.sessionId }, sessionId).catch(() => {});
      this.metadata = params.metadata;
      this.post({ type: 'frame', data: params.data, metadata: params.metadata });
      return;
    }
    const info = params.targetInfo;
    if (method === 'Target.targetCreated' && info?.type === 'page') {
      this.tabs.set(info.targetId, info);
      if (this.follow) this.select(info.targetId);
      this.publishTabs();
    } else if (method === 'Target.targetInfoChanged' && this.tabs.has(info?.targetId)) {
      const before = this.tabs.get(info.targetId);
      this.tabs.set(info.targetId, info);
      // Follow the tab the agent is navigating.
      if (this.follow && before.url !== info.url && info.targetId !== this.activeId) this.select(info.targetId);
      this.publishTabs();
    } else if (method === 'Target.targetDestroyed' && this.tabs.has(params.targetId)) {
      this.tabs.delete(params.targetId);
      if (params.targetId === this.activeId) {
        this.sessionId = null;
        this.activeId = null;
        const next = [...this.tabs.keys()].pop();
        if (next) this.select(next);
      }
      this.publishTabs();
    }
  }

  publishTabs() {
    const tabs = [...this.tabs.values()].map(({ targetId, url, title }) => ({ targetId, url, title }));
    this.post({ type: 'tabs', tabs, activeId: this.activeId, follow: this.follow });
  }

  status(text) {
    this.post({ type: 'status', text });
  }

  /** Messages from the panel. */
  async handle(message) {
    const session = this.sessionId;
    const send = (method, params = {}) => (session ? this.cdp.send(method, params, session) : Promise.resolve());
    switch (message.type) {
      case 'mouse': {
        const params = mouseCommand(message, this.metadata);
        if (params) await send('Input.dispatchMouseEvent', params);
        break;
      }
      case 'key': {
        const params = keyCommand(message);
        if (params) await send('Input.dispatchKeyEvent', params);
        break;
      }
      case 'paste':
        if (message.text) await send('Input.insertText', { text: String(message.text) });
        break;
      case 'navigate': {
        const url = navigationUrl(message.url);
        if (url) await send('Page.navigate', { url });
        break;
      }
      case 'back':
      case 'forward': {
        const { currentIndex, entries = [] } = await send('Page.getNavigationHistory');
        const entry = entries[currentIndex + (message.type === 'back' ? -1 : 1)];
        if (entry) await send('Page.navigateToHistoryEntry', { entryId: entry.id });
        break;
      }
      case 'reload':
        await send('Page.reload');
        break;
      case 'selectTab':
        this.follow = false;
        await this.select(message.targetId);
        this.publishTabs();
        break;
      case 'follow':
        this.follow = Boolean(message.value);
        this.publishTabs();
        break;
      case 'newTab':
        await this.cdp.send('Target.createTarget', { url: 'about:blank' });
        break;
      case 'closeTab':
        if (this.tabs.has(message.targetId)) await this.cdp.send('Target.closeTarget', { targetId: message.targetId });
        break;
      case 'resize':
        this.size = { width: Math.max(200, Math.round(message.width)), height: Math.max(150, Math.round(message.height)) };
        await this.screencast();
        break;
      case 'copy': {
        const result = await send('Runtime.evaluate', { expression: 'String(getSelection())', returnByValue: true });
        return result?.result?.value ?? '';
      }
    }
    return undefined;
  }

  async dispose() {
    this.disposed = true;
    this.unsubscribe();
    const sessionId = this.sessionId;
    this.sessionId = null;
    if (sessionId) {
      await this.cdp.send('Page.stopScreencast', {}, sessionId).catch(() => {});
      await this.cdp.send('Target.detachFromTarget', { sessionId }).catch(() => {});
    }
  }
}

module.exports = { BrowserView };
