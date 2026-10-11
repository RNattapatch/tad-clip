# ตัดคลิป (tad-clip) by [@pun_nattapatch](https://punnattapatch.com/free/tad-clip?utm_source=github&utm_medium=readme&utm_campaign=tad-clip)

ถ่ายคลิปพูดหน้ากล้องเสร็จ ลากไฟล์ใส่ Claude แล้วพิมพ์ว่า **ตัดคลิป** ได้ MP4 พร้อมลงกลับมา

![ก่อนตัด กับ หลังตัด](docs/images/before-after.gif)

ชุดนี้คือระบบที่ผมใช้ตัด reel ตัวเองทุกสัปดาห์ เอาส่วนที่ผูกกับเครื่องผมออก แล้วทำให้รันได้ทั้ง Mac และ Windows
จ่ายแค่ค่า Claude ที่คุณมีอยู่แล้ว ที่เหลือเป็นโปรแกรมฟรีทำงานในเครื่องคุณ ไม่มี API key ไม่ต้องสมัครแอปตัดคลิปรายเดือนเพิ่ม

## ทำอะไรได้

- ตัดเทคที่พูดซ้ำ พูดผิด ช่วงเงียบ ออกเอง แล้วสรุปให้ดูว่าตัดท่อนไหนเพราะอะไร สั่งเอาคืนได้ด้วยคำพูด
- ซับไทยแบบคาราโอเกะ ไฮไลต์ทีละคำ ตัดบรรทัดไม่ขาดกลางคำ Claude ตรวจคำที่ฟังผิดให้ก่อนฝัง
- แทรกภาพจริง คลิปจริง มีม การ์ดข้อความ กราฟ แบบ split screen หรือเต็มจอ ตามจังหวะที่พูด
- ตัดพื้นหลังคนพูดออก (Mac ใช้ Apple Vision ในเครื่อง · Windows ติดตั้งเพิ่มได้)
- ใส่เสียงเอฟเฟกต์ที่ระดับพอดีกับเสียงพูด ไม่กลบคำ
- ได้ไฟล์ 1080p หรือ 2K 60fps พร้อมชีตบอกตำแหน่งทุกชิ้น ถ้าอยากเอาไปแต่งต่อใน CapCut

ก่อนเริ่ม Claude ถามสั้นๆ ครั้งเดียวตอนต้น: คลิปนี้ตัดเทคมาเองแล้วหรือยัง จะเอาแค่ซับหรือเอาภาพประกอบด้วย ให้หาภาพจากเน็ตหรือคุณมีไฟล์เอง และจะให้ถามระหว่างทางหรือทำรวดเดียวจนจบ

![storyboard ก่อนเรนเดอร์](docs/images/storyboard.png)

## ต้องมีอะไร

