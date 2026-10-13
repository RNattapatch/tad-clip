// engine/visual/thv.mjs — plan check · brand injection · Chrome lookup · real render against the fixture
// Run: node --test "tests/*.test.mjs"   (uses the config in TAD_CLIP_HOME for ffmpeg / Chrome / python paths)
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const THV = join(ROOT, 'engine', 'visual', 'thv.mjs');
const EXAMPLES = join(ROOT, 'engine', 'visual', 'examples');
const FIXTURE = join(ROOT, 'tests', 'fixtures', 'คลิป ทดสอบ.mp4');
const kit = await import('../engine/kit.mjs');
const thv = await import('../engine/visual/thv.mjs');

const AMBIENT = kit.home();
const ambientCfg = () => { const f = join(AMBIENT, 'config.json'); return existsSync(f) ? JSON.parse(readFileSync(f, 'utf8')) : {}; };
// a throwaway TAD_CLIP_HOME = the ambient config (tool paths) with some top-level keys replaced
const made = [];   // every temp dir this file creates is removed at the end
after(() => made.forEach(d => rmSync(d, { recursive: true, force: true })));
function homeWith(over) {
  const h = mkdtempSync(join(tmpdir(), 'tad clip home ทดสอบ-')); made.push(h);
  writeFileSync(join(h, 'config.json'), JSON.stringify({ ...ambientCfg(), ...over }));
  return h;
}
function withHome(over, fn) {
  const prev = process.env.TAD_CLIP_HOME; process.env.TAD_CLIP_HOME = homeWith(over);
  try { return fn(); } finally { process.env.TAD_CLIP_HOME = prev; }
}
const workDir = () => { const d = mkdtempSync(join(tmpdir(), 'tad clip งาน ทดสอบ-')); made.push(d); return d; };
const writePlan = (dir, plan, name = 'plan.json') => { const f = join(dir, name); writeFileSync(f, JSON.stringify(plan, null, 2)); return f; };
const node = (args, env = {}) => spawnSync(process.execPath, [THV, ...args], { encoding: 'utf8', env: { ...process.env, ...env } });

// ---------------------------------------------------------------- check
test('check rejects a missing proof_file (would render a black shot)', () => {
  const dir = workDir();
  const f = writePlan(dir, { title: 't', proof_exempt: 'test', shots: [
    { id: '01', t: [0, 2], layout: 'split', visual: { proof: 'ภาพหน้าร้าน', proof_file: 'ไม่มีไฟล์นี้' }, overlays: [{ type: 'hook-pill', text: 'x' }] }] });
  thv.load(f);
  const r = thv.check();
  assert.equal(r.ok, false);
  assert.ok(r.errs.some(e => /proof_file/.test(e) && /ไม่มีไฟล์นี้/.test(e)), r.errs.join('\n'));
  const cli = node(['check', f]);
  assert.equal(cli.status, 1);
  assert.match(cli.stdout, /proof_file/);
});

test('check finds proof_file by stem in assets/proof and accepts a relative path', () => {
  const dir = workDir(); mkdirSync(join(dir, 'assets', 'proof'), { recursive: true });
  writeFileSync(join(dir, 'assets', 'proof', 'หน้าร้าน 1.jpg'), 'x');
  const f = writePlan(dir, { title: 't', shots: [
    { id: '01', t: [0, 2], layout: 'split', visual: { proof: 'p', proof_file: 'หน้าร้าน 1' }, overlays: [{ type: 'hook-pill', text: 'x' }] },
    { id: '02', t: [2, 4], layout: 'proof', visual: { proof: 'p', proof_file: 'assets/proof/หน้าร้าน 1.jpg' } }] });
  thv.load(f);
  assert.deepEqual(thv.check().errs, []);
});

test('check accepts every examples/*.plan.json', () => {
  const files = readdirSync(EXAMPLES).filter(f => f.endsWith('.plan.json'));
  assert.ok(files.length >= 2, 'examples missing');
  for (const f of files) {
    const ctx = thv.load(join(EXAMPLES, f));
    const r = thv.check();
    // a machine without any cut-out engine (Windows without transparent-background) must refuse only the cutout shots
    const other = r.errs.filter(e => !/cutout/.test(e));
    assert.deepEqual(other, [], `${f}: ${r.errs.join(' | ')}`);
    if (ctx.cutoutEngine !== 'off') assert.equal(r.ok, true, `${f}: ${r.errs.join(' | ')}`);
  }
});

test('engine off → cutout shots are errors that suggest split', () => withHome({ cutout: { engine: 'off' } }, () => {
  const dir = workDir();
  const f = writePlan(dir, { title: 't', proof_exempt: 'x', shots: [
    { id: '01', t: [0, 2], layout: 'split', visual: { panel: 'primary' }, overlays: [{ type: 'hook-pill', text: 'x' }] },
    { id: '02', t: [2, 4], layout: 'cutout', visual: { ai: 'a' } }], prompts: { a: { scene: 'A desk.' } } });
  const ctx = thv.load(f);
  assert.equal(ctx.cutoutEngine, 'off');
  const r = thv.check();
  assert.equal(r.ok, false);
  assert.ok(r.errs.some(e => /shot 02/.test(e) && /split/.test(e)), r.errs.join('\n'));
}));

const LEGACY_LAYOUT = 'full-' + 'p' + 'un';   // the pre-kit name of full-speaker (built at runtime: no owner name in the kit)
test('legacy names still load: old full-speaker name, navy/coral/gold → primary/accent/accent2', () => {
  const dir = workDir();
  const f = writePlan(dir, { title: 't', proof_exempt: 'x', shots: [
    { id: '01', t: [0, 2], layout: LEGACY_LAYOUT, overlays: [{ type: 'hook-pill', text: 'x' }, { type: 'label', text: 'y', color: 'navy' }] },
    { id: '02', t: [2, 4], layout: 'full-card', visual: { card: { type: 'cta', big: ['a'], theme: 'coral' } } }] });
  const { plan } = thv.load(f);
  assert.equal(plan.shots[0].layout, 'full-speaker');
  assert.equal(plan.shots[0].overlays[1].color, 'primary');
  assert.equal(plan.shots[1].visual.card.theme, 'accent');
  assert.deepEqual(thv.check().errs, []);
});

// ---------------------------------------------------------------- presets
test('output preset: 1080p = source fps capped at 30 · 2k60 = 1440x2560 @ 60 with 1080 cards', () => {
  const dir = workDir();
  const src = relative(dir, FIXTURE);
  const base = { title: 't', proof_exempt: 'x', source_video: src, shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }] };
  let ctx = withHome({ output: { preset: '1080p' } }, () => thv.load(writePlan(dir, base)));
  assert.equal(ctx.preset, '1080p'); assert.equal(ctx.fps, 30); assert.equal(ctx.renderScale, 1);   // fixture is 60 fps → 30 for a phone post
  ctx = withHome({ output: { preset: '2k60' } }, () => thv.load(writePlan(dir, base)));
  assert.equal(ctx.preset, '2k60'); assert.equal(ctx.fps, 60); assert.ok(Math.abs(ctx.renderScale - 4 / 3) < 1e-9); assert.equal(ctx.cardScale, 1);
  ctx = thv.load(writePlan(dir, { ...base, preset: '1080p', fps: 60 }));
  assert.equal(ctx.fps, 60);                                                                            // plan wins
  ctx = thv.load(writePlan(dir, { ...base, source_video: undefined, preset: '1080p' }));
  assert.equal(ctx.fps, 30);                                                                            // nothing to follow yet
});

