#!/usr/bin/env node
// tad-clip visual engine — plan.json → rule check · storyboard · card PNGs · motion MP4 · compose on the real clip ·
// burn karaoke subs · prompt blocks (optional, any free image tool) · CapCut edit sheet.
// Zero npm deps: drives a local Chrome / Edge headless over CDP (--remote-debugging-pipe) and pipes frames to ffmpeg.
// Usage: node thv.mjs <check|all|board|cards|motion|loose|animatic|prompts|compose|subs|sheet> <plan.json>
//        [--shot ID] [--matte green|alpha] [--ass file.karaoke.ass] [--motion (with all)] [--force (compose: redo cached cut-outs)]
// Every path inside plan.json (source_video · subs · footage · overlay images · proof files) is relative to the plan's folder.

import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, readdirSync, existsSync, statSync, copyFileSync, realpathSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { join, dirname, resolve, basename, win32 } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { homedir, tmpdir } from 'node:os';
import { KIT_ROOT, IS_MAC, home, loadConfig, tool, brandCss, fontsDir } from '../kit.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const W = 1080, H = 1920;
export const LAYOUTS = ['split', 'cutout', 'full-card', 'full-speaker', 'proof'];
const LAYOUT_ALIAS = { ['full-' + 'p' + 'un']: 'full-speaker' };   // plans written before the kit used the author's name for this layout
const TONE_ALIAS = { navy: 'primary', coral: 'accent', gold: 'accent2' };
export const FULL_CARDS = ['title', 'cta', 'stat', 'flow', 'compare', 'doc', 'quote', 'chapter', 'chart', 'timeline', 'grid'];
// plan.mode: cutaway (default · the speaker is the anchor, cut away to real material and back) · stage (the speaker cut out on one
// shared stage floor, photo backdrops change every 1–2 s · references/stage-style.md) · overlay-on-cut (text layer on a finished edit)
export const MODES = ['cutaway', 'stage', 'overlay-on-cut'];
export const STAGE = { hold: 2, avg: 2.5, cutout_scale: 0.72, sub_y: 44, whip: { dur: 0.12, sigma: 60 } };   // stage defaults + pacing targets
export const OVERLAYS = ['image', 'hook-pill', 'label', 'counter', 'chat', 'annotate', 'highlight', 'source', 'keyword', 'name', 'docchip'];
export const BEATS = ['cold-open', 'question', 'context', 'mechanism', 'turn', 'proof', 'so-what'];   // look vox · references/vox-style.md §1
// output presets (config.output.preset · plan.preset): every number in a plan stays in 1080×1920 design space
export const PRESETS = {
  '1080p': { render_scale: 1, max_fps: 30 },                 // 1080×1920 at the source fps capped at 30 (60 fps doubles render time + file size for no gain on a phone)
  '2k60': { fps: 60, render_scale: 4 / 3, card_scale: 1 },   // 1440×2560 @ 60 · full cards drawn at 1080 + lanczos (vox grain hangs Chrome at 2K)
};
export const COMMANDS = ['check', 'all', 'board', 'cards', 'motion', 'loose', 'animatic', 'prompts', 'compose', 'subs', 'sheet'];

// ---------------------------------------------------------------- plan context (module state, set by load())
let cfg, plan, PLAN_PATH, PLAN_DIR, OUT, FPS, K, CARD_SCALE, STEP, PRESET, SPEAKER, ENGINE, ARGS = [];
const flag = (name, dflt) => { const i = ARGS.indexOf('--' + name); return i >= 0 ? ARGS[i + 1] : dflt; };
const dur = s => +(s.t[1] - s.t[0]).toFixed(2);
// vox graphics clock: the time (ms) the graphics show at output frame i, floored to the graphics_fps grid (hand-made stutter)
// + 1e-6: float division lands just under an integer on some frames (30 fps / 15: 1.9999…) and floor would drop a graphics
// frame → 1-2-3 holds instead of 2-2-2
export const graphicsMs = (i, fps = FPS, step = STEP) => { const ms = (i / fps) * 1000; return step ? Math.floor(ms / (1000 / step) + 1e-6) * (1000 / step) : ms; };
// output frames each graphics frame is held for, over `sec` seconds (even holds = every run is fps / step)
export function holds(fps, step, sec = 10) {
  const runs = []; let last;
  for (let i = 0; i < Math.round(fps * sec); i++) { const ms = graphicsMs(i, fps, step); if (ms === last) runs[runs.length - 1]++; else { runs.push(1); last = ms; } }
  return runs;
}
const frames = s => Math.max(1, Math.round(s.t[1] * FPS) - Math.round(s.t[0] * FPS));   // absolute boundaries → no drift across shots
const FFMPEG = () => tool('ffmpeg');
// every encode ends limited-range BT.709 and says so: cards (RGB PNG), the speaker's clip (iPhone = often full range) and
// cut-outs then blend in one YUV space, and concat never mixes ranges between shots
const TO709 = 'scale=out_color_matrix=bt709:out_range=tv';
const TAG709 = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv'];

// path from plan.json → absolute (relative to the plan folder). Old plans appended " (comment …)" to source_video.
export function planPath(p) {
  if (!p) return null;
  const a = resolve(PLAN_DIR, p);
  if (existsSync(a)) return a;
  const b = resolve(PLAN_DIR, String(p).replace(/\s+\([^()]*\)\s*$/, ''));
  return existsSync(b) ? b : a;
}
// assets/<sub>/<id>.(png|jpg|jpeg|webp) by stem, or a path relative to the plan
const findAsset = (sub, id) => {
  if (!id) return null;
  const direct = resolve(PLAN_DIR, id);
  if (/\.(png|jpe?g|webp)$/i.test(id) && existsSync(direct)) return direct;
  const d = join(PLAN_DIR, 'assets', sub); if (!existsSync(d)) return null;
  const f = readdirSync(d).find(n => n.replace(/\.(png|jpe?g|webp)$/i, '') === id); return f ? join(d, f) : null;
};

const probeCache = new Map();
function probe(file) {   // first video stream (ffprobe json) · null when unreadable
  if (!file || !existsSync(file)) return null;
  if (!probeCache.has(file)) {
    let st = null;
    try {
      const r = spawnSync(tool('ffprobe'), ['-v', 'error', '-select_streams', 'v:0', '-show_streams', '-of', 'json', file], { encoding: 'utf8' });
      st = (JSON.parse(r.stdout || '{}').streams || [])[0] || null;
    } catch { st = null; }
    probeCache.set(file, st);
  }
  return probeCache.get(file);
}
const fps0 = st => { for (const k of ['avg_frame_rate', 'r_frame_rate']) { const [a, b] = String(st?.[k] || '0/0').split('/').map(Number); if (a > 0 && b > 0) return a / b; } return null; };

let zscaleOk;
// iPhone HDR (HLG / PQ) → tone-map to SDR so compose always ends in 8-bit SDR yuv420p
function sdr(file) {
  const st = probe(file), trc = st?.color_transfer;
  if (trc !== 'smpte2084' && trc !== 'arib-std-b67') return '';
  zscaleOk ??= / zscale /.test(spawnSync(FFMPEG(), ['-hide_banner', '-filters'], { encoding: 'utf8' }).stdout || '');
  if (!zscaleOk) { console.log(`⚠️  ${basename(file)} เป็น HDR แต่ ffmpeg นี้ไม่มี zscale — สีอาจซีด (ติดตั้ง ffmpeg ตามขั้นตอน setup)`); return ''; }
  return `zscale=tin=${trc}:pin=${st.color_primaries || 'bt2020'}:min=${st.color_space || 'bt2020nc'}:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,`;
}

// ---------------------------------------------------------------- cut-out engine (config.cutout.engine: auto | vision | tb | off)
// TAD_CLIP_TB_PYTHON (dev/testing only, not user-facing): run tb with another venv's python that already has transparent-background
function tbPython() { if (process.env.TAD_CLIP_TB_PYTHON) return process.env.TAD_CLIP_TB_PYTHON; try { return tool('python'); } catch { return null; } }
function tbInstalled() {
  const py = tbPython(); if (!py) return false;
  const r = spawnSync(py, ['-c', "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('transparent_background') else 1)"], { timeout: 60000 });
  return r.status === 0;
}
// same rule as engine/cutout resolve_engine(): auto = vision on macOS, tb elsewhere when installed, else off
export function machineEngine(c = cfg || loadConfig(), plat = process.platform, hasTb = tbInstalled) {
  let e = String(c?.cutout?.engine || 'auto').toLowerCase();
  if (e === 'none') e = 'off';
  if (e !== 'auto') return e;
  return plat === 'darwin' ? 'vision' : hasTb() ? 'tb' : 'off';
}
// per shot: shot.cutout_engine › plan.cutout.engine › machine · plan-level "none" = keep the old picture-in-picture box (no cut-out)
const shotEngine = s => { const e = s.cutout_engine || (plan.cutout || {}).engine; return !e || e === 'auto' ? ENGINE : String(e).toLowerCase(); };

// ---------------------------------------------------------------- load
export function load(planFile, args = []) {
  ARGS = args; cfg = loadConfig(); probeCache.clear();
  PLAN_PATH = resolve(planFile); PLAN_DIR = dirname(PLAN_PATH);
  plan = JSON.parse(readFileSync(PLAN_PATH, 'utf8'));
  // mode: no value = cutaway (every plan written before modes existed) · stage owns its look
  if (plan.look === 'stage' && !plan.mode) plan.mode = 'stage';
  plan.mode ??= 'cutaway';
  if (plan.mode === 'stage') {
    plan._look_was = plan.look && plan.look !== 'stage' ? plan.look : undefined;
    plan.look = 'stage';
    plan.cutout = { scale: STAGE.cutout_scale, ...(plan.cutout || {}) };
    if (plan.whip === undefined) plan.whip = { ...STAGE.whip };   // the blur across cuts is part of the style · "whip": false = hard cuts
    // the speaker is scaled down and stands at the bottom → the usual subtitle band lands on the face: lift it above the head
    (plan.shots || []).forEach(s => { if (s.layout === 'cutout' && s.sub_y == null) s.sub_y = plan.stage_sub_y ?? STAGE.sub_y; });
  }
  const tone = v => TONE_ALIAS[v] || v;
  (plan.shots || []).forEach(s => {   // legacy names → kit names (same slots)
    if (LAYOUT_ALIAS[s.layout]) s.layout = LAYOUT_ALIAS[s.layout];
    const v = s.visual || {};
    if (v.panel) v.panel = tone(v.panel);
    if (v.card && v.card.theme) v.card.theme = tone(v.card.theme);
    (s.overlays || []).forEach(o => { if (o.color) o.color = tone(o.color); });
    // visual.footage.source → the same on-screen chip an overlay `source` draws (same default spot, y 14 %)
    if (v.footage && v.footage.source && !(s.overlays || []).some(o => o.type === 'source'))
      s.overlays = [...(s.overlays || []), { type: 'source', text: v.footage.source, ...(v.footage.source_y != null ? { y: v.footage.source_y } : {}), _from: 'footage.source' }];
  });
  OUT = resolve(PLAN_DIR, plan.out || 'out');
  PRESET = plan.preset || cfg.output?.preset || '1080p';
  const P = PRESETS[PRESET] || PRESETS['1080p'];
  K = plan.render_scale ?? P.render_scale ?? 1;
  CARD_SCALE = plan.card_scale ?? P.card_scale;
  const srcFps = fps0(probe(planPath(plan.source_video)));
  FPS = plan.fps ?? P.fps ?? (srcFps ? Math.min(P.max_fps ?? 60, Math.round(srcFps)) : 30);
  // vox graphics clock: ~12 fps, picked so every graphics frame is held the same number of output frames
  // (12 at 30 fps would hold 3,2,3,2… frames — visible judder) → 12 at 24/48/60 · 15 at 30 · 12.5 at 25/50
  STEP = plan.look === 'vox' ? (plan.graphics_fps ?? (FPS % 12 === 0 ? 12 : FPS / Math.max(1, Math.round(FPS / 12.5)))) : 0;
  SPEAKER = (cfg.speaker && cfg.speaker.name) || 'ผู้พูด';
  ENGINE = machineEngine(cfg);
  return { plan, mode: plan.mode, planDir: PLAN_DIR, out: OUT, preset: PRESET, fps: FPS, graphicsFps: STEP, renderScale: K, cardScale: CARD_SCALE, speaker: SPEAKER, cutoutEngine: ENGINE };
}

