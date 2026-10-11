#!/usr/bin/env node
// tad-clip library — จัดคลังมีม/ภาพ/เสียงของผู้ใช้เอง แล้วค้นหาตอนต้องเลือกของใส่คลิป (ไม่มีคลังแถม ไม่มี path ของใคร)
//
//   node library.mjs index  [--dir D] [--force]          สแกน → <D>/.tad-clip/catalog.jsonl (id คงเดิมเมื่อรันซ้ำ · ไม่แตะ notes.json)
//   node library.mjs sheet  [--untagged] [--kind K] [--page N] [--out FILE]   ภาพรวมมีเลข id ให้ Claude ดูแล้วแท็ก
//   node library.mjs tag    <id> [--tags a,b] [--emotion X] [--beat Y] [--risk safe|caution|avoid] [--note "…"] [--favorite yes|no]
//   node library.mjs search [--q "คำ คำ"] [--beat B] [--emotion E] [--kind video|gif|image] [--risk safe,caution] [--limit 12] [--json]
//   node library.mjs sounds [--q …] [--beat B] [--kind sfx|music] [--risk …] [--limit 12] [--json]
//   node library.mjs show   <id>
//   node library.mjs stats
//
// โฟลเดอร์คลัง = --dir หรือ config.library_dir · ข้อมูลที่ผู้ใช้สอนอยู่ใน notes.json (ชนะค่าใน catalog เสมอ)
import { execFile } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, mkdtempSync, openSync, readFileSync, readSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { parseArgs, promisify } from 'node:util';
import { KitError, cacheDir, fontsDir, loadConfig, tool } from './kit.mjs';

const run = promisify(execFile);
const EXT = { '.mp4': 'video', '.mov': 'video', '.gif': 'gif', '.webp': 'image', '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.mp3': 'audio', '.wav': 'audio', '.m4a': 'audio' };
const VISUAL = new Set(['video', 'gif', 'image']);
const AUDIO = new Set(['sfx', 'music']);
const RISKS = ['safe', 'caution', 'avoid'];
const RISK_BONUS = { safe: 0, caution: 0, avoid: -2 };
const BEATS = ['hook', 'pain', 'twist', 'punchline', 'reaction', 'transition', 'cta', 'outro'];
const MUSIC_MIN_SECS = 20;          // เสียงยาวกว่านี้ = เพลง/พื้นหลัง ไม่ใช่เอฟเฟกต์
const SHEET = { cols: 6, rows: 4, tile: 240 };
const fail = msg => { throw new KitError(msg); };

// ---------- files ----------
const metaDir = d => join(d, '.tad-clip');
const catalogPath = d => join(metaDir(d), 'catalog.jsonl');
const notesPath = d => join(metaDir(d), 'notes.json');

function writeAtomic(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, text, 'utf8');
  renameSync(tmp, path);
}

function libDir(dirOpt) {
  const d = dirOpt ?? loadConfig().library_dir;
  if (!d) fail('ยังไม่ได้ตั้งโฟลเดอร์คลัง — ใส่ --dir <โฟลเดอร์> หรือบอก Claude ให้ตั้ง library_dir ใน config.json');
  const abs = resolve(d.replace(/^~(?=$|[\\/])/, homedir()));
  if (!existsSync(abs) || !statSync(abs).isDirectory()) fail(`ไม่พบโฟลเดอร์คลัง ${abs}`);
  return abs;
}

function readCatalog(dir) {
  const f = catalogPath(dir);
  if (!existsSync(f)) return [];
  return readFileSync(f, 'utf8').split(/\r?\n/).filter(l => l.trim()).flatMap(l => { try { return [JSON.parse(l)]; } catch { return []; } });
}

function readNotes(dir) {
  const f = notesPath(dir);
  if (!existsSync(f)) return {};
  try { return JSON.parse(readFileSync(f, 'utf8')); }
  catch { return fail(`อ่าน ${f} ไม่ได้ (JSON เสีย) — ยังไม่เขียนทับให้ ย้ายไฟล์นี้ออกหรือแก้ให้ถูกก่อน`); }
}

