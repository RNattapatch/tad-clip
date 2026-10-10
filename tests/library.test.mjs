import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LIB = join(ROOT, 'engine', 'library.mjs');

// Resolve ffmpeg/ffprobe under the caller's TAD_CLIP_HOME (dev machine / CI); each test then gets its own home.
const kit = await import('../engine/kit.mjs');
const FFMPEG = kit.tool('ffmpeg');
const FFPROBE = kit.tool('ffprobe');

const tmp = () => mkdtempSync(join(tmpdir(), 'tad clip คลัง-'));

function mkHome(extra = {}) {
  const home = tmp();
  writeFileSync(join(home, 'config.json'), JSON.stringify({ tools: { ffmpeg: FFMPEG, ffprobe: FFPROBE }, ...extra }));
  return home;
}

function run(args, home, { fail = false } = {}) {
  const r = spawnSync(process.execPath, [LIB, ...args], { env: { ...process.env, TAD_CLIP_HOME: home }, encoding: 'utf8' });
  if (fail) return r;
  assert.equal(r.status, 0, `exit ${r.status}\n${r.stderr}`);
  return r.stdout;
}
const runJson = (args, home) => JSON.parse(run([...args, '--json'], home));

function ff(...args) { execFileSync(FFMPEG, ['-v', 'error', '-y', ...args]); }
const png = (path, color = 'red', size = '64x48') => { mkdirSync(dirname(path), { recursive: true }); ff('-f', 'lavfi', '-i', `color=c=${color}:s=${size}`, '-frames:v', '1', path); };
const wav = (path, secs = 0.5) => { mkdirSync(dirname(path), { recursive: true }); ff('-f', 'lavfi', '-i', `sine=frequency=440:duration=${secs}`, '-ac', '1', path); };

// 2 images + 1 wav (+ things that must be ignored)
function makeLib() {
  const dir = join(tmp(), 'คลัง มีม ทดสอบ');
  png(join(dir, 'ตกใจ น้องแมว.png'), 'red', '64x48');
  png(join(dir, 'sub folder', 'ฮา มาก.jpg'), 'blue', '80x60');
  wav(join(dir, 'sfx', 'boom เสียง.wav'));
  writeFileSync(join(dir, 'readme.txt'), 'x');
  png(join(dir, '.hidden.png'));
  writeFileSync(join(dir, '._AppleDouble.png'), 'x');
  return dir;
}
const catalog = dir => readFileSync(join(dir, '.tad-clip', 'catalog.jsonl'), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l));
const byFile = rows => Object.fromEntries(rows.map(r => [r.file, r]));

test('index: ids, kinds, sizes — Thai names and spaces, hidden/other files ignored', () => {
  const home = mkHome(), dir = makeLib();
  const out = run(['index', '--dir', dir], home);
  const rows = catalog(dir);
  assert.equal(rows.length, 3);
  const f = byFile(rows);
  const cat = f['ตกใจ น้องแมว.png'], jpg = f['sub folder/ฮา มาก.jpg'], snd = f['sfx/boom เสียง.wav'];
  assert.equal(cat.kind, 'image'); assert.equal(jpg.kind, 'image'); assert.equal(snd.kind, 'sfx');
  assert.match(cat.id, /^m\d{3}$/); assert.match(jpg.id, /^m\d{3}$/); assert.equal(snd.id, 's001');
  assert.notEqual(cat.id, jpg.id);
  assert.deepEqual([cat.w, cat.h, cat.dur], [64, 48, 0]);
  assert.deepEqual([jpg.w, jpg.h], [80, 60]);
  assert.ok(Math.abs(snd.dur - 0.5) < 0.05, `dur ${snd.dur}`);
  assert.deepEqual(Object.keys(cat), ['id', 'file', 'kind', 'dur', 'w', 'h', 'tags', 'emotion', 'beat', 'risk']);
  assert.deepEqual([cat.tags, cat.emotion, cat.beat, cat.risk], [[], '', '', 'safe']);
  assert.match(out, /3 ไฟล์/);
  assert.ok(!existsSync(join(dir, '.tad-clip', 'notes.json')), 'index must not create or touch notes.json');
});

