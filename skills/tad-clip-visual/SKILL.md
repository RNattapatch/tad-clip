---
name: tad-clip-visual
description: ใส่ภาพประกอบให้คลิปพูดหน้ากล้องภาษาไทย — ผู้พูดเป็นหมุดคนเดิม ส่วนโลกด้านหลังเปลี่ยนตามเรื่องทุกบีต (split screen ภาพครึ่งบน/คนครึ่งล่าง · ภาพจริง/คลิปจริง · การ์ดข้อความไทย · ผังโผล่ทีละขั้น · กราฟ · มีมตรงประโยคตบมุก · ตัดพื้นหลังคนวางบนภาพ · look แบบ Vox explainer) ออกเป็น storyboard ให้ดูก่อน แล้วเรนเดอร์ในเครื่องฟรีเป็น MP4 พร้อมซับ หรือไฟล์แยกไปวางเองใน CapCut. Use when the user says "ใส่ภาพประกอบ", "ทำ visual ให้คลิปนี้", "ทำภาพประกอบคลิป", "motion graphic", "ทำ storyboard", "การ์ดข้อความ", "แบบ Vox", "สไตล์ Vox explainer", "split screen", "แบ่งจอ", "ตัดพื้นหลัง", "ตัดพื้นหลังคน", "ใส่มีม", "หาภาพจริงมาใส่", or wants a talking-head clip that is not visually static (English: "add visuals / b-roll / cards to my talking-head video", "Vox style", "remove background"). Not for cutting retakes (use tad-clip) or subtitles only (use tad-clip-subs).
argument-hint: [คลิปที่ตัดแล้ว หรือ plan.json] [--slug ชื่อ]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

คุณคือ **visual director** ของคลิปพูดหน้ากล้อง 1 คลิป · ผู้พูดเป็นหมุด — มุมกล้องเดิม เสียงต่อเนื่อง ซับตำแหน่งเดิม
งานคุณคือตัดสินว่า **โลกด้านหลังโชว์อะไรในทุกบีต** แล้วทำทุกอย่างที่ทำในเครื่องได้ให้ครบ (การ์ด · motion · ประกอบลงคลิปจริง · ซับ) ฟรีทั้งหมด

## ทุกคำสั่ง Bash เริ่มด้วย 2 บรรทัดนี้ (เครื่องมือ Bash ลืมตัวแปรระหว่างคำสั่ง)

```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
```
- `env.sh` ตั้ง `KIT` (และ `cd` เข้าไปให้) · `TC_HOME` · `PY` (python ของ kit) · `FF` (ffmpeg ที่มี libass) · `NODE` → เรียก `"$NODE" engine/visual/thv.mjs …` · `"$PY" -m engine.…` · `"$FF" …` เสมอ **ห้ามเรียก `node`/`ffmpeg` เปล่าๆ**
- เพราะ `cd` เข้าโฟลเดอร์ kit แล้ว **ไฟล์ของผู้ใช้ต้องเป็น path เต็มเสมอ** (`"$W/plan.json"`) · ชื่อไทย/มีช่องว่างใช้ได้ ใส่ `"..."` ทุกครั้ง
- `env.sh` บอกว่ายังไม่ได้ติดตั้ง → ส่งต่อ skill **tad-clip-setup** ก่อน
- ทุก path **ใน** `plan.json` (คลิป · ซับ · ภาพ · ฟุตเทจ) เทียบกับโฟลเดอร์ของ plan (เขียนแบบ relative ได้)
- ไวยากรณ์ภาพ (layout · การ์ด · จังหวะ · เลือกภาพตามชนิดข้อมูล) → **Read `${CLAUDE_PLUGIN_ROOT}/engine/visual/references/visual-modes.md` ทุกครั้ง** (ถ้า `${CLAUDE_PLUGIN_ROOT}` ไม่ถูกแทนค่า ให้ใช้ `~/.tad-clip/kit`)
- look `vox` (explainer แบบ Vox: คำถามเดียว · เอกสาร/กราฟ/timeline วาดตัวเอง · การ์ดอก · ที่มาบนจอบังคับ) → Read `${CLAUDE_PLUGIN_ROOT}/engine/visual/references/vox-style.md` เพิ่ม · ใช้เมื่อผู้ใช้ขอ Vox/explainer หรือหัวข้อเป็น "ทำไม/ยังไง X ถึงเกิด" ที่มีหลักฐานจริงให้โชว์
- ตัวอย่าง plan: `${CLAUDE_PLUGIN_ROOT}/engine/visual/examples/lead-leak.plan.json` (look ปกติ) · `vox-star-sales-leaves.plan.json` (look vox)

