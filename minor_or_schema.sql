-- ============================================================================
-- Minor OR Statistics — SQL Schema for 1-month Trial
-- Author: คล็อดคุง x มุ้กก | Target DB: SQLite 3 (พร้อม migrate ไป PostgreSQL)
-- ============================================================================
-- แนวคิด: 3 tables แบบ normalized
--   patients  (1 คน 1 HN)
--   visits    (1 visit = 1 ครั้งที่มา รพ.; OPD หรือ IPD แยกด้วย an)
--   or_cases  (1 row = 1 หัตถการใน ห้องผ่าตัดเล็ก)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Table 1: patients — ทะเบียนผู้ป่วย
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patients (
    hn          TEXT PRIMARY KEY,           -- Hospital Number (ไม่ซ้ำ 1 คน 1 HN ตลอดชีวิต)
    gender      TEXT CHECK (gender IN ('M','F','O')),
    birth_year  INTEGER,                    -- ปี ค.ศ. (เก็บแค่ปี คำนวณอายุได้)
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);


-- ----------------------------------------------------------------------------
-- Table 2: visits — การมา รพ. แต่ละครั้ง
--   OPD: ไม่มี AN           → an IS NULL     (มาผ่าตัดเล็ก กลับบ้านวันเดียวกัน)
--   IPD: มี AN (Admission#) → an IS NOT NULL (นอน รพ.)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS visits (
    visit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    hn          TEXT    NOT NULL,
    an          TEXT,                                        -- NULL = OPD, ค่า = IPD
    visit_type  TEXT    NOT NULL CHECK (visit_type IN ('OPD','IPD')),
    visit_date  TEXT    NOT NULL,                            -- 'YYYY-MM-DD'
    ward        TEXT,                                        -- ถ้า IPD: ward; OPD: null/clinic name
    FOREIGN KEY (hn) REFERENCES patients(hn),
    -- กันข้อมูลขัดแย้ง: IPD ต้องมี AN เสมอ, OPD ห้ามมี
    CHECK ((visit_type = 'IPD' AND an IS NOT NULL) OR
           (visit_type = 'OPD' AND an IS NULL))
);


-- ----------------------------------------------------------------------------
-- Table 3: or_cases — หัตถการในห้องผ่าตัดเล็ก (fact table)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS or_cases (
    case_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id             INTEGER NOT NULL,
    op_date              TEXT NOT NULL,        -- 'YYYY-MM-DD'
    op_start_time        TEXT,                 -- 'HH:MM'
    op_end_time          TEXT,
    procedure_name       TEXT NOT NULL,        -- ชื่อหัตถการ (normalized UPPER)
    surgeon_name         TEXT,
    division             TEXT,                 -- เช่น '75' (Minor OR)
    scrub_nurse          TEXT,
    circ_nurse           TEXT,
    anesthesia_type      TEXT,                 -- LA/GA/MAC/spinal
    op_type              TEXT CHECK (op_type IN ('elective','emergency')),
    ai_predicted_min     INTEGER,
    user_override_min    INTEGER,
    actual_duration_min  INTEGER,
    wait_min             INTEGER,
    room_no              INTEGER,
    notes                TEXT,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (visit_id) REFERENCES visits(visit_id)
);


-- ----------------------------------------------------------------------------
-- Indexes — เร่ง query ที่ใช้บ่อย
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_visits_date_type  ON visits(visit_date, visit_type);
CREATE INDEX IF NOT EXISTS idx_visits_hn         ON visits(hn);
CREATE INDEX IF NOT EXISTS idx_or_cases_op_date  ON or_cases(op_date);
CREATE INDEX IF NOT EXISTS idx_or_cases_proc     ON or_cases(procedure_name);



-- ============================================================================
-- CORE QUERIES ที่คุณมุ้กกขอ
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Q1: จำนวนหัตถการทั้งหมด ใน 1 เดือนล่าสุด
-- ----------------------------------------------------------------------------
SELECT COUNT(*) AS total_cases
FROM or_cases
WHERE op_date >= DATE('now', '-30 days');