function scan(dir) {
  const out = [];
  const walk = (abs, rel) => {
    for (const e of readdirSync(abs, { withFileTypes: true })) {
      if (e.name.startsWith('.') || e.name === '__MACOSX' || e.name === '$RECYCLE.BIN') continue;
      const p = join(abs, e.name), r = rel ? `${rel}/${e.name}` : e.name;
      let isDir = e.isDirectory(), isFile = e.isFile();
      if (e.isSymbolicLink()) { isDir = false; try { isFile = statSync(p).isFile(); } catch { isFile = false; } }
      if (isDir) walk(p, r);
      else if (isFile && EXT[extname(e.name).toLowerCase()]) out.push(r);
    }
  };
  walk(dir, '');
  return out.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (next < items.length) { const k = next++; out[k] = await fn(items[k], k); }
  }));
  return out;
}

// ---------- index ----------
function isAnimatedWebp(abs) {
  try {
    const fd = openSync(abs, 'r'), b = Buffer.alloc(32);
    const n = readSync(fd, b, 0, 32, 0);
    closeSync(fd);
    return n >= 21 && b.toString('ascii', 0, 4) === 'RIFF' && b.toString('ascii', 8, 12) === 'WEBP' && b.toString('ascii', 12, 16) === 'VP8X' && (b[20] & 0x02) !== 0;
  } catch { return false; }
}

const r2 = x => Math.round((Number(x) || 0) * 100) / 100;

async function probeFile(ffprobe, abs, rel) {
  const ext = extname(rel).toLowerCase();
  let kind = EXT[ext];
  if (ext === '.webp' && isAnimatedWebp(abs)) kind = 'gif';
  let dur = 0, w = 0, h = 0, ok = true;
  try {
    const { stdout } = await run(ffprobe, ['-v', 'error', '-print_format', 'json', '-show_entries', 'stream=codec_type,width,height,duration:format=duration', '-i', abs], { maxBuffer: 1 << 22, timeout: 60000 });
    const j = JSON.parse(stdout);
    if (!(j.streams || []).length) ok = false;
    const v = (j.streams || []).find(s => s.codec_type === 'video');
    if (kind !== 'audio' && v) { w = v.width || 0; h = v.height || 0; }
    if (kind !== 'image') dur = r2(j.format?.duration ?? (j.streams || []).find(s => s.duration)?.duration);
  } catch { ok = false; }
  if (kind === 'audio' ? !dur : !(w && h)) ok = false;   // ffprobe can "succeed" on a broken file with 0x0 / no length
  if (kind === 'audio') kind = dur > MUSIC_MIN_SECS ? 'music' : 'sfx';
  return { kind, dur, w, h, ok };
}

const prefixOf = kind => (AUDIO.has(kind) ? 's' : 'm');
const numOf = id => parseInt(String(id).slice(1), 10) || 0;
const makeId = (prefix, n) => `${prefix}${String(n).padStart(3, '0')}`;
const rowOf = (id, file, p, prev = {}) => ({
  id, file, kind: p.kind, dur: p.dur, w: p.w, h: p.h,
  tags: prev.tags ?? [], emotion: prev.emotion ?? '', beat: prev.beat ?? '', risk: prev.risk ?? 'safe',
});

