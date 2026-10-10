// Shared helpers for the Node side of the tad-clip engine — mirrors engine/kit.py.
import { existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

export const KIT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const IS_WINDOWS = platform() === 'win32';
export const IS_MAC = platform() === 'darwin';

export const DEFAULTS = {
  version: 1,
  speaker: { name: '', handle: '' },
  brand: { preset: 'neutral', font: 'Prompt', colors: {} },
  subtitle: { highlight: '#FF7F50', size: 60, margin_v: 340, max_chars: 22 },
  output: { preset: '1080p', safe_zone: 'tiktok' },
  whisper: { model: 'large-v3', device: 'auto', compute_type: 'auto' },
  library_dir: null,
  glossary: { fixes: [], glue: [], protect: [], prompt_hint: '' },
  cutout: { engine: 'auto' },
  tools: {},
};

const WHICH = {
  ffmpeg: ['ffmpeg'], ffprobe: ['ffprobe'], node: ['node'], python: ['python3', 'python'],
  chrome: ['chrome-headless-shell', 'google-chrome', 'chromium', 'msedge'], 'yt-dlp': ['yt-dlp'], uv: ['uv'],
};
const FIX_HINT = 'พิมพ์ "ตรวจเครื่อง tad-clip" ให้ Claude ติดตั้งส่วนที่ขาดให้';

export class KitError extends Error {}

export function home() {
  const p = resolve(process.env.TAD_CLIP_HOME || join(homedir(), '.tad-clip'));
  mkdirSync(p, { recursive: true });
  return p;
}
export const cacheDir = () => { const p = join(home(), 'cache'); mkdirSync(p, { recursive: true }); return p; };
export const fontsDir = () => join(KIT_ROOT, 'assets', 'fonts');
export const venvPython = () => join(home(), 'venv', ...(IS_WINDOWS ? ['Scripts', 'python.exe'] : ['bin', 'python']));

const isObj = v => v && typeof v === 'object' && !Array.isArray(v);
function merge(base, over) {
  const out = structuredClone(base);
  for (const [k, v] of Object.entries(over || {})) out[k] = isObj(v) && isObj(out[k]) ? merge(out[k], v) : v;
  return out;
}

export function presetColors(name) {
  let f = join(KIT_ROOT, 'presets', `${name}.json`);
  if (!existsSync(f)) f = join(KIT_ROOT, 'presets', 'neutral.json');
  return JSON.parse(readFileSync(f, 'utf8')).colors;
}

export function loadConfig() {
  const f = join(home(), 'config.json');
  const user = existsSync(f) ? JSON.parse(readFileSync(f, 'utf8')) : {};
  const cfg = merge(DEFAULTS, user);
  cfg.brand.colors = { ...presetColors(cfg.brand.preset), ...(user.brand?.colors || {}) };
  return cfg;
}

function which(exe) {
  try {
    const out = execFileSync(IS_WINDOWS ? 'where' : 'which', [exe], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    return out.split(/\r?\n/).find(Boolean) || null;
  } catch { return null; }
}

export function tool(name) {
  const configured = loadConfig().tools?.[name];
  if (configured && existsSync(configured)) return configured;
  if (name === 'python' && existsSync(venvPython())) return venvPython();
  const localBin = join(home(), 'bin');
  for (const exe of WHICH[name] || [name]) {
    for (const cand of [...(IS_WINDOWS ? [join(localBin, exe + '.exe')] : []), join(localBin, exe)]) if (existsSync(cand)) return cand;
    const found = which(exe);
    if (found) return found;
  }
  throw new KitError(`ไม่พบโปรแกรม ${name} ในเครื่อง — ${FIX_HINT}`);
}

const WEIGHTS = { Thin: 100, ExtraLight: 200, Light: 300, Regular: 400, Medium: 500, SemiBold: 600, Bold: 700, ExtraBold: 800, Black: 900 };

// CSS injected into cards.html: bundled fonts + brand colour tokens.
export function brandCss(cfg = loadConfig()) {
  const faces = readdirSync(fontsDir()).filter(f => /\.(ttf|otf)$/i.test(f)).map(f => {
    const [family, style = 'Regular'] = f.replace(/\.(ttf|otf)$/i, '').split('-');
    const url = 'file://' + join(fontsDir(), f).replace(/\\/g, '/').replace(/^(?=[A-Za-z]:)/, '/');
    return `@font-face{font-family:"${family}";src:url("${encodeURI(url)}");font-weight:${WEIGHTS[style] ?? 400};font-style:normal;}`;
  });
  const c = cfg.brand.colors;
  const vars = { primary: c.primary, 'primary-deep': c.primary_deep, 'primary-mid': c.primary_mid, accent: c.accent, accent2: c.accent2, paper: c.paper, ink: c.ink };
  const root = Object.entries(vars).filter(([, v]) => v).map(([k, v]) => `--${k}: ${v};`).join(' ');
  return `${faces.join('\n')}\n:root { ${root} }\nbody { font-family: "${cfg.brand.font}", sans-serif; }\n`;
}
