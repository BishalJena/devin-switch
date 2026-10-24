'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { WhiteboardHost, whiteboardHome } = require('../../src/devin_switch/desktop_extension/whiteboard');

/** A WebSocket stand-in the tests drive by hand. */
class FakeSocket {
  static all = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.listeners = {};
    FakeSocket.all.push(this);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ??= []).push(fn);
  }
  emit(type, event = {}) {
    for (const fn of this.listeners[type] ?? []) fn(event);
  }
  open() {
    this.readyState = 1;
    this.emit('open');
  }
  send(data) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
    this.emit('close');
  }
}

const tick = () => new Promise((r) => setTimeout(r, 5));
const files = (map) => async (file) => {
  const name = path.basename(file);
  if (!(name in map)) throw new Error('ENOENT');
  return map[name];
};

function makeHost(overrides = {}) {
  FakeSocket.all = [];
  const shown = [];
  const state = { folders: ['/work/app'], focused: true };
  const host = new WhiteboardHost({
    home: '/home/u/.whiteboard-mcp',
    folders: () => state.folders,
    focused: () => state.focused,
    hostId: '45824',
    onShow: async ({ canvasId, url, title }) => {
      if (title === 'boom') throw new Error('panel failed');
      shown.push({ canvasId, url, title });
    },
    WebSocketImpl: FakeSocket,
    readFile: files({ 'daemon.json': JSON.stringify({ port: 47428, pid: 1 }), token: 'tok\n' }),
    reconnectMs: 10,
    ...overrides,
  });
  return { host, shown, state };
}

test('whiteboardHome honours the setting, then WHITEBOARD_HOME, then ~/.whiteboard-mcp', () => {
  assert.equal(whiteboardHome('/custom'), '/custom');
  assert.ok(whiteboardHome('~/wb').endsWith(`${path.sep}wb`) && !whiteboardHome('~/wb').startsWith('~'));
  const saved = process.env.WHITEBOARD_HOME;
  process.env.WHITEBOARD_HOME = '/env/home';
  assert.equal(whiteboardHome(''), '/env/home');
  delete process.env.WHITEBOARD_HOME;
  assert.ok(whiteboardHome('').endsWith('.whiteboard-mcp'));
  if (saved !== undefined) process.env.WHITEBOARD_HOME = saved;
});

test('says hello with its window id and folders, opens shown boards with a tokenised URL and reports the outcome', async () => {
  const { host, shown, state } = makeHost();
  host.start();
  await tick();
  const socket = FakeSocket.all[0];
  assert.equal(socket.url, 'ws://127.0.0.1:47428/ws/host?token=tok');
  socket.open();
  // The daemon routes a chat's board to the window whose id matches the chat's WINDSURF_EXT_HOST_PID.
  assert.deepEqual(socket.sent[0], { t: 'hello', hostId: '45824', folders: ['/work/app'], focused: true });

  state.focused = false;
  host.update();
  assert.deepEqual(socket.sent[1], { t: 'state', folders: ['/work/app'], focused: false });

  socket.emit('message', { data: JSON.stringify({ t: 'show', reqId: 'r1', canvasId: 'app-1a2b3c', title: 'How MCP works' }) });
  await tick();
  assert.deepEqual(shown, [{ canvasId: 'app-1a2b3c', url: 'http://127.0.0.1:47428/c/app-1a2b3c?token=tok', title: 'How MCP works' }]);
  assert.deepEqual(socket.sent[2], { t: 'shown', reqId: 'r1', ok: true });

  socket.emit('message', { data: JSON.stringify({ t: 'show', reqId: 'r2', canvasId: 'x', title: 'boom' }) });
  await tick();
  assert.deepEqual(socket.sent[3], { t: 'shown', reqId: 'r2', ok: false, error: 'panel failed' });
  socket.emit('message', { data: '{not json' });
  socket.emit('message', { data: JSON.stringify({ t: 'other' }) });
  await tick();
  assert.equal(socket.sent.length, 4);
  host.dispose();
});

test('waits for the daemon to appear and reconnects after it restarts; dispose stops retrying', async () => {
  const available = { 'daemon.json': JSON.stringify({ port: 5000 }), token: 't' };
  const disk = {};
  const { host } = makeHost({ readFile: files(disk) });
  host.start();
  await tick();
  assert.equal(FakeSocket.all.length, 0, 'no daemon yet');
  Object.assign(disk, available);
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(FakeSocket.all.length, 1);
  FakeSocket.all[0].open();
  FakeSocket.all[0].close();
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(FakeSocket.all.length, 2, 'reconnected after the daemon went away');
  host.dispose();
  FakeSocket.all[1].close();
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(FakeSocket.all.length, 2);
});

test('lists only boards of this window\'s folders, newest first, with the token', async () => {
  let request;
  const fetchImpl = async (url, init) => {
    request = { url, init };
    return {
      ok: true,
      json: async () => [
        { id: 'a', title: 'Old', workspace: '/work/app', updatedAt: 1 },
        { id: 'b', title: 'Other space', workspace: '/work/other', updatedAt: 9 },
        { id: 'c', title: 'New', workspace: '/work/app/sub', updatedAt: 5 },
        { id: 'd', title: 'Legacy', updatedAt: 7 },
      ],
    };
  };
  const { host } = makeHost({ fetchImpl });
  assert.deepEqual((await host.boards()).map((b) => b.id), ['c', 'a']);
  assert.equal(request.url, 'http://127.0.0.1:47428/api/canvases');
  assert.equal(request.init.headers.Authorization, 'Bearer tok');
  const { host: offline } = makeHost({ readFile: files({}) });
  assert.deepEqual(await offline.boards(), []);
});