// ---------------------------------------------------------------- brand + chrome
const brandBlock = html => (html.match(/<style id="brand">([\s\S]*?)<\/style>/) || [])[1] || '';
test('brand CSS is injected after the template tokens (neutral preset)', () => withHome({ brand: { preset: 'neutral', font: 'Prompt', colors: {} } }, () => {
  const dir = workDir();
  thv.load(writePlan(dir, { title: 't', shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }] }));
  const html = thv.pageHtml({ kind: 'frame', shot: { id: '01', t: [0, 2], layout: 'full-speaker' } });
  assert.ok(html.includes('--accent: #f97316'), 'neutral accent missing');
  assert.match(brandBlock(html), /--accent: #f97316;/);
  assert.ok(html.indexOf('<style id="brand">') > html.indexOf(':root {'), 'brand tokens must come after the template :root');
  assert.ok(html.indexOf('<style id="brand">') < html.indexOf('</head>'));
  assert.match(brandBlock(html), /@font-face\{font-family:"Prompt"/);
  assert.doesNotMatch(html, new RegExp(['Sukhum' + 'vit', '--navy', '--coral', '--gold'].join('|')));   // no system font, no old token names
}));

test('another preset reaches the page too (paper)', () => withHome({ brand: { preset: 'paper', font: 'Prompt', colors: {} } }, () => {
  const dir = workDir();
  thv.load(writePlan(dir, { title: 't', shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }] }));
  assert.match(brandBlock(thv.pageHtml({ kind: 'frame', shot: { id: '01', t: [0, 2], layout: 'full-speaker' } })), /--accent: #e4572e;/);
}));

test('page data survives $ patterns and </script> in plan text', () => {
  const dir = workDir();
  thv.load(writePlan(dir, { title: 't', shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }] }));
  const html = thv.pageHtml({ kind: 'frame', shot: { id: '01', t: [0, 2], layout: 'full-speaker', overlays: [{ type: 'label', text: "ราคา $& $1 $' </script>" }] } });
  assert.ok(html.includes("ราคา $& $1 $'"));
  const tpl = readFileSync(join(ROOT, 'engine', 'visual', 'cards.html'), 'utf8');
  assert.equal(html.split('</script>').length, tpl.split('</script>').length + 1, 'plan text closed a script tag');
});

test('Chrome resolver returns a browser that exists on this machine', () => {
  const bin = thv.resolveChrome();
  assert.ok(existsSync(bin), bin);
});

test('Windows browser lookup: Edge first, then Chrome, then the kit chrome-headless-shell (mocked platform)', () => {
  const fakeHome = workDir();
  const shell = join(fakeHome, 'chrome', 'win64-140', 'chrome-headless-shell-win64', 'chrome-headless-shell.exe');
  mkdirSync(dirname(shell), { recursive: true }); writeFileSync(shell, '');
  const env = { 'ProgramFiles(x86)': 'C:\\Program Files (x86)', ProgramFiles: 'C:\\Program Files', LOCALAPPDATA: 'C:\\Users\\ผู้ใช้\\AppData\\Local' };
  const c = thv.chromeCandidates('win32', env, fakeHome);
  assert.match(c[0], /Program Files \(x86\).Microsoft.Edge.Application.msedge\.exe$/);
  assert.match(c[1], /Program Files.Microsoft.Edge.Application.msedge\.exe$/);
  assert.match(c[2], /Google.Chrome.Application.chrome\.exe$/);
  assert.equal(c.at(-1), shell);
  const mac = thv.chromeCandidates('darwin', {}, fakeHome);
  assert.equal(mac[0], null, 'no mac shell in this fake home');
  assert.ok(mac.includes('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'));
});

test('cut-out engine rule: auto = vision on mac · tb on windows only when installed · else off', () => {
  const auto = { cutout: { engine: 'auto' } };
  assert.equal(thv.machineEngine(auto, 'darwin', () => false), 'vision');
  assert.equal(thv.machineEngine(auto, 'win32', () => true), 'tb');
  assert.equal(thv.machineEngine(auto, 'win32', () => false), 'off');
  assert.equal(thv.machineEngine({ cutout: { engine: 'none' } }, 'darwin'), 'off');
  assert.equal(thv.machineEngine({ cutout: { engine: 'tb' } }, 'darwin'), 'tb');
});

test('retry predicate: hangs, closed pipes/targets and crashes retry · real errors do not', () => {
  for (const m of ['CDP Page.captureScreenshot ค้างเกิน 60 วิ — Chrome ค้าง', 'CDP pipe closed — Chrome/Edge ปิดตัวเอง (exit null)',
    'Inspected target navigated or closed', 'Target crashed', 'Session with given id not found.']) assert.ok(thv.isRetryable(new Error(m)), m);
  for (const m of ['ffmpeg exit 1', 'render หน้าว่าง (job frame shot 01)', 'ไม่เจอคลิป: /x.mp4']) assert.ok(!thv.isRetryable(new Error(m)), m);
});

test('a browser killed while a shot is loading is reopened and the shot is redone (↻), not a crash', () => {
  const dir = workDir();
  const f = writePlan(dir, { title: 't', proof_exempt: 'x', fps: 30, shots: [
    { id: '01', t: [0, 1], layout: 'full-card', visual: { card: { type: 'title', lines: ['ช็อตแรก'] } } },
    { id: '02', t: [1, 2], layout: 'full-card', visual: { card: { type: 'title', lines: ['ช็อตสอง'] } } }] });
  const r = node(['motion', f], { THV_TEST_KILL_BROWSER: '02' });   // test hook: SIGKILL the browser right after Page.navigate for shot 02
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /↻ shot 02/);
  assert.match(r.stdout, /✓ shots\/02-full-card\.mp4/);
  const v = ffprobeJson(join(dir, 'out', 'shots', '02-full-card.mp4')).streams[0];
  assert.equal(+v.nb_frames, 30);
});

// Node 22's built-in WebSocket refuses CDP messages over 4 MiB (permessage-deflate cap) → big PNG frames used to look like a
// "hung Chrome". The pipe transport has no cap: a full-frame noise photo (PNG ≫ 4 MB) must capture fine.
test('frames bigger than 4 MB capture (CDP over a pipe, not a WebSocket)', () => {
  const dir = workDir(); mkdirSync(join(dir, 'assets', 'proof'), { recursive: true });
  const noise = join(dir, 'assets', 'proof', 'noise.png');
  const g = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-y', '-f', 'lavfi', '-i', "color=s=1080x1920,geq=lum='random(1)*255':cb='random(2)*255':cr='random(3)*255'", '-frames:v', '1', noise]);
  assert.equal(g.status, 0, String(g.stderr));
  const f = writePlan(dir, { title: 't', proof_exempt: 'x', shots: [
    { id: '01', t: [0, 1], layout: 'proof', visual: { proof: 'p', proof_file: 'noise' }, overlays: [{ type: 'hook-pill', text: 'x' }] }] });
  const r = node(['cards', f]);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const png = join(dir, 'out', 'shots', '01-proof.png');
  assert.ok(statSync(png).size > 4.5 * 1024 * 1024, `frame only ${statSync(png).size} bytes — not a big-frame test`);
  rmSync(dir, { recursive: true, force: true });
});

