#!/usr/bin/env node
// Download chrome-headless-shell from Chrome for Testing into <tad-clip home>/chrome/<version>/.
// Used when the machine has no Google Chrome (Mac) or no Edge (Windows). Node stdlib only (no npm, no unzip tool).
// Usage: node get_chrome.mjs [--force] [--dest DIR]   → prints the executable path as the last line of stdout.
import { chmodSync, existsSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateRawSync } from 'node:zlib';
import { home } from '../kit.mjs';

export const CFT_JSON = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json';

export function platformKey(plat = process.platform, arch = process.arch) {
  if (plat === 'darwin') return arch === 'arm64' ? 'mac-arm64' : 'mac-x64';
  if (plat === 'win32') return arch === 'ia32' ? 'win32' : 'win64';
  if (plat === 'linux') return arch === 'arm64' ? 'linux-arm64' : 'linux64';
  throw new Error(`ระบบ ${plat}/${arch} ยังไม่รองรับ`);
}

export function pickDownload(json, key) {
  const stable = json?.channels?.Stable;
  const hit = stable?.downloads?.['chrome-headless-shell']?.find(d => d.platform === key);
  if (!hit) throw new Error(`Chrome for Testing ไม่มี chrome-headless-shell สำหรับ ${key}`);
  return { version: stable.version, url: hit.url };
}

export const exeRelPath = key => join(`chrome-headless-shell-${key}`, key.startsWith('win') ? 'chrome-headless-shell.exe' : 'chrome-headless-shell');

const inside = (root, p) => p === root || p.startsWith(root + sep);

// Minimal ZIP reader (stored + deflate, no zip64) — keeps unix permissions and symlinks so the Mac binary stays runnable.
export function extractZip(buf, dest) {
  const root = resolve(dest);
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 22 - 0xffff); i--) if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error('ไฟล์ zip เสีย (ไม่พบสารบัญท้ายไฟล์)');
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  if (count === 0xffff || p === 0xffffffff) throw new Error('zip64 ยังไม่รองรับ');
  const written = [];
  for (let n = 0; n < count; n++) {
    if (buf.readUInt32LE(p) !== 0x02014b50) throw new Error('ไฟล์ zip เสีย (สารบัญไม่ครบ)');
    const madeBy = buf.readUInt16LE(p + 4) >> 8, method = buf.readUInt16LE(p + 10), size = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28), extraLen = buf.readUInt16LE(p + 30), commentLen = buf.readUInt16LE(p + 32);
    const mode = madeBy === 3 ? buf.readUInt32LE(p + 38) >>> 16 : 0;   // 3 = made on unix → high 16 bits are st_mode
    const local = buf.readUInt32LE(p + 42);
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen);
    p += 46 + nameLen + extraLen + commentLen;
    const out = resolve(root, name);
    if (!inside(root, out)) throw new Error(`zip มี path ที่ออกนอกโฟลเดอร์: ${name}`);
    const type = mode & 0o170000;
    if (name.endsWith('/') || type === 0o040000) { mkdirSync(out, { recursive: true }); continue; }
    if (buf.readUInt32LE(local) !== 0x04034b50) throw new Error(`ไฟล์ zip เสีย (${name})`);
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const raw = buf.subarray(start, start + size);
    if (method !== 0 && method !== 8) throw new Error(`zip วิธีบีบอัด ${method} ยังไม่รองรับ (${name})`);
    const data = method === 8 ? inflateRawSync(raw) : raw;
    mkdirSync(dirname(out), { recursive: true });
    if (type === 0o120000 && process.platform !== 'win32') {
      const target = data.toString('utf8');
      if (!inside(root, resolve(dirname(out), target))) throw new Error(`zip มีลิงก์ที่ชี้ออกนอกโฟลเดอร์: ${name}`);
      rmSync(out, { force: true });
      symlinkSync(target, out);
    } else {
      writeFileSync(out, data);
      if (process.platform !== 'win32' && mode & 0o777) chmodSync(out, mode & 0o777);
    }
    written.push(out);
  }
  return written;
}

async function get(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'tad-clip-setup' } });
  if (!res.ok) throw new Error(`โหลด ${url} ไม่ได้ (HTTP ${res.status})`);
  return res;
}

export async function install({ force = false, dest = join(home(), 'chrome'), key = platformKey() } = {}) {
  const { version, url } = pickDownload(await (await get(CFT_JSON)).json(), key);
  const dir = join(dest, version), exe = join(dir, exeRelPath(key));
  if (existsSync(exe) && !force) return exe;
  console.error(`กำลังโหลด chrome-headless-shell ${version} (${key}) ประมาณ 100 MB …`);
  const buf = Buffer.from(await (await get(url)).arrayBuffer());
  rmSync(dir, { recursive: true, force: true });
  extractZip(buf, dir);
  if (!existsSync(exe)) throw new Error(`แตกไฟล์แล้วไม่เจอ ${exe}`);
  return exe;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2), at = args.indexOf('--dest');
  install({ force: args.includes('--force'), ...(at >= 0 ? { dest: resolve(args[at + 1]) } : {}) })
    .then(exe => console.log(exe))
    .catch(e => { console.error(`❌ ติดตั้ง chrome-headless-shell ไม่สำเร็จ: ${e.message}`); process.exit(1); });
}
