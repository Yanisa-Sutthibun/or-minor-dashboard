# 🚀 Supabase Setup Guide — Minor OR Dashboard

คู่มือ setup Supabase สำหรับโปรเจกต์ Minor OR Dashboard (ใช้เวลาประมาณ 15-20 นาที)

---

## 📋 Phase 1: สมัคร + สร้าง Project

### Step 1: สมัครบัญชี Supabase

1. เปิด https://supabase.com/dashboard
2. คลิก **"Start your project"** หรือ **"Sign in"**
3. เลือก login ด้วย:
   - **GitHub** (แนะนำ — เชื่อมกับ repo ได้ทันที)
   - หรือ Email + password

### Step 2: สร้าง New Project

1. คลิก **"New project"** ที่มุมขวาบน
2. เลือก **Organization** (ถ้าเป็นครั้งแรกจะสร้างให้อัตโนมัติ ชื่อตาม GitHub username)
3. กรอกรายละเอียด:

   | Field | ค่าที่แนะนำ |
   |---|---|
   | **Project name** | `minor-or-dashboard` |
   | **Database password** | สุ่มแบบยาว 16+ ตัว (กดปุ่ม `Generate a password` ได้) **— จดไว้!** |
   | **Region** | `Southeast Asia (Singapore)` — ใกล้ไทยที่สุด ping ~30ms |
   | **Pricing plan** | `Free` (500MB DB, 1GB Storage, 50K MAU — เกินพอ) |

4. คลิก **"Create new project"**
5. รอประมาณ 1-2 นาที (Supabase จะ provision database ให้)

> ⚠️ **สำคัญ:** Database password จะใช้สำหรับ connection string — copy เก็บไว้ที่ password manager เลย ถ้าลืมต้อง reset ใหม่

---

## 🔑 Step 3: เอา Connection String + API Keys

หลัง project พร้อม:

### A. Connection String (DATABASE_URL)

1. ที่ Sidebar ซ้าย → **Project Settings** (รูปเฟือง ⚙️)
2. เลือกเมนู **Database**
3. เลื่อนลงไปหา section **"Connection string"**
4. เลือก tab **"URI"** หรือ **"Transaction pooler"** (แนะนำ pooler สำหรับ Streamlit)
5. ก๊อบ string ที่ขึ้นต้นด้วย `postgresql://...`

   ตัวอย่าง:
   ```
   postgresql://postgres.[project-ref]:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
   ```

6. แทนที่ `[YOUR-PASSWORD]` ด้วย password ที่จดไว้ตอนสร้าง project

### B. API Keys (สำหรับใช้ supabase-py ในอนาคต — optional)

1. **Project Settings** → **API**
2. คัดลอก:
   - **Project URL** (เช่น `https://xxxxx.supabase.co`)
   - **anon public key** (long string ขึ้นต้นด้วย `eyJ...`)
   - **service_role key** (สำหรับ admin operations — เก็บเป็นความลับ ห้าม commit!)

---

## 📤 Step 4: ส่งให้คล็อด

หลังได้ credentials แล้ว ส่งให้ผมในรูปแบบนี้ (ก๊อบจาก dashboard มาวางได้เลย):

```
DATABASE_URL=postgresql://postgres.xxxxx:yourpassword@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
```

> 💡 **Tip:** ถ้าไม่อยากส่ง password ตรงๆ ให้ใช้ placeholder `[PASSWORD]` แทน แล้วมุ้กกใส่เองทีหลังตอน config secrets

---

## ✅ Checklist ก่อนไป Phase 2

- [ ] สร้าง Supabase project แล้ว
- [ ] จด Database password ไว้
- [ ] ก๊อบ Connection string (URI / Pooler) ได้
- [ ] ส่ง connection string ให้คล็อด (หรือเก็บไว้เองตอน config)

---

## 🛡️ Security Notes

- **`.env` และ `secrets.toml`** จะถูก `.gitignore` แล้ว — ไม่ commit ขึ้น GitHub
- **service_role key** ห้ามใช้ใน frontend หรือ Streamlit deploy — ใช้ได้แค่ฝั่ง backend admin
- **anon key** ปลอดภัยที่จะ expose (มี RLS ป้องกัน) แต่ตอนนี้ใช้ psycopg2 + DATABASE_URL ก็พอ
- **Database password** ถ้าหลุดให้ reset ทันทีที่ Project Settings → Database → "Reset database password"

---

## 🆘 Troubleshooting

**Q: ลืม password ทำยังไง?**
A: Project Settings → Database → "Reset database password" → ใส่ใหม่ → update DATABASE_URL

**Q: เลือก region ไหนดี?**
A: `Southeast Asia (Singapore)` สำหรับไทย — region อื่นจะ ping ช้ากว่า 100ms

**Q: Free tier เพียงพอไหม?**
A: ของมุ้กก 701 cases ≈ 12MB → ใช้ไม่ถึง 3% ของ quota 500MB และ Free tier ไม่จำกัด API requests