test('Edge / full Chrome get --headless=new, chrome-headless-shell gets --headless', () => {
  assert.ok(thv.chromeArgs('C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', 'u').includes('--headless=new'));
  assert.ok(thv.chromeArgs('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', 'u').includes('--headless=new'));
  const shell = thv.chromeArgs('/x/chrome-headless-shell-mac-arm64/chrome-headless-shell', 'u');
  assert.ok(shell.includes('--headless') && !shell.includes('--headless=new'));
  assert.ok(shell.includes('--remote-debugging-pipe') && !shell.some(a => a.startsWith('--remote-debugging-port')));
});

// ---------------------------------------------------------------- dogfood round (D18 · D19 · D22)
test('check warns on overlay collisions: overlay vs overlay and overlay vs the subtitle band (warnings only)', () => {
  const dir = workDir();
  const f = writePlan(dir, { title: 't', proof_exempt: 'x', shots: [
    { id: '01', t: [0, 3], layout: 'split', visual: { panel: 'primary' }, overlays: [
      { type: 'chat', messages: [{ side: 'me', text: 'เรื่องนี้ผมรับไว้เองครับ' }, { side: 'me', text: 'ผมจะรีบส่งทีมไปตรวจให้' }, { side: 'them', text: 'โอเคครับ' }] },
      { type: 'label', text: 'โทรหาเขาก่อน 4 โมง', x: 50, y: 22 }] },
    { id: '02', t: [3, 6], layout: 'full-speaker', overlays: [{ type: 'label', text: 'ป้ายต่ำเกินไป', x: 50, y: 71 }] },
    { id: '03', t: [6, 9], layout: 'full-speaker', overlays: [{ type: 'label', text: 'ป้ายเหนือหัว', x: 50, y: 16 }, { type: 'source', text: 'ที่มาทดสอบ', y: 24 }] }] });
  thv.load(f);
  const r = thv.check();
  assert.equal(r.ok, true, r.errs.join('\n'));
  assert.ok(r.warns.some(w => /shot 01/.test(w) && /label/.test(w) && /chat/.test(w) && /ทับกัน/.test(w)), r.warns.join('\n'));
  assert.ok(r.warns.some(w => /shot 02/.test(w) && /แถบซับ/.test(w)), r.warns.join('\n'));
  assert.ok(!r.warns.some(w => /shot 03/.test(w) && /ทับ/.test(w)), 'false alarm on a clean shot: ' + r.warns.filter(w => /shot 03/.test(w)).join(' | '));
});

test('visual.footage.source is drawn as the same source chip the source overlay draws', async t => {
  const dir = workDir();
  mkdirSync(join(dir, 'assets', 'footage'), { recursive: true });
  const foot = join(dir, 'assets', 'footage', 'สต็อก มืด.mp4');
  const g = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x202020:s=1080x1920:r=30', '-t', '3', '-pix_fmt', 'yuv420p', foot]);
  assert.equal(g.status, 0, String(g.stderr));
  const plan = writePlan(dir, { title: 't', source_video: relative(dir, FIXTURE), fps: 30, shots: [
    { id: '01', t: [0, 2], layout: 'split', visual: { footage: { file: 'assets/footage/สต็อก มืด.mp4', start: 0, source: 'ภาพจำลอง · ทดสอบ' } } }] });
  for (const cmd of ['motion', 'compose']) { const r = node([cmd, plan]); assert.equal(r.status, 0, r.stdout + r.stderr); }
  const chip = rgbMean(join(dir, 'out', 'preview-composite.mp4'), 1, [70, 278, 40, 24]);   // overlay `source` default spot: x 64 px, y 14 %
  const beside = rgbMean(join(dir, 'out', 'preview-composite.mp4'), 1, [700, 278, 40, 24]);
  assert.ok(chip.every(c => c > 150), `no source chip drawn: ${chip.map(Math.round)}`);
  assert.ok(beside.every(c => c < 60), `footage not under the chip: ${beside.map(Math.round)}`);
});