// ---------------------------------------------------------------- subtitles: where the band sits in each shot
// same placement rules as `subs`: shot.sub_y › split → plan.split_sub_y (59 %) › the .ass Default style (MarginV / PlayResY)
const assTime = t => { const [h, m, x] = t.split(':'); return +h * 3600 + +m * 60 + +x; };
function readAss() {
  const f = planPath(plan.subs); if (!f || !existsSync(f)) return null;
  const lines = readFileSync(f, 'utf8').replace(/^﻿/, '').split(/\r?\n/);
  const playY = +((lines.find(l => l.startsWith('PlayResY:')) || 'PlayResY: 1280').split(':')[1]);
  const st = (lines.find(l => l.startsWith('Style: Default,')) || '').split(',');
  const dialogues = lines.filter(l => l.startsWith('Dialogue:')).map(l => { const g = l.split(','); return { a: assTime(g[1]), b: assTime(g[2]), text: g.slice(9).join(',').replace(/\{[^}]*\}/g, '').trim() }; });
  return { playY, fs: +st[2] || 60, mv: +st[st.length - 2] || 340, dialogues };
}
// → { top, base, size, text } in 1080×1920 px, or null (full-card: cards keep clear of the band themselves · no speech in the shot)
function subBand(s, ass) {
  if (s.layout === 'full-card') return null;
  const playY = ass ? ass.playY : 1280, fs = ass ? ass.fs : (cfg.subtitle?.size ?? 60), mv = ass ? ass.mv : (cfg.subtitle?.margin_v ?? 340);
  const lines = ass ? ass.dialogues.filter(d => (d.a + d.b) / 2 >= s.t[0] && (d.a + d.b) / 2 < s.t[1]) : null;
  if (ass && !lines.length) return null;
  const pct = s.sub_y ?? (s.layout === 'split' ? (plan.split_sub_y ?? 59) : (1 - mv / playY) * 100);
  const base = pct / 100 * H, size = fs / playY * H, mid = (s.t[0] + s.t[1]) / 2;
  const pick = lines && (lines.find(d => d.a <= mid && d.b > mid) || lines[0]);
  return { top: base - size * 1.3, base, size, text: pick ? pick.text : '' };
}
// rough on-screen box of an overlay (px, 1080×1920) from its type, position and text length — for collision warnings only
const chars = t => String(t || '').replace(/\*/g, '').replace(/[ัิ-ฺ็-๎]/g, '').length;
const kwSize = ls => { const w = Math.max(...ls.map(chars)); return (w <= 5 ? 190 : w <= 8 ? 150 : w <= 11 ? 120 : w <= 15 ? 96 : 78) * .82; };
export function overlayBox(o) {
  const X = v => v / 100 * W, Y = v => v / 100 * H;
  if (o.type === 'hook-pill') { const w = Math.min(960, chars(o.text) * 33 + 88), cy = Y(o.y ?? 50); return [540 - w / 2, cy - 64, 540 + w / 2, cy + 64]; }
  if (o.type === 'label') { const w = chars(o.text) * 30 + 68, cx = X(o.x ?? 50), cy = Y(o.y ?? 30); return [cx - w / 2, cy - 48, cx + w / 2, cy + 48]; }
  if (o.type === 'chat') { const hs = (o.messages || []).map(m => Math.max(1, Math.ceil(chars(m.text) / 24)) * 78 + 44);
    const hh = hs.reduce((a, b) => a + b, 0) + 26 * Math.max(0, hs.length - 1), top = o.y != null ? Y(o.y) : Y(25) - hh / 2; return [60, top, 1020, top + hh]; }
  if (o.type === 'keyword') { const ls = o.lines || [], sz = Math.min(o.size || 999, kwSize(ls.length ? ls : [''])), hh = ls.length * (sz * 1.12 + 24), w = Math.max(...ls.map(chars), 1) * sz * .6 + 60, cy = Y(o.y ?? 56);
    return [540 - w / 2, cy - hh / 2, 540 + w / 2, cy + hh / 2]; }
  if (o.type === 'name') { const top = Y(o.y ?? 52); return [230, top, 850, top + 160]; }
  if (o.type === 'docchip') { const cy = Y(o.y ?? 56); return [90, cy - 75, 990, cy + 75]; }
  if (o.type === 'source') { const top = Y(o.y ?? 14); return [64, top, 64 + (chars(o.text) + 7) * 17 + 36, top + 55]; }
  if (o.type === 'counter') return [810, 300, 1020, 384];
  return null;   // image · highlight · annotate sit on purpose over pictures and text
}
const overlapY = (a, b) => Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
const overlapX = (a, b) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));

// stage: visual states = shots, plus one per extra photo of a grid carousel (each slide is a new picture)
export const stageStates = shots => shots.reduce((n, s) => n + 1 + Math.max(0, ((((s.visual || {}).card || {}).images || []).length) - 1), 0);
// whip: a horizontal blur `dur` s long centred on every cut (shot.whip: false = hard cut into that shot) → ffmpeg -vf, or null
// gblur ships in every ffmpeg build (Mac Homebrew · Windows gyan/BtbN) · sigma is in 1080-wide pixels, k scales it to the output
export function whipFilter(shots, whip, k = 1) {
  if (!whip) return null;
  const w = { ...STAGE.whip, ...whip }, cuts = shots.slice(1).filter(s => s.whip !== false).map(s => s.t[0]);
  if (!cuts.length) return null;
  const on = cuts.map(t => `between(t,${Math.max(0, t - w.dur / 2).toFixed(3)},${(t + w.dur / 2).toFixed(3)})`).join('+');
  return `gblur=sigma=${Math.round(w.sigma * k)}:sigmaV=0.01:steps=2:enable='${on}'`;
}