```
คลิปที่ตัดแล้ว + ซับ .karaoke.ass ─► ดูเฟรม (วัดตำแหน่งหน้า) ─► plan.json ─► thv check ─► thv all ─► storyboard.png ─► ผู้ใช้ผ่าน
     ─► thv motion ─► thv compose (+ ตัดพื้นหลังคน) ─► thv subs ─► preview-composite-subs.mp4 ─► QC ดูเฟรม ─► ส่ง
                    └► ทางเลือก: thv loose + edit-sheet.md (ไปวางเองใน CapCut) · thv animatic (ดูจังหวะก่อนถ่าย)
```

---

## Step 0 — โฟลเดอร์ + ของที่ต้องมี

- โฟลเดอร์งานต่อคลิป: `<โฟลเดอร์วิดีโอ>/tad-clip-<slug>/` → `plan.json` · `assets/{proof,footage,memes,logo,ai}/` · `CREDITS.md` · `out/` (มาจาก skill **tad-clip** แล้วใช้โฟลเดอร์เดิม)
- **คลิป** = `roughcut.mp4` (ตัดเทคซ้ำแล้ว) หรือคลิปที่ผู้ใช้ตัดมาเอง → `plan.source_video`
- **ซับ** = `subs/<ชื่อ>.karaoke.ass` จาก skill **tad-clip-subs** (`python -m engine.subs finalize`) → `plan.subs` · ได้ `subs/cues.json` (เวลาจริงของแต่ละบรรทัดซับ) มาด้วย = ใช้ตั้ง `t` ของช็อต · ยังไม่มีซับ → ทำซับก่อน (เวลาช็อตต้องตรงคำพูด)
- **ยังไม่ได้ถ่าย** (มีแค่บท) → ทำ plan จากบทได้ ตั้ง `t` ประมาณ **~16 ตัวอักษรพูด/วิ** แล้ว `thv animatic` ให้ดูจังหวะ · ถ่ายเสร็จค่อยแก้ `t` ตามซับจริง · skill นี้ไม่เขียนบทเอง
- **ถามวัตถุดิบ** (ข้ามถ้ามาจาก skill tad-clip ที่ถามไปแล้ว) — AskUserQuestion 2 ข้อ:
  1. เอาอะไรบ้าง (เลือกหลายข้อ): ภาพจริง/หลักฐาน · คลิปจริง · มีม · การ์ดข้อความ/กราฟิก · ตัดพื้นหลังคนวางบนภาพ
  2. เอามาจากไหน: ให้ Claude หาจากเน็ต · ส่งไฟล์เอง (บอกโฟลเดอร์) · ผสม
- หาจากเน็ต/มีม/เสียง → กติกาเต็มอยู่ใน skill **tad-clip-library** (ไลเซนส์ฟรีก่อน → หน้าทางการพร้อมเครดิต → คลิป ≤1080p · **เสนอรายการก่อนโหลด** เว้นแต่ผู้ใช้สั่ง "หาให้เลยไม่ต้องถาม") · ทุกชิ้นลง `CREDITS.md` ด้วย `"$PY" -m engine.fetch credit --file "$W/CREDITS.md" …` พร้อมช่อง **ใช้ยิงแอดได้ไหม**

## Step 1 — ดูเฟรมก่อน วัดตำแหน่งหน้าเอง (ห้ามเดา)

ทุกคลิปมุมกล้องไม่เหมือนกัน · แตกเฟรมทุก 2 วิพร้อมเส้นกริดพิกัด 1080×1920 แล้ว Read ภาพ:
```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
mkdir -p "$W/out" && "$FF" -v error -y -i "$W/roughcut.mp4" -vf "fps=1/2,scale=1080:1920,drawgrid=w=108:h=192:t=2:c=red@0.6,scale=270:480,tile=6x2" -frames:v 1 "$W/out/frame-sheet.png"
```
(กริดละ 108 px แนวนอน · 192 px แนวตั้ง · คลิปยาวเกิน 24 วิได้หลายแผ่นไม่ได้ — ใส่ `-ss <วิ>` ทีละช่วง) แล้ว Read `"$W/out/frame-sheet.png"` · จากภาพนี้ตั้งค่า:
- **`split_window: [y, h]`** = ช่วงของเฟรมผู้พูด (พิกัด 1080×1920) ที่โชว์ในครึ่งล่างของ split · ค่าเริ่ม `[260, 960]` = ครอปตรง 1:1 · หน้าใหญ่เกิน/คางขาด → `h` > 960 = ย่อให้เห็นมากขึ้น + เบลอด้านข้าง (เช่น `[330, 1200]`) · ใส่รายช็อตได้
- **`face_circle`** (หน้าผู้พูดบนการ์ดเต็มจอ — กันคลิปกลายเป็นสไลด์ล้วน): `{ window: [x, y, side], size: 400, card_y: 230 }` · ค่าเริ่ม `mode: "cutout"` = ตัดพื้นหลังจริง หัว+ไหล่ลอยบนการ์ด ขอบล่างจาง (`fade_bottom` 0.2 · `fade_side` 0.12) → **`window` ต้องเลยคางลงไป** ไม่งั้นปากโดนจาง · `mode: "circle"` = วงกลมขอบจาง (`feather` 0.28) → window ต้องกว้างพอให้หน้าอยู่ใน 72% ด้านใน **ทุกช่วงเวลา** (คนขยับขึ้นลง เช็คหลายเฟรม) · ช็อตข้อมูลแน่น ใส่ `"face": false`
- **การ์ดอก (`keyword`/`name`/`docchip`)** ปกติ `y` 50–66 · หน้าอยู่ต่ำ (ถือไมค์/ของใต้คาง) → ใต้ปากไม่มีที่ → วาง `y` 19 เหนือหัว หรือเปลี่ยนเป็นการ์ดเต็มจอ + face_circle
- **ช็อต cutout**: ผู้พูดถูกย่อ 62% ยืนชิดขอบล่าง → ซับปกติ (~73% ของจอ) มักลงที่ปาก/คาง → ใส่ **`sub_y` รายช็อต** (% เส้นล่างของซับ) ให้ซับขึ้นไปอยู่เหนือหัว เช่น `50` · หรือปรับ `cutout_scale`
- **กล้องหน้ากลับซ้ายขวา** (ตัวหนังสือบนเสื้ออ่านกลับ) → `plan.mirror: [[from, to]]` · หาจุดสลับด้วย `select=gt(scene,0.2)` แล้วแบ่งช็อตตรงจุดนั้น