test('storyboard shows the real speaker frame and the subtitle band (no grey placeholder) · says why a shot has no PNG', () => {
  const dir = workDir(); mkdirSync(join(dir, 'ซับ'));
  writeFileSync(join(dir, 'ซับ', 'คลิป.karaoke.ass'), [
    '[Script Info]', 'PlayResX: 720', 'PlayResY: 1280', '', '[V4+ Styles]',
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
    'Style: Default,Prompt,60,&H00507FFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,340,1', '',
    '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    'Dialogue: 0,0:00:00.10,0:00:02.90,Default,,0,0,0,,{\\k40}ซับ{\\k60}ทดสอบ{\\k80}ยาวยาว', ''].join('\n'));
  const shots = [{ id: '01', t: [0, 3], layout: 'full-speaker', say: 'พูด' }];
  const board = (name, extra) => {
    const f = writePlan(dir, { title: 't', proof_exempt: 'x', out: name, shots, ...extra }, name + '.json');
    const r = node(['all', f]); assert.equal(r.status, 0, r.stdout + r.stderr); return r;
  };
  const noSrc = board('a', {}), withSrc = board('b', { source_video: relative(dir, FIXTURE) }), withSubs = board('c', { source_video: relative(dir, FIXTURE), subs: 'ซับ/คลิป.karaoke.ass' });
  assert.match(withSrc.stdout, /ไม่มีกราฟิก/);   // full-speaker with nothing on it → no PNG, and the log says so
  const thumb = n => rgbMean(join(dir, n, 'storyboard.png'), 0, [60, 140, 230, 440]);
  const crop = (n, [x, y, w, h]) => spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-i', join(dir, n, 'storyboard.png'), '-vf', `crop=${w}:${h}:${x}:${y},format=gray`, '-f', 'rawvideo', '-'], { maxBuffer: 1 << 26 }).stdout;
  const diff = (a, b) => { let d = 0; for (let i = 0; i < a.length; i++) d += Math.abs(a[i] - b[i]); return d / a.length; };
  const ph = thumb('a');
  assert.ok(Math.abs(ph[0] - 201) < 12 && Math.abs(ph[2] - 184) < 12, `placeholder expected without source (${ph.map(Math.round)})`);
  assert.ok(diff(crop('a', [60, 140, 230, 440]), crop('b', [60, 140, 230, 440])) > 15, 'storyboard still shows the grey placeholder');
  const bd = diff(crop('b', [45, 440, 260, 60]), crop('c', [45, 440, 260, 60]));   // thumb box top ≈127 px + 73 % of 480
  assert.ok(bd > 2, `subtitle band not drawn on the storyboard (${bd.toFixed(2)})`);
});

test('retiming a shot re-grabs its storyboard stills (speaker + footage), keyed by timestamp', () => {
  const dir = workDir(); mkdirSync(join(dir, 'assets', 'footage'), { recursive: true });
  const foot = join(dir, 'assets', 'footage', 'นับเวลา.mp4');   // testsrc: every second looks different
  const g = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1080x1920:rate=30', '-t', '12', '-pix_fmt', 'yuv420p', foot]);
  assert.equal(g.status, 0, String(g.stderr));
  const shots = cut => [
    { id: '01', t: [0, cut], layout: 'full-speaker', overlays: [{ type: 'label', text: 'ป้าย', y: 20 }] },
    { id: '02', t: [cut, 12], layout: 'split', visual: { footage: { file: 'assets/footage/นับเวลา.mp4', start: 0, source: 'ภาพจำลอง · ทดสอบ' } } }];
  const md5 = f => createHash('md5').update(readFileSync(f)).digest('hex');
  const stills = () => readdirSync(join(dir, 'out', 'stills')).sort();
  const run = cut => { const r = node(['board', writePlan(dir, { title: 't', proof_exempt: 'x', source_video: relative(dir, FIXTURE), shots: shots(cut) })]); assert.equal(r.status, 0, r.stdout + r.stderr); return stills(); };
  const first = run(4), second = run(9);
  const pick = (list, re) => list.filter(n => re.test(n));
  const spk1 = pick(first, /^01-speaker/), spk2 = pick(second, /^01-speaker/), ft1 = pick(first, /^02-footage/), ft2 = pick(second, /^02-footage/);
  assert.equal(spk1.length, 1); assert.equal(spk2.length, 1, `old speaker still kept beside the new one: ${spk2}`);
  assert.notEqual(spk1[0], spk2[0], 'speaker still not re-keyed after retiming');
  assert.equal(ft2.length, 1, `old footage still kept: ${ft2}`);
  assert.notEqual(ft1[0], ft2[0], 'footage still not re-keyed after retiming');
  const again = run(9);
  assert.deepEqual(again, second, 'same timing must reuse the cached stills');
});

test('board removes a shot\'s old-style stills (<id>.jpg · <id>-speaker.jpg) when it writes the new-style ones', () => {
  const dir = workDir(), st = join(dir, 'out', 'stills'); mkdirSync(join(dir, 'assets', 'footage'), { recursive: true }); mkdirSync(st, { recursive: true });
  const g = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1080x1920:rate=30', '-t', '4', '-pix_fmt', 'yuv420p', join(dir, 'assets', 'footage', 'ภาพ.mp4')]);
  assert.equal(g.status, 0, String(g.stderr));
  const legacy = ['01.jpg', '01-speaker.jpg', '02.jpg', '02-speaker.jpg'], other = ['เก็บไว้.jpg', '010.jpg'];   // older kits: footage = <id>.jpg · speaker = <id>-speaker.jpg
  [...legacy, ...other].forEach(n => writeFileSync(join(st, n), 'old'));
  const r = node(['board', writePlan(dir, { title: 't', proof_exempt: 'x', source_video: relative(dir, FIXTURE), shots: [
    { id: '01', t: [0, 3], layout: 'full-speaker', overlays: [{ type: 'label', text: 'ป้าย', y: 20 }] },
    { id: '02', t: [3, 6], layout: 'split', visual: { footage: { file: 'assets/footage/ภาพ.mp4', start: 0, source: 'ภาพจำลอง · ทดสอบ' } } }] })]);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const now = readdirSync(st);
  for (const n of legacy) assert.ok(!now.includes(n), `old-style still left behind: ${n} (in ${now})`);
  for (const n of other) assert.ok(now.includes(n), `removed a file that is not this shot's old still: ${n}`);
  for (const re of [/^01-speaker-[0-9a-f]{6}-\d+\.jpg$/, /^02-speaker-[0-9a-f]{6}-\d+\.jpg$/, /^02-footage-[0-9a-f]{6}-\d+\.jpg$/])
    assert.ok(now.some(n => re.test(n)), `new-style still missing: ${re} (in ${now})`);
});

test('vox graphics_fps default gives even holds: 12 at 24/48/60 fps · 15 at 30 · 12.5 at 25/50 · explicit value wins', () => {
  const dir = workDir();
  const at = (fps, extra = {}) => thv.load(writePlan(dir, { title: 't', look: 'vox', fps, shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }], ...extra })).graphicsFps;
  assert.equal(at(60), 12); assert.equal(at(30), 15); assert.equal(at(24), 12); assert.equal(at(25), 12.5); assert.equal(at(50), 12.5); assert.equal(at(48), 12);
  for (const fps of [24, 25, 30, 48, 50, 60]) assert.equal(fps % at(fps), 0, `${fps} fps: uneven holds`);
  assert.equal(at(30, { graphics_fps: 12 }), 12);
  assert.equal(at(30, { graphics_fps: 0 }), 0);
  assert.equal(thv.load(writePlan(dir, { title: 't', fps: 30, shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }] })).graphicsFps, 0, 'non-vox stays smooth');
});

test('vox graphics clock holds every graphics frame for exactly fps/graphics_fps output frames (10 s, the clock motion uses)', () => {
  const summary = runs => { const c = {}; runs.forEach(n => { c[n] = (c[n] || 0) + 1; }); return JSON.stringify(c); };
  for (const [fps, step] of [[30, 15], [60, 12], [24, 12], [25, 12.5], [50, 12.5], [48, 12]]) {
    const runs = thv.holds(fps, step, 10), want = fps / step;
    assert.equal(runs.reduce((a, b) => a + b, 0), fps * 10, `${fps}/${step}: frame count`);
    assert.ok(runs.every(n => n === want), `${fps} fps / graphics ${step}: holds ${summary(runs)}, want all ${want}`);
  }
  // the defaults load() picks go through the same clock
  const dir = workDir();
  for (const fps of [24, 25, 30, 48, 50, 60]) {
    const step = thv.load(writePlan(dir, { title: 't', look: 'vox', fps, shots: [{ id: '01', t: [0, 2], layout: 'full-speaker' }] })).graphicsFps;
    const runs = thv.holds(fps, step, 10);
    assert.ok(runs.every(n => n === fps / step), `${fps} fps default (${step}): holds ${summary(runs)}`);
  }
  assert.deepEqual(thv.holds(30, 0, 1), Array(30).fill(1), 'graphics_fps 0 = every output frame is new');
});