test('index: uses config.library_dir when --dir is absent, Thai error when neither is set', () => {
  const dir = makeLib();
  run(['index'], mkHome({ library_dir: dir }));
  assert.equal(catalog(dir).length, 3);
  const r = run(['index'], mkHome(), { fail: true });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /ยังไม่ได้ตั้งโฟลเดอร์คลัง/);
  assert.doesNotMatch(r.stderr, /at .*library\.mjs/); // no stack trace
  const r2 = run(['index', '--dir', join(dir, 'ไม่มีอยู่')], mkHome(), { fail: true });
  assert.equal(r2.status, 1);
  assert.match(r2.stderr, /ไม่พบโฟลเดอร์/);
});

test('index twice: same catalog bytes, ids untouched, no temp files left; --force re-probes without changing ids', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  const first = readFileSync(join(dir, '.tad-clip', 'catalog.jsonl'), 'utf8');
  const out = run(['index', '--dir', dir], home);
  assert.equal(readFileSync(join(dir, '.tad-clip', 'catalog.jsonl'), 'utf8'), first);
  assert.match(out, /ใหม่ 0 · หายไป 0 · ย้าย 0/);
  run(['index', '--dir', dir, '--force'], home);
  assert.equal(readFileSync(join(dir, '.tad-clip', 'catalog.jsonl'), 'utf8'), first);
  assert.deepEqual(readdirSync(join(dir, '.tad-clip')).sort(), ['catalog.jsonl']);
});

test('index: long audio is music, short is sfx; animated webp is a gif', () => {
  const home = mkHome(), dir = tmp();
  wav(join(dir, 'short.wav'), 1);
  wav(join(dir, 'long.wav'), 25);
  const riff = flags => { const b = Buffer.alloc(30); b.write('RIFF', 0); b.writeUInt32LE(22, 4); b.write('WEBP', 8); b.write('VP8X', 12); b.writeUInt32LE(10, 16); b[20] = flags; return b; };
  writeFileSync(join(dir, 'anim.webp'), riff(0x02));
  writeFileSync(join(dir, 'still.webp'), riff(0x00));
  const out = run(['index', '--dir', dir], home); // ffprobe cannot read the fake webp: still indexed, with a warning
  const f = byFile(catalog(dir));
  assert.equal(f['short.wav'].kind, 'sfx');
  assert.equal(f['long.wav'].kind, 'music');
  assert.equal(f['anim.webp'].kind, 'gif');
  assert.equal(f['still.webp'].kind, 'image');
  assert.match(f['long.wav'].id, /^s/);
  assert.match(f['anim.webp'].id, /^m/);
  assert.match(out, /อ่านข้อมูลไม่ได้/);
});

test('index: ids stay stable on re-index (new file sorts first, file renamed, file removed)', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  const before = byFile(catalog(dir));
  run(['tag', 'm002', '--tags', 'x', '--dir', dir], home);
  const notesBefore = readFileSync(join(dir, '.tad-clip', 'notes.json'), 'utf8');

  png(join(dir, 'a-first.png'), 'green');                                   // sorts before everything
  mkdirSync(join(dir, 'moved'), { recursive: true });
  renameSync(join(dir, 'sub folder', 'ฮา มาก.jpg'), join(dir, 'moved', 'ฮา มาก.jpg'));
  const out = run(['index', '--dir', dir], home);
  const after = byFile(catalog(dir));

  assert.equal(after['ตกใจ น้องแมว.png'].id, before['ตกใจ น้องแมว.png'].id);
  assert.equal(after['sfx/boom เสียง.wav'].id, 's001');
  assert.equal(after['moved/ฮา มาก.jpg']?.id, before['sub folder/ฮา มาก.jpg'].id, 'a moved file keeps its id (same name, size, length)');
  assert.ok(after['a-first.png'].id > 'm002' && !Object.values(before).some(r => r.id === after['a-first.png'].id), 'new file gets a fresh id');
  assert.equal(readFileSync(join(dir, '.tad-clip', 'notes.json'), 'utf8'), notesBefore, 'notes.json untouched');
  assert.match(out, /ใหม่ 1/);
  assert.match(out, /ย้าย 1/);
});

