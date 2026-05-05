-- ============================================================
-- Minor OR Management — Supabase (PostgreSQL) Schema
-- สร้างโดย คล็อดคุง สำหรับมุ้กกี้
-- วันที่: 2026-05-05
-- ============================================================
-- วิธีใช้: copy ทั้งไฟล์ไปวางใน Supabase SQL Editor แล้วกด Run
-- ============================================================

-- ============================================================
-- 1. procedures — แฟ้มรายการหัตถการ + ราคา
-- ตอบโจทย์: ข้อ 2 (Top 5 op), ข้อ 3 (ราคา), ข้อ 7 (ชิ้นเนื้อ)
-- ============================================================
CREATE TABLE IF NOT EXISTS procedures (
    procedure_id    SERIAL PRIMARY KEY,
    procedure_name  TEXT NOT NULL UNIQUE,       -- ชื่อ EN (ใช้ match กับ HIS)
    procedure_name_th TEXT,                     -- ชื่อไทย (แสดงผล)
    default_price   INTEGER DEFAULT 0,          -- ราคาค่าหัตถการ (บาท)
    patho_fee       INTEGER DEFAULT 0,          -- ค่าส่งชิ้นเนื้อ (บาท) ถ้ามี
    division_code   TEXT,                       -- สาขาที่ทำบ่อย (default)
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE procedures IS 'รายการหัตถการทั้งหมดของห้องผ่าตัดเล็ก พร้อมราคา';

-- ============================================================
-- 2. surgeons — แฟ้มรายชื่อแพทย์
-- ตอบโจทย์: ข้อ 5 (แยกสาขา), ข้อ 10 (แพทย์ทำ op อะไร)
-- ============================================================
CREATE TABLE IF NOT EXISTS surgeons (
    surgeon_id      SERIAL PRIMARY KEY,
    surgeon_name    TEXT NOT NULL UNIQUE,        -- ชื่อเต็ม เช่น "นพ.สมชาย"
    division_code   TEXT,                        -- สาขา เช่น '70','72','75'
    division_name   TEXT,                        -- ชื่อสาขา เช่น 'ศัลยกรรม'
    license_no      TEXT,                        -- เลขใบประกอบวิชาชีพ (optional)
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE surgeons IS 'รายชื่อแพทย์ผ่าตัดทั้งหมด แก้ชื่อที่เดียวอัปเดตทุกเคส';

-- ============================================================
-- 3. nurses — แฟ้มรายชื่อพยาบาล
-- ตอบโจทย์: ข้อ 9 (progress tracking รายคน)
-- ============================================================
CREATE TABLE IF NOT EXISTS nurses (
    nurse_id        SERIAL PRIMARY KEY,
    nurse_name      TEXT NOT NULL UNIQUE,        -- ชื่อ เช่น "พว.มุ้กกี้"
    nurse_role      TEXT DEFAULT 'both',         -- 'scrub' / 'circ' / 'both'
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE nurses IS 'รายชื่อพยาบาลห้องผ่าตัด scrub/circulate';

-- ============================================================
-- 4. cases — แฟ้มหลัก (1 แถว = 1 เคสผ่าตัด)
-- ตอบโจทย์: ทุกข้อ (1-10)
-- ============================================================
CREATE TABLE IF NOT EXISTS cases (
    case_id             SERIAL PRIMARY KEY,

    -- ข้อมูลผู้ป่วย
    op_date             DATE NOT NULL,              -- วันผ่าตัด
    hn                  TEXT,                       -- Hospital Number
    an                  TEXT,                       -- Admission Number
    name                TEXT,                       -- ชื่อผู้ป่วย

    -- เชื่อมกับ master tables (FK)
    procedure_id        INTEGER REFERENCES procedures(procedure_id),
    surgeon_id          INTEGER REFERENCES surgeons(surgeon_id),

    -- ยังเก็บ text ไว้ด้วย (กันกรณี procedure/surgeon ยังไม่มีใน master)
    procedure_name      TEXT NOT NULL,              -- ชื่อหัตถการ (text backup)
    surgeon_name        TEXT,                       -- ชื่อแพทย์ (text backup)
    division_code       TEXT,                       -- สาขา

    -- ประเภทเคส → ตอบข้อ 6
    patient_type        TEXT,                       -- 'OPD' / 'IPD' / 'นอกเวลา'
    case_category       TEXT,                       -- 'เคสนัดหมาย' / 'Walk-in'
    op_type             TEXT DEFAULT 'elective',    -- 'elective' / 'emergency'
    estimated_time      TEXT,                       -- เวลาประมาณจาก HIS

    -- Status & Workflow → ตอบข้อ 8 (dashboard ความยุ่ง)
    status              TEXT DEFAULT 'scheduled',   -- scheduled → arrived → in_or → post_op → discharged / cancelled
    cancel_reason       TEXT,
    room_no             INTEGER DEFAULT 1,

    -- Timestamps (workflow) → ตอบข้อ 8, 9
    arrived_at          TIMESTAMPTZ,
    in_or_at            TIMESTAMPTZ,
    op_end_at           TIMESTAMPTZ,
    discharged_at       TIMESTAMPTZ,
    post_op_dest        TEXT DEFAULT 'transfer',

    -- Staff assignment → ตอบข้อ 9
    scrub_nurse_id      INTEGER REFERENCES nurses(nurse_id),
    circ_nurse_id       INTEGER REFERENCES nurses(nurse_id),
    scrub_nurse         TEXT,                       -- text backup
    circ_nurse          TEXT,                       -- text backup

    -- AI Prediction → ตอบข้อ 1 (วิจัย)
    ai_predicted_min    INTEGER,
    user_override_min   INTEGER,
    actual_duration_min INTEGER,

    -- Checklist พยาบาล
    oss_visited         BOOLEAN DEFAULT FALSE,
    oss_by_or           BOOLEAN DEFAULT FALSE,
    or_pre_visit        BOOLEAN DEFAULT FALSE,
    post_call           BOOLEAN DEFAULT FALSE,
    post_call_status    TEXT,

    -- Finance → ตอบข้อ 3, 7
    treatment_cost      INTEGER DEFAULT 0,          -- ค่าหัตถการจริง
    patho_sent          BOOLEAN DEFAULT FALSE,      -- ส่งชิ้นเนื้อหรือไม่
    patho_cost          INTEGER DEFAULT 0,          -- ค่าชิ้นเนื้อ

    -- HIS reference
    procnote            TEXT,                       -- procedure note จาก HIS

    -- Meta
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes สำหรับ query ที่ใช้บ่อย
CREATE INDEX IF NOT EXISTS idx_cases_op_date       ON cases(op_date);
CREATE INDEX IF NOT EXISTS idx_cases_status        ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_hn            ON cases(hn);
CREATE INDEX IF NOT EXISTS idx_cases_date_status   ON cases(op_date, status);
CREATE INDEX IF NOT EXISTS idx_cases_procedure     ON cases(procedure_id);
CREATE INDEX IF NOT EXISTS idx_cases_surgeon       ON cases(surgeon_id);
CREATE INDEX IF NOT EXISTS idx_cases_patient_type  ON cases(patient_type);
CREATE INDEX IF NOT EXISTS idx_cases_scrub_nurse   ON cases(scrub_nurse_id);
CREATE INDEX IF NOT EXISTS idx_cases_circ_nurse    ON cases(circ_nurse_id);

-- ป้องกัน import ซ้ำ
CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_unique_import
    ON cases(op_date, hn, procedure_name);

COMMENT ON TABLE cases IS 'เคสผ่าตัดทั้งหมด — 1 แถว = 1 เคส';

-- ============================================================
-- 5. prediction_log — ประวัติ AI ทำนาย (สำหรับวิจัย)
-- ตอบโจทย์: ข้อ 1 (AI accuracy + retrain)
-- ============================================================
CREATE TABLE IF NOT EXISTS prediction_log (
    pred_id             SERIAL PRIMARY KEY,
    case_id             INTEGER REFERENCES cases(case_id) ON DELETE CASCADE,
    model_version       TEXT,                       -- เวอร์ชัน model เช่น 'v1.0'
    procedure_name      TEXT,                       -- snapshot ชื่อหัตถการตอนทำนาย
    surgeon_name        TEXT,                       -- snapshot ชื่อแพทย์ตอนทำนาย
    predicted_min       INTEGER,                    -- AI ทำนาย (นาที)
    actual_min          INTEGER,                    -- เวลาจริง (นาที)
    abs_error           INTEGER,                    -- |predicted - actual|
    confidence          TEXT,                       -- confidence interval
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pred_case ON prediction_log(case_id);

COMMENT ON TABLE prediction_log IS 'บันทึกทุกครั้งที่ AI ทำนาย สำหรับวิเคราะห์ accuracy และ retrain';

-- ============================================================
-- 6. room_settings — ตั้งค่าห้องผ่าตัด
-- ============================================================
CREATE TABLE IF NOT EXISTS room_settings (
    room_no         INTEGER PRIMARY KEY,
    enabled         BOOLEAN DEFAULT TRUE,
    scrub_nurse_ids INTEGER[] DEFAULT '{}',      -- array ของ nurse_id
    circ_nurse_ids  INTEGER[] DEFAULT '{}',      -- array ของ nurse_id
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE room_settings IS 'ตั้งค่าห้องผ่าตัด เปิด/ปิด + assign พยาบาลประจำห้อง';

-- ============================================================
-- 7. audit_log — log การเปลี่ยนแปลง
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    log_id          SERIAL PRIMARY KEY,
    case_id         INTEGER REFERENCES cases(case_id) ON DELETE SET NULL,
    action          TEXT NOT NULL,               -- 'status_change', 'update', 'delete'
    old_value       TEXT,
    new_value       TEXT,
    detail          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at);

COMMENT ON TABLE audit_log IS 'บันทึกทุกการเปลี่ยนแปลง ใครแก้อะไรเมื่อไหร่';


-- ============================================================
-- AUTO-UPDATE updated_at — trigger สำหรับ PostgreSQL
-- (SQLite ไม่มี trigger แบบนี้ — นี่คือข้อดีของ PostgreSQL)
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cases_updated
    BEFORE UPDATE ON cases
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_procedures_updated
    BEFORE UPDATE ON procedures
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_surgeons_updated
    BEFORE UPDATE ON surgeons
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_nurses_updated
    BEFORE UPDATE ON nurses
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================================
-- SEED DATA: นำเข้ารายการหัตถการจาก or_minor_price.csv
-- ============================================================
INSERT INTO procedures (procedure_name, procedure_name_th, default_price) VALUES
    ('Excision', 'Excision', 2500),
    ('I&D', 'I&D', 2000),
    ('Off Perm Catheter', 'Off Perm Catheter', 1800),
    ('Electrocauterization (Wart on sole)', 'Electrocauterization จี้ตาปลา', 300),
    ('Debridement', 'Debridement', 2500),
    ('Suture Wound', 'Suture Wound', 2000),
    ('Frenolotomy', 'Frenolotomy', 1600),
    ('Partial/Total Nail Extraction', 'Partial/Total Nail Extraction', 1300),
    ('Circumcision', 'Circumcision', 2500),
    ('Lymphnode Biopsy', 'Lymphnode Biopsy', 2500),
    ('DLC UV Permanent/Double Lumen', 'DLC UV Permanent/Double Lumen', 2000),
    ('SSV Ligation & Foam Sclerotherapy', 'SSV Ligation & Foam Sclerotherapy', 8000),
    ('Vasectomy', 'Vasectomy', 2500),
    ('Upper Blepharoplasty', 'Upper Blepharoplasty', 6000),
    ('Lower Blepharoplasty', 'Lower Blepharoplasty', 6000),
    ('Rhinoplasty (excl. silicone)', 'Rhinoplasty (ไม่รวมซิลิโคน)', 10000),
    ('Vacuum Dressing', 'Vacuum Dressing', 2500),
    ('Electrocauterization (Wart)', 'Electrocauterization จี้หูด', 2000),
    ('Release Trigger', 'Release Trigger', 2500),
    ('Mini Face Lift', 'Mini Face Lift', 6000),
    ('Remove Foreign Body', 'Remove FB', 2500),
    ('Ligation', 'Ligation', 2000),
    ('Change Jejunostomy', 'Change Jejunostomy', 2000),
    ('Helios3 Test Spot (<1 sq.cm.)', 'Helios3 Test Spot น้อยกว่า 1 ตร.ซม.', 1000),
    ('Helios3 1-10 sq.cm.', 'Helios3 1-10 ตร.ซม.', 1500),
    ('Helios3 11-25 sq.cm.', 'Helios3 11-25 ตร.ซม.', 3000),
    ('Helios3 26-50 sq.cm.', 'Helios3 26-50 ตร.ซม.', 4000),
    ('Helios3 51-75 sq.cm.', 'Helios3 51-75 ตร.ซม.', 5000),
    ('Helios3 76-100 sq.cm.', 'Helios3 76-100 ตร.ซม.', 6000),
    ('Helios3 101-125 sq.cm.', 'Helios3 101-125 ตร.ซม.', 7000),
    ('Helios3 126-150 sq.cm.', 'Helios3 126-150 ตร.ซม.', 8000),
    ('Helios3 >150 sq.cm.', 'Helios3 มากกว่า 150 ตร.ซม.', 9000),
    ('Smaz <150 shots', 'Smaz น้อยกว่า 150 shot', 2000),
    ('Smaz 151-200 shots', 'Smaz 151-200 shot', 2500),
    ('Smaz 201-250 shots', 'Smaz 201-250 shot', 3000),
    ('Smaz 251-300 shots', 'Smaz 251-300 shot', 4000),
    ('Smaz 301-350 shots', 'Smaz 301-350 shot', 5000),
    ('Smaz 351-400 shots', 'Smaz 351-400 shot', 6000),
    ('Smaz >400 shots', 'Smaz มากกว่า 400 shot', 7000),
    ('Scarlet Full Face Session 1', 'Scarlet Full Face ครั้งที่ 1', 10000),
    ('Scarlet Full Face Session 2', 'Scarlet Full Face ครั้งที่ 2', 3000),
    ('Scarlet Full Face Session 3', 'Scarlet Full Face ครั้งที่ 3', 2000),
    ('Scarlet Half Face Session 1', 'Scarlet Half Face ครั้งที่ 1', 7000),
    ('Scarlet Half Face Session 2', 'Scarlet Half Face ครั้งที่ 2', 2000),
    ('Scarlet Half Face Session 3', 'Scarlet Half Face ครั้งที่ 3', 1000),
    ('Enerjet Session 1', 'Enerjet ครั้งที่ 1', 10000),
    ('Enerjet Session 2', 'Enerjet ครั้งที่ 2', 10000),
    ('Enerjet Session 3', 'Enerjet ครั้งที่ 3', 10000),
    ('EMSCULPT Abdomen/Buttock/Thigh 1zone x1', 'EMSCULPT หน้าท้อง/ก้น/ต้นขา 1จุด 1ครั้ง', 6000),
    ('EMSCULPT Abdomen/Buttock/Thigh 1zone x4', 'EMSCULPT หน้าท้อง/ก้น/ต้นขา 1จุด 4ครั้ง', 14000),
    ('EMSCULPT Abdomen/Buttock/Thigh 1zone x6', 'EMSCULPT หน้าท้อง/ก้น/ต้นขา 1จุด 6ครั้ง', 22000),
    ('EMSCULPT Abdomen/Buttock/Thigh 1zone x8', 'EMSCULPT หน้าท้อง/ก้น/ต้นขา 1จุด 8ครั้ง', 29000),
    ('EMSCULPT 2zone x1', 'EMSCULPT 2จุด 2หัว 1ครั้ง', 10000),
    ('EMSCULPT 2zone x4', 'EMSCULPT 2จุด 2หัว 4ครั้ง', 30000),
    ('EMSCULPT 2zone x6', 'EMSCULPT 2จุด 2หัว 6ครั้ง', 45000),
    ('EMSCULPT 2zone x8', 'EMSCULPT 2จุด 2หัว 8ครั้ง', 60000),
    ('Cooltech Size M 1zone', 'Cooltech ขนาด M 1ตำแหน่ง 1หัว', 5000),
    ('Cooltech Size M 2zone', 'Cooltech ขนาด M 2ตำแหน่ง 2หัว', 7000),
    ('Cooltech Size M 4zone', 'Cooltech ขนาด M 4ตำแหน่ง 4หัว', 12500),
    ('Cooltech Size L 1zone', 'Cooltech ขนาด L 1ตำแหน่ง 1หัว', 65000),
    ('ESWL Gov welfare Session 1', 'ESWL สวัสดิการข้าราชการ ครั้งที่ 1', 20000),
    ('ESWL Social security Session 1', 'ESWL ประกันสังคม ครั้งที่ 1', 20000),
    ('ESWL Universal coverage Session 1', 'ESWL หลักประกันสุขภาพถ้วนหน้า ครั้งที่ 1', 20000),
    ('ESWL Gov welfare Session 2', 'ESWL สวัสดิการข้าราชการ ครั้งที่ 2', 10000),
    ('ESWL Social security Session 2', 'ESWL ประกันสังคม ครั้งที่ 2', 10000),
    ('ESWL Universal coverage Session 2', 'ESWL หลักประกันสุขภาพถ้วนหน้า ครั้งที่ 2', 10000)
ON CONFLICT (procedure_name) DO NOTHING;

-- ============================================================
-- SEED DATA: ห้องผ่าตัด 4 ห้อง
-- ============================================================
INSERT INTO room_settings (room_no, enabled) VALUES
    (1, TRUE), (2, TRUE), (3, TRUE), (4, TRUE)
ON CONFLICT (room_no) DO NOTHING;


-- ============================================================
-- ตัวอย่าง QUERY ที่ตอบ 10 ข้อของมุ้กกี้
-- (ไม่ต้อง run — เป็นตัวอย่างให้ดูว่า schema นี้ query ยังไง)
-- ============================================================

/*
-- ข้อ 1: AI Prediction Accuracy
SELECT
    COUNT(*) AS total_predictions,
    AVG(ABS(c.ai_predicted_min - c.actual_duration_min)) AS mae,
    AVG(ABS(c.ai_predicted_min - c.actual_duration_min) * 100.0
        / NULLIF(c.actual_duration_min, 0)) AS mape_pct
FROM cases c
WHERE c.ai_predicted_min IS NOT NULL
  AND c.actual_duration_min IS NOT NULL
  AND c.actual_duration_min > 0
  AND c.patient_type != 'นอกเวลา';

-- ข้อ 2: Top 5 Operation ในแต่ละเดือน
SELECT
    TO_CHAR(c.op_date, 'YYYY-MM') AS month,
    p.procedure_name,
    COUNT(*) AS case_count
FROM cases c
JOIN procedures p ON c.procedure_id = p.procedure_id
GROUP BY month, p.procedure_name
ORDER BY month DESC, case_count DESC
LIMIT 5;

-- ข้อ 3: ราคาค่าผ่าตัดรวม / แยก operation
SELECT
    p.procedure_name,
    COUNT(*) AS case_count,
    SUM(c.treatment_cost) AS total_revenue,
    AVG(c.treatment_cost) AS avg_revenue
FROM cases c
JOIN procedures p ON c.procedure_id = p.procedure_id
WHERE c.status = 'discharged'
GROUP BY p.procedure_name
ORDER BY total_revenue DESC;

-- ข้อ 4: จำนวน operation ในแต่ละเดือน
SELECT
    TO_CHAR(op_date, 'YYYY-MM') AS month,
    COUNT(*) AS total_cases
FROM cases
WHERE status != 'cancelled'
GROUP BY month
ORDER BY month;

-- ข้อ 5: จำนวน case แยกตามสาขา
SELECT
    s.division_name,
    COUNT(*) AS case_count
FROM cases c
JOIN surgeons s ON c.surgeon_id = s.surgeon_id
GROUP BY s.division_name
ORDER BY case_count DESC;

-- ข้อ 6: ประเภทผู้ป่วย
SELECT
    patient_type,
    case_category,
    COUNT(*) AS n
FROM cases
GROUP BY patient_type, case_category
ORDER BY n DESC;

-- ข้อ 7: ค่าชิ้นเนื้อ + จำนวนส่ง
SELECT
    COUNT(*) FILTER (WHERE patho_sent = TRUE) AS patho_sent_count,
    SUM(patho_cost) AS total_patho_cost
FROM cases
WHERE status = 'discharged';

-- ข้อ 8: Dashboard ความยุ่งในแต่ละวัน
SELECT
    op_date,
    COUNT(*) AS total_cases,
    COUNT(*) FILTER (WHERE status = 'discharged') AS completed,
    COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
FROM cases
GROUP BY op_date
ORDER BY op_date DESC;

-- ข้อ 9: Progress tracking แต่ละคน (scrub nurse)
SELECT
    n.nurse_name,
    COUNT(*) AS total_cases,
    COUNT(*) FILTER (WHERE c.status = 'discharged') AS completed
FROM cases c
JOIN nurses n ON c.scrub_nurse_id = n.nurse_id
GROUP BY n.nurse_name
ORDER BY total_cases DESC;

-- ข้อ 10: แพทย์คนไหนทำ operation อะไรบ้าง
SELECT
    s.surgeon_name,
    p.procedure_name,
    COUNT(*) AS case_count
FROM cases c
JOIN surgeons s ON c.surgeon_id = s.surgeon_id
JOIN procedures p ON c.procedure_id = p.procedure_id
GROUP BY s.surgeon_name, p.procedure_name
ORDER BY s.surgeon_name, case_count DESC;
*/
