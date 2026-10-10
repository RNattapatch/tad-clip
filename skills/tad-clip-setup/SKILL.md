---
name: tad-clip-setup
description: ติดตั้งและตั้งค่า tad-clip (ชุดตัดคลิปพูดหน้ากล้องภาษาไทย) บน Mac หรือ Windows ครั้งแรก ตรวจเครื่องว่าพร้อมไหม และอัปเดตเป็นรุ่นใหม่ ฟรีทั้งหมด ไม่ใช้ API key. Install, configure, check, and update the tad-clip kit. Use when the user says "ติดตั้ง tad-clip", "ตั้งค่าตัดคลิป", "ตรวจเครื่อง tad-clip", "อัปเดต tad-clip", "setup tad-clip", "ติดตั้ง tad-clip ต่อ", "ตัดคลิปใช้ไม่ได้", or when the tad-clip skill finds the kit missing or broken (doctor shows ❌).
argument-hint: "[ติดตั้ง | ตรวจเครื่อง | อัปเดต]"
---

# ติดตั้ง ตั้งค่า ตรวจเครื่อง (tad-clip-setup) — by @pun_nattapatch

skill นี้มี 3 งาน เลือกตามที่ผู้ใช้พูด

- "ติดตั้ง" / "ตั้งค่า" / "setup" → ข้อ A (ครั้งแรก) แล้วต่อข้อ B
- "ตรวจเครื่อง" / "ตัดคลิปใช้ไม่ได้" → ข้อ C
- "อัปเดต" → ข้อ D

ผู้ใช้ส่วนใหญ่ไม่เคยเขียนโค้ด พูดกับเขาเป็นภาษาไทยสั้นๆ บอกว่ากำลังทำอะไรและรออีกนานไหม ไม่ต้องอธิบายคำสั่ง
งานติดตั้งทุกอย่างคุณรันเองใน Bash ผู้ใช้ไม่ต้องพิมพ์อะไร ยกเว้นตอนติดตั้ง Homebrew บน Mac (ต้องใช้รหัสผ่านเครื่อง)

## 0. ตัวแปรที่ใช้ทุกครั้ง

ทุกคำสั่งรันใน Bash (บน Windows คือ Git Bash ที่มากับ Claude Code ไวยากรณ์เดียวกัน) เริ่มด้วย:

```bash
KIT="${CLAUDE_PLUGIN_ROOT}"; [ -d "$KIT/engine" ] || KIT="$HOME/.tad-clip/kit"
TC_HOME="${TAD_CLIP_HOME:-$HOME/.tad-clip}"
PY="$TC_HOME/venv/bin/python"; [ -x "$PY" ] || PY="$TC_HOME/venv/Scripts/python.exe"
case "$(uname -s)" in Darwin) OS=mac ;; MINGW*|MSYS*|CYGWIN*) OS=windows ;; *) OS=other ;; esac
echo "$OS · kit $KIT · home $TC_HOME"
```

ถ้า `${CLAUDE_PLUGIN_ROOT}` ไม่ถูกแทนค่า ให้ใช้ `~/.tad-clip/kit` (บรรทัดแรกจัดการให้แล้ว)
ถ้า `OS=other` ให้บอกผู้ใช้ตรงๆ ว่า tad-clip รองรับแค่ macOS กับ Windows 10/11 แบบ 64 บิต แล้วหยุด

## A. ติดตั้งครั้งแรก

### A1. ดูว่าเครื่องนี้ต้องลงอะไรบ้าง (ยังไม่ติดตั้ง)

Mac:

```bash
command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null
python3 "$KIT/engine/install/setup.py"
```

ถ้าไม่เจอ `brew` เลย ให้หยุดตรงนี้แล้วบอกผู้ใช้ประมาณนี้ (คุณติดตั้งให้ไม่ได้ เพราะต้องพิมพ์รหัสผ่านเครื่อง):