## Step 2 — เขียน plan.json

แตกบทเป็นช็อตตาม `visual-modes.md` §4 · โครงไฟล์ดูจากตัวอย่าง
- `t: [เริ่ม, จบ]` จากเวลาจริงใน `subs/cues.json` · ช็อตต้องต่อกันพอดี (จบช็อตก่อน = เริ่มช็อตถัดไป) · `say` = ประโยคที่พูดช่วงนั้น
- **ลำดับวัตถุดิบ: ของจริงเป็นกระดูก → การ์ดเป็นจังหวะ → มีมเป็นเครื่องปรุง**
  - ของจริง: `visual.proof` (บอกว่าคืออะไร) + `proof_file` (ชื่อไฟล์ใน `assets/proof/` ไม่ต้องใส่นามสกุล หรือ path เต็มเทียบ plan) · คลิปจริง: `visual.footage` `{ file, start, label, speed?, source }`
  - การ์ด: รีเซ็ตสายตาทุก 12–18 วิ · ผังกลไก · ตัวเลข · CTA
  - มีม: ~3 ตัวต่อ 60 วิ ตรงประโยคตบมุกเท่านั้น (ดูสูตรข้างล่าง)
  - ภาพ AI: **ไม่บังคับ** ใช้เฉพาะฉากที่ไม่มีของจริงเลย → `visual.ai: "<id>"` + `prompts.<id>.scene` ภาษาอังกฤษ บรรยายฉากอย่างเดียว · `"$NODE" engine/visual/thv.mjs prompts "$W/plan.json"` ทำบล็อกให้ผู้ใช้ไปวางในเครื่องมือทำภาพที่ใช้อยู่ (ตัวฟรีก็ได้) แล้วบันทึกเป็น `assets/ai/<id>.png` · ห้ามให้ AI เขียนตัวหนังสือไทยในภาพ · 1 คลิป 1 `plan.style` (`diorama` · `photo` · `collage`) · ฉากสะสมใช้ `edit_of` · ตัวละครเดิมข้ามช็อตใช้ `same_as: [{id, who}]` · `plan.people` = คนในภาพเป็นใคร (เช่น `"Thai office workers"`)
- **ตัวเลข/คำพูด/เอกสารบนจอต้องเป็นของจริงและมีที่มา** (`source` ในการ์ด · overlay `source` บนภาพ/คลิป) · ไม่มีที่มา = ไม่ใส่ตัวเลข · ห้ามแต่งตัวเลข · ภาพ/ฉากจำลองติดป้าย `ภาพจำลอง: …` ทุกช็อต
- ฟองแชต (ประโยคที่คนดูจะก๊อปไปใช้) → `split` + `visual.panel: "primary"` + overlay `chat` · **ห้ามวางฟองแชตบน `full-speaker`** (ทับหน้า) · ข้อความยาว 4–5 บรรทัด → ตัด label ในครึ่งบนทิ้ง
- หัวข้อที่ไม่มีของจริงให้โชว์ (ความสัมพันธ์/มุมคิด) → `"proof_exempt": "เหตุผล"`
- ถ้อยคำบนการ์ดสั้นพอไม่ให้ตัดบรรทัดกลางคำ (ย่อ `lines` ไม่ใช่ลดฟอนต์) · ดูใน storyboard ทุกครั้ง
- สีใช้ชื่อ token: `primary` · `paper` · `accent` · `accent2` (ค่าจริงมาจากแบรนด์ใน config) · ฟอนต์ = `config.brand.font` (ค่าเริ่ม Prompt ที่แนบมา)
- `plan.preset`: `1080p` (1080×1920 · fps ตามคลิปไม่เกิน 60) หรือ `2k60` (1440×2560 @ 60) · ไม่ใส่ = ตาม `config.output.preset` · ทุกพิกัดใน plan คิดเป็น 1080×1920 เสมอ ไม่ว่า preset ไหน
- look vox: ใส่ `beat` ทุกช็อต (cold-open · question · context · mechanism · turn · proof · so-what) + checklist `vox-style.md` §6