// ---------------------------------------------------------------- check
export function check() {
  const errs = [], warns = [], shots = plan.shots || [];
  const ONCUT = plan.mode === 'overlay-on-cut';   // on top of an edit someone already made: pacing/layout belong to that edit, check text only
  if (!shots.length) errs.push('plan.shots ว่าง');
  if (!MODES.includes(plan.mode)) errs.push(`mode "${plan.mode}" ไม่รู้จัก (${MODES.join(' · ')})`);
  if (plan._look_was) warns.push(`mode stage ใช้หน้าตาของตัวเอง — look "${plan._look_was}" ถูกข้าม`);
  if (plan.preset && !PRESETS[plan.preset]) errs.push(`preset "${plan.preset}" ไม่รู้จัก (${Object.keys(PRESETS).join(' · ')})`);
  if (plan.source_video && !existsSync(planPath(plan.source_video))) warns.push(`ไม่เจอ source_video "${plan.source_video}" (เทียบกับโฟลเดอร์ของ plan) — compose จะทำไม่ได้`);
  let prevEnd = 0, lastReset = 0;
  const prompts = plan.prompts || {};
  const ASS = readAss();
  shots.forEach((s, i) => {
    const tag = `shot ${s.id}`;
    if (!LAYOUTS.includes(s.layout)) errs.push(`${tag}: layout "${s.layout}" ไม่รู้จัก (${LAYOUTS.join(' · ')})`);
    if (!Array.isArray(s.t) || s.t.length !== 2 || s.t[1] <= s.t[0]) { errs.push(`${tag}: t ต้องเป็น [เริ่ม, จบ]`); return; }
    if (Math.abs(s.t[0] - prevEnd) > 0.01) errs.push(`${tag}: เริ่ม ${s.t[0]} แต่ช็อตก่อนจบ ${prevEnd} (ต้องต่อกันพอดี)`);
    prevEnd = s.t[1];
    const v = s.visual || {};
    if (v.card && !FULL_CARDS.includes(v.card.type)) errs.push(`${tag}: card.type "${v.card.type}" ไม่รู้จัก (${FULL_CARDS.join(' · ')})`);
    if (v.ai && !prompts[v.ai]) errs.push(`${tag}: visual.ai "${v.ai}" ไม่มีใน plan.prompts`);
    if (v.image && !existsSync(planPath(v.image))) errs.push(`${tag}: visual.image ไม่เจอไฟล์ (${v.image}) — ช็อตจะออกเป็นจอดำ`);
    if (v.image && !/\.(png|jpe?g|webp)$/i.test(v.image)) errs.push(`${tag}: visual.image ต้องเป็นไฟล์ภาพ png/jpg/webp (${v.image})`);
    if (v.pose) {   // a still cut-out photo of the speaker stands on the stage instead of the live clip
      if (s.layout !== 'cutout') errs.push(`${tag}: visual.pose ใช้กับ layout cutout เท่านั้น`);
      if (!v.pose.file || !existsSync(planPath(v.pose.file))) errs.push(`${tag}: visual.pose.file ไม่เจอ (${v.pose.file || '-'}) — ทำรูปพื้นใสด้วย python -m engine.cutout <รูป> <ชื่อ>.png`);
      else if (!/\.png$/i.test(v.pose.file)) warns.push(`${tag}: visual.pose.file ไม่ใช่ .png — รูปที่ยังไม่ตัดพื้นจะเห็นพื้นหลังเดิมเป็นกล่อง`);
    }
    if (v.footage && !existsSync(planPath(v.footage.file || ''))) errs.push(`${tag}: visual.footage.file ไม่เจอ (${v.footage.file})`);
    if (v.footage && !v.footage.source && !(s.overlays || []).some(o => o.type === 'source')) warns.push(`${tag}: คลิปจริงไม่มีป้ายที่มา (footage.source หรือ overlay source)`);
    (s.overlays || []).forEach(o => { if (o.type === 'image' && !existsSync(planPath(o.file || ''))) errs.push(`${tag}: overlay image ไม่เจอไฟล์ (${o.file})`); });
    (s.overlays || []).forEach(o => { if (!OVERLAYS.includes(o.type)) errs.push(`${tag}: overlay "${o.type}" ไม่รู้จัก (${OVERLAYS.join(' · ')})`); });
    if (s.layout === 'full-card' && !v.card) errs.push(`${tag}: full-card ต้องมี visual.card`);
    if ((s.layout === 'split' || s.layout === 'cutout') && !v.image && !v.ai && !v.proof && !v.footage && !v.card && !(s.layout === 'split' && v.panel)) errs.push(`${tag}: ${s.layout} ต้องมีภาพ (visual.image / visual.ai / visual.proof / visual.footage / visual.card${s.layout === 'split' ? ' / visual.panel' : ''})`);
    if (s.layout === 'proof' && !v.proof) errs.push(`${tag}: proof ต้องระบุ visual.proof ว่าของจริงคืออะไร`);
    if (s.layout === 'cutout' && !v.pose) {   // the speaker is really cut out of the frame → needs an engine on this machine
      const e = shotEngine(s);
      if (e === 'off') errs.push(`${tag}: layout cutout ต้องตัดพื้นหลังคน แต่เครื่องนี้ยังไม่มีตัวตัดพื้น (cutout.engine = off) — เปลี่ยนเป็น layout "split" (ภาพครึ่งบน ผู้พูดครึ่งล่าง) หรือติดตั้งตัวตัดพื้นก่อน (Mac ใช้ vision ได้เลย · Windows รัน setup พร้อม --with-cutout)`);
      else if (e === 'vision' && !IS_MAC) errs.push(`${tag}: cutout engine "vision" ใช้ได้เฉพาะ Mac — ใช้ "tb" หรือเปลี่ยนเป็น layout "split"`);
      else if (!['vision', 'tb', 'none'].includes(e)) errs.push(`${tag}: cutout engine "${e}" ไม่รู้จัก (vision · tb · none)`);
    }
    // vox grammar fields
    const c = v.card || {};
    if (c.type === 'doc' && !(c.lines || []).length) errs.push(`${tag}: doc ต้องมี lines[]`);
    if (c.type === 'doc' && !(c.lines || []).some(l => /\*.+?\*/.test(l))) warns.push(`${tag}: doc ไม่มี *คำ* ให้ไฮไลต์ — เอกสารบนจอต้องบอกว่าให้อ่านตรงไหน`);
    if (c.type === 'chart' && !(c.series || []).every(x => Array.isArray(x.values) && x.values.length && x.values.every(n => typeof n === 'number'))) errs.push(`${tag}: chart ต้องมี series[{values:[ตัวเลข]}]`);
    if (c.type === 'chart' && c.kind === 'bar' && (c.series || []).length > 1) warns.push(`${tag}: chart bar ใช้ series แรกชุดเดียว`);
    if (c.type === 'quote' && !c.who) errs.push(`${tag}: quote ต้องมี who (ใครพูด — คำพูดจริงเท่านั้น)`);
    if (c.type === 'chapter' && (!c.n || !c.title)) errs.push(`${tag}: chapter ต้องมี n + title`);
    if (c.type === 'grid') {   // stage card: photos on grid paper · references/stage-style.md §2
      if (!(c.images || []).length && !(c.lines || []).length && c.big == null) errs.push(`${tag}: grid ต้องมี images[] หรือ lines[] หรือ big`);
      (c.images || []).forEach(f => { if (!existsSync(planPath(f))) errs.push(`${tag}: grid image ไม่เจอไฟล์ (${f})`); });
      if (c.theme && !['light', 'dark'].includes(c.theme)) errs.push(`${tag}: grid theme "${c.theme}" ไม่รู้จัก (light · dark)`);
      if (c.at && (!Array.isArray(c.at) || c.at.length !== Math.max(0, (c.images || []).length - 1))) errs.push(`${tag}: grid at[] ต้องมี ${Math.max(0, (c.images || []).length - 1)} ค่า (วินาทีที่ปัดไปภาพถัดไป · ภาพละ 1 ค่ายกเว้นภาพแรก)`);
      if (c.big != null && !c.source) warns.push(`${tag}: grid มีตัวเลข (big) แต่ไม่มี source — ตัวเลขบนจอต้องบอกที่มา`);
      if (c.badge && !c.badge.big) errs.push(`${tag}: grid badge ต้องมี big`);
    }
    if (c.type === 'timeline' && !(c.events || []).length) errs.push(`${tag}: timeline ต้องมี events[{t, text}]`);
    if (['chart', 'doc', 'stat', 'quote'].includes(c.type) && !c.source) (plan.look === 'vox' ? errs : warns).push(`${tag}: ${c.type} ไม่มี source — ตัวเลข/เอกสาร/คำพูดบนจอต้องบอกที่มา (ของจริงเท่านั้น)`);
    if (c.source && /ตัวอย่าง|placeholder|TODO/i.test(c.source)) warns.push(`${tag}: source ยังเป็นค่าตัวอย่าง "${c.source}" — แทนด้วยที่มาจริงก่อนเรนเดอร์ใช้งาน`);
    if (v.treat && !['print', 'duotone'].includes(v.treat)) errs.push(`${tag}: visual.treat "${v.treat}" ไม่รู้จัก (print · duotone)`);
    if (v.proof_file && !findAsset('proof', v.proof_file)) errs.push(`${tag}: ไม่เจอไฟล์ proof_file "${v.proof_file}" ใน assets/proof/ — ช็อตจะออกเป็นจอดำ (ชื่อไฟล์เปลี่ยน? นามสกุลต้องเป็น png/jpg/webp)`);
    if (v.treat && !v.ai && !v.proof_file) warns.push(`${tag}: visual.treat ใช้กับภาพ AI หรือ proof_file เท่านั้น`);
    (s.overlays || []).forEach(o => {
      if (o.type === 'annotate' && !['circle', 'underline', 'arrow', 'box'].includes(o.shape || 'circle')) errs.push(`${tag}: annotate shape "${o.shape}" ไม่รู้จัก (circle · underline · arrow · box)`);
      if (o.type === 'annotate' && o.shape === 'arrow' && !(o.from && o.to)) errs.push(`${tag}: annotate arrow ต้องมี from [x,y] + to [x,y] (%)`);
      if (o.type === 'keyword' && !(o.lines || []).length) errs.push(`${tag}: keyword ต้องมี lines[] (≤3 บรรทัด)`);
      if (o.type === 'keyword' && (o.lines || []).length > 3) warns.push(`${tag}: keyword ${o.lines.length} บรรทัด — Vox ใช้ไม่เกิน 3`);
      if (o.type === 'name' && !o.name) errs.push(`${tag}: name ต้องมี name (+ role)`);
      if (o.type === 'docchip' && !o.text) errs.push(`${tag}: docchip ต้องมี text (ชื่อเอกสารจริง)`);
      if (o.type === 'highlight' && (o.x == null || o.y == null)) errs.push(`${tag}: highlight ต้องมี x y (+ w h) เป็น %`);
      if (o.type === 'highlight' && !v.proof_file && !v.ai) warns.push(`${tag}: highlight ไม่มีภาพจริงข้างใต้ (visual.proof_file) — ไฟล์พื้นเขียวจะได้แค่ขีดเส้นใต้ทึบ (key ปากกาโปร่งไม่ได้) · ใส่ proof_file เพื่อให้ไฮไลต์ซ้อนบนภาพจริงในเครื่อง`);
    });
    if (s.beat && !BEATS.includes(s.beat)) errs.push(`${tag}: beat "${s.beat}" ไม่รู้จัก (${BEATS.join(' · ')})`);
    // pacing rules (references/visual-modes.md §3)
    const HOLD = plan.look === 'vox' ? 4 : plan.mode === 'stage' ? STAGE.hold : 3;   // vox: measured median shot 3.2–3.4 s, every shot carries one new piece of information · stage: a new picture every 1–2 s
    if (!ONCUT && s.t[0] < 12 && dur(s) > HOLD + .01 && s.layout !== 'full-card') warns.push(`${tag}: ช่วงเปิด 12 วิแรก ภาพค้าง ${dur(s)} วิ (เกิน ${HOLD} วิ) — ตัดเป็นหลายช็อต`);
    if (s.layout === 'full-card' || s.layout === 'split') lastReset = s.t[1];
    if (!ONCUT && s.t[1] - lastReset > 18) { warns.push(`${tag}: ผ่านไป ${(s.t[1] - lastReset).toFixed(1)} วิ ยังไม่มีการ์ดเต็มจอรีเซ็ตสายตา (เป้า ทุก 12–18 วิ)`); lastReset = s.t[1]; }
    (s.overlays || []).forEach(o => { if ((o.type === 'label' || o.type === 'hook-pill' || o.type === 'chat') && o.y != null && (o.y < 12 || o.y > 72)) warns.push(`${tag}: ${o.type} ที่ y ${o.y}% ทับโซนปุ่มของแอป (บน 12% / ล่าง 28%)`); });
    (s.overlays || []).forEach(o => { if (['keyword', 'name', 'docchip'].includes(o.type) && o.y != null && o.y < 46) warns.push(`${tag}: ${o.type} ที่ y ${o.y}% ทับหน้าผู้พูด — การ์ดอก (chest card) วาง y 50–66 (หน้าอยู่ต่ำจริงให้ดูเฟรมแล้วยอมได้)`); });
    if (!ONCUT && s.layout === 'full-speaker' && (s.overlays || []).some(o => o.type === 'chat' || o.type === 'hook-pill')) warns.push(`${tag}: ฟองแชต/pill บน full-speaker อาจทับหน้า — ดูเฟรมจริง หรือใช้ split + visual.panel`);
    const texts = (s.overlays || []).filter(o => o.type !== 'counter').length;
    if (texts > 2) warns.push(`${tag}: ข้อความซ้อน ${texts} ชั้น (เกิน 2) — จอเล็กอ่านไม่ทัน`);
    // collisions (estimated from type · position · text length — a warning: open shots/<id>-*.png to confirm)
    const boxes = (s.overlays || []).map(o => [o, overlayBox(o)]).filter(([, b]) => b);
    boxes.forEach(([o, a], k) => boxes.slice(k + 1).forEach(([q, b]) => {
      const area = overlapX(a, b) * overlapY(a, b), small = Math.min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]));
      if (area > .15 * small) warns.push(`${tag}: ${o.type} กับ ${q.type} ทับกัน (ประมาณจากตำแหน่ง+ความยาวข้อความ) — ขยับ y หรือตัดชิ้นหนึ่งออก · ดู out/shots/${s.id}-${s.layout}.png`);
    }));
    const band = subBand(s, ASS);
    if (band) boxes.forEach(([o, b]) => {
      const sb = [60, band.top, 1020, band.base + band.size * .25];
      if (overlapY(b, sb) > .3 * Math.min(b[3] - b[1], sb[3] - sb[1])) warns.push(`${tag}: ${o.type} ทับแถบซับ (ซับอยู่ราว y ${Math.round(band.top / H * 100)}–${Math.round(band.base / H * 100)}%) — ย้าย overlay หรือใส่ sub_y ให้ช็อตนี้`);
    });
    if (!ONCUT && s.layout === 'full-speaker' && dur(s) > 6 && !(s.overlays || []).length) warns.push(`${tag}: ผู้พูดเต็มจอเปล่าๆ ${dur(s)} วิ — พักได้ไม่เกิน ~6 วิ หรือเติม label`);
  });
  if (plan.face_circle && (plan.face_circle.mode || 'cutout') === 'cutout' && ENGINE === 'off' && (plan.cutout || {}).engine !== 'none')
    warns.push('face_circle.mode "cutout" แต่เครื่องนี้ไม่มีตัวตัดพื้น — การ์ดเต็มจอจะใช้หน้าวงกลมขอบจางแทน');
  if (!ONCUT && shots[0] && shots[0].layout !== 'split' && !(shots[0].overlays || []).some(o => o.type === 'hook-pill' || o.type === 'keyword'))
    warns.push('shot แรก: ไม่ใช่ split และไม่มี hook-pill — 2 วิแรกต้องเห็นหลักฐาน + ประโยคที่บอกว่าคนดูจะได้อะไร');
  if (!shots.some(s => s.visual && (s.visual.proof || s.visual.footage)) && !plan.proof_exempt) warns.push('ไม่มีช็อตของจริง (proof / footage) — กราฟิกใช้อธิบาย แต่ต้องปิดด้วยหลักฐานจริง (หรือใส่ plan.proof_exempt: "เหตุผล")');
  if (plan.look === 'vox' && !ONCUT) {   // story arc checks — references/vox-style.md §1
    const end = shots.at(-1) ? shots.at(-1).t[1] : 0, early = shots.filter(s => s.t[0] < 8);
    const asks = s => s.beat === 'question' || /\?|ทำไม|ยังไง|อะไร/.test([...(((s.visual || {}).card || {}).lines || []), ...(s.overlays || []).flatMap(o => o.lines || [o.text || ''])].join(''));
    if (!early.some(asks)) warns.push('look vox: 8 วิแรกยังไม่มีคำถามหลัก (beat "question" หรือการ์ดที่ถาม "ทำไม/ยังไง/?") — Vox ล็อกคำถามเดียวให้คนดูตามหาคำตอบทั้งคลิป');
    if (shots.length && !shots.some(s => s.beat)) warns.push('look vox: ยังไม่ได้ใส่ beat ในช็อต — ใส่เพื่อเช็คโครงเรื่อง (cold-open → question → context → mechanism → turn → proof → so-what)');
    else if (shots.some(s => s.beat)) {
      if (end >= 30 && !shots.some(s => s.beat === 'turn')) warns.push('look vox: คลิป ≥30 วิ ไม่มี beat "turn" (จุดหักมุม "แต่...") — ช่วงกลางจะแบน');
      if (!shots.slice(-2).some(s => s.beat === 'so-what')) warns.push('look vox: 2 ช็อตท้ายไม่มี beat "so-what" — จบด้วยข้อสรุปที่คนดูเอาไปใช้ ไม่ใช่สรุปซ้ำ');
      if (!shots.some(s => s.beat === 'mechanism')) warns.push('look vox: ไม่มี beat "mechanism" — Vox ขายการ*เห็น*กลไก ต้องมีช็อตผัง/กราฟ/ฉากสะสมอย่างน้อย 1');
    }
    if (plan.style && !['collage', 'photo'].includes(plan.style)) warns.push(`look vox: style "${plan.style}" ไม่เข้าชุด — ใช้ collage (ค่าแนะนำ) หรือ photo + visual.treat`);
  }
  if (plan.mode === 'stage' && shots.length && prevEnd > 0) {   // stage pacing — references/stage-style.md §1
    const avg = prevEnd / stageStates(shots);
    if (avg > STAGE.avg) warns.push(`mode stage: เฉลี่ย ${avg.toFixed(2)} วิต่อภาพ (เป้า ≤${STAGE.avg}) — ซอยประโยคเป็นหลายฉาก`);
    if (!plan.stage_floor) warns.push('mode stage: ไม่มี plan.stage_floor — ฉาก cutout จะไม่มีพื้นเวทีร่วม ผู้พูดจะดูย้ายที่ทุกช็อต');
  }
  if (plan.stage_floor && !existsSync(planPath(plan.stage_floor))) errs.push(`plan.stage_floor ไม่เจอไฟล์ (${plan.stage_floor})`);
  if (plan.whip && !(+(plan.whip.dur ?? STAGE.whip.dur) > 0 && +(plan.whip.sigma ?? STAGE.whip.sigma) > 0)) errs.push('plan.whip ต้องเป็น { dur: วินาที > 0, sigma: > 0 }');
  const styles = new Set(Object.values(prompts).map(p => p.style || plan.style));
  if (styles.size > 1) errs.push(`ภาพ AI ใช้ ${styles.size} สไตล์ (${[...styles].join(', ')}) — 1 คลิป 1 ภาษาภาพ`);
  Object.entries(prompts).forEach(([id, p]) => {
    if (/[฀-๿]/.test(p.scene || '')) warns.push(`prompt ${id}: มีตัวอักษรไทยใน scene — เขียน prompt เป็นอังกฤษ และห้ามให้ AI เขียนตัวหนังสือ (ใส่เป็นการ์ดแทน)`);
    if (p.edit_of && !prompts[p.edit_of]) errs.push(`prompt ${id}: edit_of "${p.edit_of}" ไม่มีอยู่`);
    [].concat(p.same_as || []).forEach(x => { if (!prompts[x.id]) errs.push(`prompt ${id}: same_as "${x.id}" ไม่มีอยู่`); if (!x.who) errs.push(`prompt ${id}: same_as ต้องมี who`); });
  });
  return { ok: errs.length === 0, errs, warns, shots: shots.length, seconds: prevEnd };
}