> เครื่องนี้ยังไม่มี Homebrew ซึ่งเป็นตัวติดตั้งโปรแกรมของ Mac ขอให้ทำ 3 ขั้นนี้ครับ
> 1. กด Cmd+Space พิมพ์ Terminal แล้วกด Enter
> 2. วางบรรทัดนี้แล้วกด Enter: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
> 3. ถ้าถามรหัสผ่าน ให้พิมพ์รหัสที่ใช้เข้าเครื่อง (ตัวอักษรจะไม่ขึ้นบนจอ เป็นเรื่องปกติ) แล้วกด Enter รอจนขึ้นว่า Installation successful
>
> เสร็จแล้วพิมพ์บอกผมว่า "ติดตั้ง tad-clip ต่อ"

Windows (โหลด uv ตัวเดียวก่อน ~20 MB ไม่ต้องมี Python ในเครื่อง ไม่ต้องใช้สิทธิ์ admin):

```bash
powershell -NoProfile -ExecutionPolicy Bypass -Command '$ProgressPreference = 0; [Net.ServicePointManager]::SecurityProtocol = 3072; $h = $env:TAD_CLIP_HOME; if (-not $h) { $h = Join-Path $env:USERPROFILE .tad-clip }; $b = Join-Path $h bin; New-Item -ItemType Directory -Force -Path $b | Out-Null; $z = Join-Path $b uv.zip; Invoke-WebRequest -UseBasicParsing -Uri https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -OutFile $z; Expand-Archive -Force -Path $z -DestinationPath $b; Remove-Item $z'
UV="$TC_HOME/bin/uv.exe"
"$UV" run --no-project --python 3.12 "$KIT/engine/install/setup.py"
nvidia-smi -L 2>/dev/null || echo "no NVIDIA"
```

คำสั่ง setup ที่ไม่มี `--yes` จะพิมพ์รายการที่ต้องลง ขนาด และพื้นที่ว่าง แล้วหยุดโดยไม่ติดตั้งอะไร
เอารายการนั้นมาเล่าให้ผู้ใช้ฟังเป็นภาษาง่ายๆ ตัวอย่าง:

> จะลงโปรแกรมฟรี 5 อย่าง ใช้พื้นที่ราว 5 GB (เครื่องคุณว่าง 120 GB)
> - ffmpeg สำหรับตัดต่อและฝังซับ
> - Node.js สำหรับวาดการ์ดข้อความ
> - Python กับตัวถอดเสียงภาษาไทย (faster-whisper)
> - โมเดลถอดเสียง large-v3 ขนาด 3 GB (โหลดครั้งเดียว หลังจากนั้นถอดเสียงได้แม้ไม่ต่อเน็ต)
> - yt-dlp สำหรับโหลดคลิปจากเว็บมาแทรก
>
> ใช้เวลาประมาณ 10–30 นาที ขึ้นกับเน็ต ไม่ต้องจ่ายอะไร ไม่ต้องสมัครอะไรเพิ่ม

ถ้าพื้นที่ไม่พอ setup จะบอกเอง ให้ผู้ใช้ลบไฟล์ก่อนแล้วค่อยทำต่อ

### A2. ถามก่อนเริ่ม (AskUserQuestion ครั้งเดียว)

1. เริ่มติดตั้งเลยไหม: "ติดตั้งเลย" / "ยังก่อน"
2. โมเดลถอดเสียง: "large-v3 แม่นสุด 3 GB (แนะนำ)" / "large-v3-turbo เร็วกว่า 1.6 GB (เครื่อง RAM 8 GB หรือเครื่องช้า)"
   setup เลือก turbo ให้เองถ้า RAM น้อยกว่า 8 GB · ถ้าผู้ใช้เลือกเองให้ใส่ `--model <ชื่อ>`
3. เฉพาะ Windows: ตัวตัดพื้นหลังคน (ไม่บังคับ) ใช้กับช็อตแบบ cutout ที่ตัวผู้พูดลอยอยู่บนภาพ
   - เจอการ์ดจอ NVIDIA (`nvidia-smi` บอกชื่อรุ่น) → "ติดตั้งเพิ่ม ราว 5 GB ใช้การ์ดจอ เร็ว" / "ไม่เอา"
   - ไม่เจอ → "ติดตั้งเพิ่ม ราว 2 GB ใช้ CPU ได้ผลเท่ากันแต่ช้ามาก ช็อตสั้นๆ ก็หลายนาที" / "ไม่เอา (แนะนำ)"
   - ไม่ติดตั้งก็ยังตัดคลิปได้ครบ แค่ช็อต cutout จะเปลี่ยนเป็นแบบแบ่งครึ่งจอแทน
   - Mac ไม่ต้องถาม ใช้ Apple Vision ที่มากับเครื่อง