async function cmdIndex(dir, force) {
  const ffprobe = tool('ffprobe');
  const old = readCatalog(dir), notes = readNotes(dir);
  const files = scan(dir), present = new Set(files);
  const oldByFile = new Map(old.map(r => [r.file, r]));
  const todo = files.filter(f => force || !oldByFile.has(f));
  const probed = new Map();
  const warnings = [];
  await pool(todo, 8, async f => {
    const p = await probeFile(ffprobe, join(dir, ...f.split('/')), f);
    if (!p.ok) warnings.push(`อ่านข้อมูลไม่ได้ (ffprobe): ${f}`);
    probed.set(f, p);
  });

  const rows = [], fresh = [];
  for (const f of files) {
    const prev = oldByFile.get(f);
    if (!prev) { fresh.push(f); continue; }
    const p = probed.get(f);
    rows.push(p && prefixOf(p.kind) === prev.id[0] ? rowOf(prev.id, f, p, prev) : prev);
  }

  // a file that disappeared and reappeared elsewhere with the same name + length + size is the same item
  const orphans = old.filter(r => !present.has(r.file));
  const claimed = new Set();
  let moved = 0, added = 0;
  const maxNum = { m: 0, s: 0 };
  for (const id of [...old.map(r => r.id), ...Object.keys(notes)]) maxNum[id[0]] = Math.max(maxNum[id[0]] ?? 0, numOf(id));
  for (const f of fresh) {
    const p = probed.get(f);
    const twin = orphans.find(o => !claimed.has(o.id) && basename(o.file) === basename(f) && o.id[0] === prefixOf(p.kind)
      && Math.abs(o.dur - p.dur) <= 0.05 && o.w === p.w && o.h === p.h);
    if (twin) { claimed.add(twin.id); moved++; rows.push(rowOf(twin.id, f, p, twin)); continue; }
    const pre = prefixOf(p.kind);
    rows.push(rowOf(makeId(pre, ++maxNum[pre]), f, p));
    added++;
  }
  const gone = orphans.length - claimed.size;

  rows.sort((a, b) => (a.id[0] === b.id[0] ? numOf(a.id) - numOf(b.id) : a.id[0] < b.id[0] ? -1 : 1));
  mkdirSync(metaDir(dir), { recursive: true });
  writeAtomic(catalogPath(dir), rows.map(r => JSON.stringify(r)).join('\n') + (rows.length ? '\n' : ''));

  const mem = rows.filter(r => VISUAL.has(r.kind)).length, snd = rows.length - mem;
  const tagged = rows.filter(r => notes[r.id]).length;
  console.log(`สแกน ${dir}`);
  console.log(`ทั้งหมด ${rows.length} ไฟล์ (มีม ${mem} · เสียง ${snd}) · ใหม่ ${added} · หายไป ${gone} · ย้าย ${moved}`);
  console.log(`แท็กแล้ว ${tagged} · ยังไม่แท็ก ${rows.length - tagged}`);
  warnings.sort().forEach(w => console.log(`  ! ${w}`));
}

// ---------- merge catalog + notes ----------
const str = v => (typeof v === 'string' ? v : '');

function load(dir) {
  const notes = readNotes(dir);
  return readCatalog(dir).map(c => {
    const n = notes[c.id];
    const it = {
      ...c,
      tags: Array.isArray(n?.tags) ? n.tags : (c.tags || []),
      emotion: n && 'emotion' in n ? str(n.emotion) : str(c.emotion),
      beat: n && 'beat' in n ? str(n.beat) : str(c.beat),
      risk: RISKS.includes(n?.risk) ? n.risk : (RISKS.includes(c.risk) ? c.risk : 'safe'),
      note: str(n?.note),
      favorite: n?.favorite === true,
      edited: Boolean(n),
      abs: join(dir, ...c.file.split('/')),
    };
    // not enumerable: kept out of --json / show output
    Object.defineProperty(it, '_human', { value: n ? [str(n.note), ...(n.tags || []), str(n.emotion), str(n.beat)] : [] });
    Object.defineProperty(it, '_machine', { value: [c.file, ...(c.tags || []), str(c.emotion), str(c.beat)] });
    return it;
  });
}

// ---------- ranking (ported from the owner's meme-pick: what the user taught first, then favourites, then tags) ----------
const words = s => String(s ?? '').toLowerCase().split(/[\s,]+/).filter(Boolean);

function textScore(tokens, human, machine) {
  if (!tokens.length) return 0;
  const h = human.filter(Boolean).join(' ').toLowerCase(), m = machine.filter(Boolean).join(' ').toLowerCase();
  return tokens.reduce((s, t) => s + (h.includes(t) ? 2 : 0) + (m.includes(t) ? 1 : 0), 0);
}

