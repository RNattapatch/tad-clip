import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, lstatSync, mkdtempSync, readFileSync, readlinkSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { deflateRawSync } from 'node:zlib';

process.env.TAD_CLIP_HOME = mkdtempSync(join(tmpdir(), 'tad clip ทดสอบ-'));
const gc = await import('../engine/install/get_chrome.mjs');

// tiny ZIP writer for the test (CRC left 0 — the extractor does not verify it)
function zip(entries) {
  const locals = [], centrals = [];
  let offset = 0;
  for (const { name, data = Buffer.alloc(0), mode = 0o100644, deflate = true } of entries) {
    const nameBuf = Buffer.from(name, 'utf8');
    const body = deflate && data.length ? deflateRawSync(data) : data;
    const method = deflate && data.length ? 8 : 0;
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(method, 8);
    lh.writeUInt32LE(body.length, 18); lh.writeUInt32LE(data.length, 22); lh.writeUInt16LE(nameBuf.length, 26);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE((3 << 8) | 20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(method, 10);
    ch.writeUInt32LE(body.length, 20); ch.writeUInt32LE(data.length, 24); ch.writeUInt16LE(nameBuf.length, 28);
    ch.writeUInt32LE((mode << 16) >>> 0, 38); ch.writeUInt32LE(offset, 42);
    locals.push(lh, nameBuf, body); centrals.push(ch, nameBuf);
    offset += 30 + nameBuf.length + body.length;
  }
  const cd = Buffer.concat(centrals), eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(entries.length, 8); eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12); eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

test('platform keys match Chrome for Testing names', () => {
  assert.equal(gc.platformKey('darwin', 'arm64'), 'mac-arm64');
  assert.equal(gc.platformKey('darwin', 'x64'), 'mac-x64');
  assert.equal(gc.platformKey('win32', 'x64'), 'win64');
  assert.equal(gc.platformKey('linux', 'x64'), 'linux64');
});

test('pickDownload takes the Stable chrome-headless-shell for the platform', () => {
  const json = { channels: { Stable: { version: '155.0.1.2', downloads: {
    chrome: [{ platform: 'win64', url: 'https://x/chrome-win64.zip' }],
    'chrome-headless-shell': [{ platform: 'mac-arm64', url: 'https://x/m.zip' }, { platform: 'win64', url: 'https://x/w.zip' }] } } } };
  assert.deepEqual(gc.pickDownload(json, 'win64'), { version: '155.0.1.2', url: 'https://x/w.zip' });
  assert.throws(() => gc.pickDownload(json, 'linux64'), /linux64/);
  assert.match(gc.exeRelPath('win64'), /chrome-headless-shell-win64[\\/]chrome-headless-shell\.exe$/);
});

test('extractZip writes files, keeps the exec bit and symlinks', () => {
  const dest = join(process.env.TAD_CLIP_HOME, 'แตก ไฟล์');
  const bin = Buffer.from('#!/bin/sh\necho hi\n'.repeat(50));
  gc.extractZip(zip([
    { name: 'shell-mac/', mode: 0o040755 },
    { name: 'shell-mac/chrome-headless-shell', data: bin, mode: 0o100755 },
    { name: 'shell-mac/res.pak', data: Buffer.from('pak'), deflate: false },
    { name: 'shell-mac/link', data: Buffer.from('res.pak'), mode: 0o120777, deflate: false },
  ]), dest);
  assert.deepEqual(readFileSync(join(dest, 'shell-mac/chrome-headless-shell')), bin);
  assert.equal(readFileSync(join(dest, 'shell-mac/res.pak'), 'utf8'), 'pak');
  if (process.platform !== 'win32') {
    assert.equal(statSync(join(dest, 'shell-mac/chrome-headless-shell')).mode & 0o111, 0o111);
    assert.ok(lstatSync(join(dest, 'shell-mac/link')).isSymbolicLink());
    assert.equal(readlinkSync(join(dest, 'shell-mac/link')), 'res.pak');
  }
});

test('extractZip refuses paths that escape the folder', () => {
  const dest = join(process.env.TAD_CLIP_HOME, 'safe');
  assert.throws(() => gc.extractZip(zip([{ name: '../evil.txt', data: Buffer.from('x') }]), dest), /ออกนอกโฟลเดอร์/);
  assert.equal(existsSync(join(process.env.TAD_CLIP_HOME, 'evil.txt')), false);
  if (process.platform !== 'win32')
    assert.throws(() => gc.extractZip(zip([{ name: 'l', data: Buffer.from('../../etc'), mode: 0o120777, deflate: false }]), dest), /ลิงก์/);
});

test('extractZip rejects a broken file with a Thai message', () => {
  assert.throws(() => gc.extractZip(Buffer.from('not a zip at all, definitely not'), tmpdir()), /zip เสีย/);
});