// ---------------------------------------------------------------- page html (cards.html + brand CSS + job data)
export function pageHtml(job) {
  const tpl = readFileSync(join(HERE, 'cards.html'), 'utf8');   // read per job: a template swapped mid-run is caught by the DOM check
  const data = JSON.stringify({ ...job, speaker: SPEAKER, faceSlot: !!plan.face_circle, look: plan.look }).replace(/</g, '\\u003c');
  // replacer functions: plan text such as "$&" must never be read as a replacement pattern
  return tpl.replace('</head>', () => `<style id="brand">\n${brandCss(cfg)}</style>\n</head>`)
    .replace('<div id="root"></div>', () => `<div id="root"></div>\n<script>window.JOB=${data};</script>`);
}

// ---------------------------------------------------------------- chrome / edge over CDP
function findUnder(dir, name, depth = 4) {
  if (!existsSync(dir) || depth < 0) return null;
  let entries; try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return null; }
  for (const e of entries) if (e.isFile() && e.name === name) return join(dir, e.name);
  for (const e of entries.filter(x => x.isDirectory()).sort((a, b) => b.name.localeCompare(a.name))) { const f = findUnder(join(dir, e.name), name, depth - 1); if (f) return f; }
  return null;
}
// ordered browser candidates per platform (pure → unit-testable for Windows on any machine)
export function chromeCandidates(plat = process.platform, env = process.env, kitHome = home()) {
  if (plat === 'win32') {
    const J = win32.join;
    return [
      J(env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),   // every Windows 10/11 has Edge
      J(env.ProgramFiles || 'C:\\Program Files', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      J(env.ProgramFiles || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
      env.LOCALAPPDATA ? J(env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe') : null,   // per-user Chrome install
      findUnder(join(kitHome, 'chrome'), 'chrome-headless-shell.exe'),
    ];
  }
  return [
    findUnder(join(kitHome, 'chrome'), 'chrome-headless-shell'),   // engine/install/get_chrome.mjs puts it here
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    join(homedir(), 'Applications', 'Google Chrome.app', 'Contents', 'MacOS', 'Google Chrome'),
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    findUnder(join(homedir(), 'Library', 'Caches', 'ms-playwright'), 'chrome-headless-shell'),   // dev machines with Playwright
  ];
}
export function resolveChrome() {
  try { return tool('chrome'); } catch { /* not configured / not on PATH → platform candidates */ }
  const hit = chromeCandidates().find(c => c && existsSync(c));
  if (hit) return hit;
  throw new Error('ไม่พบ Chrome หรือ Edge สำหรับวาดการ์ด — พิมพ์ "ตรวจเครื่อง tad-clip" ให้ Claude ติดตั้งให้ (โหลด chrome-headless-shell เข้า ~/.tad-clip/chrome)');
}
// chrome-headless-shell only has the old headless mode · full Chrome / Edge need the new one
// CDP runs over --remote-debugging-pipe (fd 3 = commands in, fd 4 = replies out, NUL-separated JSON) — the same transport
// Playwright uses on every OS. Not a WebSocket: Node 22's built-in WebSocket drops any CDP message over 4 MiB
// (permessage-deflate cap → close 1006), and one frame PNG of a photo or paper grain is bigger than that, worst at 2K.
// That drop is what used to look like "Chrome hangs" (60 s CDP timeout → retry at 1080).
export function chromeArgs(bin, userDataDir) {
  const shell = /chrome-headless-shell/i.test(basename(bin));
  return [shell ? '--headless' : '--headless=new', '--disable-gpu', '--hide-scrollbars', '--allow-file-access-from-files', '--remote-debugging-pipe',
    `--user-data-dir=${userDataDir}`, '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--mute-audio', 'about:blank'];
}

async function openBrowser() {
  const udd = mkdtempSync(join(tmpdir(), 'thv-'));
  const bin = resolveChrome();
  const proc = spawn(bin, chromeArgs(bin, udd), { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });
  const toChrome = proc.stdio[3], fromChrome = proc.stdio[4];
  let errTail = '', dead = null;
  proc.stderr.on('data', d => { if (process.env.THV_DEBUG) process.stderr.write(d); errTail = (errTail + d).slice(-600); });
  toChrome.on('error', () => {});   // EPIPE after Chrome died — reported through `dead`
  let seq = 0; const pending = new Map(), listeners = new Set();
  const fail = why => { dead ??= new Error(why); pending.forEach(p => p.rej(dead)); pending.clear(); listeners.forEach(f => f({ method: '__dead' })); };
  proc.on('error', e => fail(`เปิด ${bin} ไม่ได้: ${e.message}`));
  proc.on('exit', c => fail(`CDP pipe closed — Chrome/Edge ปิดตัวเอง (exit ${c}) ${errTail.trim().split('\n').pop() || ''}`));
  let parts = [];
  fromChrome.on('data', chunk => {
    let from = 0, at;
    while ((at = chunk.indexOf(0, from)) !== -1) {
      parts.push(chunk.subarray(from, at)); from = at + 1;
      const m = JSON.parse(Buffer.concat(parts).toString('utf8')); parts = [];
      if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); }
      else listeners.forEach(f => f(m));
    }
    if (from < chunk.length) parts.push(chunk.subarray(from));
  });
  // every CDP call times out: a hung headless Chrome once left a 2K render stuck at 0% CPU for 30 min
  const raw = (method, params = {}, sessionId) => new Promise((res, rej) => {
    if (dead) return rej(dead);
    const id = ++seq;
    const to = setTimeout(() => { pending.delete(id); rej(new Error(`CDP ${method} ค้างเกิน 60 วิ — Chrome ค้าง`)); }, 60000);
    pending.set(id, { res: v => { clearTimeout(to); res(v); }, rej: e => { clearTimeout(to); rej(e); } });
    toChrome.write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0');
  });
  const close = async () => {   // wait for chrome to exit before deleting its profile dir (it keeps writing until then) · a hung chrome ignores SIGTERM
    const exited = new Promise(r => { if (proc.exitCode !== null || proc.signalCode) r(); else proc.once('exit', r); });
    try { toChrome.end(); } catch { /* gone */ }
    proc.kill(); const k = setTimeout(() => proc.kill('SIGKILL'), 5000); await exited; clearTimeout(k);
    for (const d of [udd, tmp]) { try { rmSync(d, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* Windows may hold a file a moment longer */ } }
  };
  let tmp = udd;
  let sessionId;
  const send = (method, params = {}) => raw(method, params, sessionId);
  try {   // any failure while setting up closes this browser and its temp profile before the error leaves
    const { targetId } = await raw('Target.createTarget', { url: 'about:blank' });
    ({ sessionId } = await raw('Target.attachToTarget', { targetId, flatten: true }));
    await send('Page.enable');
  } catch (e) { await close(); throw new Error(`เปิด Chrome/Edge ไม่สำเร็จ (${bin}): ${e.message}`); }
  const once = name => new Promise((r, rej) => {   // an event, with the same 60 s guard and death check as a command
    const to = setTimeout(() => { listeners.delete(f); rej(new Error(`CDP ${name} ค้างเกิน 60 วิ — Chrome ค้าง`)); }, 60000);
    const f = m => { if (m.method === name && m.sessionId === sessionId) { clearTimeout(to); listeners.delete(f); r(m.params); }
      else if (m.method === '__dead') { clearTimeout(to); listeners.delete(f); rej(dead); } };
    listeners.add(f); });
  tmp = mkdtempSync(join(tmpdir(), 'thv-job-'));
  const b = {
    send,
    evaluate: async expr => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result.value,
    async load(job, w = W, h = H) {
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: job.kind === 'board' ? 1 : (job.scale || K), mobile: false });   // K 1.3333 = 1440×2560
      await send('Emulation.setDefaultBackgroundColorOverride', { color: { r: 0, g: 0, b: 0, a: 0 } });
      // overlay image: file path (relative to plan) → file URL the page can load
      const imgUrl = o => o.type === 'image' && o.file ? { ...o, src: pathToFileURL(planPath(o.file)).href } : o;
      // stage: grid-card photos · the shared stage floor · pose cut-outs are paths relative to the plan → file URLs
      const url = f => pathToFileURL(planPath(f)).href;
      const fix = sh => { if (!sh) return sh; const c = sh.visual && sh.visual.card;
        return { ...sh, ...(sh.overlays ? { overlays: sh.overlays.map(imgUrl) } : {}),
          ...(c && c.images ? { visual: { ...sh.visual, card: { ...c, images: c.images.map(url) } } } : {}) }; };
      const poseOf = sh => sh && sh.layout === 'cutout' && sh.visual && sh.visual.pose && sh.visual.pose.file ? { ...sh.visual.pose, src: url(sh.visual.pose.file) } : null;
      job = { ...job, shot: fix(job.shot), shots: job.shots && job.shots.map(sh => ({ ...fix(sh), _pose: poseOf(sh) })),
        pose: poseOf(job.shot), floor: plan.stage_floor ? url(plan.stage_floor) : null, floorTop: plan.floor_top };
      const f = join(tmp, `job-${seq}.html`);
      writeFileSync(f, pageHtml(job));
      // a blank page renders as an all-black (transparent) clip with no error — seen when the template file was swapped
      // mid-run by a sync tool → verify the frame was built, retry once, else stop loudly
      for (let attempt = 0; ; attempt++) {
        const loaded = once('Page.loadEventFired');
        const nav = send('Page.navigate', { url: pathToFileURL(f).href });
        if (testKill(job)) proc.kill('SIGKILL');
        await Promise.all([nav, loaded]);   // never leave `loaded` without a handler: a browser dying mid-navigate rejects both → perShot retries
        await b.evaluate('document.fonts.ready.then(() => true)');
        if (await b.evaluate("document.querySelectorAll('#root *').length > 0")) break;
        if (attempt) throw new Error(`render หน้าว่าง (job ${job.kind} ${job.shot ? 'shot ' + job.shot.id : ''}) — เช็ค engine/visual/cards.html`);
      }
    },
    async png(clip) {
      const r = await send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true, ...(clip ? { clip: { ...clip, scale: 1 }, captureBeyondViewport: true } : {}) });
      return Buffer.from(r.data, 'base64');
    },
    close,
  };
  return b;
}

