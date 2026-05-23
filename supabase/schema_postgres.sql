-- ═══════════════════════════════════════════════════════════════════
-- 🏥 Minor OR Dashboard — PostgreSQL Schema (Supabase)
-- ═══════════════════════════════════════════════════════════════════
-- วิธีใช้:
--   1. เปิด Supabase Dashboard → SQL Editor
--   2. คลิก "New query"
--   3. Paste ไฟล์นี้ทั้งหมด → คลิก "Run" (Ctrl+Enter)
--   4. ตรวจสอบที่ Table Editor ว่ามี 6 tables: cases, audit_log,
--      prediction_log, backup_log, room_settings, app_settings
-- ═══════════════════════════════════════════════════════════════════

-- ─── Drop ทั้งหมดก่อน (ถ้าต้องการ rerun) ─────────────────────────
-- ⚠️ เปิด comment เฉพาะตอน reset เท่านั้น — ระวังลบ data จริง!
-- DROP TABLE IF EXISTS audit_log CASCADE;
-- DROP TABLE IF EXISTS prediction_log CASCADE;
-- DROP TABLE IF EXISTS backup_log CASCADE;
-- DROP TABLE IF EXISTS room_settings CASCADE;
-- DROP TABLE IF EXISTS app_settings CASCADE;
-- DROP TABLE IF EXISTS cases CASCADE;

-- ═══════════════════════════════════════════════════════════════════
-- TABLE 1: cases — ตารางหลักเก็บเคสผ่าตัด
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS cases (
    case_id             SERIAL PRIMARY KEY,
    op_date             TEXT NOT NULL,         -- 'YYYY-MM-DD'
    name                TEXT,                  -- ชื่อ-สกุล (PII)
    hn                  TEXT,                  -- Hospital Number
    an                  TEXT,                  -- Admission Number
    diagnosis           TEXT,
    procedure_name      TEXT NOT NULL,
    surgeon_name        TEXT,                  -- intra-op surgeon (จริงที่ทำ)
    division_code       TEXT,                  -- รหัสแผนก (e.g., '75' = นรีเวช)
    case_category       TEXT,                  -- 'Elective', 'Urgent', 'Emergency'
    patient_type        TEXT,                  -- 'นัดหมาย' / 'Walk-in'
    op_type             TEXT,
    estimated_time      TEXT,
    procnote            TEXT,

    -- Status
    status              TEXT DEFAULT 'scheduled',  -- scheduled/arrived/in_or/post_op/discharged/cancelled
    cancel_reason       TEXT,

    -- Checkboxes (ใช้ INTEGER 0/1 เพื่อ compat กับโค้ดเดิม)
    oss_visited         INTEGER DEFAULT 0,
    oss_by_or           INTEGER DEFAULT 0,
    or_pre_visit        INTEGER DEFAULT 0,
    post_call           INTEGER DEFAULT 0,
    post_call_status    TEXT,

    -- Timing & AI
    ai_predicted_min    INTEGER,
    user_override_min   INTEGER,
    actual_duration_min INTEGER,
    scrub_nurse         TEXT,
    circ_nurse          TEXT,
    anesthesia_type     TEXT,
    wait_min            INTEGER DEFAULT 0,
    room_no             INTEGER DEFAULT 1,

    -- Workflow timestamps (v2)
    arrived_at          TEXT,
    in_or_at            TEXT,
    op_end_at           TEXT,
    discharged_at       TEXT,
    post_op_dest        TEXT DEFAULT 'transfer',
    treatment_cost      INTEGER DEFAULT 0,
    patho_cost          INTEGER DEFAULT 0,

    -- Scheduled surgeon (จาก schedule.csv — ไม่ overwrite ตอน intraop import)
    scheduled_surgeon   TEXT,

    -- Meta
    created_at          TEXT DEFAULT (to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS')),
    updated_at          TEXT DEFAULT (to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_cases_op_date      ON cases(op_date);
CREATE INDEX IF NOT EXISTS idx_cases_status       ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_hn           ON cases(hn);
CREATE INDEX IF NOT EXISTS idx_cases_date_status  ON cases(op_date, status);
CREATE INDEX IF NOT EXISTS idx_cases_surgeon      ON cases(surgeon_name);
CREATE INDEX IF NOT EXISTS idx_cases_procedure    ON cases(procedure_name);


-- ═══════════════════════════════════════════════════════════════════
-- TABLE 2: audit_log — ประวัติการแก้ไข
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS audit_log (
    log_id      SERIAL PRIMARY KEY,
    case_id     INTEGER,
    action      TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    detail      TEXT,
    created_at  TEXT DEFAULT (to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);


-- ═══════════════════════════════════════════════════════════════════
-- TABLE 3: prediction_log — เก็บ ML predictions สำหรับ retrain + วิจัย
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS prediction_log (
    pred_id          SERIAL PRIMARY KEY,
    case_id          INTEGER,
    model_version    TEXT,
    procedure_name   TEXT,
    surgeon_name     TEXT,
    predicted_min    INTEGER,
    actual_min       INTEGER,
    abs_error        INTEGER,
    confidence       TEXT,
    created_at       TEXT DEFAULT (to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_pred_case ON prediction_log(case_id);


-- ═══════════════════════════════════════════════════════════════════
-- TABLE 4: backup_log — ประวัติการ backup
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS backup_log (
    backup_id   SERIAL PRIMARY KEY,
    backup_path TEXT,
    row_count   INTEGER,
    created_at  TEXT DEFAULT (to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS'))
);


-- ═══════════════════════════════════════════════════════════════════
-- TABLE 5: room_settings — การตั้งค่าห้องผ่าตัด + nurse assignment
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS room_settings (
    room_no     INTEGER PRIMARY KEY,
    enabled     INTEGER DEFAULT 1,
    scrub_json  TEXT DEFAULT '["",""]',
    circ_json   TEXT DEFAULT '["","","",""]',
    updated_at  TEXT DEFAULT (to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS'))
);


-- ═══════════════════════════════════════════════════════════════════
-- TABLE 6: app_settings — key/value store (flags, configs)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);


-- ═══════════════════════════════════════════════════════════════════
-- TRIGGER: auto-update updated_at เมื่อมีการแก้ไข cases
-- ═══════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION update_cases_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cases_updated_at ON cases;
CREATE TRIGGER trg_cases_updated_at
    BEFORE UPDATE ON cases
    FOR EACH ROW
    EXECUTE FUNCTION update_cases_updated_at();


-- ═══════════════════════════════════════════════════════════════════
-- VERIFY: ตรวจสอบว่า tables สร้างครบ
-- ═══════════════════════════════════════════════════════════════════
SELECT
    table_name,
    (SELECT count(*) FROM information_schema.columns WHERE table_name = t.table_name) AS column_count
FROM information_schema.tables t
WHERE table_schema = 'public'
  AND table_name IN ('cases', 'audit_log', 'prediction_log', 'backup_log', 'room_settings', 'app_settings')
ORDER BY table_name;

-- คาดหวัง:
-- app_settings    | 2
-- audit_log       | 7
-- backup_log      | 4
-- cases           | 39
-- prediction_log  | 10
-- room_settings   | 5