-- ----------------------------------------------------------------------------
-- Q2: Top 5 หัตถการ (เรียงจากทำบ่อยสุด)
-- ----------------------------------------------------------------------------
SELECT
    procedure_name,
    COUNT(*)                           AS n_cases,
    ROUND(AVG(actual_duration_min),1)  AS avg_min,
    ROUND(AVG(ai_predicted_min),1)     AS avg_ai_pred
FROM or_cases
WHERE op_date >= DATE('now', '-30 days')
GROUP BY procedure_name
ORDER BY n_cases DESC
LIMIT 5;


-- ----------------------------------------------------------------------------
-- Q3: จำนวนผู้ป่วยทั้งหมด แยก OPD / IPD (ใน 1 เดือน)
--   unique_patients = นับคน (HN ไม่ซ้ำ)
--   total_visits    = นับครั้งที่มา (1 คนอาจมาหลายครั้ง)
-- ----------------------------------------------------------------------------
SELECT
    visit_type,
    COUNT(DISTINCT hn) AS unique_patients,
    COUNT(*)           AS total_visits
FROM visits
WHERE visit_date >= DATE('now', '-30 days')
GROUP BY visit_type;


-- ----------------------------------------------------------------------------
-- Q4 (Bonus): รวมจำนวนเคส + ผู้ป่วย OPD/IPD ในคำสั่งเดียว
--   JOIN or_cases กับ visits เพื่อดู OPD/IPD ของแต่ละเคสผ่าตัด
-- ----------------------------------------------------------------------------
SELECT
    v.visit_type,
    COUNT(DISTINCT v.hn)     AS unique_patients,
    COUNT(DISTINCT c.case_id) AS n_operations
FROM or_cases c
JOIN visits   v ON v.visit_id = c.visit_id
WHERE c.op_date >= DATE('now', '-30 days')
GROUP BY v.visit_type;



-- ============================================================================
-- SAMPLE INSERT (ไว้ทดสอบ / seed data)
-- ============================================================================
-- ผู้ป่วย OPD (ไม่มี AN) — มาผ่าตัด EXCISION MASS
INSERT INTO patients (hn, gender, birth_year) VALUES ('HN001', 'M', 1980);
INSERT INTO visits (hn, an, visit_type, visit_date, ward)
            VALUES ('HN001', NULL, 'OPD', '2026-04-14', 'OR-Minor-OPD');
INSERT INTO or_cases (visit_id, op_date, op_start_time, procedure_name,
                      surgeon_name, division, scrub_nurse, circ_nurse,
                      anesthesia_type, op_type, ai_predicted_min,
                      actual_duration_min, room_no)
            VALUES (last_insert_rowid(), '2026-04-14', '09:00',
                    'EXCISION MASS', 'Dr.A', '75', 'N1', 'N2',
                    'LA', 'elective', 30, 35, 1);

-- ผู้ป่วย IPD (มี AN) — ผ่าตัดจากที่นอน รพ. ลงมาทำที่ minor OR
INSERT INTO patients (hn, gender, birth_year) VALUES ('HN002', 'F', 1975);
INSERT INTO visits (hn, an, visit_type, visit_date, ward)
            VALUES ('HN002', 'AN550001', 'IPD', '2026-04-14', 'Ward 5B');
INSERT INTO or_cases (visit_id, op_date, op_start_time, procedure_name,
                      surgeon_name, division, anesthesia_type, op_type,
                      ai_predicted_min, actual_duration_min, room_no)
            VALUES (last_insert_rowid(), '2026-04-14', '10:30',
                    'DEBRIDEMENT WOUND', 'Dr.B', '75', 'LA', 'elective',
                    25, 28, 1);


-- ============================================================================
-- NOTE: Migration path → PostgreSQL
-- ============================================================================
-- พอ trial จบ ย้ายไป PostgreSQL แค่แก้นิดหน่อย:
--   TEXT          → VARCHAR(...) หรือ TEXT (PG ก็มี)
--   INTEGER PK AUTOINCREMENT → SERIAL PRIMARY KEY
--   DATE('now','-30 days')   → CURRENT_DATE - INTERVAL '30 days'
--   last_insert_rowid()      → RETURNING visit_id หรือ CURRVAL('visits_visit_id_seq')
-- โครงสร้าง table/index/query logic เหมือนกันทั้งหมด
