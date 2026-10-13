# Changelog

## 1.1.0 — 2026-10-13
- Visuals now come in two modes. `cutaway` (the default, and every plan written before) is the existing style: the speaker is the anchor, the video cuts away to real material, memes or cards and comes back. `stage` is new: the speaker is cut out and stands on one shared floor in every scene, the photo backdrop changes every 1–2 s, photo cards sit on grid paper, and every cut gets a short horizontal blur. Set it with `"mode": "stage"` in plan.json, or ask for "stage" / "ยืนบนฉาก". User guide: `docs/09-modes.md` · spec: `engine/visual/references/stage-style.md`.
- New plan fields: `visual.image` (any image file as the picture of a shot, every layout and mode) · card `grid` (light/dark grid paper, one photo or a sliding carousel, round badge, big number with source, text lines) · `stage_floor`, `floor_top`, `visual.stage: false` · `visual.pose` (a still cut-out photo of the speaker instead of the live clip, works without a cut-out engine) · `whip` + shot `whip: false` (blur across cuts, done with ffmpeg `gblur`) · `cutout.fade_side` (fade the side edges of a half-body cut-out) · `stage_sub_y`.
- `thv check` for stage: opening shots hold ≤2 s, average >2.5 s per picture warns (a carousel photo counts as one picture), a missing stage floor warns, a grid number without a source warns, missing image / pose / floor files are errors. An unknown `mode` is an error.
- Cut-out of still photos: `python -m engine.cutout photo.jpg pose.png` gives a transparent PNG cropped to the person, with the phone's EXIF rotation applied (Apple Vision on Mac, transparent-background on Windows). iPhone `.heic` photos work on Mac; on Windows convert them to JPG first.
- Fix: Apple Vision returned an empty matte for the first frame of every clip, so each cut-out shot opened with one frame without the speaker. Frame 0 now uses frame 1's matte. The Vision helper rebuilds itself on first use after the update.
- Example: `engine/visual/examples/stage-demo.plan.json` with drawn graphics only (no real people) · storyboard in `docs/images/stage-storyboard.png`.
- Skills: the orchestrator asks which mode when you choose full visuals; the visual skill defaults to cutaway and says so in one line.

## 1.0.1 — 2026-10-11
- Subtitles: a weak transcription is no longer retried when faster-whisper never sent the Thai hint (models smaller than large-v3, i.e. Windows defaults). The retry only repeated the same decode, about 2 minutes on CPU for medium. Mac and large-v3 keep it.
- Docs: the `prompt_hint` notes now say when the hint is used (Mac and large-v3) and point to `glossary.fixes` for misheard words with smaller models.
- Skill: the re-cut step now runs `doctor.py --probe` with the `roughcut.mp4` file it needs.
- Windows: `engine/env.sh` runs its python call with `PYTHONUTF8=1`, so tool paths under a non-ASCII home folder print correctly.
- Setup: the default model is now always `large-v3`, also on machines with less than 8 GB RAM (int8 needs about 3 GB). `medium` is no longer picked automatically: its Thai varies by CPU (the same fixed-seed run gave 8 good segments, then one mixed-script segment). `--model medium` still works as an explicit last resort. Doctor's low-RAM warning now says to close other apps instead of recommending `medium`, and a configured `medium` shows a warning that Thai accuracy is noticeably worse. Docs, skill and CI (Windows installs and smokes `large-v3`) match.

## 1.0.0 — 2026-10-11
- First public release: rough cut (retakes + silences), Thai karaoke subtitles, inserts (real photos/footage, memes, SFX, text and motion cards, split screen, cut-out), Vox-style explainer look, 1080p or 2K60 output, CapCut hand-off sheet.
- Runs on macOS and Windows. Free tools only: faster-whisper, ffmpeg, headless Chrome/Edge, Apple Vision (Mac).