// test hook (tests/visual.test.mjs): THV_TEST_KILL_BROWSER=<shot id> SIGKILLs the browser once, right after Page.navigate for that shot
let testKilled = false;
const testKill = job => { if (testKilled || !job.shot || process.env.THV_TEST_KILL_BROWSER !== String(job.shot.id)) return false; testKilled = true; return true; };

// a hung headless Chrome only spoils the shot it was drawing → restart Chrome and redo that shot, 3 tries
// (a 24-shot 2K render died on one CDP timeout; rerunning that shot alone passed)
// hang (our 60 s timeouts) · browser gone (pipe closed / exit) · renderer gone ("Inspected target navigated or closed",
// "Target crashed", "Session with given id not found") → worth a fresh browser; anything else is a real error
export const isRetryable = e => /ค้างเกิน|closed|crash|ปิดตัวเอง|session with given id not found/i.test((e && e.message) || '');
async function perShot(B, list, fn) {
  for (const s of list) for (let i = 1; ; i++) {
    try { await fn(B.cur, s, i); break; } catch (e) {
      if (i >= 3 || !isRetryable(e)) throw e;
      console.log(`↻ shot ${s.id}: Chrome ค้าง → เปิดใหม่ ลองครั้งที่ ${i + 1}${K > 1 ? ' (วาดที่ 1080 แล้วขยาย)' : ''}`);
      await B.cur.close().catch(() => {}); B.cur = await openBrowser();
    }
  }
}

// frames → ffmpeg stdin, honouring backpressure (a long held shot at 60 fps would otherwise queue GBs of PNGs in memory)
function encoder(file, vf, enc) {
  const ff = spawn(FFMPEG(), ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-', '-vf', [vf, TO709].filter(Boolean).join(','), ...enc, ...TAG709, '-r', String(FPS), file], { stdio: ['pipe', 'inherit', 'inherit'] });
  ff.stdin.on('error', () => {});   // EPIPE when ffmpeg dies — reported by `done`
  const done = new Promise((res, rej) => ff.on('close', c => c === 0 ? res() : rej(new Error('ffmpeg exit ' + c))));
  const write = buf => new Promise(r => { if (ff.stdin.write(buf)) return r(); const ok = () => { ff.stdin.off('drain', ok); ff.off('close', ok); r(); }; ff.stdin.on('drain', ok); ff.on('close', ok); });
  return { write, async end() { ff.stdin.end(); await done; }, kill() { done.catch(() => {}); ff.kill('SIGKILL'); } };
}
// seek → capture every frame of a shot; repeats the PNG when the graphics clock did not move (vox graphics_fps) or the animation is over
async function captureShot(b, s, enc, bgRuns) {
  const n = frames(s), lastEnd = bgRuns ? Infinity : await b.evaluate('window.__lastEnd()');   // Ken Burns runs the whole shot
  let held = null, lastMs = -1, buf = null;
  for (let i = 0; i < n; i++) {
    if (held) { await enc.write(held); continue; }
    const ms = graphicsMs(i);
    if (ms !== lastMs) { await b.evaluate(`window.__seek(${ms})`); buf = await b.png(); lastMs = ms; if (ms > lastEnd) held = buf; }
    await enc.write(buf);
  }
}

// ---------------------------------------------------------------- board + cards
const bgFor = s => { const v = s.visual || {}; if (v.image) return planPath(v.image); if (v.ai) return findAsset('ai', v.ai); if (v.proof && v.proof_file) return findAsset('proof', v.proof_file); return null; };
const needsRender = s => (s.visual && (s.visual.card || s.visual.panel)) || (s.overlays || []).length || bgFor(s);

// visual.footage = { file (relative to plan), start (s in that file), speed? (<1 = slow-mo to stretch a short shot), source? } → real clip fills the picture area (split top half · proof full frame)
const fresh = (out, src) => existsSync(out) && statSync(out).mtimeMs > statSync(src).mtimeMs;
// storyboard stills are cached under a name that says what they show (shot · source file · timestamp in ms), so retiming a
// shot or pointing it at another file grabs a new frame; the shot's older stills of the same kind are removed, and so are
// the names older kits used (<id>.jpg = footage · <id>-speaker.jpg = speaker), which nothing reads any more
const stillName = (s, kind, file, at, tail = '') => `${s.id}-${kind}-${createHash('md5').update(file).digest('hex').slice(0, 6)}-${Math.round(at * 1000)}${tail}.jpg`;
function grabStill(s, kind, file, at, vf, tail = '') {
  if (!file || !existsSync(file)) return null;
  const dir = join(OUT, 'stills'), name = stillName(s, kind, file, at, tail), out = join(dir, name); mkdirSync(dir, { recursive: true });
  for (const legacy of [`${s.id}.jpg`, `${s.id}-${kind}.jpg`]) rmSync(join(dir, legacy), { force: true });
  if (!fresh(out, file)) {
    for (const old of readdirSync(dir)) if (old !== name && old.startsWith(`${s.id}-${kind}-`)) rmSync(join(dir, old), { force: true });
    spawnSync(FFMPEG(), ['-loglevel', 'error', '-y', '-ss', at.toFixed(3), '-i', file, '-frames:v', '1', '-q:v', '3', '-vf', vf, out]);
  }
  return existsSync(out) ? out : null;
}
const footStill = s => { const f = s.visual && s.visual.footage; if (!f) return null; const file = planPath(f.file);
  return grabStill(s, 'footage', file, (f.start || 0) + (dur(s) / 2) * (f.speed || 1), `${sdr(file)}scale=-2:960`); };

// one real frame of the speaker at the shot's midpoint, cover-cropped to 1080×1920 like compose (mirror applied) → storyboard
const mirrored = s => (plan.mirror || []).some(([a, b]) => s.t[0] >= a - 0.02 && s.t[1] <= b + 0.02);
function speakerStill(s) {
  const src = planPath(plan.source_video), flip = mirrored(s);
  return grabStill(s, 'speaker', src, (s.t[0] + s.t[1]) / 2, `${sdr(src)}scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}${flip ? ',hflip' : ''}`, flip ? '-flip' : '');
}
const showsSpeaker = s => (['split', 'full-speaker', 'cutout'].includes(s.layout) && !(s.visual && s.visual.pose)) || (s.layout === 'full-card' && plan.face_circle && s.face !== false);

const boardTitle = () => `${plan.title || basename(PLAN_DIR)} · ${plan.shots.at(-1).t[1]} วิ · ${plan.shots.length} ช็อต`;
async function board(b) {
  const ass = readAss(), url = f => f && pathToFileURL(f).href;
  const shots = plan.shots.map(s => {
    const bg = bgFor(s) || footStill(s), spk = showsSpeaker(s) ? speakerStill(s) : null, band = subBand(s, ass);
    return { ...s, visual: { ...(s.visual || {}) }, _bg: url(bg), _spk: url(spk),
      _win: s.split_window || plan.split_window || [260, 960], _cut: s.cutout_scale ?? (plan.cutout || {}).scale ?? 0.62,
      _face: plan.face_circle ? { window: [40, 500, 1000], size: 400, card_y: 230, ...plan.face_circle } : null,
      _sub: band && band.text ? { text: band.text, base: band.base, size: band.size } : null };
  });
  await b.load({ kind: 'board', title: boardTitle(), shots }, 1232, 800);
  const hgt = await b.evaluate('document.querySelector(".board").scrollHeight');
  await b.load({ kind: 'board', title: boardTitle(), shots }, 1232, hgt);
  await b.evaluate('window.__seek(60000)');   // show every card in its finished state
  writeFileSync(join(OUT, 'storyboard.png'), await b.png());
  const real = shots.filter(s => s._spk).length;
  console.log(`✓ storyboard.png (${plan.shots.length} ช็อต${real ? ` · เฟรมจริงของผู้พูด ${real} ช็อต` : plan.source_video ? '' : ' · ยังไม่มี source_video: ผู้พูดเป็นภาพตัวแทน'}${ass ? ' · แถบซับจากไฟล์จริง' : ''})`);
}

async function cards(B) {
  mkdirSync(join(OUT, 'shots'), { recursive: true });
  const bare = plan.shots.filter(s => !needsRender(s)).map(s => s.id);
  if (bare.length) console.log(`· ช็อต ${bare.join(', ')} ไม่มีกราฟิก (ผู้พูดเต็มจอ/คลิปล้วน) → ไม่มี PNG — ดูภาพจริงใน storyboard.png`);
  await perShot(B, plan.shots.filter(needsRender), async (b, s) => {
    const bg = bgFor(s);
    await b.load({ kind: 'frame', shot: s, bgImage: bg && pathToFileURL(bg).href, scale: 1 });   // stills at 1080: 2K card PNGs hang Chrome most
    await b.evaluate('window.__seek(60000)');
    writeFileSync(join(OUT, 'shots', `${s.id}-${s.layout}.png`), await b.png());
    console.log(`✓ shots/${s.id}-${s.layout}.png`);
  });
}

// ---------------------------------------------------------------- motion (frame-by-frame → ffmpeg)
// card_scale: full-card shots drawn at this device scale then upscaled (vox paper-grain cards hang Chrome's capture at 2K)
// a retry at 2K (attempt ≥ 2) also falls back to 1080 + lanczos: split shots with a big real photo hung all 3 tries at 2K
const cardScale = (s, attempt = 1) => s.layout === 'full-card' && CARD_SCALE ? CARD_SCALE : attempt >= 2 && K > 1 ? 1 : undefined;
async function motion(B) {
  mkdirSync(join(OUT, 'shots'), { recursive: true });
  const only = flag('shot'), matte = flag('matte', 'green');
  const list = plan.shots.filter(s => needsRender(s) && (!only || s.id === only));
  await perShot(B, list, async (b, s, attempt) => {
    const bg = bgFor(s);
    const plain = s.visual && (s.visual.card || s.visual.panel);
    if ((s.layout === 'split' || s.layout === 'cutout') && !bg && !plain && !(s.visual && s.visual.footage))
      console.log(`⚠️  shot ${s.id}: ยังไม่มีภาพใน assets/ai/ หรือ assets/proof/ — เรนเดอร์เฉพาะข้อความบนพื้นเขียว`);
    const scale = cardScale(s, attempt);
    await b.load({ kind: 'frame', shot: bg || plain ? s : { ...s, layout: 'full-speaker' }, bgImage: bg && pathToFileURL(bg).href,
      kb: !!bg, kbDur: dur(s), matte: matte === 'alpha' ? 'none' : 'green', scale });
    const ext = matte === 'alpha' ? 'mov' : 'mp4';
    const file = join(OUT, 'shots', `${s.id}-${s.layout}.${ext}`);
    const enc = matte === 'alpha' ? ['-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le'] : ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', '-preset', 'medium'];
    const up = scale && scale < K ? `scale=${Math.round(W * K)}:${Math.round(H * K)}:flags=lanczos` : null;
    const e = encoder(file, up, enc);
    try { await captureShot(b, s, e, !!bg); } catch (err) { e.kill(); throw err; }   // drop the half-written file, perShot redoes the shot
    await e.end();
    console.log(`✓ shots/${s.id}-${s.layout}.${ext} (${dur(s)} วิ · ${frames(s)} เฟรม${matte === 'alpha' ? ' · alpha' : (s.layout === 'full-card' || (bg && s.layout === 'cutout')) ? '' : ' · พื้นเขียวให้ CapCut key'})`);
  });
}

// ---------------------------------------------------------------- loose (graphics only · the editor keys + places them in CapCut)
// every shot with a card or overlay → out/loose/<id>-<kind>.mp4 on flat #00FF00 (whole frame) + .png still with real transparency
// no AI image / primary panel / card fill · positions match the full render · place-sheet.md = when to drop each file on the timeline
async function loose(B) {
  const dir = join(OUT, 'loose'); mkdirSync(dir, { recursive: true });
  const only = flag('shot');
  const kindOf = s => (s.visual && s.visual.card) ? s.visual.card.type : (s.overlays || []).map(o => o.type).filter((v, i, a) => a.indexOf(v) === i).join('+');
  const list = plan.shots.filter(s => ((s.visual && s.visual.card) || (s.overlays || []).length) && (!only || s.id === only));
  const rows = [];
  await perShot(B, list, async (b, s) => {
    const name = `${s.id}-${kindOf(s)}`;
    await b.load({ kind: 'frame', shot: s, loose: true, matte: 'none' });
    await b.evaluate('window.__seek(60000)');
    writeFileSync(join(dir, `${name}.png`), await b.png());
    await b.load({ kind: 'frame', shot: s, loose: true, matte: 'green' });
    const e = encoder(join(dir, `${name}.mp4`), null, ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '14', '-preset', 'medium']);
    try { await captureShot(b, s, e, false); } catch (err) { e.kill(); throw err; }
    await e.end();
    const what = (s.visual && s.visual.card) ? 'การ์ดเต็มจอ (ตัวหนังสือมีขอบเข้ม อ่านได้บนวิดีโอ)'
      : (s.overlays || []).map(o => o.type === 'chat' ? `ฟองแชต ${(o.messages || []).length} ฟอง` : `${o.type} "${(o.text || (o.lines || []).join(' ') || '').replace(/\*/g, '')}"`).join(' · ');
    const under = s.layout === 'split' ? (s.visual && s.visual.ai ? `ภาพ \`${s.visual.ai}\` ครึ่งบน` : 'ภาพจริง/พื้นเรียบครึ่งบน ให้ข้อความอ่านง่าย')
      : s.layout === 'cutout' ? `ภาพเต็มจอ + ${SPEAKER}ตัดพื้น` : s.layout === 'full-card' ? `วางทับ${SPEAKER}เลย หรือใส่พื้นสีก่อนก็ได้` : `${SPEAKER}เต็มจอ`;
    rows.push(`| ${s.id} | ${s.t[0].toFixed(2)} | ${dur(s).toFixed(2)} | \`${name}.mp4\` | ${what} | ${under} |`);
    console.log(`✓ loose/${name}.mp4 + .png (${dur(s)} วิ)`);
  });
  if (!only) {
    writeFileSync(join(dir, 'place-sheet.md'), `# Loose graphics — ${plan.title || ''}\n\n` +
      `ไฟล์ .mp4 = กราฟิกบนพื้นเขียว #00FF00 ทั้งเฟรม ${Math.round(W * K)}×${Math.round(H * K)} · ใน CapCut: ลากเป็นแทร็กบน → **Chroma key** ดูดสีเขียว (Intensity ~10 · Shadow 0) · วางตรงเวลา "เริ่ม" แล้วไฟล์ยาวพอดีช็อต\n` +
      `ไฟล์ .png ชื่อเดียวกัน = ภาพนิ่งเฟรมสุดท้ายพื้นใสจริง (ไม่ต้อง key) ใช้แทนได้ถ้าไม่ต้องการให้ขยับ · ตำแหน่งในเฟรมตรงกับ storyboard ย้าย/ย่อเองได้\n` +
      `เวลาอ้างอิงคลิป \`${plan.source_video || '-'}\` (ถ้าตัดคลิปเพิ่มทีหลัง เลื่อนตาม)\n\n` +
      `| ช็อต | เริ่ม (วิ) | ยาว (วิ) | ไฟล์ | มีอะไร | ข้างใต้ควรเป็น |\n|---|---|---|---|---|---|\n${rows.join('\n')}\n`);
    console.log('✓ loose/place-sheet.md');
  }
}