### A3. ติดตั้ง (รันแบบ background เพราะนานเกิน 10 นาทีได้)

ใช้ Bash แบบ `run_in_background: true` แล้วอ่านผลเป็นระยะ ระหว่างรอให้ทำข้อ B (ถามการตั้งค่า) ไปพร้อมกัน

Mac:

```bash
python3 "$KIT/engine/install/setup.py" --yes
```

Windows (ใส่ `--with-cutout` เฉพาะเมื่อผู้ใช้ตอบว่าเอา):

```bash
"$TC_HOME/bin/uv.exe" run --no-project --python 3.12 "$KIT/engine/install/setup.py" --yes
```

ต่อท้าย `--model large-v3-turbo` ถ้าผู้ใช้เลือก turbo

setup ทำ 5 ขั้นตามลำดับ `[1/5]` ถึง `[5/5]` บอกผู้ใช้สั้นๆ เมื่อขึ้นขั้นใหม่ ขั้น 4 (โหลดโมเดล) นานที่สุด
บรรทัดท้ายสุดเป็นตารางตรวจเครื่อง `ผล: พร้อมใช้ ✅` แปลว่าเสร็จ

ถ้าจบด้วย `❌` ให้อ่านข้อความเหนือมัน ทำตามบรรทัด "วิธีแก้" แล้วรันคำสั่งเดิมซ้ำ (ของที่ลงแล้วจะข้าม ส่วนที่โหลดค้างจะโหลดต่อ) ปัญหาที่เจอบ่อย:

- ไม่มี Homebrew → ให้ผู้ใช้ทำ 3 ขั้นใน A1
- เน็ตหลุดตอนโหลดโมเดล → รันซ้ำได้เลย
- Windows: yt-dlp ถูกโปรแกรมกันไวรัสกักไว้ → ให้ผู้ใช้กด "อนุญาต" ในหน้าต่างแจ้งเตือนของ Windows Security แล้วรันซ้ำ
- Mac: เบราว์เซอร์ Google Chrome เปิดแบบซ่อนหน้าจอไม่ได้ (เช่นเครื่องที่ไม่ได้ต่อจอ) → setup ข้ามไปโหลด chrome-headless-shell ให้เอง ไม่ต้องทำอะไร
- Mac: ไม่มี swiftc (ใช้ตัดพื้นหลังคน) → `xcode-select --install` จะมีหน้าต่างให้ผู้ใช้กดติดตั้ง

## B. ตั้งค่าของผู้ใช้ (ถามระหว่างรอ A3)

ถามด้วย AskUserQuestion รวบในครั้งเดียว ข้อไหนผู้ใช้ข้าม ให้ใช้ค่าเริ่มต้น

1. ชื่อที่จะขึ้นในคลิป และชื่อบัญชี (เช่น `สมชาย ใจดี` และ `@somchai.sales`) ใช้บนการ์ดและในไฟล์ซับ
2. สีของการ์ดข้อความ เลือก 1 ใน 3 หรือบอกสีเอง
   - `neutral` เทาเข้มเกือบดำ ตัวเน้นสีส้ม (ค่าเริ่มต้น)
   - `paper` พื้นครีม ตัวหนังสือเทาถ่าน ตัวเน้นสีแดงอิฐ
   - `pun-navy-coral` สไตล์ @pun_nattapatch กรมท่า ตัวเน้นสีชมพูแดง มีทองเป็นสีรอง
   - หรือบอกสีแบรนด์เอง (รหัสสี เช่น `#0f766e` หรือส่งโลโก้มาให้ดู แล้วคุณเลือกรหัสสีให้)