test('index: a removed file drops its row; a tagged one retires its id for good', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  const gone = byFile(catalog(dir))['ตกใจ น้องแมว.png'];
  run(['tag', gone.id, '--note', 'จะหาย', '--dir', dir], home);
  unlinkSync(join(dir, 'ตกใจ น้องแมว.png'));
  png(join(dir, 'brand new.png'), 'yellow', '10x10');
  const out = run(['index', '--dir', dir], home);
  const rows = catalog(dir);
  assert.ok(!rows.some(r => r.file === 'ตกใจ น้องแมว.png'));
  assert.notEqual(byFile(rows)['brand new.png'].id, gone.id, 'a retired id with notes must not be reused');
  assert.match(out, /หายไป 1/);
});

test('tag then search: the tagged item comes first and tag data is merged', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  const rows = byFile(catalog(dir));
  const cat = rows['ตกใจ น้องแมว.png'], jpg = rows['sub folder/ฮา มาก.jpg'];
  // untagged: id order, nothing is "edited"
  const plain = runJson(['search', '--kind', 'image', '--dir', dir], home);
  assert.equal(plain.length, 2);
  assert.ok(plain.every(r => r.edited === false));
  // tag the one that would sort second
  const second = plain[1];
  run(['tag', second.id, '--tags', 'ช็อก,ตกใจ', '--emotion', 'ช็อก', '--beat', 'reaction,punchline', '--risk', 'caution', '--note', 'ตัวเลขช็อก ใช้ตอนเปิดยอด', '--dir', dir], home);
  const notes = JSON.parse(readFileSync(join(dir, '.tad-clip', 'notes.json'), 'utf8'));
  assert.deepEqual(notes[second.id], { tags: ['ช็อก', 'ตกใจ'], emotion: 'ช็อก', beat: 'reaction,punchline', risk: 'caution', note: 'ตัวเลขช็อก ใช้ตอนเปิดยอด' });

  const after = runJson(['search', '--kind', 'image', '--dir', dir], home);
  assert.equal(after[0].id, second.id, 'edited item first even without a query');
  assert.equal(after[0].edited, true);
  assert.deepEqual(after[0].tags, ['ช็อก', 'ตกใจ']);
  assert.equal(after[0].risk, 'caution');
  assert.equal(after[0].abs, join(dir, ...second.file.split('/')));

  const q = runJson(['search', '--q', 'ช็อก', '--dir', dir], home);
  assert.deepEqual(q.map(r => r.id), [second.id]);                       // only the tagged one matches
  // both files match by name ("ตกใจ" is in cat's file name): the tagged one still wins
  const both = runJson(['search', '--q', 'ตกใจ', '--dir', dir], home);
  assert.equal(both[0].id, second.id);
  assert.ok([cat.id, jpg.id].includes(second.id));
});

test('tag: favourite bonus, merge semantics, validation', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  const [a, b] = runJson(['search', '--kind', 'image', '--dir', dir], home);
  run(['tag', a.id, '--tags', 'x', '--note', 'n1', '--dir', dir], home);
  run(['tag', a.id, '--emotion', 'ฮา', '--dir', dir], home);                    // second call keeps earlier fields
  const n = JSON.parse(readFileSync(join(dir, '.tad-clip', 'notes.json'), 'utf8'))[a.id];
  assert.deepEqual([n.tags, n.note, n.emotion], [['x'], 'n1', 'ฮา']);
  run(['tag', b.id, '--favorite', 'yes', '--dir', dir], home);
  const order = runJson(['search', '--kind', 'image', '--dir', dir], home).map(r => r.id);
  assert.equal(order[0], b.id, 'favourite beats merely-edited');
  run(['tag', b.id, '--favorite', 'no', '--dir', dir], home);

  for (const bad of [['tag', 'm999', '--tags', 'x'], ['tag', a.id, '--risk', 'maybe'], ['tag', a.id], ['tag']]) {
    const r = run([...bad, '--dir', dir], home, { fail: true });
    assert.equal(r.status, 1, bad.join(' '));
    assert.match(r.stderr, /ผิดพลาด/);
  }
});