// ---------------------------------------------------------------- animatic (whole timeline, placeholders for the speaker / pending images)
async function animatic(b) {
  const file = join(OUT, 'animatic.mp4');
  const e = encoder(file, 'scale=540:960', ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '23']);
  try {
    for (const s of plan.shots) {
      const bg = bgFor(s);
      await b.load({ kind: 'frame', shot: s, placeholder: true, bgImage: bg && pathToFileURL(bg).href, kb: !!bg, kbDur: dur(s), scale: 1 });
      await captureShot(b, s, e, !!bg);
    }
  } catch (err) { e.kill(); throw err; }
  await e.end();
  console.log(`✓ animatic.mp4 (${plan.shots.at(-1).t[1]} วิ · ภาพตัวแทนตรงโซนผู้พูด/ภาพที่ยังไม่มี)`);
}

// ---------------------------------------------------------------- compose (preview on the real clip · needs motion first)
// split = the speaker's upper body in the bottom half · cutout = the speaker really cut out (engine/cutout) standing bottom-centre on the image
const run = (args, opts = {}) => new Promise((res, rej) => { const p = spawn(FFMPEG(), ['-loglevel', 'error', '-y', ...args], { stdio: ['ignore', 'inherit', 'inherit'], ...opts });
  p.on('error', rej); p.on('close', c => c === 0 ? res() : rej(new Error('ffmpeg exit ' + c))); });