function rank(items, o) {
  const tokens = words(o.q);
  const risks = o.risk ? o.risk.split(',').map(s => s.trim()).filter(Boolean) : null;
  const beats = o.beat ? words(o.beat) : null;
  const emo = o.emotion?.toLowerCase();
  return items
    .filter(it => (risks ? risks.includes(it.risk) : it.risk !== 'avoid'))
    .filter(it => !o.kind || it.kind === o.kind)
    .filter(it => !beats || words(it.beat).some(b => beats.includes(b)))
    .filter(it => !emo || it.emotion.toLowerCase().includes(emo) || it.tags.some(t => t.toLowerCase().includes(emo)))
    .map(it => {
      const t = textScore(tokens, it._human, it._machine);
      return { it, t, score: t * 3 + (it.favorite ? 4 : 0) + (it.edited ? 1 : 0) + RISK_BONUS[it.risk] };
    })
    .filter(r => !tokens.length || r.t > 0)
    .sort((a, b) => b.score - a.score || (a.it.id < b.it.id ? -1 : 1))
    .slice(0, Number.isFinite(Number(o.limit)) && Number(o.limit) > 0 ? Number(o.limit) : 12);
}

function describe(it, score) {
  const meta = [`${it.kind}${it.dur ? ` ${it.dur}s` : ''}${it.w ? ` ${it.w}x${it.h}` : ''}`, it.risk, it.favorite && 'ตัวโปรด', it.edited && 'แก้แล้ว'].filter(Boolean).join(' · ');
  return [
    `${it.id} [${meta}] score=${score}`,
    `   แท็ก: ${it.tags.join(', ') || '-'}  |  อารมณ์: ${it.emotion || '-'}  |  beat: ${it.beat || '-'}`,
    ...(it.note ? [`   โน้ต: ${it.note}`] : []),
    `   ไฟล์: ${it.abs}`,
  ].join('\n');
}

function cmdFind(dir, v, scope) {
  const items = load(dir).filter(it => scope.has(it.kind));
  const rows = rank(items, v);
  if (v.json) { console.log(JSON.stringify(rows.map(r => ({ ...r.it, score: r.score })), null, 1)); return; }
  console.log(rows.length ? rows.map(r => describe(r.it, r.score)).join('\n')
    : `ไม่เจอ — ลองคำค้นที่กว้างขึ้น หรือตัด --beat/--emotion/--kind${items.length ? '' : ' (คลังนี้ยังไม่มีรายการประเภทนี้ — รัน index ก่อน)'}`);
}

// ---------- tag / show / stats ----------
function cmdTag(dir, v, pos) {
  const id = pos[1];
  if (!id) fail('ใช้: tag <id> [--tags a,b] [--emotion X] [--beat Y] [--risk safe|caution|avoid] [--note "…"] [--favorite yes|no]');
  if (!readCatalog(dir).some(r => r.id === id)) fail(`ไม่มี ${id} ในคลัง — ดู id จาก search / sheet (เพิ่งเพิ่มไฟล์ใหม่ให้รัน index ก่อน)`);
  const upd = {};
  if (v.tags !== undefined) upd.tags = v.tags.split(',').map(s => s.trim()).filter(Boolean);
  if (v.emotion !== undefined) upd.emotion = v.emotion.trim();
  if (v.beat !== undefined) {
    upd.beat = v.beat.split(',').map(s => s.trim()).filter(Boolean).join(',');
    const odd = upd.beat.split(',').filter(b => b && !BEATS.includes(b));
    if (odd.length) console.error(`หมายเหตุ: beat "${odd.join(', ')}" ไม่อยู่ในรายการมาตรฐาน (${BEATS.join(' · ')}) — ค้นด้วย --beat ต้องพิมพ์ให้ตรงกัน`);
  }
  if (v.risk !== undefined) {
    if (!RISKS.includes(v.risk)) fail(`--risk ต้องเป็น ${RISKS.join(' | ')}`);
    upd.risk = v.risk;
  }
  if (v.note !== undefined) upd.note = v.note.trim();
  if (v.favorite !== undefined) {
    const f = v.favorite.toLowerCase();
    if (!['yes', 'no', 'true', 'false', 'ใช่', 'ไม่'].includes(f)) fail('--favorite ต้องเป็น yes หรือ no');
    upd.favorite = ['yes', 'true', 'ใช่'].includes(f);
  }
  if (!Object.keys(upd).length) fail('ยังไม่ได้ระบุอะไรให้แก้ — ใส่อย่างน้อย 1 อย่าง: --tags --emotion --beat --risk --note --favorite');
  const notes = readNotes(dir);
  notes[id] = { ...(notes[id] || {}), ...upd };
  writeAtomic(notesPath(dir), JSON.stringify(notes, null, 2) + '\n');
  console.log(`แท็ก ${id} แล้ว: ${JSON.stringify(notes[id])}`);
}

