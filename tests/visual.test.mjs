// engine/visual/thv.mjs — plan check · brand injection · Chrome lookup · real render against the fixture
// Run: node --test "tests/*.test.mjs"   (uses the config in TAD_CLIP_HOME for ffmpeg / Chrome / python paths)
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

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
  md.forEach((l, i) => { if (/thv\.mjs (motion|compose|subs|all|animatic|loose)\b/.test(l) && !/check/.test(l)) {
    const near = md.slice(Math.max(0, i - 8), i + 3).join('\n');
    assert.match(near, /run_in_background/, `line ${i + 1}: long command without run_in_background guidance`); } });
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