test('search: filters, risk default hides avoid, multi-word ranking, limit', () => {
  const home = mkHome(), dir = tmp();
  for (const n of ['a', 'b', 'c', 'd']) png(join(dir, `${n}.png`));
  run(['index', '--dir', dir], home);
  run(['tag', 'm001', '--tags', 'ช็อก,ตกใจ', '--emotion', 'ช็อก', '--beat', 'reaction', '--dir', dir], home);
  run(['tag', 'm002', '--tags', 'ตกใจ', '--beat', 'punchline', '--dir', dir], home);
  run(['tag', 'm003', '--tags', 'ช็อก,ตกใจ', '--risk', 'avoid', '--dir', dir], home);
  const ids = (...a) => runJson(['search', ...a, '--dir', dir], home).map(r => r.id);

  assert.deepEqual(ids('--q', 'ช็อก ตกใจ'), ['m001', 'm002'], 'm003 is avoid → hidden; two-word match beats one-word match');
  assert.deepEqual(ids('--q', 'ช็อก ตกใจ', '--risk', 'safe,caution,avoid'), ['m001', 'm003', 'm002'], 'avoid still ranks lower than an equal safe match');
  assert.deepEqual(ids('--q', 'ช็อก', '--risk', 'avoid'), ['m003']);
  assert.deepEqual(ids('--beat', 'reaction'), ['m001']);
  assert.deepEqual(ids('--beat', 'punchline'), ['m002']);
  assert.deepEqual(ids('--emotion', 'ช็อก'), ['m001']);
  assert.deepEqual(ids('--q', 'ไม่มีคำนี้เลย'), []);
  assert.equal(ids('--limit', '1').length, 1);
  assert.match(run(['search', '--q', 'ไม่มีคำนี้เลย', '--dir', dir], home), /ไม่เจอ/);
  // human-readable output carries the real path
  assert.match(run(['search', '--q', 'ช็อก', '--dir', dir], home), new RegExp('ไฟล์: ' + dir.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
});

test('sounds: only audio, ranked by notes then name', () => {
  const home = mkHome(), dir = makeLib();
  wav(join(dir, 'sfx', 'vine boom.wav'));
  run(['index', '--dir', dir], home);
  const all = runJson(['sounds', '--dir', dir], home);
  assert.equal(all.length, 2);
  assert.ok(all.every(s => ['sfx', 'music'].includes(s.kind)));
  const boom = all.find(s => s.file.includes('vine'));
  run(['tag', boom.id, '--tags', 'บูม,ช็อก', '--beat', 'reaction', '--note', 'ตัวเลขช็อก', '--dir', dir], home);
  assert.equal(runJson(['sounds', '--q', 'ช็อก', '--dir', dir], home)[0].id, boom.id);
  assert.deepEqual(runJson(['sounds', '--q', 'boom', '--dir', dir], home).map(s => s.id).sort(), all.map(s => s.id).sort());
  assert.ok(runJson(['search', '--dir', dir], home).every(m => !['sfx', 'music'].includes(m.kind)), 'search never returns sounds');
});

test('show and stats', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  run(['tag', 'm001', '--favorite', 'yes', '--note', 'โปรด', '--dir', dir], home);
  const shown = JSON.parse(run(['show', 'm001', '--dir', dir], home));
  assert.equal(shown.id, 'm001'); assert.equal(shown.favorite, true); assert.equal(shown.note, 'โปรด');
  assert.ok(existsSync(shown.abs));
  const bad = run(['show', 'm404', '--dir', dir], home, { fail: true });
  assert.equal(bad.status, 1);
  const st = run(['stats', '--dir', dir], home);
  assert.match(st, /มีม 2/); assert.match(st, /เสียง 1/); assert.match(st, /แท็กแล้ว 1/); assert.match(st, /ตัวโปรด 1/);
});

test('sheet: numbered contact sheet JPG, order printed, --untagged skips tagged items', () => {
  const home = mkHome(), dir = makeLib();
  run(['index', '--dir', dir], home);
  const out = run(['sheet', '--dir', dir], home);
  const sheet = join(dir, '.tad-clip', 'sheets', 'sheet-1.jpg');
  assert.ok(existsSync(sheet) && statSync(sheet).size > 1000, out);
  assert.match(out, /หน้า 1\/1/);
  assert.match(out, /m001/); assert.match(out, /m002/);
  assert.doesNotMatch(out, /s001/, 'sounds never go on a visual sheet');
  const info = JSON.parse(execFileSync(FFPROBE, ['-v', 'error', '-print_format', 'json', '-show_entries', 'stream=width,height', sheet], { encoding: 'utf8' })).streams[0];
  assert.ok(info.width >= 240 && info.height >= 240);

  run(['tag', 'm001', '--tags', 'x', '--dir', dir], home);
  const only = run(['sheet', '--untagged', '--out', join(dir, 'ที่ ทดสอบ', 'ภาพรวม.jpg'), '--dir', dir], home);
  assert.doesNotMatch(only, /m001/);
  assert.match(only, /m002/);
  assert.ok(existsSync(join(dir, 'ที่ ทดสอบ', 'ภาพรวม.jpg')));
  const none = run(['sheet', '--untagged', '--kind', 'gif', '--dir', dir], home);
  assert.match(none, /ไม่มีรายการ/);
});

test('unknown command prints usage and exits 1', () => {
  const r = run(['nope'], mkHome(), { fail: true });
  assert.equal(r.status, 1);
  assert.match(r.stderr + r.stdout, /index/);
});

// ---- the skill that drives this CLI ----
const readSkill = () => readFileSync(join(ROOT, 'skills', 'tad-clip-library', 'SKILL.md'), 'utf8');

test('SKILL.md: frontmatter, triggers, engine paths and fallback line', () => {
  const skill = readSkill();
  const m = skill.match(/^---\n([\s\S]*?)\n---\n/);
  assert.ok(m, 'frontmatter');
  assert.match(m[1], /^name: tad-clip-library$/m);
  for (const trig of ['คลังมีม', 'หามีมใส่คลิป', 'หาภาพประกอบ', 'หาเสียงเอฟเฟกต์', 'meme library']) assert.ok(m[1].includes(trig), trig);
  assert.match(skill, /\$\{CLAUDE_PLUGIN_ROOT\}\/engine\/library\.mjs/);
  assert.match(skill, /\$\{CLAUDE_PLUGIN_ROOT\}\/engine\/fetch\.py|-m engine\.fetch/);
  assert.match(skill, /sfx_mix/);
  assert.ok(skill.includes('ถ้า `${CLAUDE_PLUGIN_ROOT}` ไม่ถูกแทนค่า ให้ใช้ `~/.tad-clip/kit`'));
});

test('SKILL.md: the sourcing rules are all there and no owner-specific strings leak', () => {
  const skill = readSkill();
  for (const must of ['หาให้เลยไม่ต้องถาม', 'CREDITS.md', 'ใช้ยิงแอดได้', 'ภาพจำลอง', 'Wikimedia Commons', 'Pexels', 'yt-dlp', 'ลายน้ำ', 'ตัวเลข']) assert.ok(skill.includes(must), must);
  assert.ok(!skill.includes('คอร์ส'), 'use คลาส');
  for (const bad of ['ปัน', 'Pun', 'agentmacmini', '100.64.172.7', '~/Documents/content', 'Telegram', 'Codex', 'kie', '.hermes']) {
    assert.ok(!skill.includes(bad), `owner-specific string "${bad}" must not appear`);
  }
});