function cmdShow(dir, pos) {
  const it = load(dir).find(x => x.id === pos[1]);
  if (!it) fail(`ไม่มี ${pos[1] ?? '(ไม่ได้ระบุ id)'} ในคลัง`);
  console.log(JSON.stringify(it, null, 1));
}

function cmdStats(dir) {
  const items = load(dir);
  const n = f => items.filter(f).length;
  const vis = items.filter(i => VISUAL.has(i.kind)), aud = items.filter(i => AUDIO.has(i.kind));
  const by = (arr, k) => arr.filter(i => i.kind === k).length;
  console.log(`คลัง: ${dir}`);
  console.log(`มีม ${vis.length} (video ${by(vis, 'video')} · gif ${by(vis, 'gif')} · image ${by(vis, 'image')}) · เสียง ${aud.length} (sfx ${by(aud, 'sfx')} · music ${by(aud, 'music')})`);
  console.log(`แท็กแล้ว ${n(i => i.edited)} · ยังไม่แท็ก ${n(i => !i.edited)} · ตัวโปรด ${n(i => i.favorite)} · ห้ามใช้ (avoid) ${n(i => i.risk === 'avoid')}`);
}

// ---------- sheet ----------
async function ffmpegOk(ffmpeg, args, cwd) {
  try { await run(ffmpeg, args, { cwd, timeout: 60000, maxBuffer: 1 << 22 }); return true; } catch { return false; }
}