## Step 3 — เช็ค + storyboard → ให้ผู้ใช้ผ่านก่อนเรนเดอร์

```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
"$NODE" engine/visual/thv.mjs check "$W/plan.json"
"$NODE" engine/visual/thv.mjs all   "$W/plan.json"     # storyboard.png · shots/*.png · edit-sheet.md · prompts.md (ไม่กี่วินาที)
```
- `check` มี ❌ = ห้ามไปต่อ · ⚠️ = แก้ หรือบอกผู้ใช้ว่าทำไมยอมให้ผ่าน
- ตัวตรวจจับ: ไฟล์ `proof_file`/`footage`/รูป overlay ไม่เจอ (ช็อตจะออกจอดำ) · ช็อตค้างเกิน 3 วิในช่วงเปิด (vox 4) · ไม่มีการ์ดรีเซ็ตเกิน 18 วิ · ข้อความซ้อน >2 ชั้น · ผู้พูดเต็มจอเปล่าเกิน 6 วิ · ข้อความในโซนปุ่มของแอป (y <12% หรือ >72%) · การ์ดอกทับหน้า · chart/stat/doc/quote ไม่มี source (vox = error) · source ยังเป็น "ตัวอย่าง" · ภาพ AI หลายสไตล์ · ไทยใน prompt · `cutout` บนเครื่องที่ไม่มีตัวตัดพื้น (error → เปลี่ยนเป็น `split`)
- **Read `out/storyboard.png` เองก่อน** (ไทยตัดกลางคำ · การ์ดล้น · ภาพผิดช็อต) แล้วส่ง path ให้ผู้ใช้ดู พร้อมสรุปสั้นว่าแต่ละช่วงโชว์อะไร · **รอผู้ใช้ผ่านก่อนเรนเดอร์** — แก้แผนตอนนี้ถูกที่สุด

## Step 4 — เรนเดอร์ในเครื่อง

```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
"$NODE" engine/visual/thv.mjs motion  "$W/plan.json"   # การ์ด/ข้อความขยับ → out/shots/*.mp4 (พื้นเขียวตรงโซนผู้พูด)
"$NODE" engine/visual/thv.mjs compose "$W/plan.json"   # ประกอบลงคลิปจริง + ตัดพื้นหลังคน → out/preview-composite.mp4
"$NODE" engine/visual/thv.mjs subs    "$W/plan.json"   # burn ซับ karaoke → out/preview-composite-subs.mp4 + preview-composite-720p.mp4
```
- `compose` ต้องมี `plan.source_video` + รัน `motion` ก่อน · ภาพออก 8-bit SDR yuv420p fps คงที่เสมอ (คลิป iPhone HDR/10-bit/VFR ถูกแปลงให้)
- `subs` ต้องมี `plan.subs` (หรือ `--ass <ไฟล์>`) · **ช็อต split ซับย้ายไปใต้รอยต่อเอง** (`plan.split_sub_y` % · ค่าเริ่ม 59) เพราะแถบซับปกติทับหน้าครึ่งล่าง · บรรทัดไหนอยู่ช็อตไหนตัดสินจากกึ่งกลางบรรทัด · `sub_y` รายช็อต = ย้ายซับเฉพาะช็อตนั้น · การ์ดเต็มจอถูกดันขึ้นพ้นแถบซับอยู่แล้ว
- **ตัดพื้นหลังคน** (`cutout` layout + `face_circle.mode: "cutout"`): `config.cutout.engine` — `auto` = **vision** บน Mac (Apple Vision เร็ว ~2 วิต่อคลิป 1 วิ ไม่ต้องลงอะไร) · **tb** = transparent-background (ช้า ~30 วิต่อคลิป 1 วิบน Mac · มีการ์ดจอ NVIDIA เร็วกว่ามาก · CPU ช้ามาก) ใช้เมื่อ vision ตัดพัง (ช่องระหว่างนิ้วโปร่ง ขอบแตก ไมค์/ของในมือหาย) หรืองานที่ต้องเนี้ยบ · `off` = เครื่องนี้ไม่มี → ใช้ `split` แทน · รายช็อตเลือกได้ด้วย `cutout_engine` · `plan.cutout.scale` (ค่าเริ่ม 0.62) · `"engine": "none"` = กลับไปใช้กรอบ PiP แบบเดิม
  - ผลแคชที่ `out/cutout/<id>-<เริ่ม>-<เฟรม>-<engine>.webm` (VP9 พื้นใส ~10MB/ช็อต) · `compose --force` = ตัดใหม่ทั้งหมด · ต้องการไฟล์ไปใช้ใน CapCut/DaVinci = ProRes 4444 พื้นใส (ใหญ่มากที่ 2K อย่าใช้เป็นแคช):
    ```bash
    K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
    W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
    "$PY" -m engine.cutout "$W/roughcut.mp4" "$W/out/speaker-cutout.mov"          # engine ตาม config · ระบุเองได้: vision | tb
    ```
  - ห้ามใช้โหมด fast ของ transparent-background (คุณภาพตกชัด)
  - tb ต้องติดตั้งเพิ่มก่อน (skill tad-clip-setup → `--with-cutout` ~2GB) · ใช้ครั้งแรกจะโหลดโมเดลจากเน็ตเอง
