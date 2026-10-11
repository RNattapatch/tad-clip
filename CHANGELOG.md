# Changelog

## 1.0.1 — 2026-10-11
- Subtitles: a weak transcription is no longer retried when faster-whisper never sent the Thai hint (models smaller than large-v3, i.e. Windows defaults). The retry only repeated the same decode, about 2 minutes on CPU for medium. Mac and large-v3 keep it.
- Docs: the `prompt_hint` notes now say when the hint is used (Mac and large-v3) and point to `glossary.fixes` for misheard words with smaller models.
- Skill: the re-cut step now runs `doctor.py --probe` with the `roughcut.mp4` file it needs.
- Windows: `engine/env.sh` runs its python call with `PYTHONUTF8=1`, so tool paths under a non-ASCII home folder print correctly.
- Setup: the default model is now always `large-v3`, also on machines with less than 8 GB RAM (int8 needs about 3 GB). `medium` is no longer picked automatically: its Thai varies by CPU (the same fixed-seed run gave 8 good segments, then one mixed-script segment). `--model medium` still works as an explicit last resort. Doctor's low-RAM warning now says to close other apps instead of recommending `medium`, and a configured `medium` shows a warning that Thai accuracy is noticeably worse. Docs, skill and CI (Windows installs and smokes `large-v3`) match.

## 1.0.0 — 2026-10-11
- First public release: rough cut (retakes + silences), Thai karaoke subtitles, inserts (real photos/footage, memes, SFX, text and motion cards, split screen, cut-out), Vox-style explainer look, 1080p or 2K60 output, CapCut hand-off sheet.
- Runs on macOS and Windows. Free tools only: faster-whisper, ffmpeg, headless Chrome/Edge, Apple Vision (Mac).
