'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { existsSync, mkdtempSync, readFileSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { BrowserView } = require('../../src/devin_switch/desktop_extension/browser');
const { Cdp } = require('../../src/devin_switch/desktop_extension/cdp');

/** Records commands and lets tests emit CDP events. */
function fakeCdp(targets) {
  const calls = [];
  let listener = () => {};
  let session = 0;
  return {
    calls,
    emit: (message) => listener(message),
    on(fn) {
      listener = fn;
      return () => (listener = () => {});
    },
    async send(method, params = {}, sessionId) {
      calls.push({ method, params, sessionId });
      if (method === 'Target.getTargets') return { targetInfos: targets };
      if (method === 'Target.attachToTarget') return { sessionId: `s${++session}` };
      if (method === 'Page.getLayoutMetrics') return { cssVisualViewport: { clientWidth: 1000, clientHeight: 500 } };
      if (method === 'Page.captureScreenshot') return { data: 'first' };
      return {};
    },
  };
}

const page = (targetId, url) => ({ targetId, type: 'page', url, title: url });
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

test('streams the newest tab, follows the agent to new and navigating tabs, and stops when the human picks one', async () => {
  const cdp = fakeCdp([page('A', 'https://a.test'), page('B', 'https://b.test'), { targetId: 'W', type: 'service_worker', url: 'x' }]);
  const posted = [];
  const view = new BrowserView(cdp, (m) => posted.push(m));
  await view.start();
  assert.equal(view.activeId, 'B');
  assert.deepEqual(posted.find((m) => m.type === 'frame'), { type: 'frame', data: 'first', metadata: { deviceWidth: 1000, deviceHeight: 500 } });
  assert.equal(posted.filter((m) => m.type === 'tabs').at(-1).tabs.length, 2, 'workers are not tabs');

  cdp.emit({ method: 'Target.targetCreated', params: { targetInfo: page('C', 'about:blank') } });
  await settle();
  assert.equal(view.activeId, 'C');
  assert.ok(cdp.calls.some((c) => c.method === 'Page.stopScreencast' && c.sessionId === 's1'), 'previous tab stops streaming');

  cdp.emit({ method: 'Target.targetInfoChanged', params: { targetInfo: page('A', 'https://a.test/next') } });
  await settle();
  assert.equal(view.activeId, 'A', 'the agent navigated tab A');

  await view.handle({ type: 'selectTab', targetId: 'B' });
  await settle();
  assert.equal(view.activeId, 'B');
  assert.equal(view.follow, false);
  cdp.emit({ method: 'Target.targetInfoChanged', params: { targetInfo: page('C', 'https://c.test') } });
  await settle();
  assert.equal(view.activeId, 'B', 'manual choice wins until follow is turned back on');
});

test('acknowledges frames from the current session only and maps input with the latest metadata', async () => {
  const cdp = fakeCdp([page('A', 'https://a.test')]);
  const posted = [];
  const view = new BrowserView(cdp, (m) => posted.push(m));
  await view.start();
  const frames = () => posted.filter((m) => m.type === 'frame').length;
  const before = frames();
  cdp.emit({ method: 'Page.screencastFrame', sessionId: 'stale', params: { data: 'x', sessionId: 9, metadata: {} } });
  assert.equal(frames(), before);
  cdp.emit({ method: 'Page.screencastFrame', sessionId: 's1', params: { data: 'y', sessionId: 7, metadata: { deviceWidth: 800, deviceHeight: 600 } } });
  assert.equal(frames(), before + 1);
  assert.ok(cdp.calls.some((c) => c.method === 'Page.screencastFrameAck' && c.params.sessionId === 7 && c.sessionId === 's1'));

  await view.handle({ type: 'mouse', kind: 'down', x: 0.5, y: 0.5, button: 0, buttons: 1 });
  const click = cdp.calls.find((c) => c.method === 'Input.dispatchMouseEvent');
  assert.deepEqual([click.params.x, click.params.y, click.sessionId], [400, 300, 's1']);

  await view.handle({ type: 'navigate', url: 'example.com' });
  assert.ok(cdp.calls.some((c) => c.method === 'Page.navigate' && c.params.url === 'https://example.com'));
});