- **2K60** (`preset: "2k60"`): Chrome วาดการ์ดที่ 2K จริง ยกเว้นการ์ดเต็มจอที่วาด 1080 แล้วขยายแบบ lanczos (`card_scale` 1 — เกรนกระดาษของ vox ที่ 2K หนักมาก) · ไม่ต้องรัน `all` ใหม่ด้วย 2K (storyboard/การ์ด PNG วาดที่ 1080 เสมอ) · ใช้เวลา motion ~1 นาที/ช็อต · ไฟล์ 50–100MB ต่อคลิป 1 นาที
- เช็คที่ว่างก่อนเรนเดอร์ยาว (`df -h "$W"`) — เหลือ <3GB บอกผู้ใช้ก่อน · งานยาวรันเบื้องหลังแล้วรอด้วยการดูบรรทัดสุดท้ายของ log (`→ <โฟลเดอร์>`) ไม่ใช่ `pgrep` (จับ shell ที่รอเองด้วย)
- เรนเดอร์ใหม่ทีละช็อต: `--shot 07` · Chrome/Edge ค้างระหว่างวาด → เปิดใหม่แล้วทำช็อตนั้นซ้ำเอง สูงสุด 3 ครั้ง (log `↻ shot xx`) · รอบ retry ที่ 2K ถอยไปวาดที่ 1080 แล้วขยาย
- **ไฟล์สุดท้ายผ่าน `sfx_mix` เสมอ** (ไม่มีเสียง FX ก็ใส่ `[]` ใน `sfx.json`) — ตัวนี้คุมระดับเสียง FX ต่ำกว่าเสียงพูด −8 dB และมี limiter กันพีค · thv ไม่มี limiter ของตัวเอง · หาเสียงจากคลังผ่าน skill tad-clip-library
  ```bash
  K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
  W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
  [ -f "$W/sfx.json" ] || echo '[]' > "$W/sfx.json"
  "$PY" -m engine.sfx_mix "$W/out/preview-composite-subs.mp4" "$W/sfx.json" "$W/final.mp4"
  ```

## Step 5 — QC ก่อนส่ง (ดูเอง ห้ามเดาว่าออกมาดี)

- แตกเฟรม **กลางทุกช็อต** จากไฟล์สุดท้าย (`final.mp4` หรือ `out/preview-composite-subs.mp4`) แล้ว Read ทีละภาพ:
  ```bash
  K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
  W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
  for t in 1.2 4.5 9.0; do "$FF" -v error -y -ss $t -i "$W/final.mp4" -frames:v 1 -vf scale=540:-2 "$W/out/qc-$t.png"; done   # ใส่เวลากลางของทุกช็อตจริง
  ```
  เช็ค: จอดำ/เขียวหลุด · ไทยตกหล่น/ล้นกรอบ/ตัดกลางคำ · ซับทับหน้า/ทับการ์ด · หน้าโดนครอปขาด · ตัวตัดพื้นขอบแตก · ไฮไลต์อยู่บนคำที่ถูก
- เจอปัญหา = แก้ plan/การ์ดแล้วเรนเดอร์เฉพาะช็อตนั้น (`motion --shot`, แล้ว `compose` + `subs`) ไม่ใช่ไปแก้ใน CapCut
- ส่งท้าย: path เต็มของ `final.mp4` (+ `out/preview-composite-720p.mp4` ไว้ดูในมือถือ) · `CREDITS.md` + สรุปว่าชิ้นไหน**ห้ามใช้ยิงแอด** · ช็อตที่ยังรอภาพ · สิ่งที่ผู้ใช้ต้องทำเอง

---

## ทางเลือกการส่งงาน