// ---------------------------------------------------------------- the skill users run
test('SKILL.md: clean code fences · every bash block sources env.sh · no bare tools · no dev-only knobs', () => {
  const md = readFileSync(join(ROOT, 'skills', 'tad-clip-visual', 'SKILL.md'), 'utf8').split('\n');
  const HEAD = 'K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"';
  let open = null, blocks = 0;
  md.forEach((l, i) => {
    const t = l.trim();
    if (!t.startsWith('```')) { if (open?.bash) { open.lines.push(t); } return; }
    assert.ok(t === '```' || t === '```bash', `line ${i + 1}: fence with trailing text: ${t.slice(0, 40)}`);
    if (open) {
      if (open.bash) {
        blocks++;
        assert.equal(open.lines[0], HEAD, `bash block at line ${open.at}: first line must source env.sh`);
        for (const c of open.lines) assert.doesNotMatch(c, /^(node|ffmpeg|ffprobe|python3?|yt-dlp)\s/, `bash block at line ${open.at}: bare tool "${c.slice(0, 30)}"`);
      }
      open = null;
    } else open = { at: i + 1, bash: t === '```bash', lines: [] };
  });
  assert.equal(open, null, 'unclosed code fence');
  assert.ok(blocks >= 8, `only ${blocks} bash blocks`);
  const text = md.join('\n');
  assert.doesNotMatch(text, /TAD_CLIP_TB_PYTHON/, 'dev-only env var in user-facing text');
  assert.doesNotMatch(text, /ytsearch\d+:/, 'raw yt-dlp search — use python -m engine.fetch ytsearch');
  // long commands: every block that runs motion/compose/subs/all/animatic/loose tells Claude to background it
  md.forEach((l, i) => { if (/thv\.mjs (motion|compose|subs|all|animatic|loose)\b|-m engine\.cutout/.test(l) && !/check/.test(l)) {
    const near = md.slice(Math.max(0, i - 8), i + 3).join('\n');
    assert.match(near, /run_in_background/, `line ${i + 1}: long command without run_in_background guidance`); } });
  // block comments agree with the top rule: background OR a 600000 ms timeout
  md.filter(l => /^\s*#.*run_in_background/.test(l)).forEach(l => assert.match(l, /600000/, `block comment lacks the timeout alternative: ${l.trim().slice(0, 60)}`));
  // several long thv commands in one block are chained with && so a failed step stops the rest
  let blk = null;
  md.forEach(l => { const t = l.trim();
    if (t === '```bash') blk = []; else if (t === '```') { if (blk) { const cmds = blk.filter(x => /thv\.mjs (motion|compose|subs)\b/.test(x));
      cmds.slice(1).forEach(x => assert.match(x, /^&&/, `not chained with &&: ${x.slice(0, 60)}`)); } blk = null; } else if (blk) blk.push(t); });
  // a runnable long thv command written in prose escapes the && rule above (two separate background calls can overlap) → bash block only
  let inFence = false;
  md.forEach((l, i) => { if (l.trim().startsWith('```')) { inFence = !inFence; return; }
    if (!inFence) assert.doesNotMatch(l, /"\$NODE" engine\/visual\/thv\.mjs (motion|compose|subs|all|animatic|loose)\b/, `line ${i + 1}: long thv command in prose — put it in a bash block chained with &&`); });
  // the vox graphics_fps default depends on the output fps (thv load(): 15 at 30 · 12 at 24/48/60 · 12.5 at 25/50)
  const gfps = md.find(l => /^\|[^|]*`graphics_fps`/.test(l)) || '';
  assert.match(gfps, /15[^|]*30/, `graphics_fps row must give 15 at 30 fps: ${gfps.slice(0, 120)}`);
  assert.match(gfps, /12\.5[^|]*25\/50/, `graphics_fps row must give 12.5 at 25/50 fps: ${gfps.slice(0, 120)}`);
  assert.match(gfps, /12[^|.]*24\/48\/60/, `graphics_fps row must give 12 at 24/48/60 fps: ${gfps.slice(0, 120)}`);
  assert.match(text, /กึ่งกลาง/, 'highlight/annotate x,y = box centre must be stated');
  assert.match(text, /สต็อก[^\n]*ภาพจำลอง|ภาพจำลอง[^\n]*สต็อก/, 'stock footage standing in for the event gets a ภาพจำลอง chip');
  assert.match(text, /compose[^\n]*ทั้งคลิป/, 'compose renders the whole clip — time expectations');
  for (const field of ['`source`', '`highlight`', '`treat`', '`keyword`']) assert.ok(text.includes(field), `${field} documented in the skill itself`);
});

// ---------------------------------------------------------------- real render against the fixture
const ffprobeJson = f => JSON.parse(spawnSync(kit.tool('ffprobe'), ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', f], { encoding: 'utf8' }).stdout);
function yavg(file, at, crop) {
  const vf = `${crop ? `crop=${crop},` : ''}signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG:file=-`;
  const r = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-ss', String(at), '-i', file, '-frames:v', '1', '-vf', vf, '-f', 'null', '-'], { encoding: 'utf8' });
  const m = r.stdout.match(/YAVG=([\d.]+)/); assert.ok(m, r.stderr); return +m[1];
}
const PILL_WHITE = [520, 1132, 40, 12];   // inside the white hook-pill of shot 02 (y 62%), top padding above the text
function rgbMean(file, at, [x, y, w, h], pre = '') {   // mean R,G,B of a patch, decoded with the file's own range/matrix
  const r = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', ...(at ? ['-ss', String(at)] : []), '-i', file, '-frames:v', '1', '-vf', `${pre}crop=${w}:${h}:${x}:${y},format=rgb24`, '-f', 'rawvideo', '-'], { maxBuffer: 1 << 26 });
  const m = [0, 0, 0]; for (let i = 0; i < r.stdout.length; i++) m[i % 3] += r.stdout[i];
  assert.ok(r.stdout.length, String(r.stderr)); return m.map(v => v / (r.stdout.length / 3));
}
function grayBand(file, at) {   // raw luma of the subtitle band (y 1240–1460 of 1920)
  const r = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-ss', String(at), '-i', file, '-frames:v', '1', '-vf', 'crop=1080:220:0:1240', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], { maxBuffer: 1 << 26 });
  return r.stdout;
}

test('compose removes its temp folder when ffmpeg fails mid-way (Windows keeps whatever is left in %TEMP%)', () => {
  const dir = workDir(), tmp = workDir();
  mkdirSync(join(dir, 'assets', 'footage'), { recursive: true });
  writeFileSync(join(dir, 'assets', 'footage', 'พัง.mp4'), 'not a video');   // passes check (file exists), breaks ffmpeg
  const plan = writePlan(dir, { title: 't', source_video: relative(dir, FIXTURE), proof_exempt: 'test', shots: [
    { id: '01', t: [0, 1], layout: 'full-speaker', say: 'ก่อน' },
    { id: '02', t: [1, 2], layout: 'proof', say: 'พัง', visual: { proof: 'x', footage: { file: 'assets/footage/พัง.mp4', start: 0 } } }] });
  const r = node(['compose', plan], { TMPDIR: tmp, TMP: tmp, TEMP: tmp });
  assert.notEqual(r.status, 0, 'compose should fail on a broken footage file');
  assert.deepEqual(readdirSync(tmp).filter(n => n.startsWith('thv-compose-')), [], 'temp folder left behind');
  assert.ok(!existsSync(join(dir, 'out', 'preview-composite.mp4')));
});

test('2-shot plan (full-card + overlay) renders onto the fixture: 1080x1920, no black shots, subs burn', async t => {
  const dir = workDir();
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const subsDir = join(dir, 'ซับ คลิป'); mkdirSync(subsDir);
  writeFileSync(join(subsDir, 'คลิป ทดสอบ.karaoke.ass'), [
    '[Script Info]', 'ScriptType: v4.00+', 'PlayResX: 720', 'PlayResY: 1280', 'WrapStyle: 2', 'ScaledBorderAndShadow: yes', '',
    '[V4+ Styles]',
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
    'Style: Default,Prompt,60,&H00507FFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,340,1', '',
    '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    'Dialogue: 0,0:00:00.20,0:00:03.80,Default,,0,0,0,,{\\k40}ทดสอบ{\\k60}ซับ{\\k80}ภาษาไทย', ''].join('\n'));
  const plan = writePlan(dir, {
    title: 'ทดสอบ 2 ช็อต', source_video: relative(dir, FIXTURE), subs: 'ซับ คลิป/ไม่มีไฟล์นี้.ass', proof_exempt: 'test',
    shots: [
      { id: '01', t: [0, 2], layout: 'full-card', say: 'การ์ด', visual: { card: { type: 'title', lines: ['ทดสอบ', 'การ์ด*เต็มจอ*'], accent: 1 } } },
      { id: '02', t: [2, 4], layout: 'full-speaker', say: 'ป้าย', overlays: [{ type: 'hook-pill', text: 'ผู้พูด + *ป้าย*', y: 62 }] }] });

  const all = node(['all', plan]);
  assert.equal(all.status, 0, all.stdout + all.stderr);
  for (const f of ['storyboard.png', 'edit-sheet.md', 'prompts.md', 'shots/01-full-card.png', 'shots/02-full-speaker.png']) assert.ok(existsSync(join(dir, 'out', f)), f);

  const motion = node(['motion', plan]);
  assert.equal(motion.status, 0, motion.stdout + motion.stderr);
  const compose = node(['compose', plan]);
  assert.equal(compose.status, 0, compose.stdout + compose.stderr);
  const out = join(dir, 'out', 'preview-composite.mp4');
  const info = ffprobeJson(out); const v = info.streams.find(s => s.codec_type === 'video');
  assert.equal(v.width, 1080); assert.equal(v.height, 1920); assert.equal(v.pix_fmt, 'yuv420p');
  assert.equal(v.color_range, 'tv', 'limited range (the fixture is full range)'); assert.equal(v.color_space, 'bt709');
  assert.ok(info.streams.some(s => s.codec_type === 'audio'), 'audio kept');
  assert.ok(Math.abs(+info.format.duration - 4) < 0.15, info.format.duration);
  for (const at of [1, 3]) assert.ok(yavg(out, at) > 16, `black frame at ${at}s`);

  // colour: the fixture is FULL range — the speaker must keep its colours and the graphics must stay pure (no grey shift)
  const spk = [100, 300, 40, 40], pill = PILL_WHITE;
  const src = rgbMean(FIXTURE, 3, spk, 'scale=1080:1920,'), got = rgbMean(out, 3, spk);
  assert.ok(src.every((c, i) => Math.abs(c - got[i]) < 6), `speaker colour shifted: source ${src.map(Math.round)} → composite ${got.map(Math.round)}`);
  const card = rgbMean(join(dir, 'out', 'shots', '02-full-speaker.png'), 0, pill);
  assert.ok(card.every(c => c > 250), `pill patch is not white on the card PNG (${card.map(Math.round)}) — test geometry`);
  const pillOut = rgbMean(out, 3, pill);
  assert.ok(pillOut.every(c => c > 245), `white graphic turned grey: ${pillOut.map(Math.round)}`);

  // --ass beats plan.subs and a relative --ass is relative to the plan folder (env.sh has cd'd into the kit)
  const subs = node(['subs', plan, '--ass', 'ซับ คลิป/คลิป ทดสอบ.karaoke.ass']);
  assert.equal(subs.status, 0, subs.stdout + subs.stderr);
  const burned = join(dir, 'out', 'preview-composite-subs.mp4');
  assert.ok(existsSync(burned) && existsSync(join(dir, 'out', 'preview-composite-720p.mp4')));
  const bv = ffprobeJson(burned).streams.find(s => s.codec_type === 'video');
  assert.equal(bv.pix_fmt, 'yuv420p'); assert.equal(bv.color_range, 'tv');
  const a = grayBand(out, 3), b = grayBand(burned, 3);
  let diff = 0; for (let i = 0; i < a.length; i++) diff += Math.abs(a[i] - b[i]);
  assert.ok(diff / a.length > 2, `subtitle band unchanged (${(diff / a.length).toFixed(2)})`);
});

// ---------------------------------------------------------------- modes: cutaway (default) · stage
const STAGE_DEMO = join(EXAMPLES, 'stage-demo');
// a stage work folder: copies of the example graphics under plain + Thai names
function stageDir() {
  const dir = workDir(); mkdirSync(join(dir, 'ภาพ ฉาก'), { recursive: true });
  for (const [from, to] of [['bg/office.jpg', 'ภาพ ฉาก/ออฟฟิศ.jpg'], ['bg/floor.png', 'ภาพ ฉาก/พื้น.png'], ['cards/chat.jpg', 'ภาพ ฉาก/แชต.jpg'],
    ['cards/calendar.jpg', 'ภาพ ฉาก/ปฏิทิน.jpg'], ['pose/figure-point.png', 'ภาพ ฉาก/ท่า ชี้.png']]) writeFileSync(join(dir, to), readFileSync(join(STAGE_DEMO, from)));
  return dir;
}
const stagePlan = (over = {}, shots) => ({ title: 'stage', mode: 'stage', fps: 30, stage_floor: 'ภาพ ฉาก/พื้น.png', proof_exempt: 'test', ...over, shots: shots || [
  { id: '01', t: [0, 1.5], layout: 'cutout', say: 'a', visual: { image: 'ภาพ ฉาก/ออฟฟิศ.jpg', pose: { file: 'ภาพ ฉาก/ท่า ชี้.png', h: 1250, y: 640 } }, overlays: [{ type: 'hook-pill', text: 'x', y: 30 }] },
  { id: '02', t: [1.5, 3.5], layout: 'full-card', say: 'b', visual: { card: { type: 'grid', theme: 'dark', images: ['ภาพ ฉาก/แชต.jpg', 'ภาพ ฉาก/ปฏิทิน.jpg'], at: [0.8], badge: { big: 'ทั้งวัน', at: 0.3 } } } },
  { id: '03', t: [3.5, 4.5], layout: 'full-speaker', say: 'c', whip: false }] });

test('mode: no value = cutaway (old plans) · unknown mode is an error · overlay-on-cut still loads', () => {
  for (const f of ['lead-leak.plan.json', 'vox-star-sales-leaves.plan.json']) {
    const ctx = thv.load(join(EXAMPLES, f));
    assert.equal(ctx.mode, 'cutaway', f);
    assert.notEqual(ctx.plan.look, 'stage', f);
    assert.ok(ctx.plan.shots.every(s => s.layout !== 'cutout' || s.sub_y == null), `${f}: cutaway must not move subtitles`);
  }
  thv.load(join(ROOT, 'tests', 'smoke', 'plan.json'));
  assert.deepEqual(thv.check().errs, [], 'smoke plan');
  const dir = workDir();
  thv.load(writePlan(dir, { title: 't', mode: 'theatre', proof_exempt: 'x', shots: [{ id: '01', t: [0, 2], layout: 'split', visual: { panel: 'primary' } }] }));
  assert.ok(thv.check().errs.some(e => /mode "theatre"/.test(e)));
  const oc = thv.load(writePlan(dir, { title: 't', mode: 'overlay-on-cut', shots: [{ id: '01', t: [0, 20], layout: 'full-speaker' }] }, 'oc.json'));
  assert.equal(oc.mode, 'overlay-on-cut');
  assert.deepEqual(thv.check().errs, []);
});

test('stage: look, cut-out scale and subtitle height defaults · explicit values win', () => {
  const dir = stageDir();
  const ctx = thv.load(writePlan(dir, stagePlan({ look: 'vox' }, [
    { id: '01', t: [0, 1.5], layout: 'cutout', visual: { image: 'ภาพ ฉาก/ออฟฟิศ.jpg' } },
    { id: '02', t: [1.5, 3], layout: 'cutout', sub_y: 60, visual: { image: 'ภาพ ฉาก/ออฟฟิศ.jpg' } },
    { id: '03', t: [3, 4], layout: 'full-speaker' }])));
  assert.equal(ctx.plan.look, 'stage');
  assert.equal(ctx.plan.cutout.scale, thv.STAGE.cutout_scale);
  assert.deepEqual(ctx.plan.shots.map(s => s.sub_y), [44, 60, undefined]);
  assert.ok(thv.check().warns.some(w => /look "vox"/.test(w)), 'a look the stage overrides is reported');
  const own = thv.load(writePlan(dir, stagePlan({ cutout: { scale: 0.6 }, stage_sub_y: 40 }, [{ id: '01', t: [0, 1.5], layout: 'cutout', visual: { image: 'ภาพ ฉาก/ออฟฟิศ.jpg' } }]), 'own.json'));
  assert.equal(own.plan.cutout.scale, 0.6);
  assert.equal(own.plan.shots[0].sub_y, 40);
  const legacy = thv.load(writePlan(dir, { ...stagePlan(), mode: undefined, look: 'stage' }, 'look.json'));
  assert.equal(legacy.mode, 'stage', 'look "stage" without mode = stage');
});

test('stage plan passes check · missing image / floor / pose files are errors · pose only on cutout', () => {
  const dir = stageDir();
  thv.load(writePlan(dir, stagePlan()));
  const ok = thv.check();
  assert.deepEqual(ok.errs, [], ok.errs.join('\n'));
  const bad = stagePlan({ stage_floor: 'ภาพ ฉาก/ไม่มีพื้น.png' });
  bad.shots[0].visual = { image: 'ไม่มีฉาก.jpg', pose: { file: 'ไม่มีท่า.png' } };
  bad.shots[2].visual = { pose: { file: 'ภาพ ฉาก/ท่า ชี้.png' } };
  thv.load(writePlan(dir, bad, 'bad.json'));
  const errs = thv.check().errs.join('\n');
  for (const want of [/stage_floor ไม่เจอไฟล์/, /visual\.image ไม่เจอไฟล์/, /visual\.pose\.file ไม่เจอ/, /shot 03: visual\.pose ใช้กับ layout cutout เท่านั้น/]) assert.match(errs, want);
  const cli = node(['check', join(dir, 'bad.json')]);
  assert.equal(cli.status, 1);
});

test('a pose shot needs no cut-out engine (Windows without transparent-background)', () => withHome({ cutout: { engine: 'off' } }, () => {
  const dir = stageDir();
  thv.load(writePlan(dir, stagePlan()));
  assert.deepEqual(thv.check().errs, []);
  const live = stagePlan(); delete live.shots[0].visual.pose;
  thv.load(writePlan(dir, live, 'live.json'));
  assert.ok(thv.check().errs.some(e => /shot 01: layout cutout/.test(e)), 'a live cut-out still needs an engine');
}));

test('a pose needs a backdrop file: none = error · an AI image not made yet = warning', () => {
  const dir = stageDir(), pose = { file: 'ภาพ ฉาก/ท่า ชี้.png', h: 1250, y: 640 };
  thv.load(writePlan(dir, stagePlan({ prompts: { scene: { prompt: 'office, photo', style: 'photo' } } }, [
    { id: '01', t: [0, 1.5], layout: 'cutout', visual: { ai: 'scene', pose } },
    { id: '02', t: [1.5, 3], layout: 'cutout', visual: { card: { type: 'grid', big: '3', source: 'x' }, pose } }])));
  const r = thv.check();
  assert.ok(r.warns.some(w => /shot 01: visual\.pose ต้องมีภาพฉากหลัง/.test(w)), r.warns.join('\n'));
  assert.ok(r.errs.some(e => /shot 02: visual\.pose ต้องมีภาพฉากหลัง/.test(e)), r.errs.join('\n'));
  assert.ok(!r.errs.some(e => /shot 01/.test(e)), 'an AI image can still be made after check');
});

test('grid card: every variant checks · bad fields are errors · a number without source warns', () => {
  const dir = stageDir();
  const grid = (id, t, card) => ({ id, t, layout: 'full-card', visual: { card: { type: 'grid', ...card } } });
  thv.load(writePlan(dir, stagePlan({}, [
    grid('01', [0, 1.5], { theme: 'light', images: ['ภาพ ฉาก/แชต.jpg'], badge: { big: 'ทั้งวัน', small: 'ตอบแชต' }, source: 'ภาพจำลอง · x' }),
    grid('02', [1.5, 3.5], { theme: 'dark', images: ['ภาพ ฉาก/แชต.jpg', 'ภาพ ฉาก/ปฏิทิน.jpg'], at: [0.8] }),
    grid('03', [3.5, 5], { theme: 'dark', big: '3 เท่า', caption: 'เวลาคุยลูกค้าใหม่', source: 'บันทึกทีม 2026' }),
    grid('04', [5, 6.5], { images: ['ภาพ ฉาก/ปฏิทิน.jpg'], lines: ['ทีมเสียเวลา', 'ไปกับอะไร?'], accent: 1 })])));
  const r = thv.check();
  assert.deepEqual(r.errs, [], r.errs.join('\n'));
  thv.load(writePlan(dir, stagePlan({}, [
    grid('01', [0, 1.5], {}),
    grid('02', [1.5, 3], { theme: 'blue', images: ['ภาพ ฉาก/ไม่มี.jpg', 'ภาพ ฉาก/แชต.jpg'], at: [0.5, 1] }),
    grid('03', [3, 4.5], { big: '7 หลัก', badge: { small: 'x' } })]), 'bad.json'));
  const b = thv.check(), errs = b.errs.join('\n');
  for (const want of [/shot 01: grid ต้องมี images/, /shot 02: grid theme "blue"/, /shot 02: grid image ไม่เจอไฟล์/, /shot 02: grid at\[\] ต้องมี 1 ค่า/, /shot 03: grid badge ต้องมี big/]) assert.match(errs, want);
  assert.ok(b.warns.some(w => /shot 03: grid มีตัวเลข \(big\) แต่ไม่มี source/.test(w)));
});

test('stage pacing: opening hold >2 s · average >2.5 s per picture (carousel photos count) · no floor', () => {
  const dir = stageDir();
  const cut = (id, t) => ({ id, t, layout: 'cutout', visual: { image: 'ภาพ ฉาก/ออฟฟิศ.jpg' } });
  thv.load(writePlan(dir, stagePlan({ stage_floor: undefined }, [cut('01', [0, 2.6]), cut('02', [2.6, 5.4]), cut('03', [5.4, 8.4])])));
  const w = thv.check().warns.join('\n');
  assert.match(w, /shot 01: ช่วงเปิด 12 วิแรก ภาพค้าง 2\.6 วิ \(เกิน 2 วิ\)/);
  assert.match(w, /mode stage: เฉลี่ย 2\.80 วิต่อภาพ/);
  assert.match(w, /ไม่มี plan\.stage_floor/);
  // the same 8.4 s with a 3-photo carousel = 5 pictures → 1.68 s each, no pacing warning
  const shots = [cut('01', [0, 1.8]), { id: '02', t: [1.8, 6.6], layout: 'full-card', visual: { card: { type: 'grid', images: ['ภาพ ฉาก/แชต.jpg', 'ภาพ ฉาก/ปฏิทิน.jpg', 'ภาพ ฉาก/แชต.jpg'], at: [1.6, 3.2] } } }, cut('03', [6.6, 8.4])];
  assert.equal(thv.stageStates(shots), 5);
  thv.load(writePlan(dir, stagePlan({}, shots), 'fast.json'));
  assert.ok(!thv.check().warns.some(x => /เฉลี่ย/.test(x)));
  // cutaway keeps its own 3 s opening rule
  thv.load(writePlan(dir, { title: 't', proof_exempt: 'x', shots: [{ id: '01', t: [0, 2.6], layout: 'split', visual: { image: 'ภาพ ฉาก/ออฟฟิศ.jpg' } }] }, 'cutaway.json'));
  assert.ok(!thv.check().warns.some(x => /ภาพค้าง/.test(x)));
});

test('whip filtergraph: gblur on every cut except whip:false · sigma scales with the output · none without plan.whip', () => {
  const shots = [{ t: [0, 1.5] }, { t: [1.5, 3] }, { t: [3, 4], whip: false }, { t: [4, 5] }];
  const f = thv.whipFilter(shots, { dur: 0.12, sigma: 60 });
  assert.equal(f, "gblur=sigma=60:sigmaV=0.01:steps=2:enable='between(t,1.440,1.560)+between(t,3.940,4.060)'");
  assert.match(thv.whipFilter(shots, {}, 4 / 3), /^gblur=sigma=80:/, 'defaults + 2K scale');
  assert.equal(thv.whipFilter(shots, undefined), null);
  assert.equal(thv.whipFilter([{ t: [0, 1] }, { t: [1, 2], whip: false }], {}), null);
  const dir = workDir();
  thv.load(writePlan(dir, { title: 't', proof_exempt: 'x', whip: { dur: 0 }, shots: [{ id: '01', t: [0, 2], layout: 'split', visual: { panel: 'primary' } }] }));
  assert.ok(thv.check().errs.some(e => /plan\.whip/.test(e)));
  const r = spawnSync(kit.tool('ffmpeg'), ['-hide_banner', '-filters'], { encoding: 'utf8' });
  assert.match(r.stdout, / gblur /, 'this ffmpeg has gblur');
});

test('stage renders: storyboard + grid/pose PNGs, then compose on the fixture with pose, carousel and whip', async t => {
  const dir = stageDir();
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const plan = writePlan(dir, stagePlan({ source_video: relative(dir, FIXTURE) }));
  const all = node(['all', plan]);
  assert.equal(all.status, 0, all.stdout + all.stderr);
  for (const f of ['storyboard.png', 'shots/01-cutout.png', 'shots/02-full-card.png']) assert.ok(existsSync(join(dir, 'out', f)), f);
  // pose PNG: the silhouette stands at x 50 % on the stage, the floor plate covers the bottom of the backdrop
  const pose = rgbMean(join(dir, 'out', 'shots', '01-cutout.png'), 0, [520, 1000, 40, 40]);
  assert.ok(pose.every(c => c < 90), `pose silhouette not drawn: ${pose.map(Math.round)}`);
  // carousel: after the slide (t 0.8 + 0.22 s) the second photo sits where the first was
  const card = join(dir, 'out', 'shots', '02-full-card.png');
  assert.ok(rgbMean(card, 0, [400, 300, 60, 60]).some(c => c > 150), 'grid photo missing');

  const motion = node(['motion', plan]);
  assert.equal(motion.status, 0, motion.stdout + motion.stderr);
  const compose = node(['compose', plan]);
  assert.equal(compose.status, 0, compose.stdout + compose.stderr);
  assert.match(compose.stdout, /รูปนิ่ง 1 ช็อต/);
  assert.match(compose.stdout, /whip 1 รอยต่อ/);
  const out = join(dir, 'out', 'preview-composite.mp4');
  const info = ffprobeJson(out), v = info.streams.find(s => s.codec_type === 'video');
  assert.equal(v.width, 1080); assert.equal(v.height, 1920); assert.equal(v.color_range, 'tv');
  assert.ok(Math.abs(+info.format.duration - 4.5) < 0.15, info.format.duration);
  for (const at of [0.7, 2.5, 4]) assert.ok(yavg(out, at) > 16, `black frame at ${at}s`);
  assert.ok(rgbMean(out, 0.7, [520, 1000, 40, 40]).every(c => c < 100), 'pose shot shows the still, not the live speaker');
  // whip: the frame on the cut (1.5 s) is blurred sideways → much less horizontal detail than the same card a moment later
  const detail = at => { const r = spawnSync(kit.tool('ffmpeg'), ['-v', 'error', '-ss', String(at), '-i', out, '-frames:v', '1', '-vf', 'crop=1080:700:0:200,format=gray,convolution=0 0 0 -1 0 1 0 0 0:0 0 0 -1 0 1 0 0 0:0 0 0 -1 0 1 0 0 0:0 0 0 -1 0 1 0 0 0,signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG:file=-', '-f', 'null', '-'], { encoding: 'utf8' });
    const m = r.stdout.match(/YAVG=([\d.]+)/); assert.ok(m, r.stderr); return +m[1]; };
  const onCut = detail(1.52), after = detail(2.2);
  assert.ok(onCut < after * 0.6, `no whip blur on the cut: edge energy ${onCut.toFixed(2)} vs ${after.toFixed(2)}`);
});