async function compose() {
  if (!plan.source_video) throw new Error('compose ต้องมี plan.source_video (path คลิปที่ตัดแล้ว เทียบกับโฟลเดอร์ของ plan)');
  const src = planPath(plan.source_video);
  if (!existsSync(src)) throw new Error('ไม่เจอคลิป: ' + src);
  const tmp = mkdtempSync(join(tmpdir(), 'thv-compose-')); const list = [];
  try {   // the temp folder goes away on success and on failure (a failed ffmpeg on Windows would leave GBs in %TEMP%)
    // output = 1080×1920 × K (2k60 → 1440×2560) · every number below stays in 1080 design space, px() converts
    const px = n => Math.round(n * K), OW = px(W), OH = px(H);
    // the speaker's clip enters every graph already limited-range BT.709 — graphics are limited range, and a full-range
    // phone clip that skipped roughcut would otherwise be blended in a different range
    const P = `${sdr(src)}scale=${OW}:${OH}:force_original_aspect_ratio=increase,crop=${OW}:${OH},fps=${FPS},setsar=1,${TO709}`;
    const KEY = 'chromakey=0x00FF00:0.28:0.06';
    // plan.face_circle = { window: [x, y, side] of the speaker's 1080×1920 frame, size, card_y, cutout_y, feather, mode }
    // → on full-card shots (and instead of the PiP box on cutout) the speaker's live face sits in a circle whose edge fades out
    //   (opaque inside (1-feather)·R, linear fade to 0 at R · no hard ring, the face must not touch the edge)
    const FC = plan.face_circle;
    const face = (inp, y, PS = P) => { const [fx, fy, side] = (FC.window || [40, 500, 1000]).map(px), S = px(FC.size || 400), r = S / 2, f = FC.feather ?? 0.28;
      return `[${inp}]${PS},crop=${side}:${side}:${fx}:${fy},scale=${S}:${S},format=rgba,` +
        `geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*clip((${r}-hypot(X-${r},Y-${r}))/${(r * f).toFixed(1)},0,1)'[fc];` +
        `[b][fc]overlay=${(OW - S) / 2}:${px(y)}[v]`; };
    // face_circle.mode "cutout" (default) → the same window of the speaker, really cut out (engine/cutout matte), no circle
    // bottom (fade_bottom 0.2) and side (fade_side 0.12) fade so crop edges never show · window must reach below the chin or the mouth fades
    const bust = (inp, y, flip) => { const [fx, fy, side] = (FC.window || [40, 500, 1000]).map(px), S = px(FC.size || 400);
      return `[${inp}]${flip ? 'hflip,' : ''}scale=${OW}:${OH}:force_original_aspect_ratio=increase,crop=${OW}:${OH},fps=${FPS},crop=${side}:${side}:${fx}:${fy},scale=${S}:${S},format=rgba,` +
        `geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*clip((${S}-Y)/${(S * (FC.fade_bottom ?? 0.2)).toFixed(1)},0,1)*clip(min(X,${S}-X)/${(S * (FC.fade_side ?? 0.12)).toFixed(1)},0,1)'[fc];` +
        `[b][fc]overlay=${(OW - S) / 2}:${px(y)}:format=auto[v]`; };
    // plan.cutout = { engine, scale } · per shot: cutout_engine, cutout_scale · results cached in OUT/cutout/*.webm
    // (name carries start + frame count + engine → a retimed shot re-cuts · VP9 alpha ~10 MB/shot; ProRes 4444 at 2K is ~470 MB/shot, never a cache)
    const CUT = { scale: 0.62, ...(plan.cutout || {}) };
    // cutout.fade_side (0–0.5): fade the cut-out's left/right edges — a half-body clip ends in a straight line where the
    // original frame stopped, which shows on a full-frame backdrop
    const sideFade = CUT.fade_side ? `,format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*clip(min(X,W-X)/(W*${CUT.fade_side}),0,1)'` : '';
    const cutoutOf = async (s, ss, n) => {
      const engine = shotEngine(s); if (engine === 'none' || engine === 'off') return null;
      const dir = join(OUT, 'cutout'); mkdirSync(dir, { recursive: true });
      const mov = join(dir, `${s.id}-${ss}-${n}-${engine}.webm`);
      if (!ARGS.includes('--force') && existsSync(mov) && statSync(mov).mtimeMs > statSync(src).mtimeMs) return mov;   // --force = cut again
      const seg = join(tmp, `cut-${list.length}.mp4`);   // constant fps, 8-bit SDR → the matte lines up frame for frame
      await run(['-ss', ss, '-i', src, '-t', (n / FPS + 0.1).toFixed(3), '-an', '-vf', `${sdr(src)}fps=${FPS},${TO709},format=yuv420p`, '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', ...TAG709, seg]);
      console.log(`… shot ${s.id}: ตัดพื้นหลัง${SPEAKER} (${engine})`);
      await new Promise((res, rej) => {
        const p = spawn(tool('python'), ['-m', 'engine.cutout', seg, mov, engine, '--force'], { cwd: KIT_ROOT, stdio: ['ignore', 'ignore', 'inherit'], env: { ...process.env, PYTHONUTF8: '1' } });
        p.on('error', rej); p.on('close', c => c === 0 ? res() : rej(new Error(`ตัดพื้นหลังไม่สำเร็จ (shot ${s.id} · exit ${c})`))); });
      return mov;
    };
    // plan.mirror = [[from, to], …] — ranges where the clip is mirrored (front camera: shirt text reads backwards) → hflip back
    const MR = plan.mirror || [];
    const useCutCard = FC && (FC.mode || 'cutout') === 'cutout' && ENGINE !== 'off';
    for (const s of plan.shots) {
      const flip = MR.some(([a, b]) => s.t[0] >= a - 0.02 && s.t[1] <= b + 0.02);
      if (!flip && MR.some(([a, b]) => s.t[0] < b && s.t[1] > a)) console.log(`⚠️  shot ${s.id} คร่อมขอบ plan.mirror — แบ่งช็อตตรงจุดสลับ`);
      const PS = flip ? `${P},hflip` : P;
      const n = frames(s), ss = (Math.round(s.t[0] * FPS) / FPS).toFixed(4);
      const clip = ['mp4', 'mov'].map(e => join(OUT, 'shots', `${s.id}-${s.layout}.${e}`)).find(existsSync);
      const seg = join(tmp, `seg-${String(list.length).padStart(3, '0')}.mp4`); list.push(seg);
      const inMain = ['-ss', ss, '-i', src];
      let fc, ins = inMain;
      const posed = s.layout === 'cutout' && s.visual && s.visual.pose;   // still photo of the speaker already drawn on the stage → no live speaker
      const cut = s.layout === 'cutout' && clip && !posed ? await cutoutOf(s, ss, n) : null;
      const cutCard = s.layout === 'full-card' && clip && FC && s.face !== false && useCutCard ? await cutoutOf(s, ss, n) : null;
      const foot = ft => `${sdr(planPath(ft.file))}setpts=PTS/${ft.speed || 1},fps=${FPS},${TO709}`;
      if (cutCard) { ins = ['-i', clip, '-c:v', 'libvpx-vp9', '-i', cutCard]; fc = `[0:v]fps=${FPS},setsar=1[b];${bust('1:v', FC.card_y ?? 230, flip)}`; }
      else if (s.layout === 'full-card' && clip && FC && s.face !== false) { ins = ['-i', clip, ...inMain]; fc = `[0:v]fps=${FPS},setsar=1[b];${face('1:v', FC.card_y ?? 230, PS)}`; }
      else if ((s.layout === 'full-card' || posed) && clip) { ins = ['-i', clip]; fc = `[0:v]fps=${FPS},setsar=1[v]`; }
      else if (s.layout === 'split' && s.visual && s.visual.footage) {   // real clip fills the top half (cover-crop) · speaker below · text layer keyed on top
        const ft = s.visual.footage, [wy, wh] = s.split_window || plan.split_window || [260, 960], half = px(960);
        ins = [...inMain, '-ss', String(ft.start || 0), '-i', planPath(ft.file), ...(clip ? ['-i', clip] : [])];
        const sp = wh <= 960 ? `[0:v]${PS},crop=${OW}:${half}:0:${px(wy)}`
          : `[0:v]${PS},split[pa][pb];[pa]crop=${OW}:${half}:0:${px(wy)},boxblur=40:2[pbg];[pb]crop=${OW}:${px(wh)}:0:${px(wy)},scale=-2:${half}[pfg];[pbg][pfg]overlay=(W-w)/2:0`;
        fc = `${sp},pad=${OW}:${OH}:0:${half}[pp];[1:v]${foot(ft)},scale=${OW}:${half}:force_original_aspect_ratio=increase,crop=${OW}:${half},setsar=1[ft];[pp][ft]overlay=0:0` + (clip ? `[p];[2:v]${KEY}[o];[p][o]overlay=0:0[v]` : '[v]'); }
      else if (s.layout === 'proof' && s.visual && s.visual.footage) {   // real clip full frame (cover-crop)
        const ft = s.visual.footage; ins = ['-ss', String(ft.start || 0), '-i', planPath(ft.file), ...(clip ? ['-i', clip] : [])];
        fc = `[0:v]${foot(ft)},scale=${OW}:${OH}:force_original_aspect_ratio=increase,crop=${OW}:${OH},setsar=1` + (clip ? `[p];[1:v]${KEY}[o];[p][o]overlay=0:0[v]` : '[v]'); }
      else if (s.layout === 'split' && clip) {   // split_window = [y, h]: window of the speaker's 1080×1920 frame shown in the bottom half
        ins = [...inMain, '-i', clip]; const [wy, wh] = s.split_window || plan.split_window || [260, 960];   // per-shot override (e.g. zoom out to show a shirt logo)
        const half = px(960);
        const sp = wh <= 960 ? `[0:v]${PS},crop=${OW}:${half}:0:${px(wy)}`   // 1:1 crop (default)
          : `[0:v]${PS},split[pa][pb];[pa]crop=${OW}:${half}:0:${px(wy)},boxblur=40:2[pbg];[pb]crop=${OW}:${px(wh)}:0:${px(wy)},scale=-2:${half}[pfg];[pbg][pfg]overlay=(W-w)/2:0`;   // fit + blurred sides
        fc = `${sp},pad=${OW}:${OH}:0:${half}[p];[1:v]${KEY}[o];[p][o]overlay=0:0[v]`; }
      else if (cut) { ins = ['-i', clip, '-c:v', 'libvpx-vp9', '-i', cut]; const cw = Math.round(OW * (s.cutout_scale ?? CUT.scale) / 2) * 2;   // libvpx decoder keeps the alpha · whole frame scaled down, bottom edge on the frame bottom
        fc = `[0:v]fps=${FPS},setsar=1[b];[1:v]${flip ? 'hflip,' : ''}scale=${OW}:${OH}:force_original_aspect_ratio=increase,crop=${OW}:${OH},fps=${FPS},scale=${cw}:-2,setsar=1${sideFade}[p];[b][p]overlay=(W-w)/2:H-h:format=auto[v]`; }
      else if (s.layout === 'cutout' && clip && FC && s.face !== false) { ins = [...inMain, '-i', clip]; fc = `[1:v]fps=${FPS},setsar=1[b];${face('0:v', FC.cutout_y ?? 850, PS)}`; }
      else if (s.layout === 'cutout' && clip) { ins = [...inMain, '-i', clip]; fc = `[1:v]fps=${FPS},setsar=1[b];[0:v]${PS},scale=${px(600)}:-2,crop=${px(600)}:${px(760)}:0:${px(120)},pad=${px(612)}:${px(772)}:${px(6)}:${px(6)}:white[p];[b][p]overlay=(W-w)/2:H-h-${px(40)}[v]`; }
      else if (clip) { ins = [...inMain, '-i', clip]; fc = `[0:v]${PS}[p];[1:v]${KEY}[o];[p][o]overlay=0:0[v]`; }
      else fc = `[0:v]${PS}[v]`;
      await run([...ins, '-filter_complex', `${fc};[v]${TO709},format=yuv420p[vo]`, '-map', '[vo]', '-frames:v', String(n), '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'veryfast', ...TAG709, '-r', String(FPS), seg]);
    }
    // concat list with bare names next to it (no quoting / drive-letter trouble on Windows)
    writeFileSync(join(tmp, 'list.txt'), list.map(f => `file '${basename(f)}'`).join('\n'));
    const silent = join(tmp, 'video.mp4'), out = join(OUT, 'preview-composite.mp4');
    await run(['-f', 'concat', '-safe', '0', '-i', 'list.txt', '-c', 'copy', silent], { cwd: tmp });
    // plan.whip: blur across every cut (the stage transition) → the one place compose re-encodes the joined video
    const whip = whipFilter(plan.shots, plan.whip, K);
    const vcodec = whip ? ['-vf', `${whip},${TO709}`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', '-preset', 'medium', ...TAG709] : ['-c:v', 'copy'];
    await run(['-i', silent, '-i', src, '-map', '0:v', '-map', '1:a?', ...vcodec, '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', out]);
    const cuts = plan.shots.filter(s => s.layout === 'cutout' && !(s.visual && s.visual.pose)).map(shotEngine);
    const poses = plan.shots.filter(s => s.visual && s.visual.pose).length, whips = whip ? (whip.match(/between/g) || []).length : 0;
    console.log(`✓ preview-composite.mp4 (${plan.shots.at(-1).t[1]} วิ · ${OW}×${OH} @ ${FPS} · ภาพ+การ์ดบนคลิปจริง${FC ? (useCutCard ? ` · ${SPEAKER}ตัดพื้นบนการ์ด` : ` · หน้า${SPEAKER}วงกลมบนการ์ด`) : ''}${cuts.length ? ` · cutout ตัดพื้นจริง (${[...new Set(cuts)].join('/')})` : ''}${poses ? ` · รูปนิ่ง ${poses} ช็อต` : ''}${whips ? ` · whip ${whips} รอยต่อ` : ''})`);
  } finally {
    try { rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* Windows may hold a file a moment longer */ }
  }
}

// ---------------------------------------------------------------- subs (burn the karaoke .ass on the composite · needs compose first)
// split shots: the speaker sits in the bottom half, so the normal subtitle band lands on the face → those lines get style "Split"
// with the baseline just under the seam (plan.split_sub_y %, default 59). Everything else keeps the .ass as made by engine.subs.
async function subs() {
  const ass = planPath(flag('ass') || plan.subs);   // --ass wins · relative paths are relative to the plan folder (env.sh cd's into the kit)
  if (!ass) throw new Error('subs ต้องมี plan.subs (path ไฟล์ .karaoke.ass เทียบกับโฟลเดอร์ของ plan · ได้จาก python -m engine.subs finalize) หรือ --ass <ไฟล์>');
  const comp = join(OUT, 'preview-composite.mp4');
  if (!existsSync(ass)) throw new Error('ไม่เจอไฟล์ซับ: ' + ass);
  if (!existsSync(comp)) throw new Error('ยังไม่มี preview-composite.mp4 — รัน thv.mjs compose ก่อน');
  const lines = readFileSync(ass, 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/);
  const playY = +((lines.find(l => l.startsWith('PlayResY:')) || 'PlayResY: 1280').split(':')[1]);
  const marginV = Math.round(playY * (1 - (plan.split_sub_y ?? 59) / 100));
  const splits = plan.shots.filter(s => s.layout === 'split').map(s => s.t);
  // shot.sub_y = baseline % for that shot only (e.g. an opening where the normal band sits on the mouth) → style "Y<n>"
  const custom = plan.shots.filter(s => s.sub_y != null), ys = [...new Set(custom.map(s => s.sub_y))];
  const sec = t => { const [h, m, s] = t.split(':'); return +h * 3600 + +m * 60 + +s; };
  let moved = 0;
  const out = lines.flatMap(l => {
    if (l.startsWith('Style: Default,')) { const f = l.split(','); const v = (name, m) => { const g = [...f]; g[0] = 'Style: ' + name; g[g.length - 2] = String(m); return g.join(','); };   // MarginV is the 2nd-last field
      return [l, v('Split', marginV), ...ys.map(y => v('Y' + y, Math.round(playY * (1 - y / 100))))]; }
    if (l.startsWith('Dialogue:')) { const f = l.split(','); const st = (sec(f[1]) + sec(f[2])) / 2;   // line midpoint decides (a line that starts 0.1 s before a cut belongs to the next shot)
      const c = custom.find(s => st >= s.t[0] && st < s.t[1]);
      if (c) { f[3] = 'Y' + c.sub_y; moved++; return [f.join(',')]; }
      if (splits.some(([a, b]) => st >= a && st < b)) { f[3] = 'Split'; moved++; return [f.join(',')]; } }
    return [l];
  });
  writeFileSync(join(OUT, 'composite.karaoke.ass'), out.join('\n'));
  // libass gets the bundled fonts from a copy next to the .ass: ffmpeg runs with cwd = OUT and bare relative names,
  // because the subtitles filter parses ':' and '\' (a Windows drive letter or a Thai path breaks the filter string)
  const fdir = join(OUT, 'fonts'); mkdirSync(fdir, { recursive: true });
  for (const f of readdirSync(fontsDir()).filter(n => /\.(ttf|otf)$/i.test(n))) copyFileSync(join(fontsDir(), f), join(fdir, f));
  const dst = join(OUT, 'preview-composite-subs.mp4'), small = join(OUT, 'preview-composite-720p.mp4');
  try {
    await run(['-i', comp, '-vf', `subtitles=composite.karaoke.ass:fontsdir=fonts,${TO709}`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p', ...TAG709, '-c:a', 'copy', '-movflags', '+faststart', dst], { cwd: OUT });
  } catch (e) { throw new Error(`${e.message} — ffmpeg นี้อาจไม่มี libass (ตัวกรอง subtitles) · พิมพ์ "ตรวจเครื่อง tad-clip"`); }
  await run(['-i', dst, '-vf', `scale=720:-2,${TO709}`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '26', '-pix_fmt', 'yuv420p', ...TAG709, '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', small]);
  console.log(`✓ preview-composite-subs.mp4 + preview-composite-720p.mp4 (ซับ ${moved} บรรทัดย้ายตำแหน่ง: ช็อต split ใต้รอยต่อ y ${plan.split_sub_y ?? 59}%${ys.length ? ' · sub_y รายช็อต ' + ys.join('/') + '%' : ''} · ที่เหลือตำแหน่งเดิม)`);
}

// ---------------------------------------------------------------- prompt blocks (optional · paste into any image tool, free ones included)
const STYLES = () => {
  const c = cfg.brand.colors || {};
  return {
    diorama: `Clean isometric 3D diorama illustration, soft matte materials, gentle studio light from the upper left, off-white background with ${c.primary || '#111827'} and ${c.accent || '#f97316'} accents, small stylised people with simple faces, tidy composition, generous empty space.`,
    collage: `Editorial paper-collage explainer illustration: black-and-white halftone photo cut-outs with clean white cut edges and soft drop shadows, layered on warm off-white textured paper (${c.paper || '#f5f5f0'}), a few flat paper shapes in ${c.primary || '#111827'} and one ${c.accent || '#f97316'} accent, subtle print grain and slight misregistration, calm magazine composition with generous empty paper.`,
    photo: `Candid documentary photograph in an editorial style, 35mm f/1.8 with shallow depth of field, lit by the one real light source named in the scene with a soft shadow on one side, muted Kodak Portra 400 colour grade with subtle film grain, lived-in real-world clutter. ${plan.people ? `Every person is ${plan.people}, ` : 'People are '}in ordinary everyday clothes, caught mid-motion and not looking at the camera, with visible skin pores, natural skin texture, natural facial asymmetry and flyaway hair strands — not models, NOT airbrushed, NOT HDR, NOT over-saturated.`,
  };
};
const COMPOSE_HINT = {
  split: 'Square 1:1 image. It will fill the top half of a vertical video, so keep the key subject centred with nothing important touching the edges.',
  cutout: 'Portrait 2:3 image. Keep the lower third calm and uncluttered (a presenter will stand there) and the top 15% clear for captions. Put the action in the middle band.',
  proof: 'Portrait 2:3 image.', 'full-card': 'Portrait 2:3 image.', 'full-speaker': 'Portrait 2:3 image.',
};
const NO_TEXT = 'Absolutely no text, letters, numbers, logos, signage words or UI text anywhere in the image.';
// same_as: [{ id, who }] → keep a character consistent with an earlier image in the same chat
const sameAs = p => [].concat(p.same_as || []).map(x => `${x.who[0].toUpperCase() + x.who.slice(1)} must look exactly like in the image named "${x.id}" earlier in this chat — same face, hairstyle and clothes.`).join(' ');

function prompts() {
  const P = plan.prompts || {}, usedBy = {}, S = STYLES();
  plan.shots.forEach(s => { const a = s.visual && s.visual.ai; if (a) (usedBy[a] ||= []).push(s); });
  let md = `# Image prompt blocks — ${plan.title || ''}\n\n` +
    `ไม่บังคับ — ใช้เมื่อไม่มีภาพจริงให้ช็อตนั้น · วางในเว็บ/แอปทำภาพที่คุณใช้อยู่ (ตัวฟรีก็ได้) **แชทใหม่ 1 แชทต่อคลิป** · วางทีละบล็อกตามลำดับ (รอภาพเสร็จก่อนวางบล็อกถัดไป · บรรทัดแรก \`[Image: ชื่อ]\` คือชื่อที่ภาพหลังๆ อ้างถึง ห้ามลบ) · บล็อกที่เขียนว่า **แก้ภาพก่อนหน้า** ให้ส่งในแชทเดิมต่อจากภาพนั้น (ภาพจะได้ต่อเนื่อง)\n` +
    `บันทึกภาพเป็น \`assets/ai/<id>.png\` แล้วรัน \`thv.mjs motion\` อีกรอบ · ห้ามให้ AI เขียนตัวหนังสือไทยในภาพ — ข้อความทุกคำมาจากการ์ด\n\nสไตล์ทั้งคลิป: **${plan.style || '-'}** (1 คลิป 1 ภาษาภาพ)\n\n`;
  for (const [id, p] of Object.entries(P)) {
    const shots = usedBy[id] || []; const layout = (shots[0] || {}).layout || 'cutout';
    const body = p.edit_of
      ? `[Image: ${id}] Edit the previous image (${p.edit_of}). Keep EVERYTHING identical — camera angle, lighting, layout, colours and every existing object and person. Change only this: ${p.scene} ${sameAs(p)} ${NO_TEXT}`
      : `[Image: ${id}] ${S[p.style || plan.style] || ''} ${p.scene} ${sameAs(p)} ${COMPOSE_HINT[layout] || ''} ${NO_TEXT}`;
    md += `## ${id}${p.edit_of ? ' · แก้ภาพก่อนหน้า' : ''}\nใช้ในช็อต: ${shots.map(s => s.id).join(', ') || '—'} · layout ${layout}\n\n\`\`\`\n${body.replace(/\s+/g, ' ').trim()}\n\`\`\`\n\n`;
  }
  writeFileSync(join(OUT, 'prompts.md'), md);
  console.log(`✓ prompts.md (${Object.keys(P).length} บล็อก)`);
}

// ---------------------------------------------------------------- CapCut edit sheet
const HOW = {
  split: s => `แทร็กหลัก = วิดีโอ${SPEAKER} เลื่อนลงให้หน้าอยู่ครึ่งล่าง · แทร็กบน = \`${s.id}-split.mp4\` → Chroma key สีเขียว (ครึ่งล่างจะเห็น${SPEAKER})`,
  cutout: s => s.visual && s.visual.pose ? `\`${s.id}-cutout.mp4\` เต็มจอ (ฉาก + พื้นเวที + รูปนิ่งของ${SPEAKER}อยู่ในไฟล์แล้ว · เสียงพูดเดินต่อ)` : `แทร็กหลัก = \`${s.id}-cutout.mp4\` (ภาพเต็มจอ) · แทร็กบน = ${SPEAKER}ตัดพื้นแล้ว (\`python -m engine.cutout <คลิป> <ชื่อ>.mov\` · compose ทำ \`cutout/${s.id}-*.webm\` ไว้แล้ว) → ย่อ ~${Math.round((s.cutout_scale ?? (plan.cutout || {}).scale ?? 0.62) * 100)}% ชิดล่างกลาง`,
  'full-card': s => `\`${s.id}-full-card.mp4\` เต็มจอ ทับภาพ${SPEAKER} (เสียงพูดเดินต่อ)`,
  'full-speaker': s => `วิดีโอ${SPEAKER}เต็มจอ${(s.overlays || []).length ? ` · แทร็กบน = \`${s.id}-full-speaker.mp4\` → Chroma key สีเขียว` : ''}`,
  proof: s => `แทร็กหลัก = ของจริง: ${(s.visual || {}).proof || ''}${(s.overlays || []).length ? ` · แทร็กบน = \`${s.id}-proof.mp4\` → Chroma key` : ''}`,
};
// sound cues for look vox (CapCut › Audio › Sound effects) — quiet, under the voice; music changes only at chapter / turn
function sfx(s) {
  const c = (s.visual || {}).card || {}, o = (s.overlays || []).map(x => x.type), out = [];
  if (s.beat === 'turn' || c.type === 'chapter') out.push('ดนตรีหยุด/เปลี่ยนท่อน');
  if (c.type === 'doc' || (s.visual || {}).treat === 'print') out.push('กระดาษสไลด์');
  if (o.includes('highlight') || (c.type === 'doc')) out.push('ปากกาไฮไลต์');
  if (o.includes('annotate')) out.push('ปากกามาร์กเกอร์');
  if (c.type === 'chart' || c.type === 'timeline') out.push('whoosh เบา');
  if (c.type === 'stat') out.push('tick เบา');
  return out.join(' · ') || '—';
}
function sheet() {
  const vox = plan.look === 'vox';
  let md = `# CapCut edit sheet — ${plan.title || ''}\n\nความยาว ${plan.shots.at(-1).t[1]} วิ · ${plan.shots.length} ช็อต · ไฟล์อยู่ใน \`shots/\` · ดูภาพรวมที่ \`storyboard.png\` · ถ้าไม่อยากวางเอง: \`thv.mjs compose\` ประกอบให้เป็น \`preview-composite.mp4\`\n\n` +
    `| ช็อต | เวลา | layout | วางยังไงใน CapCut | เสียงพูดช่วงนี้ |${vox ? ' SFX (Vox) |' : ''}\n|---|---|---|---|---|${vox ? '---|' : ''}\n`;
  plan.shots.forEach(s => { md += `| ${s.id} | ${s.t[0]}–${s.t[1]} | ${s.layout} | ${(HOW[s.layout] || (() => ''))(s)} | ${(s.say || '').replace(/\|/g, '/')} |${vox ? ` ${sfx(s)} |` : ''}\n`; });
  if (vox) md += `\n**Vox:** ช็อตกราฟิกต่อกันใช้ transition **Push/Slide + Blur** สั้น 6–8 เฟรม (ไม่ใช้ dissolve) · ดนตรีคลอใต้เสียง (-22 ถึง -26 dB) เปลี่ยนท่อนเฉพาะตรง chapter/turn · SFX ทุกตัวเบากว่าเสียงพูด\n`;
  md += `\n**ทั้งคลิป:** ซับตำแหน่งเดียวกันทุกช็อต (กลางค่อนล่าง เหนือโซนปุ่มของแอป) ยกเว้นช็อต split ที่ซับย้ายไปใต้รอยต่อ · ไฟล์ .png ใน shots/ = ภาพนิ่งของช็อตเดียวกัน (พื้นใส) ใช้แทน .mp4 ได้ถ้าไม่ต้องการให้ขยับ\n`;
  writeFileSync(join(OUT, 'edit-sheet.md'), md);
  console.log('✓ edit-sheet.md');
}

// ---------------------------------------------------------------- main
export async function main(argv) {
  const [cmd, planArg, ...rest] = argv;
  if (!COMMANDS.includes(cmd) || !planArg) {
    console.error(`Usage: node thv.mjs <${COMMANDS.join('|')}> <plan.json> [--shot ID] [--matte green|alpha] [--ass file] [--motion] [--force]`);
    return 1;
  }
  const ctx = load(planArg, rest);
  mkdirSync(OUT, { recursive: true });
  const r = check();
  r.warns.forEach(w => console.log('⚠️  ' + w));
  r.errs.forEach(e => console.log('❌ ' + e));
  console.log(`check: ${r.shots} ช็อต · ${r.seconds} วิ · ${r.errs.length} error · ${r.warns.length} warning · ${ctx.preset} ${Math.round(W * K)}×${Math.round(H * K)} @ ${FPS} fps · cutout ${ENGINE}`);
  if (cmd === 'check') return r.ok ? 0 : 1;
  if (!r.ok) { console.error('แก้ error ก่อน'); return 1; }
  if (cmd === 'prompts' || cmd === 'all') prompts();
  if (cmd === 'sheet' || cmd === 'all') sheet();
  if (cmd === 'compose') await compose();
  if (cmd === 'subs') await subs();
  if (['board', 'cards', 'motion', 'animatic', 'loose', 'all'].includes(cmd)) {
    const B = { cur: await openBrowser() };   // holder: perShot swaps in a fresh Chrome when one hangs
    try {
      if (cmd === 'board' || cmd === 'all') await board(B.cur);
      if (cmd === 'cards' || cmd === 'all') await cards(B);
      if (cmd === 'motion' || (cmd === 'all' && rest.includes('--motion'))) await motion(B);
      if (cmd === 'animatic') await animatic(B.cur);
      if (cmd === 'loose') await loose(B);
    } finally { await B.cur.close(); }
  }
  console.log(`→ ${OUT}`);
  return 0;
}

const isMain = (() => { try { return !!process.argv[1] && realpathSync(resolve(process.argv[1])) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } })();
if (isMain) {
  const code = await main(process.argv.slice(2)).catch(e => { console.error('❌ ' + (e && e.message || e)); return 1; });
  process.exit(code);
}
