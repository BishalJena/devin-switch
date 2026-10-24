'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { keyCommand, modifiers, mouseCommand, navigationUrl } = require('../../src/devin_switch/desktop_extension/input');

const metadata = { deviceWidth: 1440, deviceHeight: 900 };

test('mouse coordinates scale from frame fractions to page CSS pixels and clamp', () => {
  assert.deepEqual(mouseCommand({ kind: 'down', x: 0.5, y: 0.25, button: 0, buttons: 1, clickCount: 2 }, metadata), {
    type: 'mousePressed', x: 720, y: 225, modifiers: 0, button: 'left', buttons: 1, clickCount: 2,
  });
  const outside = mouseCommand({ kind: 'move', x: 1.4, y: -3 }, metadata);
  assert.deepEqual([outside.x, outside.y, outside.button, outside.clickCount], [1440, 0, 'none', undefined]);
  assert.equal(mouseCommand({ kind: 'up', x: 0, y: 0, button: 2 }, metadata).button, 'right');
  assert.deepEqual(mouseCommand({ kind: 'wheel', x: 0, y: 0, deltaY: 120 }, metadata).deltaY, 120);
  assert.equal(mouseCommand({ kind: 'down', x: 0, y: 0 }, null), null, 'no frame yet, nothing to map onto');
  assert.equal(mouseCommand({ kind: 'drag', x: 0, y: 0 }, metadata), null);
});

test('modifier bits follow CDP: alt 1, ctrl 2, meta 4, shift 8', () => {
  assert.equal(modifiers({ alt: true, ctrl: true, meta: true, shift: true }), 15);
  assert.equal(modifiers({ meta: true }), 4);
  assert.equal(modifiers({}), 0);
});

test('printable keys insert text; shortcuts and named keys do not', () => {
  assert.deepEqual(keyCommand({ kind: 'down', key: 'a', code: 'KeyA' }), {
    type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 0, text: 'a', unmodifiedText: 'a',
  });
  const shortcut = keyCommand({ kind: 'down', key: 'a', code: 'KeyA', meta: true });
  assert.equal(shortcut.type, 'rawKeyDown');
  assert.equal(shortcut.text, undefined);
  assert.equal(keyCommand({ kind: 'down', key: 'Enter', code: 'Enter' }).text, '\r');
  assert.equal(keyCommand({ kind: 'down', key: 'ArrowLeft' }).windowsVirtualKeyCode, 37);
  assert.equal(keyCommand({ kind: 'down', key: 'ArrowLeft' }).type, 'rawKeyDown');
  assert.equal(keyCommand({ kind: 'up', key: 'a' }).type, 'keyUp');
  assert.equal(keyCommand({ kind: 'press', key: 'a' }), null);
});

test('address bar input becomes a URL or a search', () => {
  assert.equal(navigationUrl('example.com/path'), 'https://example.com/path');
  assert.equal(navigationUrl('localhost:5173'), 'http://localhost:5173');
  assert.equal(navigationUrl('http://x.test'), 'http://x.test');
  assert.equal(navigationUrl('how to center a div'), 'https://www.google.com/search?q=how%20to%20center%20a%20div');
  assert.equal(navigationUrl('   '), null);
});