| | |
|---|---|
| Claude | แพ็กเกจ Pro ขึ้นไป (Claude Code มาในแพ็กเกจ) ใช้ผ่านแอป Claude บนคอม แท็บ Code |
| เครื่อง | Mac (แนะนำชิป Apple M1 ขึ้นไป) หรือ Windows 10/11 แบบ 64-bit (Windows ต้องมี [Git for Windows](https://git-scm.com/download/win) ซึ่งฟรี เพราะ Claude ใช้ Git Bash รันคำสั่ง) |
| RAM | 8 GB ขึ้นไป · 16 GB สบายกว่า |
| พื้นที่ว่าง | ราว 15 GB (โมเดลถอดเสียงภาษาไทยตัวใหญ่ 3.1 GB + โปรแกรม) |
| เน็ต | ใช้ตอนติดตั้ง และ Claude ต้องต่อเน็ตตอนทำงาน |

โปรแกรมที่ชุดนี้ติดตั้งให้ ฟรีทั้งหมด: whisper (ถอดเสียง · Mac ใช้ whisper.cpp ผ่านชิปกราฟิก · Windows ใช้ faster-whisper) · ffmpeg (ตัดและประกอบวิดีโอ) · Chrome หรือ Edge แบบไม่มีหน้าต่าง (วาดการ์ดและกราฟิก) · yt-dlp (ดึงคลิปเมื่อคุณอนุญาต) · ฟอนต์ Prompt

## ติดตั้ง 3 ขั้น

**1. ติดตั้ง plugin**

ในแอป Claude แท็บ Code กด **+** → **Plugins** → **Add plugin** แล้วใส่ `RNattapatch/tad-clip`

หรือพิมพ์ในช่องแชท 2 บรรทัดนี้ ทีละบรรทัด:

```
/plugin marketplace add RNattapatch/tad-clip
/plugin install tad-clip@pun-nattapatch
```

**2. ให้ Claude ติดตั้งโปรแกรมที่ต้องใช้**

พิมพ์ว่า `ติดตั้ง tad-clip` Claude จะบอกก่อนว่าจะลงอะไร ใช้พื้นที่เท่าไหร่ แล้วถามชื่อคุณ สีแบรนด์ และคำที่มักถอดเสียงผิด (ชื่อคุณ ชื่อแบรนด์)

- Mac: ต้องมี [Homebrew](https://brew.sh) ถ้ายังไม่มี Claude จะให้คำสั่ง 1 บรรทัดไปวางใน Terminal (ต้องใส่รหัสผ่านเครื่องเอง)
- Windows: ลง [Git for Windows](https://git-scm.com/download/win) ก่อนครั้งเดียว (ฟรี กด Next ตามค่าเดิมได้เลย) แล้วปิดเปิดแอป Claude ใหม่ ส่วนตัวติดตั้งของชุดนี้ไม่ต้องใช้สิทธิ์ admin

ครั้งแรกใช้เวลาราว 10–30 นาที ขึ้นกับเน็ต ส่วนใหญ่คือโหลดโมเดลถอดเสียง

**3. ตัดคลิปแรก**

ลากไฟล์วิดีโอเข้าไปในช่องแชท แล้วพิมพ์ `ตัดคลิป`

คู่มือทีละขั้น: [Mac](docs/01-install-mac.md) · [Windows](docs/02-install-windows.md) · [ตัดคลิปแรก](docs/03-first-clip.md)

## ใช้เวลาแค่ไหน

วัดจริงบน Mac ชิป M4: คลิปดิบ 107 วินาที ทำครบทุกขั้น (ตัดเทค ซับ หาภาพจริงจากเน็ต วางภาพ เรนเดอร์ ตรวจงาน) ใช้ 30 นาทีโดยไม่มีใครแตะเครื่อง ได้คลิป 72 วินาที ตัดทิ้ง 19 ท่อน
เลือกแค่ตัด + ซับ จะเร็วกว่ามาก · บน Windows ที่ไม่มีการ์ดจอ NVIDIA ขั้นถอดเสียงช้ากว่า Mac หลายเท่า เลือกโหมด "ทำรวดเดียว" แล้วปล่อยเครื่องทำไปได้เลย

## คู่มือ

| เรื่อง | ไฟล์ |
|---|---|
| ติดตั้งบน Mac | [docs/01-install-mac.md](docs/01-install-mac.md) |
| ติดตั้งบน Windows | [docs/02-install-windows.md](docs/02-install-windows.md) |
| ตัดคลิปแรก | [docs/03-first-clip.md](docs/03-first-clip.md) |
| แทรกภาพ คลิป มีม เสียง | [docs/04-insert-materials.md](docs/04-insert-materials.md) |
| สี ฟอนต์ ตำแหน่งซับ | [docs/05-brand-style.md](docs/05-brand-style.md) |
| เจอปัญหา | [docs/06-troubleshooting.md](docs/06-troubleshooting.md) |
| ลิขสิทธิ์ของที่แทรก | [docs/07-copyright.md](docs/07-copyright.md) |
| อัปเดตเวอร์ชันใหม่ | [docs/08-update.md](docs/08-update.md) |

## สิทธิ์การใช้งาน

ใช้ตัดคลิปของตัวเอง ของธุรกิจ หรือรับจ้างตัดได้ทั้งหมด คลิปที่ได้เป็นของคุณ
ห้ามเอาชุดนี้ไปขาย หรือทำเป็นแอป/บริการเก็บเงินรายเดือน · สรุปภาษาไทย: [LICENSE-TH.md](LICENSE-TH.md) · ตัวสัญญา: [LICENSE](LICENSE) (PolyForm Perimeter 1.0.1)

## คนทำ

ปัน ณัฐพัชร์ ([@pun_nattapatch](https://punnattapatch.com/free/tad-clip?utm_source=github&utm_medium=readme_footer&utm_campaign=tad-clip)) · ใช้แล้วติดตรงไหน เปิด [Issue](https://github.com/RNattapatch/tad-clip/issues) ได้เลย
