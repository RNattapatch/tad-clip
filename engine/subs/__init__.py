"""Thai subtitles for tad-clip: ASR words -> short readable cues -> SRT / ASS / LRC / karaoke ASS.

Run with the kit python from the kit root:
    python -m engine.subs <video> -o <dir> [--karaoke] [--max-chars N]    -> raw cues + review.md
    python -m engine.subs finalize <dir> [--edits edits.json]             -> final subtitle files
    python -m engine.subs patch <dir> --from SEC [--to SEC]                -> re-transcribe a stretch (snaps to silence)
    python -m engine.subs pauses <dir> [--around SEC]                      -> list silences, safe places to cut
    python -m engine.subs burn <video> <ass> -o <out.mp4>                  -> burn subtitles with ffmpeg
"""