test('the active tab closing moves to another tab; dispose stops streaming and ignores late events', async () => {
  const cdp = fakeCdp([page('A', 'https://a.test'), page('B', 'https://b.test')]);
  const posted = [];
  const view = new BrowserView(cdp, (m) => posted.push(m));
  await view.start();
  cdp.emit({ method: 'Target.targetDestroyed', params: { targetId: 'B' } });
  await settle();
  assert.equal(view.activeId, 'A');
  await view.dispose();
  assert.ok(cdp.calls.some((c) => c.method === 'Target.detachFromTarget' && c.params.sessionId === 's2'));
  const count = posted.length;
  cdp.emit({ method: 'Target.targetCreated', params: { targetInfo: page('C', 'about:blank') } });
  await settle();
  assert.equal(posted.length, count);
});

const CHROME = process.env.DS_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

test('real headless Chrome: frames arrive, clicks and typing reach the page', { skip: !existsSync(CHROME) && 'Chrome is not installed', timeout: 60_000 }, async () => {
  const data = mkdtempSync(path.join(tmpdir(), 'panels-chrome-'));
  const chrome = spawn(CHROME, [`--user-data-dir=${data}`, '--headless=new', '--remote-debugging-port=0', '--no-first-run', '--window-size=800,600', 'about:blank'], { stdio: 'ignore' });
  try {
    let port;
    for (let i = 0; i < 100 && !port; i++) {
      await new Promise((r) => setTimeout(r, 100));
      try {
        port = Number(readFileSync(path.join(data, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
      } catch {}
    }
    assert.ok(port, 'Chrome wrote DevToolsActivePort');
    const cdp = await Cdp.connect(port);
    const posted = [];
    const view = new BrowserView(cdp, (m) => posted.push(m), { width: 800, height: 600 });
    await view.start();
    const html = '<input id=q autofocus style="position:fixed;left:0;top:0;width:100%;height:50%"><button id=b style="position:fixed;left:0;top:50%;width:100%;height:50%" onclick="document.title=\'clicked:\'+q.value">go</button>';
    await view.handle({ type: 'navigate', url: `data:text/html,${encodeURIComponent(html)}` });
    const waitFor = async (check, what) => {
      for (let i = 0; i < 100; i++) {
        if (await check()) return;
        await new Promise((r) => setTimeout(r, 100));
      }
      assert.fail(`timed out waiting for ${what}`);
    };
    const title = async () => ((await cdp.send('Runtime.evaluate', { expression: 'document.title', returnByValue: true }, view.sessionId)).result.value);
    await waitFor(async () => (await cdp.send('Runtime.evaluate', { expression: '!!document.getElementById("b")', returnByValue: true }, view.sessionId)).result.value, 'the page');
    await waitFor(() => posted.filter((m) => m.type === 'frame').length > 1, 'screencast frames');

    await view.handle({ type: 'mouse', kind: 'down', x: 0.5, y: 0.2, button: 0, buttons: 1, clickCount: 1 });
    await view.handle({ type: 'mouse', kind: 'up', x: 0.5, y: 0.2, button: 0, buttons: 0, clickCount: 1 });
    for (const key of 'hi') {
      await view.handle({ type: 'key', kind: 'down', key, code: `Key${key.toUpperCase()}` });
      await view.handle({ type: 'key', kind: 'up', key, code: `Key${key.toUpperCase()}` });
    }
    await view.handle({ type: 'paste', text: '!' });
    await view.handle({ type: 'mouse', kind: 'down', x: 0.5, y: 0.8, button: 0, buttons: 1, clickCount: 1 });
    await view.handle({ type: 'mouse', kind: 'up', x: 0.5, y: 0.8, button: 0, buttons: 0, clickCount: 1 });
    await waitFor(async () => (await title()) === 'clicked:hi!', 'the click handler');
    await view.dispose();
    cdp.close();
  } finally {
    chrome.kill('SIGTERM');
    await new Promise((r) => chrome.once('exit', r));
    rmSync(data, { recursive: true, force: true });
  }
});
