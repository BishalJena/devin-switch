'use strict';
// Canvas requests from `ds canvas show`: files in <DS_HOME>/panels/requests that one window claims.

const fs = require('node:fs');
const path = require('node:path');

/** Windows whose workspace contains the chat's folder claim at once; a focused window after a pause; any window last. */
const FOCUSED_DELAY_MS = 600;
const ANY_DELAY_MS = 1500;

function contains(folder, target) {
  const relative = path.relative(folder, target);
  return relative === '' || (!!relative && !relative.startsWith('..') && !path.isAbsolute(relative));
}

/** How long this window waits before claiming, or null when it must not claim yet. */
function claimDelay(request, folders) {
  if (typeof request.cwd === 'string' && folders.some((folder) => contains(folder, request.cwd))) return 0;
  return null;
}

/**
 * Claims pending requests this window should handle. Rename is atomic, so exactly one window wins.
 * @returns {Array<{url: string}>}
 */
function claim(directory, { folders, focused, now = Date.now() }) {
  let names;
  try {
    names = fs.readdirSync(directory).filter((name) => name.endsWith('.json'));
  } catch {
    return [];
  }
  const claimed = [];
  for (const name of names.sort()) {
    const file = path.join(directory, name);
    let request;
    try {
      request = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch {
      continue;
    }
    const age = now - (Number(request.at) || 0);
    const delay = claimDelay(request, folders) ?? (focused ? FOCUSED_DELAY_MS : ANY_DELAY_MS);
    if (age < delay || typeof request.url !== 'string') continue;
    try {
      fs.renameSync(file, `${file}.claimed-${process.pid}`);
    } catch {
      continue; // another window won
    }
    fs.rmSync(`${file}.claimed-${process.pid}`, { force: true });
    claimed.push(request);
  }
  return claimed;
}

module.exports = { ANY_DELAY_MS, FOCUSED_DELAY_MS, claim, contains };
