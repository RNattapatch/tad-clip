# Test fixtures

`คลิป ทดสอบ.mp4` — 21.6 s, HEVC 10-bit, 720×1280 @ 60 fps, AAC. A real phone take of a Thai talking head
(cut from 9.4–31.0 s of the author's raw clip). The file name has Thai characters and a space on purpose.

What a correct rough cut drops (times are on this fixture):

| Time (s) | What is said | Expected |
|---|---|---|
| ~2.2–4.6 | first try of "และนี่คือหลักคิด…" | drop (retake follows) |
| ~5.1–9.3 | full take of the same sentence | keep |
| ~17.4–18.9 | first try of "ผลจากกระทะรุ่นใหม่ Black Pan" | drop (retake follows) |
| ~19.4 | 0.1 s blip | drop (noise) |
| ~19.6–21.4 | retake | keep |

`asr-sample.json` (added by the subtitle task) is faster-whisper output for this fixture in whisper.cpp `-ojf` shape.