3. คำที่ตัวถอดเสียงมักฟังผิด เช่น ชื่อคน ชื่อร้าน ชื่อสินค้า ศัพท์อังกฤษที่พูดบ่อย (ขอทั้งคำที่ถูก และถ้ารู้ คำที่มันชอบถอดผิด)
4. มีโฟลเดอร์เก็บมีม คลิปตลก หรือเสียงเอฟเฟกต์ไหม (ไม่มีก็ได้) ถ้ามี ขอ path เต็ม
5. ความละเอียดไฟล์ที่ได้: "1080p (แนะนำ เร็ว ไฟล์เล็ก)" / "2K 60fps (คมกว่า เรนเดอร์นานขึ้นราว 2 เท่า)"

รอให้ setup จบก่อน แล้วค่อยเขียนคำตอบลง `$TC_HOME/config.json` (setup ก็เขียนไฟล์นี้ ถ้าเขียนพร้อมกันอาจทับกัน)
อ่านไฟล์เดิมด้วย Read แล้วแก้เฉพาะ key ที่ได้คำตอบด้วย Edit ห้ามลบ `tools` กับ `whisper` ที่ setup เขียนไว้ รูปแบบ:

```json
{
  "speaker": { "name": "สมชาย ใจดี", "handle": "@somchai.sales" },
  "brand": { "preset": "paper" },
  "glossary": {
    "fixes": [["ช้อปปี้", "Shopee"], ["ชาไทยใจ ดี", "ชาไทยใจดี"]],
    "protect": ["ชาไทยใจดี", "Shopee"],
    "glue": ["ชาไทยใจดี"],
    "prompt_hint": "ชาไทยใจดี, Shopee, Lazada, TikTok Shop"
  },
  "library_dir": "/Users/somchai/Movies/meme",
  "output": { "preset": "1080p" }
}
```

- `brand.colors` ใส่เฉพาะเมื่อผู้ใช้บอกสีเอง key ที่ใช้: `primary` `primary_deep` `primary_mid` `accent` `accent2` `paper` `ink` ใส่บางตัวก็ได้ ที่เหลือมาจาก preset
  สีที่ใส่เองจะชนะ preset เสมอ ถ้าภายหลังผู้ใช้อยากเปลี่ยนกลับไปใช้ preset อื่น ต้องลบ `brand.colors` ออกด้วย
- `glossary.fixes` = คู่ `[คำที่ถอดผิด, คำที่ถูก]` แทนที่อัตโนมัติหลังถอดเสียง · `protect` = คำที่ห้ามแก้ตอนตรวจซับ · `glue` = คำที่ห้ามตัดแยกบรรทัด · `prompt_hint` = คำที่ส่งให้ตัวถอดเสียงรู้ล่วงหน้า (คั่นด้วย `, `)
- ถ้าผู้ใช้ไม่รู้ว่าถอดผิดเป็นคำว่าอะไร ใส่แค่ `protect` + `prompt_hint` ก็พอ พอตัดคลิปแรกแล้วเห็นคำผิดจริงค่อยเพิ่ม `fixes`
- `output.preset` ค่าที่ใช้ได้: `1080p` หรือ `2k60`
- `library_dir` ใช้ path เต็ม บน Windows ใช้ `/` คั่นได้ (เช่น `C:/Users/somchai/Videos/meme`) ถ้าผู้ใช้ให้มา ชวนเขาต่อด้วย skill **tad-clip-library** เพื่อสแกนและแท็กคลัง

เขียนเสร็จแล้วตรวจว่าไฟล์ยังอ่านได้ และสีออกมาตามที่เลือก:

```bash
cd "$KIT" && "$PY" -c "from engine import kit; c = kit.load_config(); print(c['speaker'], c['brand']['preset'], c['brand']['colors']['accent'], c['output']['preset'])"
```

## C. ตรวจเครื่อง

```bash
if [ -x "$PY" ]; then cd "$KIT" && "$PY" engine/doctor.py
elif [ "$OS" = mac ]; then python3 "$KIT/engine/doctor.py"
else "$TC_HOME/bin/uv.exe" run --no-project --python 3.12 "$KIT/engine/doctor.py"; fi
```

