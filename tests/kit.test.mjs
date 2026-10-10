import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

process.env.TAD_CLIP_HOME = mkdtempSync(join(tmpdir(), 'tad clip ทดสอบ-'));
const kit = await import('../engine/kit.mjs');

test('loadConfig honours TAD_CLIP_HOME and preset', () => {
  writeFileSync(join(process.env.TAD_CLIP_HOME, 'config.json'), JSON.stringify({ brand: { preset: 'paper' } }));
  const cfg = kit.loadConfig();
  assert.equal(kit.home(), process.env.TAD_CLIP_HOME);
  assert.equal(cfg.brand.colors.accent, '#e4572e');
  assert.equal(cfg.subtitle.max_chars, 22);
});

test('brandCss has bundled Prompt faces and colour tokens', () => {
  writeFileSync(join(process.env.TAD_CLIP_HOME, 'config.json'), JSON.stringify({}));
  const css = kit.brandCss();
  assert.match(css, /@font-face\{font-family:"Prompt";src:url\("file:\/\/.*Prompt-Bold\.ttf"\);font-weight:700/);
  assert.match(css, /--accent: #f97316;/);
  assert.match(css, /body \{ font-family: "Prompt", sans-serif; \}/);
});

test('tool throws a Thai KitError for unknown tools', () => {
  assert.throws(() => kit.tool('definitely-not-a-tool-xyz'), /ไม่พบโปรแกรม definitely-not-a-tool-xyz/);
});