**ไปวางเองใน CapCut** —
```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
"$NODE" engine/visual/thv.mjs loose "$W/plan.json"            # --shot 07 = ช็อตเดียว
```
→ `out/loose/<id>-<ชนิด>.mp4` กราฟิกล้วนบนพื้นเขียว `#00FF00` ทั้งเฟรม (CapCut: Chroma key · Intensity ~10 · Shadow 0) + `.png` เฟรมสุดท้ายพื้นใสจริง + `place-sheet.md` (เวลาเริ่ม/ยาว/ข้างใต้ควรเป็นอะไร) · ไม่มีภาพ/พื้นการ์ด/เงาฟุ้ง (เงาโปร่งบนเขียว = ขอบติดเขียวหลัง key) · ตัวหนังสือขาวได้ขอบเข้มให้อ่านบนวิดีโอได้ · ส่งคู่กับ `out/edit-sheet.md` (ทุกช็อตบอกแทร็ก/ไฟล์/วิธี key · look vox มีคอลัมน์ SFX) · `motion --matte alpha` = `.mov` ProRes 4444 พื้นใสแทนพื้นเขียว

**คลิปที่ตัดเสร็จแล้ว (overlay-on-cut)** — ผู้ใช้ตัดเองแบบมีม/แบ่งจอ/ใส่ภาพไว้แล้ว → **ห้ามทับ edit เดิม** เพิ่มเฉพาะ "ชั้นข้อมูล" ที่ทำให้คนดู save (ประโยคให้ก๊อป · บรรทัดสำคัญ · CTA)
1. ทำซับ/เวลาคำของคลิปนั้น (tad-clip-subs)
2. ดู edit เดิมก่อนวางอะไร: contact sheet ทุก 2 วิ + เวลาคัต (`select=gt(scene,0.3)`) → หาช่วงที่พูดเปล่าๆ และจุดว่างที่ไม่ทับหน้า/มีม/ข้อความเดิม
3. `plan-overlay.json`: `"mode": "overlay-on-cut"` · `"out": "out-overlay"` · ทุกช็อต `full-speaker` · ช่วงที่ไม่แตะ = ช็อตเปล่า · ตัวตรวจข้ามกฎจังหวะ (จังหวะเป็นของ edit เดิม) เหลือเช็คโซนปุ่ม/ข้อความซ้อน
4. `"$NODE" engine/visual/thv.mjs motion "$W/plan-overlay.json"` → `… compose "$W/plan-overlay.json"` = คลิปเดิม + ชั้นข้อมูล + เสียงเดิม (เฟรมนับจากเวลาเด็ดขาด ไม่เลื่อน)
- ถ้าคลิปมีซับเต็มอยู่แล้ว → pill ที่พูดซ้ำเสียงจะซ้ำซ้อน · เก็บแค่ฟองแชต (ประโยคที่ก๊อปได้) + CTA

**ดูจังหวะก่อนถ่าย/ก่อนมีภาพ (animatic)** —
```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
"$NODE" engine/visual/thv.mjs animatic "$W/plan.json"
```
→ `out/animatic.mp4` 540×960 ทั้งไทม์ไลน์ มีภาพตัวแทนตรงโซนผู้พูด/ภาพที่ยังไม่มี

**ผู้ใช้เอา preview ไปแต่งต่อเอง แล้วอยากเรนเดอร์ใหม่** — เทียบภาพ final ของผู้ใช้กับ `preview-composite-subs.mp4` (`blend=difference,signalstats` ที่ 10fps · YAVG > 6 = ช่วงที่ผู้ใช้แก้ภาพ) · ถ้าเขาแค่เพิ่มเสียง → ห้ามวางทับ final (ซับซ้อนสองชั้น) · ใช้ภาพจาก roughcut + เสียงจาก final แล้วชี้ `source_video` ไปไฟล์ใหม่ (เช็คก่อนว่าเสียงไม่เลื่อน):
```bash
K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
"$FF" -v error -y -i "$W/roughcut.mp4" -i "<path เต็มของ final ที่ผู้ใช้แต่ง>" -map 0:v -map 1:a -c copy "$W/src-mix.mp4"
```
ช่วงที่เขาแต่งภาพเอง → ช็อต `full-speaker` เปล่า · ช่วงเปิดที่เขาใส่ hook text เองแล้ว → ตัดบรรทัดซับที่ซ้ำ

## สูตร "ของจริง + มีม" (คลิปเล่าเรื่องคน/แบรนด์จริง)

**1. ไล่หาของจริงตามขั้น** (ทุกชิ้นลง `CREDITS.md`)
- หน้าประวัติบริษัททางการ → ภาพ + คำบรรยาย ใช้เป็น `proof_file` + `treat: print` · **โหลดภาพขนาดเต็มจากหน้าต้นทาง** (ภาพย่อ 600px แตกบนจอ)
- โฆษณาจริงของประเทศต้นทาง + ไทย — ค้นก่อน (ยังไม่โหลดอะไร) → เสนอรายการให้ผู้ใช้เลือก → โหลดเฉพาะที่ผ่าน (ภาพไม่เกิน 1080p · ตัดช่วงให้):
  ```bash
  K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
  W="<path เต็มของโฟลเดอร์งาน tad-clip-<slug> — ใส่ค่าจริงทุกครั้ง>"
  "$PY" -m engine.fetch ytsearch "<คำค้น>" --limit 8                                   # JSON: id · ความยาว · ช่อง · url · ชื่อ
  "$PY" -m engine.fetch video "<url ที่ผู้ใช้เลือก>" --out "$W/assets/footage" --start 12 --end 18 --no-audio
  ```
  · คำค้นภาษาต้นทางได้ผลดี (ญี่ปุ่น: `<สินค้า> CM 1971` · `<บริษัท> 公式`) · ไทยค้นทั้งชื่อไทยและ `<brand> Thailand TVC` · เลือกช่องทางการก่อน
