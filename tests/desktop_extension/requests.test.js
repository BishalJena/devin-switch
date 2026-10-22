'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { ANY_DELAY_MS, FOCUSED_DELAY_MS, claim, contains } = require('../../src/devin_switch/desktop_extension/requests');

function requests(entries) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'panels-requests-'));
  entries.forEach((entry, i) => fs.writeFileSync(path.join(dir, `${i}.json`), JSON.stringify(entry)));
  return dir;
}

test('workspace containment is path-aware', () => {
  assert.ok(contains('/repo', '/repo'));
  assert.ok(contains('/repo', '/repo/src/x'));
  assert.ok(!contains('/repo', '/repository'));
  assert.ok(!contains('/repo/src', '/repo'));
});

test('the window with the chat folder claims at once; others wait their turn', () => {
  const now = 10_000;
  const dir = requests([{ url: 'http://127.0.0.1:1/c/a', cwd: '/work/app/sub', at: now }]);
  assert.deepEqual(claim(dir, { folders: ['/work/other'], focused: true, now }), [], 'focused but not matching waits');
  assert.equal(claim(dir, { folders: ['/work/app'], focused: false, now }).length, 1);
  assert.deepEqual(fs.readdirSync(dir), [], 'claimed requests are removed');
});

test('a focused window takes unmatched requests before an unfocused one', () => {
  const now = 50_000;
  const dir = requests([{ url: 'u', cwd: '/elsewhere', at: now - FOCUSED_DELAY_MS }]);
  assert.deepEqual(claim(dir, { folders: ['/a'], focused: false, now }), []);
  assert.equal(claim(dir, { folders: ['/a'], focused: true, now }).length, 1);
  const late = requests([{ url: 'u', cwd: '/elsewhere', at: now - ANY_DELAY_MS }]);
  assert.equal(claim(late, { folders: [], focused: false, now }).length, 1);
});

test('only one of several windows wins a request; broken files are skipped', () => {
  const now = 1_000;
  const dir = requests([{ url: 'u', cwd: '/w', at: now }]);
  fs.writeFileSync(path.join(dir, 'broken.json'), '{');
  const results = [claim(dir, { folders: ['/w'], focused: true, now }), claim(dir, { folders: ['/w'], focused: true, now })];
  assert.deepEqual(results.map((r) => r.length), [1, 0]);
  assert.deepEqual(claim(path.join(dir, 'missing'), { folders: [], focused: true, now }), []);
});
