'use strict';
// Pure translation from panel events to Chrome DevTools Protocol commands.

const MOUSE_TYPES = { down: 'mousePressed', up: 'mouseReleased', move: 'mouseMoved', wheel: 'mouseWheel' };
const BUTTONS = ['left', 'middle', 'right', 'back', 'forward'];

/** CDP modifier bit field: Alt=1, Ctrl=2, Meta=4, Shift=8. */
function modifiers(event) {
  return (event.alt ? 1 : 0) | (event.ctrl ? 2 : 0) | (event.meta ? 4 : 0) | (event.shift ? 8 : 0);
}

/**
 * Panel coordinates are fractions (0..1) of the frame image; the screencast metadata
 * gives the page size in CSS pixels, which is what Input.dispatchMouseEvent expects.
 */
function mouseCommand(event, metadata) {
  const type = MOUSE_TYPES[event.kind];
  if (!type || !metadata) return null;
  const clamp = (v) => Math.min(1, Math.max(0, Number(v) || 0));
  const params = {
    type,
    x: Math.round(clamp(event.x) * metadata.deviceWidth),
    y: Math.round(clamp(event.y) * metadata.deviceHeight),
    modifiers: modifiers(event),
    button: type === 'mouseMoved' || type === 'mouseWheel' ? 'none' : (BUTTONS[event.button] ?? 'left'),
    buttons: Number(event.buttons) || 0,
  };
  if (type === 'mousePressed' || type === 'mouseReleased') params.clickCount = Math.max(1, Number(event.clickCount) || 1);
  if (type === 'mouseWheel') Object.assign(params, { deltaX: Number(event.deltaX) || 0, deltaY: Number(event.deltaY) || 0 });
  return params;
}

const KEY_CODES = {
  Backspace: 8, Tab: 9, Enter: 13, Shift: 16, Control: 17, Alt: 18, Escape: 27, ' ': 32,
  PageUp: 33, PageDown: 34, End: 35, Home: 36, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
  Delete: 46, Meta: 91,
};

function keyCode(key) {
  if (key in KEY_CODES) return KEY_CODES[key];
  if (key.length === 1) {
    const upper = key.toUpperCase();
    if (/[A-Z0-9]/.test(upper)) return upper.charCodeAt(0);
  }
  return 0;
}

/** Printable keys carry `text` so Chrome inserts them; shortcuts (Ctrl/Meta) must not. */
function keyCommand(event) {
  const key = String(event.key ?? '');
  if (!key || (event.kind !== 'down' && event.kind !== 'up')) return null;
  const shortcut = event.ctrl || event.meta;
  const text = key === 'Enter' ? '\r' : key.length === 1 && !shortcut ? key : '';
  const params = {
    type: event.kind === 'up' ? 'keyUp' : text ? 'keyDown' : 'rawKeyDown',
    key,
    code: String(event.code ?? ''),
    windowsVirtualKeyCode: keyCode(key),
    modifiers: modifiers(event),
  };
  if (params.type === 'keyDown') Object.assign(params, { text, unmodifiedText: text });
  return params;
}

/** Address-bar input: URLs as typed, bare hosts get https://, everything else is a search. */
function navigationUrl(input) {
  const value = String(input ?? '').trim();
  if (!value) return null;
  if (/^(https?|file|about|chrome|data):/i.test(value)) return value;
  if (/^(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i.test(value)) return `http://${value}`;
  if (/^[^\s/]+\.[a-z]{2,}(:\d+)?(\/\S*)?$/i.test(value)) return `https://${value}`;
  return `https://www.google.com/search?q=${encodeURIComponent(value)}`;
}

module.exports = { keyCommand, modifiers, mouseCommand, navigationUrl };