- ฟุตเทจยุค: `<เมือง> <ปี> footage` · **ทิ้งคลิปที่มีลายน้ำ/เลขเวลาฝัง**
- ฉากที่ไม่มีใครถ่ายไว้ → แอนิเมชันประวัติของมูลนิธิ/พิพิธภัณฑ์ ติด `source` "ภาพจำลอง: …" ทุกช็อต
- ตัวเลขบนจอดึงจากเอกสารต้นทางจริง (งบประจำปี PDF) + chip `source` บอกปี · คลิปแข่ง/ข่าวของเจ้าของลิขสิทธิ์เสี่ยงโดน claim → บอกผู้ใช้ก่อน ผู้ใช้ตัดสินเอง

**2. เตรียมคลิปก่อนใส่ plan** (เก็บเป็นไฟล์สั้นใน `assets/footage/` ไม่ชี้ไฟล์ต้นทางยาวๆ ถ้าต้องครอป)
- ดูเฟรมทุก 0.5 วิ (`fps=2,tile`) หาจุดเริ่มที่ไม่ติดรอยคัตของต้นทาง · หาเวลาคัตด้วย `select=gt(scene,0.25)` ไม่งั้นติดภาพช็อตถัดไป
- ครอปตัวหนังสือที่ฝังมา (ซับต่างภาษา · เนื้อเพลง · ขอบฟิล์มดำ) ออกก่อน ไม่งั้นโผล่ข้างป้ายของเรา
- ภาพเทียบ 2 โลก = **ซ้าย-ขวา `hstack` 540×960 ต่อฝั่ง** + label `x 27` / `x 73` · อย่าซ้อนบน-ล่าง (แถบกว้าง 2.25:1 ตัดหน้า/ของในมือทิ้ง)
- ต้นทางสั้นกว่าช็อต → `footage.speed` < 1 (slow-mo) หรือ `setpts=(PTS-STARTPTS)*k` ตอนตัด
- โลโก้/กราฟิกแนวตั้งในช็อต split **ห้ามให้ cover-crop เอง** (โลโก้ PNG ใสขึ้นพื้นดำ · กราฟิก 4:5 หัวขาด) → ทำภาพ 1080×960 ก่อน (วางกลางบนพื้น `paper`/ขาว) แล้วใช้เป็น `proof_file`
- โลโก้พื้นขาว → ตัดพื้นด้วย PIL `ImageDraw.floodfill` จากขอบ/มุมภาพ (thresh 40) แล้วทำ alpha + `MinFilter(3)` + blur 0.8 + ครอปตามขอบเขต (ตัวหนังสือขาวด้านในไม่หาย เพราะเติมจากขอบเท่านั้น) → overlay `image`

**3. มีม** (จากคลังของผู้ใช้ผ่าน skill **tad-clip-library** หรือที่ผู้ใช้อนุมัติ)
- **~3 ตัวต่อคลิป 60 วิ** · ลงตรง **ประโยคตบมุก** (รีแอคชันต่อประโยคนั้นจริงๆ) ไม่ใช่ใส่คั่นเวลา · ยาว 1.5–2.5 วิ
- วางเป็น `split` (มีมครึ่งบน หน้าผู้พูดยังอยู่ครึ่งล่าง) · **ภาพอย่างเดียว ไม่มีเสียงมีม** (เสียง FX ใส่ทีหลังด้วย sfx_mix) · `footage.source` = ที่มาของมีม · ห้ามวางมีมทับช็อตหลักฐาน
- GIF → mp4 1080×960 แบบ fit บนพื้นเบลอของตัวเอง: `"$PY" -m engine.fetch gif2clip "$W/assets/memes/<ชื่อ>.gif" "$W/assets/memes/<ชื่อ>.mp4"` (หลังบรรทัด env.sh)
- จังหวะที่เวิร์ก: การ์ดเต็มจอสั้น 1.3–1.8 วิ ตั้งประโยค → มีมรับมุก
- อยากเก็บ motion ของการ์ดไว้แต่มีมุก → overlay `image` สติกเกอร์ (PNG ขอบขาว ~22px · เอียง −3° · เงาเข้ม) วางทับกล่องที่พูดไปแล้ว ให้หัวการ์ด + กล่องสุดท้ายยังเห็น · `pop: true` · `delay` = วินาทีของคำตบมุกในช็อต · ภาพที่มีเลข/ข้อความที่ไม่เกี่ยว → ครอปออก

