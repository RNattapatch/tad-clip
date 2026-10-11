[← กลับหน้าแรก (README)](../README.md)

# อัปเดต และถอนการติดตั้ง

## อัปเดตเป็นรุ่นใหม่

plugin จาก marketplace นอกของ Anthropic ค่าเริ่มต้นจะไม่อัปเดตเอง ต้องสั่งอัปเดต
รุ่นใหม่แต่ละรุ่นบอกว่าเปลี่ยนอะไรไว้ใน [CHANGELOG.md](../CHANGELOG.md)

### ทางที่ง่ายที่สุด: บอก Claude

1. พิมพ์ `อัปเดต tad-clip`
   Claude สั่งอัปเดต plugin ให้ ถ้าเครื่องไม่มีคำสั่ง `claude` ให้เรียก Claude จะบอกให้คุณทำทางที่ 2 ด้านล่าง
2. ปิดแอป Claude แล้วเปิดใหม่ skill รุ่นใหม่จะโหลดตอนเปิดแอป
3. พิมพ์ `อัปเดต tad-clip` อีกครั้ง
   Claude ทำต่อจากขั้นที่ค้างไว้ คือรันตัวติดตั้งซ้ำเพื่อลงของที่รุ่นใหม่ต้องใช้ ของเดิมข้ามหมด ไม่โหลดโมเดลถอดเสียงใหม่ แล้วอัปเดตตัวโหลดคลิปจากเว็บ (yt-dlp) และรันตัวตรวจเครื่อง
4. เห็น `ผล: พร้อมใช้ ✅` เป็นอันเสร็จ

### ทางที่ 2: สั่งอัปเดต plugin เอง

พิมพ์ในช่องแชท

```
/plugin marketplace update pun-nattapatch
```

แล้วพิมพ์ `/plugin` เปิดแท็บ **Installed** เลือก tad-clip → **Update now**
หรือในแท็บ **Marketplaces** เลือก pun-nattapatch → **Update marketplace** ซึ่งอัปเดต plugin ทุกตัวจาก marketplace นี้

ถ้าใช้ Terminal อยู่แล้ว คำสั่งเดียวกันคือ

```
claude plugin marketplace update pun-nattapatch
claude plugin update tad-clip@pun-nattapatch
```

เสร็จแล้วปิดแอปเปิดใหม่ (หรือพิมพ์ `/reload-plugins`) แล้วพิมพ์ `อัปเดต tad-clip` เหมือนข้อ 3 ด้านบน

### อยากให้อัปเดตเอง

พิมพ์ `/plugin` → แท็บ **Marketplaces** → เลือก pun-nattapatch → **Enable auto-update**
หลังจากนั้นแอปจะโหลดรุ่นใหม่มาเก็บไว้เองตอนเริ่ม session และใช้รุ่นใหม่ตั้งแต่ session ถัดไป
ขั้นรันตัวติดตั้งซ้ำไม่ได้เกิดเอง เมื่อเห็นข้อความ `Plugin updated: tad-clip` ให้พิมพ์ `อัปเดต tad-clip` หนึ่งครั้ง Claude จะลงของที่รุ่นใหม่ต้องใช้ให้

### อัปเดตแล้วอะไรยังอยู่

ทุกอย่างใน `~/.tad-clip` (Windows: `%USERPROFILE%\.tad-clip`) ไม่หายตอนอัปเดต

- ค่าที่ตั้งไว้ใน `config.json`: ชื่อ สีแบรนด์ คลังคำ โฟลเดอร์คลังมีม ความละเอียด
- โมเดลถอดเสียงและ Python ของ kit
- ป้ายแท็กของคลังมีม ซึ่งเก็บในโฟลเดอร์ `.tad-clip` ภายในโฟลเดอร์คลังของคุณ
- โฟลเดอร์งานของคลิปที่ทำไปแล้ว ซึ่งอยู่ข้างไฟล์คลิปแต่ละไฟล์

ที่อาจหายคือไฟล์ที่คุณใส่เข้าไปในตัว plugin เอง เช่นฟอนต์ที่วางเพิ่มใน `assets/fonts` หลังอัปเดตให้เช็คอีกครั้ง

### โหลดคลิปจากเว็บไม่ได้ ทั้งที่ยังไม่มีรุ่นใหม่

เว็บวิดีโอเปลี่ยนระบบบ่อย ตัวโหลด yt-dlp จึงต้องอัปเดตบ่อยกว่า tad-clip พิมพ์ `อัปเดตตัวโหลดคลิป`
บน Mac Claude อัปเดตผ่าน Homebrew บน Windows สั่ง `yt-dlp.exe` อัปเดตตัวเอง

## ถอนการติดตั้ง

### 1. ถอด plugin

พิมพ์ `/plugin` → แท็บ **Installed** → เลือก tad-clip → **Uninstall**
หรือกดปุ่ม **+** → **Plugins** → **Manage plugins** แล้วถอน tad-clip
จะลบ marketplace ออกด้วยก็ได้ พิมพ์ `/plugin marketplace remove pun-nattapatch`

### 2. ลบโฟลเดอร์ `.tad-clip`

โฟลเดอร์นี้มีโมเดลถอดเสียง 3.1 GB, Python ของ kit, แคชดาวน์โหลด และน้ำหนักโมเดลตัดพื้นหลัง (ถ้าลงไว้) ลบแล้วได้พื้นที่คืนหลาย GB

Mac:

1. ใน Finder กด Shift+Cmd+G พิมพ์ `~` แล้วกด Enter จะเปิดโฟลเดอร์บ้านของคุณ
2. กด Cmd+Shift+. ให้เห็นโฟลเดอร์ที่ซ่อนอยู่
3. ลากโฟลเดอร์ `.tad-clip` ไปถังขยะ แล้วล้างถังขยะ

Windows:

1. ปิดแอป Claude ก่อน ไม่งั้นบางไฟล์อาจถูกใช้อยู่และลบไม่ได้
2. เปิด File Explorer พิมพ์ `%USERPROFILE%` ในช่องที่อยู่ แล้วกด Enter
3. คลิกขวาที่โฟลเดอร์ `.tad-clip` → Delete แล้วล้างถังขยะ

### 3. ของที่ไม่ได้ลบให้

| อะไร | ทำยังไงกับมัน |
|---|---|
| โปรแกรมที่ลงผ่าน Homebrew บน Mac | โปรแกรมอื่นใช้ร่วมได้ เก็บไว้ก็ได้ ถ้าจะลบ เปิด Terminal พิมพ์ `brew uninstall ffmpeg-full whisper-cpp yt-dlp` (ส่วน `node` กับ `uv` ลบเมื่อแน่ใจว่าไม่มีโปรแกรมอื่นใช้) |
| Git for Windows | Claude Code ใช้รันคำสั่ง เก็บไว้ |
| โฟลเดอร์งาน `tad-clip-<ชื่อคลิป>` ข้างคลิปของคุณ | มีคลิปที่ตัดเสร็จ ไฟล์ซับ และ `roughcut.mp4` ที่ใหญ่เกือบเท่าไฟล์ต้นฉบับ ลบเองถ้าไม่ใช้แล้ว |
| โฟลเดอร์ `.tad-clip` ในคลังมีม | เก็บป้ายแท็ก ลบได้ถ้าไม่ใช้คลังกับ tad-clip อีก |

---

หน้าแนะนำ tad-clip: [punnattapatch.com/free/tad-clip](https://punnattapatch.com/free/tad-clip?utm_source=github&utm_medium=docs&utm_campaign=tad-clip) · kit นี้ทำโดย @pun_nattapatch

[← กลับหน้าแรก (README)](../README.md)