async function cmdSheet(dir, v) {
  const items = load(dir).filter(i => VISUAL.has(i.kind) && (!v.kind || i.kind === v.kind) && (!v.untagged || !i.edited));
  if (!items.length) { console.log('ไม่มีรายการให้ทำภาพรวม — ลองตัด --untagged / --kind หรือรัน index ก่อน'); return; }
  const per = SHEET.cols * SHEET.rows, pages = Math.ceil(items.length / per);
  const page = Math.min(Math.max(parseInt(v.page ?? '1', 10) || 1, 1), pages);
  const slice = items.slice((page - 1) * per, page * per);
  const outPath = v.out ? resolve(v.out) : join(metaDir(dir), 'sheets', `sheet-${page}.jpg`);
  mkdirSync(dirname(outPath), { recursive: true });
  const ffmpeg = tool('ffmpeg'), T = SHEET.tile;
  // fontfile is given relative to cwd = fonts dir, so no Windows drive-letter escaping inside the filter string
  const draw = id => `drawtext=fontfile=Prompt-Bold.ttf:text='${id}':fontsize=26:fontcolor=white:box=1:boxcolor=0xDD4155@0.9:boxborderw=5:x=6:y=6`;
  const labels = await ffmpegOk(ffmpeg, ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64', '-frames:v', '1', '-vf', draw('m000'), '-f', 'null', '-'], fontsDir());
  const cols = Math.min(SHEET.cols, slice.length), rows = Math.ceil(slice.length / cols);
  const work = mkdtempSync(join(cacheDir(), 'sheet-'));
  try {
    await pool(slice, 4, async (it, k) => {
      const dst = join(work, `t${String(k + 1).padStart(3, '0')}.jpg`);
      const vf = [`scale=${T}:${T}:force_original_aspect_ratio=decrease`, `pad=${T}:${T}:(ow-iw)/2:(oh-ih)/2:black`, ...(labels ? [draw(it.id)] : []), 'format=yuvj420p'].join(',');
      const seek = it.kind !== 'image' && it.dur > 0 ? ['-ss', (it.dur * 0.4).toFixed(2)] : [];
      const base = ['-v', 'error', '-y'];
      const tail = ['-i', it.abs, '-frames:v', '1', '-vf', vf, '-q:v', '3', dst];
      if ((await ffmpegOk(ffmpeg, [...base, ...seek, ...tail], fontsDir()) && existsSync(dst))
        || (await ffmpegOk(ffmpeg, [...base, ...tail], fontsDir()) && existsSync(dst))) return;
      await ffmpegOk(ffmpeg, [...base, '-f', 'lavfi', '-i', `color=c=gray:s=${T}x${T}`, '-frames:v', '1', '-vf', labels ? draw(it.id) : 'null', '-q:v', '3', dst], fontsDir());
    });
    const ok = await ffmpegOk(ffmpeg, ['-v', 'error', '-y', '-framerate', '1', '-i', join(work, 't%03d.jpg'), '-vf', `tile=${cols}x${rows}:margin=4:padding=4:color=black`, '-frames:v', '1', '-q:v', '3', outPath], fontsDir());
    if (!ok || !existsSync(outPath)) fail('ทำภาพรวมไม่สำเร็จ — ตรวจ ffmpeg ด้วยการสั่ง "ตรวจเครื่อง tad-clip"');
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
  console.log(`หน้า ${page}/${pages} · ${slice.length} รายการ → ${outPath}`);
  if (!labels) console.log('(ffmpeg ตัวนี้เขียนเลขลงภาพไม่ได้ — อ่านตามลำดับซ้าย→ขวา บน→ล่าง ตามรายการข้างล่าง)');
  else console.log(`เลข id อยู่มุมซ้ายบนของแต่ละช่อง · ${cols} ช่องต่อแถว`);
  for (let r = 0; r < rows; r++) console.log(`  แถว ${r + 1}: ${slice.slice(r * cols, (r + 1) * cols).map(i => i.id).join(' ')}`);
  slice.forEach(i => console.log(`${i.id}  ${i.file}  (${i.kind}${i.dur ? ` ${i.dur}s` : ''}${i.w ? ` ${i.w}x${i.h}` : ''}${i.edited ? ' · แท็กแล้ว' : ''})`));
  if (page < pages) console.log(`ยังมีต่อ: --page ${page + 1}`);
}

// ---------- main ----------
const USAGE = 'ใช้: node library.mjs <index|sheet|tag|search|sounds|show|stats> [ตัวเลือก] — ดูรายการเต็มที่หัวไฟล์ engine/library.mjs';
const OPTIONS = {
  dir: { type: 'string' }, tags: { type: 'string' }, emotion: { type: 'string' }, beat: { type: 'string' }, risk: { type: 'string' },
  note: { type: 'string' }, favorite: { type: 'string' }, q: { type: 'string' }, kind: { type: 'string' }, limit: { type: 'string' },
  page: { type: 'string' }, out: { type: 'string' }, json: { type: 'boolean' }, force: { type: 'boolean' }, untagged: { type: 'boolean' },
};

async function main() {
  let parsed;
  try { parsed = parseArgs({ args: process.argv.slice(2), options: OPTIONS, allowPositionals: true }); }
  catch (e) { return fail(`${e.message}\n${USAGE}`); }
  const { values: v, positionals: pos } = parsed;
  const cmd = pos[0];
  if (!['index', 'sheet', 'tag', 'search', 'sounds', 'show', 'stats'].includes(cmd)) fail(USAGE);
  const dir = libDir(v.dir);
  if (cmd !== 'index' && !existsSync(catalogPath(dir))) fail(`ยังไม่มี catalog ของ ${dir} — รัน index ก่อน`);
  if (cmd === 'index') await cmdIndex(dir, v.force);
  else if (cmd === 'sheet') await cmdSheet(dir, v);
  else if (cmd === 'tag') cmdTag(dir, v, pos);
  else if (cmd === 'search') cmdFind(dir, v, VISUAL);
  else if (cmd === 'sounds') cmdFind(dir, v, AUDIO);
  else if (cmd === 'show') cmdShow(dir, pos);
  else cmdStats(dir);
}

main().catch(e => {
  if (e instanceof KitError) { console.error(`ผิดพลาด: ${e.message}`); process.exit(1); }
  throw e;
});