## ฟิลด์ใน plan.json ที่ใช้บ่อย (นอกตาราง visual-modes)

| ฟิลด์ | ความหมาย |
|---|---|
| `source_video` · `subs` | คลิปที่ตัดแล้ว · ไฟล์ `.karaoke.ass` (เทียบโฟลเดอร์ plan) |
| `out` | โฟลเดอร์ผลลัพธ์ (ค่าเริ่ม `out`) · plan หลายตัวในโฟลเดอร์เดียวใช้ `out` คนละชื่อ |
| `preset` · `fps` · `render_scale` · `card_scale` | ความละเอียด (ดู Step 2) · ใส่ตรงๆ ได้ ทับค่าของ preset |
| `look` · `style` · `graphics_fps` | `vox` / ปกติ · สไตล์ภาพ AI · fps ของกราฟิก vox (12) |
| `split_window` · `split_sub_y` · shot `sub_y` | กรอบผู้พูดในครึ่งล่าง · ตำแหน่งซับในช็อต split · ตำแหน่งซับรายช็อต |
| `face_circle` · shot `face` | หน้าผู้พูดบนการ์ดเต็มจอ (ดู Step 1) · `false` = ปิดรายช็อต |
| `cutout` · shot `cutout_engine` / `cutout_scale` | `{engine, scale}` ตัดพื้นหลังคน |
| `mirror` | ช่วงที่ภาพกลับซ้ายขวา `[[from, to]]` |
| `mode: "overlay-on-cut"` · `proof_exempt` · `people` | ทับ edit เดิม · เหตุผลที่ไม่มีของจริง · คนในภาพ AI เป็นใคร |
| shot `beat` | look vox: cold-open · question · context · mechanism · turn · proof · so-what |

## Gotchas

- การ์ดวาดด้วย Chrome/Edge แบบ headless ผ่าน `--remote-debugging-pipe` (ไม่ต้องลงอะไร) · หาเบราว์เซอร์จาก `config.tools.chrome` → chrome-headless-shell ใน `~/.tad-clip/chrome` → Google Chrome (Mac) / Edge (Windows) · ไม่เจอ → skill tad-clip-setup
- ฟอนต์ไทยมาจาก `assets/fonts` ของ kit ทั้งในการ์ด (`@font-face`) และซับ (libass `fontsdir`) → ไม่ต้องติดตั้งฟอนต์ลงเครื่อง · ซับต้องใช้ ffmpeg ที่มี libass (setup ติดตั้งให้)
- ภาษาไทยห้ามใช้ `letter-spacing` (สระ/วรรณยุกต์แยกจากพยัญชนะ)
- อนิเมชันที่เปลี่ยน `transform` จะทับ `translate(-50%)` ของตัวที่จัดกลาง → ตัวที่จัดกลางแล้วมีอนิเมชันเข้า ใช้ property `translate:` แยก (ดู `.docchip` ใน `cards.html`)
- **การ์ด `flow` เกิน 3 กล่องชนแถบซับ** และถ้าเปิด face_circle หัวการ์ดทับหน้า → flow ≤3 กล่อง + `"face": false` · ของที่พูดรัว 4 อย่างรวมเป็นกล่องเดียวด้วย ` · ` แล้วตั้ง `delay`/`step` ให้กล่องโผล่ตรงคำพูด (กล่อง i โผล่ที่ `delay + i×(step+0.3)` วิ)
- ช็อต split ห้ามวาง pill ที่ y 50–60 (ซับย้ายมาอยู่ใต้รอยต่อ) → ใช้ `label` ในครึ่งบนแทน
- ช็อต `full-speaker` ที่หน้าอยู่สูงและมีของ/มือช่วงอก → อย่าใช้ keyword ใช้การ์ดเต็มจอ + face_circle แทน
- ภาพ AI: split ใช้ 1:1 (ถูกครอปเป็นแถบบน) · cutout ใช้ 2:3 เว้นล่าง 1/3 ให้คนยืน — prompt ใส่ให้เองแล้ว
- `highlight` ต้องมีภาพจริงข้างใต้ (`proof_file`) ไม่งั้นไฟล์พื้นเขียวได้แค่ขีดเส้นใต้ทึบ
- การ์ด/overlay ชนิดใหม่ = เพิ่ม type ใน `engine/visual/cards.html` + รายชื่อใน `thv.mjs` (`FULL_CARDS`/`OVERLAYS`) + ตาราง §2 ใน `visual-modes.md`
- plan รุ่นก่อน kit (layout ชื่อเดิมของ `full-speaker` · สี `navy` / `coral` / `gold`) อ่านได้ — แปลงเป็น `full-speaker` / `primary` / `accent` / `accent2` ให้เอง