doctor พิมพ์ตารางภาษาไทย ✅ ผ่าน · ⚠️ ใช้ได้แต่ควรรู้ · ❌ ต้องแก้ก่อนตัดคลิป · ℹ️ ข้อมูล และมีบรรทัด "วิธีแก้" เป็นคำสั่งเดียวต่อปัญหา

- แปลตารางให้ผู้ใช้เป็นภาษาคน เช่น "ตัวฝังซับยังขาดชิ้นหนึ่ง เดี๋ยวผมลงให้"
- คำสั่งแก้ส่วนใหญ่คือรัน setup ซ้ำ ซึ่งข้ามของที่มีแล้ว รันได้เลย
- ถามผู้ใช้ก่อน ถ้าคำสั่งแก้จะโหลดของใหญ่เกิน 1 GB (เช่นเปลี่ยนโมเดล หรือลงตัวตัดพื้นหลัง) หรือต้องใช้รหัสผ่านเครื่อง
- ⚠️ พื้นที่ว่างน้อยกว่า 15 GB: ตัดคลิปได้ แต่คลิปยาวอาจไม่พอ บอกผู้ใช้ไว้
- ⚠️ RAM น้อยกว่า 8 GB กับโมเดล large-v3: เสนอเปลี่ยนเป็น `--model large-v3-turbo`
- ตรวจคลิปที่ทำเสร็จ (ขั้น QC ของ skill tad-clip ใช้เอง): `"$PY" "$KIT/engine/doctor.py" --qc "<final.mp4>" --expect-duration <วินาที>`

## D. อัปเดต

1. อัปเดต plugin

```bash
claude plugin marketplace update pun-nattapatch && claude plugin update tad-clip@pun-nattapatch
```

ถ้าไม่มีคำสั่ง `claude` ในเครื่อง ให้ผู้ใช้พิมพ์ `/plugin` ในช่องแชท เลือก tad-clip แล้วกดอัปเดต
ถ้าติดตั้งแบบ clone ไว้ที่ `~/.tad-clip/kit` (ไม่ได้ลงผ่าน plugin) ให้รัน `git -C "$HOME/.tad-clip/kit" pull` แล้ว copy `skills/*` ไปทับใน `~/.claude/skills/` อีกครั้ง

2. ให้ผู้ใช้ปิดแล้วเปิดแอป Claude ใหม่ (skill รุ่นใหม่จะโหลดตอนเปิดแอป)
3. รัน setup ซ้ำแบบ A3 เพื่อลงของที่รุ่นใหม่ต้องใช้ (ของเดิมข้ามหมด ไม่ต้องโหลดโมเดลใหม่)
4. อัปเดตตัวโหลดคลิปจากเว็บ (เว็บเปลี่ยนบ่อย ถ้าโหลดไม่ได้ให้ทำข้อนี้): Mac `brew upgrade yt-dlp` · Windows `"$TC_HOME/bin/yt-dlp.exe" -U`
5. รันข้อ C ให้เห็นว่าทุกอย่าง ✅

การตั้งค่า โมเดล และคลังมีมอยู่ใน `~/.tad-clip` ไม่หายตอนอัปเดต

ถ้าผู้ใช้อยากลบ tad-clip ทิ้ง: ถอน plugin ใน `/plugin` แล้วลบโฟลเดอร์ `~/.tad-clip` (โมเดลถอดเสียงอยู่ในนั้น คืนพื้นที่ได้หลาย GB) ส่วนโปรแกรมที่ลงผ่าน Homebrew บน Mac ใช้กับงานอื่นได้ จะเก็บไว้ก็ได้

## จบงานทุกครั้ง

สรุปให้ผู้ใช้ 2–3 บรรทัด: พร้อมใช้แล้วหรือยัง ค่าที่ตั้งไว้ และพิมพ์อะไรต่อ (เช่น "ลากไฟล์คลิปมาวาง แล้วพิมพ์ ตัดคลิป")
แล้วปิดด้วยบรรทัดนี้ตรงตัว:

kit นี้ทำโดย @pun_nattapatch
