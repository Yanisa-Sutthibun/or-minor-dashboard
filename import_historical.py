"""
import_historical.py — รวม scheduling CSV + intraop CSV แล้ว import เข้า cases table
ใช้สำหรับข้อมูลย้อนหลังที่ผ่าตัดเสร็จแล้ว (status = discharged)

Case category logic:
    เคสนัดหมาย = reqdate < opedate (booked at least 1 day in advance)
    Walk-in    = reqdate == opedate, missing reqdate, or reqdate > opedate

Plus walk-in import from cost Excel for cases not in schedule.csv.
"""
import pandas as pd
import sqlite3
import os
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'minor_or.db')


def _classify_case_category(req_iso, op_iso):
    """Return 'เคสนัดหมาย' if booked in advance, else 'Walk-in'.

    req_iso, op_iso: 'YYYY-MM-DD' strings (output of _norm_date), None,
    or pandas NaN (float — happens when row missing date).
    """
    # Defensive: pandas reads missing → NaN (float, not None) which is
    # truthy! Need explicit pd.isna check before truthiness test, otherwise
    # str < float crashes in lexicographic compare below.
    try:
        if pd.isna(req_iso) or pd.isna(op_iso):
            return 'Walk-in'
    except (TypeError, ValueError):
        pass
    if not req_iso or not op_iso:
        return 'Walk-in'
    # Force both to str — defensive against NaN/numeric/Timestamp slipping through
    req_s, op_s = str(req_iso), str(op_iso)
    return 'เคสนัดหมาย' if req_s < op_s else 'Walk-in'


def _norm_date(d):
    """Convert '6/5/2026 00:00:00' -> '2026-05-06'"""
    if pd.isna(d):
        return None
    parts = str(d).split(' ')[0].split('/')
    if len(parts) == 3:
        return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
    return None


def _time_int_to_hhmm(t):
    """Convert 91800 -> '09:18', 80000 -> '08:00'"""
    if pd.isna(t):
        return None
    t = int(t)
    hh = t // 10000
    mm = (t % 10000) // 100
    return f"{hh:02d}:{mm:02d}"


def _make_timestamp(date_str, time_int):
    """Combine date '2026-05-06' + time 91800 -> '2026-05-06 09:18:00'"""
    if not date_str or pd.isna(time_int):
        return None
    hhmm = _time_int_to_hhmm(time_int)
    if not hhmm:
        return None
    return f"{date_str} {hhmm}:00"


def _duration_to_min(d):
    """Convert '00:32:00' -> 32"""
    if pd.isna(d):
        return None
    parts = str(d).split(':')
    if len(parts) >= 2:
        return int(parts[0]) * 60 + int(parts[1])
    return None


def _classify_patient_type(an, estmtime, procnote):
    """Classify patient type."""
    an = str(an or '').strip()
    if an and an.upper() not in ('', 'NAN', 'NONE', '-'):
        return 'IPD'
    # Check after-hours by estmtime
    est = str(estmtime or '').strip()
    note = str(procnote or '').strip()
    if 'นอกเวลา' in note:
        return 'นอกเวลา'
    if est:
        try:
            t = int(est)
            if t >= 160000 or t < 70000:
                return 'นอกเวลา'
        except ValueError:
            pass
    return 'OPD'


def import_historical(sched_path: str, intra_path: str, dry_run: bool = False):
    """
    Import historical data from 2 CSV files.
    
    sched_path: scheduling CSV (has procedure, diagnosis, patient info)
    intra_path: intraop CSV (has timestamps, nurses, duration)
    """
    # Read files
    sched = pd.read_csv(sched_path, encoding='utf-16')
    intra = pd.read_csv(intra_path, encoding='utf-16')
    
    # Normalize keys for matching
    sched['_date'] = sched['opedate'].apply(_norm_date)
    intra['_date'] = intra['opedate'].apply(_norm_date)
    sched['_hn'] = sched['hn'].astype(str).str.strip()
    intra['_hn'] = intra['hn'].astype(str).str.strip()
    
    # Build intraop lookup: (hn, date) -> row
    intra_lookup = {}
    for _, row in intra.iterrows():
        key = (row['_hn'], row['_date'])
        intra_lookup[key] = row
    
    # Connect DB
    conn = sqlite3.connect(DB_PATH)
    
    # Ensure diagnosis column exists
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(cases)").fetchall()}
    if 'diagnosis' not in existing_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN diagnosis TEXT")
        conn.commit()
    
    inserted = 0
    skipped = 0
    results = []
    
    # Ensure requested_date column exists (for traceability + future reclassification)
    if 'requested_date' not in existing_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN requested_date TEXT")
        conn.commit()
        existing_cols.add('requested_date')

    skipped_no_date = 0
    skipped_no_hn = 0

    for _, s in sched.iterrows():
        hn = str(s['hn']).strip()
        op_date = s['_date']
        # Defensive: skip rows missing critical fields
        # (pandas NaN bypasses `if not x` because NaN is truthy)
        if pd.isna(op_date) or not op_date or str(op_date).lower() in ('nan', 'none', ''):
            skipped_no_date += 1
            continue
        if not hn or hn.lower() in ('nan', 'none', ''):
            skipped_no_hn += 1
            continue
        req_date = _norm_date(s.get('reqdate'))
        case_cat = _classify_case_category(req_date, op_date)
        proc = str(s.get('icd9cm_name', '') or '').strip()
        if not proc or proc.upper() in ('NAN', 'NONE', ''):
            proc = '-'
        
        # Check duplicate
        exists = conn.execute(
            "SELECT case_id FROM cases WHERE op_date=? AND hn=? AND procedure_name=?",
            (op_date, hn, proc)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        
        # Get diagnosis
        diag = str(s.get('icd10_name', '') or '').strip()
        if diag.upper() in ('', 'NAN', 'NONE'):
            diag = None
        
        # Get patient info from scheduling
        name = str(s.get('dspname', '') or '').strip()
        an_val = s.get('an')
        if pd.isna(an_val) or str(an_val).strip().upper() in ('', 'NAN', 'NONE'):
            an_val = None
        else:
            an_val = str(int(float(an_val))) if '.' in str(an_val) else str(an_val)
        
        division = str(s.get('division', '') or '').strip()
        surgeon = str(s.get('surgstfnm', '') or '').strip()
        if surgeon.upper() in ('NAN', 'NONE', ''):
            surgeon = None
        procnote = str(s.get('procnote', '') or '').strip()
        if procnote.upper() in ('NAN', 'NONE', ''):
            procnote = None
        estmtime = s.get('estmtime')
        
        pt_type = _classify_patient_type(an_val, estmtime, procnote)
        
        # Get intraop data
        key = (hn, op_date)
        i = intra_lookup.get(key)
        
        if i is not None:
            # Case was operated — status = discharged
            status = 'discharged'
            
            arrived_at = _make_timestamp(op_date, i.get('arrivtime'))
            # Use room-in / room-out (wheels-in / wheels-out) — มาตรฐาน OR utilization
            # ห้องเริ่มยุ่งตั้งแต่คนไข้เข้าห้อง ไม่ใช่ตอนลงมีด
            # (เดิมใช้ opesttime/opendtime = incision/closure ซึ่งสั้นกว่าจริง ~5-15 นาที)
            in_or_at = _make_timestamp(op_date, i.get('roomtimein'))
            op_end_at = _make_timestamp(op_date, i.get('roomtimeout'))
            actual_min = _duration_to_min(i.get('opusetime'))
            
            scrub = str(i.get('nursurgnm', '') or '').strip()
            if scrub.upper() in ('NAN', 'NONE', ''):
                scrub = None
            circ = str(i.get('nurcircunm', '') or '').strip()
            if circ.upper() in ('NAN', 'NONE', ''):
                circ = None
            
            # Surgeon from intraop (more reliable — actual surgeon)
            intra_surg = str(i.get('dctnm', '') or '').strip()
            if intra_surg and intra_surg.upper() not in ('NAN', 'NONE', ''):
                surgeon = intra_surg
            
            room_no = i.get('orroom', 32)
            
            # Calculate wait_min (arrived → op start)
            wait_min = None
            if arrived_at and in_or_at:
                try:
                    t_arr = datetime.strptime(arrived_at, '%Y-%m-%d %H:%M:%S')
                    t_start = datetime.strptime(in_or_at, '%Y-%m-%d %H:%M:%S')
                    wait_min = max(0, int((t_start - t_arr).total_seconds() / 60))
                except Exception:
                    pass
            
            # discharged_at = op_end + ~10 min (approximate)
            discharged_at = op_end_at
        else:
            # Not in intraop — cancelled
            status = 'cancelled'
            arrived_at = None
            in_or_at = None
            op_end_at = None
            discharged_at = None
            actual_min = None
            scrub = None
            circ = None
            wait_min = None
            room_no = 32
        
        if not dry_run:
            conn.execute("""
                INSERT INTO cases (op_date, name, hn, an, diagnosis, procedure_name,
                                  surgeon_name, division_code, case_category, patient_type,
                                  status, arrived_at, in_or_at, op_end_at, discharged_at,
                                  actual_duration_min, scrub_nurse, circ_nurse,
                                  wait_min, room_no, procnote, requested_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                op_date, name, hn, an_val, diag, proc,
                surgeon, division, case_cat, pt_type,
                status, arrived_at, in_or_at, op_end_at, discharged_at,
                actual_min, scrub, circ,
                wait_min, room_no, procnote, req_date,
            ))
        
        inserted += 1
        results.append({
            'name': name, 'hn': hn, 'date': op_date, 'proc': proc,
            'diag': diag, 'status': status,
            'duration': actual_min, 'wait': wait_min,
        })
    
    if not dry_run:
        conn.commit()
    conn.close()

    if skipped_no_date or skipped_no_hn:
        print(f"[IMPORT] Skipped invalid rows: "
              f"{skipped_no_date} missing op_date, "
              f"{skipped_no_hn} missing HN")

    # Stash skip stats on function attribute (accessible to callers like
    # import_historical_with_costs that want to surface them to the UI)
    import_historical._last_skip_stats = {
        'no_date': skipped_no_date,
        'no_hn': skipped_no_hn,
    }

    return inserted, skipped, results


def reclassify_existing(sched_path: str, dry_run: bool = False):
    """Re-classify case_category for cases already imported with the old buggy
    logic (where everything was hardcoded to 'เคสนัดหมาย').

    Reads the scheduling CSV again and updates case_category + requested_date
    for every matching (hn, op_date) row in the DB.
    """
    sched = pd.read_csv(sched_path, encoding='utf-16')
    sched['_op_date'] = sched['opedate'].apply(_norm_date)
    sched['_req_date'] = sched['reqdate'].apply(_norm_date)
    sched['_hn'] = sched['hn'].astype(str).str.strip()

    conn = sqlite3.connect(DB_PATH)

    # Make sure requested_date column exists
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(cases)").fetchall()}
    if 'requested_date' not in existing_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN requested_date TEXT")
        conn.commit()

    updated = 0
    not_found = 0
    set_to_walkin, set_to_scheduled, unchanged = 0, 0, 0
    samples = []

    for _, s in sched.iterrows():
        hn = s['_hn']
        op_date = s['_op_date']
        req_date = s['_req_date']
        # pandas NaN check — NaN is truthy in `if not x` so use pd.isna explicitly
        if pd.isna(op_date) or not op_date:
            continue
        new_cat = _classify_case_category(req_date, op_date)

        # Find matching case in DB
        rows = conn.execute(
            "SELECT case_id, case_category FROM cases WHERE op_date=? AND hn=?",
            (op_date, hn)
        ).fetchall()
        if not rows:
            not_found += 1
            continue

        for case_id, old_cat in rows:
            samples.append((hn, op_date, req_date, old_cat, new_cat))
            if old_cat == new_cat:
                unchanged += 1
            elif new_cat == 'Walk-in':
                set_to_walkin += 1
            else:
                set_to_scheduled += 1
            if not dry_run:
                conn.execute(
                    "UPDATE cases SET case_category=?, requested_date=? WHERE case_id=?",
                    (new_cat, req_date, case_id)
                )
                updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    return {
        'updated': updated, 'not_found': not_found, 'unchanged': unchanged,
        'set_to_walkin': set_to_walkin, 'set_to_scheduled': set_to_scheduled,
        'samples': samples,
    }


def reimport_timestamps(intra_path: str, dry_run: bool = False):
    """Re-update room timestamps (arrived_at, in_or_at, op_end_at) and
    actual_duration_min for cases already in DB, by re-reading the intraop CSV.

    ใช้ตอนเปลี่ยน column mapping ของ in_or_at/op_end_at เช่น เปลี่ยนจาก
    opesttime/opendtime → roomtimein/roomtimeout — ทำให้ heatmap "ช่วงเวลาที่ยุ่ง"
    ใช้ค่า room-in/room-out จริง (มาตรฐาน OR utilization)
    """
    intra = pd.read_csv(intra_path, encoding='utf-16')
    intra['_op_date'] = intra['opedate'].apply(_norm_date)
    intra['_hn'] = intra['hn'].astype(str).str.strip()

    conn = sqlite3.connect(DB_PATH)

    updated = 0
    not_found = 0
    changed = 0  # cases where new timestamps differ from old
    samples = []

    for _, i in intra.iterrows():
        hn = i['_hn']
        op_date = i['_op_date']
        if pd.isna(op_date) or not op_date:
            continue

        rows = conn.execute(
            "SELECT case_id, arrived_at, in_or_at, op_end_at FROM cases "
            "WHERE op_date=? AND hn=?",
            (op_date, hn)
        ).fetchall()
        if not rows:
            not_found += 1
            continue

        new_arrived = _make_timestamp(op_date, i.get('arrivtime'))
        new_in_or = _make_timestamp(op_date, i.get('roomtimein'))
        new_op_end = _make_timestamp(op_date, i.get('roomtimeout'))
        new_actual_min = _duration_to_min(i.get('opusetime'))

        for case_id, old_arrived, old_in_or, old_op_end in rows:
            same = (old_in_or == new_in_or and old_op_end == new_op_end
                    and old_arrived == new_arrived)
            samples.append({
                'hn': hn, 'op_date': op_date,
                'old_in_or': old_in_or, 'new_in_or': new_in_or,
                'old_op_end': old_op_end, 'new_op_end': new_op_end,
                'changed': not same,
            })
            if not same:
                changed += 1
            if not dry_run:
                conn.execute(
                    """UPDATE cases SET
                        arrived_at = COALESCE(?, arrived_at),
                        in_or_at = COALESCE(?, in_or_at),
                        op_end_at = COALESCE(?, op_end_at),
                        actual_duration_min = COALESCE(?, actual_duration_min)
                       WHERE case_id=?""",
                    (new_arrived, new_in_or, new_op_end, new_actual_min, case_id)
                )
                updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    return {
        'updated': updated, 'not_found': not_found, 'changed': changed,
        'samples': samples,
    }


def _parse_thai_date(s) -> str | None:
    """Convert Thai BE date '01/05/2569' → ISO '2026-05-01'.

    รองรับทั้ง:
      - '01/05/2569'    (DD/MM/BE)
      - '1/5/2569'      (single digit day/month)
      - datetime/Timestamp objects
      - 'YYYY-MM-DD'    (already ISO — pass through)
    """
    if pd.isna(s):
        return None
    # Already a datetime
    if hasattr(s, 'strftime'):
        return s.strftime('%Y-%m-%d')
    s = str(s).strip()
    if not s:
        return None
    # Try ISO first
    if '-' in s and len(s) >= 10 and s[4] == '-':
        return s[:10]
    # Thai BE format DD/MM/YYYY
    parts = s.split(' ')[0].split('/')
    if len(parts) != 3:
        return None
    try:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        # Buddhist Era → CE if year > 2400
        if y > 2400:
            y -= 543
        return f"{y:04d}-{m:02d}-{d:02d}"
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> int:
    """Convert numeric value to int safely, returning 0 for NaN/None."""
    if v is None or pd.isna(v):
        return 0
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def merge_costs_from_excel(cost_path: str, dry_run: bool = False) -> dict:
    """Match cost data from OR_Stats Excel → existing cases by (HN, Date).

    Updates `treatment_cost` and `patho_cost` columns.
    Returns dict with summary: matched, not_found, samples.

    Excel columns expected:
      - HN          (numeric — float)
      - Date        (Thai BE date string '01/05/2569')
      - ราคาผ่าตัด    (treatment cost)
      - ราคาชิ้นเนื้อ (pathology cost)
    """
    df = pd.read_excel(cost_path)

    required = {'HN', 'Date', 'ราคาผ่าตัด', 'ราคาชิ้นเนื้อ'}
    missing = required - set(df.columns)
    if missing:
        return {'error': f'ขาด columns: {missing}',
                'matched': 0, 'not_found': 0, 'samples': []}

    conn = sqlite3.connect(DB_PATH)
    matched = 0
    not_found = 0
    samples = []

    for _, r in df.iterrows():
        hn_raw = r['HN']
        if pd.isna(hn_raw):
            continue
        hn = str(int(float(hn_raw))).strip()

        op_iso = _parse_thai_date(r['Date'])
        if not op_iso:
            continue

        treat = _safe_int(r['ราคาผ่าตัด'])
        patho = _safe_int(r['ราคาชิ้นเนื้อ'])

        # Find matching case in DB (could be multiple if same hn/date)
        rows = conn.execute(
            "SELECT case_id, procedure_name FROM cases WHERE op_date=? AND hn=?",
            (op_iso, hn)
        ).fetchall()

        if not rows:
            not_found += 1
            samples.append({
                'HN': hn, 'Date': op_iso, 'หัตถการ': '(ไม่เจอใน DB)',
                'ราคาผ่าตัด': treat, 'ราคาชิ้นเนื้อ': patho,
                'status': 'NOT FOUND',
            })
            continue

        for case_id, proc in rows:
            samples.append({
                'HN': hn, 'Date': op_iso, 'หัตถการ': str(proc)[:40],
                'ราคาผ่าตัด': treat, 'ราคาชิ้นเนื้อ': patho,
                'status': 'MATCHED',
            })
            matched += 1
            if not dry_run:
                conn.execute(
                    "UPDATE cases SET treatment_cost=?, patho_cost=? "
                    "WHERE case_id=?",
                    (treat, patho, case_id)
                )

    if not dry_run:
        conn.commit()
    conn.close()

    return {'matched': matched, 'not_found': not_found, 'samples': samples}


def _parse_time_str(time_str, date_iso) -> str | None:
    """Combine 'HH:MM' string + ISO date → 'YYYY-MM-DD HH:MM:00'.

    Returns None if either can't parse.
    Handles edge cases: '7:40', '07:40', NaN, empty strings.
    """
    if pd.isna(time_str) or not date_iso:
        return None
    s = str(time_str).strip()
    if not s or s.lower() in ('nan', 'none'):
        return None
    parts = s.split(':')
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f'{date_iso} {h:02d}:{m:02d}:00'
    except (ValueError, TypeError):
        pass
    return None


def import_walkins_from_cost_excel(cost_path: str,
                                    dry_run: bool = False) -> dict:
    """สำหรับ rows ใน cost Excel ที่ไม่มีเคสตรงกันใน DB
    → สร้างเคสใหม่ category='Walk-in', status='discharged'
    พร้อมข้อมูลเต็มจาก cost Excel (name, operation, surgeon, times, cost).

    Returns dict with summary: inserted, skipped_already_exists, samples.
    """
    df = pd.read_excel(cost_path)

    required = {'HN', 'Date'}
    missing = required - set(df.columns)
    if missing:
        return {'error': f'ขาด columns: {missing}',
                'inserted': 0, 'skipped_already_exists': 0, 'samples': []}

    conn = sqlite3.connect(DB_PATH)
    # Make sure requested_date column exists
    existing_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(cases)").fetchall()}
    if 'requested_date' not in existing_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN requested_date TEXT")
        conn.commit()

    inserted = 0
    skipped = 0
    samples = []

    for _, r in df.iterrows():
        hn_raw = r['HN']
        if pd.isna(hn_raw):
            continue
        try:
            hn = str(int(float(hn_raw))).strip()
        except (ValueError, TypeError):
            continue

        op_iso = _parse_thai_date(r['Date'])
        if not op_iso:
            continue

        # ข้ามถ้ามีเคสนี้ใน DB อยู่แล้ว
        existing = conn.execute(
            "SELECT case_id FROM cases WHERE op_date=? AND hn=?",
            (op_iso, hn)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        # ดึงข้อมูลจาก cost Excel
        name = str(r.get('ชื่อ-สกุลผู้ป่วย', '') or '').strip()
        proc = str(r.get('Operation', '') or '').strip()
        if not proc or proc.lower() in ('nan', 'none'):
            proc = '-'
        surgeon = str(r.get('แพทย์ทำผ่าตัด', '') or '').strip() or None
        if surgeon and surgeon.lower() in ('nan', 'none'):
            surgeon = None
        scrub = str(r.get('Scrub nurse', '') or '').strip() or None
        if scrub and scrub.lower() in ('nan', 'none'):
            scrub = None
        circ = str(r.get('Circulating nurse', '') or '').strip() or None
        if circ and circ.lower() in ('nan', 'none'):
            circ = None

        arrived = _parse_time_str(r.get('เวลารับ'), op_iso)
        in_or = _parse_time_str(r.get('Op.start'), op_iso)
        op_end = _parse_time_str(r.get('Op.end'), op_iso)
        discharged = _parse_time_str(r.get('D/C'), op_iso)

        dur = r.get('Duration (min)')
        actual_min = int(float(dur)) if pd.notna(dur) else None

        treat = _safe_int(r.get('ราคาผ่าตัด'))
        patho = _safe_int(r.get('ราคาชิ้นเนื้อ'))

        if not dry_run:
            conn.execute("""
                INSERT INTO cases (
                    op_date, hn, name, procedure_name, surgeon_name,
                    case_category, patient_type, status,
                    arrived_at, in_or_at, op_end_at, discharged_at,
                    actual_duration_min,
                    scrub_nurse, circ_nurse,
                    treatment_cost, patho_cost, room_no,
                    requested_date
                ) VALUES (?,?,?,?,?,?,?,'discharged',?,?,?,?,?,?,?,?,?,?,?)
            """, (
                op_iso, hn, name, proc, surgeon,
                'Walk-in', 'OPD',
                arrived, in_or, op_end, discharged,
                actual_min,
                scrub, circ,
                treat, patho, 32,
                op_iso,  # walk-in: requested_date == op_date
            ))
            inserted += 1

        samples.append({
            'HN': hn, 'Date': op_iso,
            'ชื่อ': name[:25] if name else '-',
            'Operation': proc[:30],
            'แพทย์': surgeon or '-',
            'ราคาผ่าตัด': treat, 'ราคาชิ้นเนื้อ': patho,
            'Duration': actual_min or 0,
        })

    if not dry_run:
        conn.commit()
        # User เพิ่มเคสสำเร็จ → ล้าง flag กัน auto-import
        try:
            from minor_or_db import _set_app_setting
            _set_app_setting('skip_auto_import', '0')
        except Exception:
            pass
    conn.close()

    return {
        'inserted': inserted,
        'skipped_already_exists': skipped,
        'samples': samples,
    }


def import_merged_csv(csv_path: str, dry_run: bool = False) -> dict:
    """Import single pre-merged CSV/Excel — columns ตรงกับ DB schema ของ cases.

    ใช้เมื่อ user เตรียมไฟล์ merged แล้ว (e.g. รวม sched+intraop+cost ภายนอก)
    Required columns: op_date, hn, procedure_name
    Optional: ทุก column อื่นๆ ที่ cases table มี
    """
    # Auto-detect format
    if csv_path.lower().endswith(('.xlsx', '.xls')):
        df = pd.read_excel(csv_path)
    else:
        try:
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
        except (UnicodeError, UnicodeDecodeError):
            try:
                df = pd.read_csv(csv_path, encoding='utf-16')
            except (UnicodeError, UnicodeDecodeError):
                df = pd.read_csv(csv_path, encoding='utf-8')

    required = {'op_date', 'hn'}
    missing = required - set(df.columns)
    if missing:
        return {'error': f'CSV ขาด columns: {missing}',
                'inserted': 0, 'skipped': 0}

    conn = sqlite3.connect(DB_PATH)
    existing_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(cases)").fetchall()}
    if 'requested_date' not in existing_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN requested_date TEXT")
        conn.commit()
        existing_cols.add('requested_date')

    inserted = skipped_dup = skipped_invalid = 0
    samples = []

    for _, r in df.iterrows():
        op_date = r.get('op_date')
        hn = r.get('hn')
        if pd.isna(op_date) or pd.isna(hn) or not str(op_date).strip():
            skipped_invalid += 1
            continue
        op_date = str(op_date).strip()
        hn = str(hn).strip()

        # Skip duplicate
        if conn.execute("SELECT case_id FROM cases WHERE op_date=? AND hn=?",
                        (op_date, hn)).fetchone():
            skipped_dup += 1
            continue

        # Build row dict, only include columns that exist in cases table
        row_data = {}
        for col in df.columns:
            if col not in existing_cols:
                continue
            v = r.get(col)
            if pd.isna(v):
                row_data[col] = None
            elif isinstance(v, (int, float)) and 'cost' in col or col in (
                    'actual_duration_min', 'wait_min', 'room_no'):
                try:
                    row_data[col] = int(float(v))
                except (ValueError, TypeError):
                    row_data[col] = None
            else:
                s = str(v).strip()
                row_data[col] = s if s and s.lower() not in ('nan', 'none') else None

        # Defaults for required NOT NULL
        if not row_data.get('op_date'):
            row_data['op_date'] = op_date
        if not row_data.get('hn'):
            row_data['hn'] = hn
        if not row_data.get('procedure_name'):
            row_data['procedure_name'] = '-'
        if not row_data.get('status'):
            row_data['status'] = 'discharged'

        if not dry_run:
            cols = list(row_data.keys())
            placeholders = ','.join(['?'] * len(cols))
            sql = (f"INSERT INTO cases ({','.join(cols)}) "
                   f"VALUES ({placeholders})")
            conn.execute(sql, [row_data[c] for c in cols])
            inserted += 1

        if len(samples) < 10:
            samples.append({
                'HN': hn, 'op_date': op_date,
                'หัตถการ': row_data.get('procedure_name', '-')[:30],
                'cat': row_data.get('case_category', '-'),
                'ค่าผ่า': row_data.get('treatment_cost', 0),
            })

    if not dry_run:
        conn.commit()
        try:
            from minor_or_db import _set_app_setting
            _set_app_setting('skip_auto_import', '0')
        except Exception:
            pass
    conn.close()

    return {
        'inserted': inserted,
        'skipped_duplicate': skipped_dup,
        'skipped_invalid': skipped_invalid,
        'samples': samples,
        'columns_used': sorted(set(df.columns) & existing_cols),
        'columns_ignored': sorted(set(df.columns) - existing_cols),
    }


def import_cost_driven(cost_path: str,
                        sched_path: str = None,
                        intra_path: str = None,
                        dry_run: bool = False) -> dict:
    """Cost-driven import: Cost Excel เป็น master ของ "เคสที่ผ่าตัดจริง"
    enrich ด้วยข้อมูลจาก schedule + intraop (ถ้ามี)

    Logic:
      1. iterate cost Excel → ทุก row = real surgical case
      2. lookup matching schedule row (HN+Date) → ดึง reqdate, division, age, diag
      3. lookup matching intraop row (HN+Date) → ดึง precise timestamps
      4. สร้างเคสรวมข้อมูลจาก 3 แหล่ง
      5. cases ที่อยู่ใน sched/intraop แต่ไม่ใน cost → SKIP
         (น่าจะ cancel ก่อนผ่าตัด หรือไม่ได้บันทึกในสมุดสถิติ)

    Returns dict with detailed stats.
    """
    cost_df = pd.read_excel(cost_path)
    if 'HN' not in cost_df.columns or 'Date' not in cost_df.columns:
        return {'error': 'Cost Excel ขาด columns: HN หรือ Date'}

    # Build sched + intraop lookups
    sched_lookup = {}
    if sched_path:
        sched_df = pd.read_csv(sched_path, encoding='utf-16')
        sched_df['_date'] = sched_df['opedate'].apply(_norm_date)
        sched_df['_hn'] = sched_df['hn'].astype(str).str.strip()
        for _, r in sched_df.iterrows():
            if pd.isna(r['_date']) or not r['_date']:
                continue
            sched_lookup[(r['_hn'], r['_date'])] = r.to_dict()

    intra_lookup = {}
    if intra_path:
        intra_df = pd.read_csv(intra_path, encoding='utf-16')
        intra_df['_date'] = intra_df['opedate'].apply(_norm_date)
        intra_df['_hn'] = intra_df['hn'].astype(str).str.strip()
        for _, r in intra_df.iterrows():
            if pd.isna(r['_date']) or not r['_date']:
                continue
            intra_lookup[(r['_hn'], r['_date'])] = r.to_dict()

    conn = sqlite3.connect(DB_PATH)
    existing_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(cases)").fetchall()}
    if 'requested_date' not in existing_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN requested_date TEXT")
        conn.commit()

    inserted = skipped_no_hn = skipped_no_date = skipped_duplicate = 0
    enriched_sched = enriched_intra = 0
    samples = []
    cost_keys = set()

    def _str_or_none(v):
        if v is None or pd.isna(v):
            return None
        s = str(v).strip()
        if not s or s.lower() in ('nan', 'none', '-'):
            return None
        return s

    for _, c in cost_df.iterrows():
        # HN
        hn_raw = c.get('HN')
        if pd.isna(hn_raw):
            skipped_no_hn += 1
            continue
        try:
            hn = str(int(float(hn_raw))).strip()
        except (ValueError, TypeError):
            skipped_no_hn += 1
            continue

        # Date
        op_iso = _parse_thai_date(c.get('Date'))
        if not op_iso:
            skipped_no_date += 1
            continue
        cost_keys.add((hn, op_iso))

        # Skip duplicate
        if conn.execute("SELECT case_id FROM cases WHERE op_date=? AND hn=?",
                        (op_iso, hn)).fetchone():
            skipped_duplicate += 1
            continue

        s_row = sched_lookup.get((hn, op_iso))
        i_row = intra_lookup.get((hn, op_iso))
        if s_row: enriched_sched += 1
        if i_row: enriched_intra += 1

        # Field consolidation (priority: intraop > cost > schedule)
        name = (_str_or_none(c.get('ชื่อ-สกุลผู้ป่วย'))
                or (_str_or_none(s_row.get('dspname')) if s_row else None))
        proc = (_str_or_none(c.get('Operation'))
                or (_str_or_none(s_row.get('icd9cm_name')) if s_row else None)
                or '-')
        surgeon = (
            (_str_or_none(i_row.get('dctnm')) if i_row else None)
            or _str_or_none(c.get('แพทย์ทำผ่าตัด'))
            or (_str_or_none(s_row.get('surgstfnm')) if s_row else None)
        )

        # case_category from sched reqdate (if avail)
        req_iso = None
        if s_row:
            req_iso = _norm_date(s_row.get('reqdate'))
        case_cat = _classify_case_category(req_iso, op_iso)

        # AN + patient_type
        an_val = None
        if s_row:
            an_raw = s_row.get('an')
            if pd.notna(an_raw):
                try:
                    an_val = (str(int(float(an_raw)))
                              if '.' in str(an_raw) else str(an_raw).strip())
                except (ValueError, TypeError):
                    pass
        pt_type = 'IPD' if an_val else 'OPD'

        diag = _str_or_none(s_row.get('icd10_name')) if s_row else None
        division = _str_or_none(s_row.get('division')) if s_row else None

        # Timestamps (intraop precise > cost HH:MM)
        arrived_at = (_make_timestamp(op_iso, i_row.get('arrivtime'))
                      if i_row else None) \
                     or _parse_time_str(c.get('เวลารับ'), op_iso)
        in_or_at = (_make_timestamp(op_iso, i_row.get('roomtimein'))
                    if i_row else None) \
                   or _parse_time_str(c.get('Op.start'), op_iso)
        op_end_at = (_make_timestamp(op_iso, i_row.get('roomtimeout'))
                     if i_row else None) \
                    or _parse_time_str(c.get('Op.end'), op_iso)
        discharged_at = _parse_time_str(c.get('D/C'), op_iso) or op_end_at

        # Duration
        actual_min = None
        if i_row:
            actual_min = _duration_to_min(i_row.get('opusetime'))
        if actual_min is None:
            d = c.get('Duration (min)')
            if pd.notna(d):
                try:
                    actual_min = int(float(d))
                except (ValueError, TypeError):
                    pass

        # Nurses
        scrub = (_str_or_none(i_row.get('nursurgnm')) if i_row else None) \
                or _str_or_none(c.get('Scrub nurse'))
        circ = (_str_or_none(i_row.get('nurcircunm')) if i_row else None) \
               or _str_or_none(c.get('Circulating nurse'))

        treat = _safe_int(c.get('ราคาผ่าตัด'))
        patho = _safe_int(c.get('ราคาชิ้นเนื้อ'))

        room_no = 32
        if i_row and pd.notna(i_row.get('orroom')):
            try: room_no = int(i_row['orroom'])
            except (ValueError, TypeError): pass

        if not dry_run:
            conn.execute("""
                INSERT INTO cases (
                    op_date, hn, name, an, diagnosis, procedure_name,
                    surgeon_name, division_code, case_category, patient_type,
                    status, arrived_at, in_or_at, op_end_at, discharged_at,
                    actual_duration_min, scrub_nurse, circ_nurse,
                    treatment_cost, patho_cost, room_no, requested_date
                ) VALUES (?,?,?,?,?,?,?,?,?,?,'discharged',
                          ?,?,?,?,?,?,?,?,?,?,?)
            """, (
                op_iso, hn, name, an_val, diag, proc,
                surgeon, division, case_cat, pt_type,
                arrived_at, in_or_at, op_end_at, discharged_at,
                actual_min, scrub, circ,
                treat, patho, room_no, req_iso,
            ))
            inserted += 1

        samples.append({
            'HN': hn, 'Date': op_iso,
            'ชื่อ': (name or '-')[:25],
            'หัตถการ': proc[:25],
            'cat': case_cat,
            'sched': '✓' if s_row else '-',
            'intra': '✓' if i_row else '-',
            'ค่าผ่า': treat,
        })

    # Cases in sched/intraop but NOT in cost (likely cancelled/not-performed)
    sched_only = []
    if sched_lookup:
        for k, sr in sched_lookup.items():
            if k not in cost_keys:
                sched_only.append({
                    'HN': k[0], 'Date': k[1],
                    'ชื่อ': str(sr.get('dspname', '') or '-')[:25],
                    'หัตถการ (sched)': str(sr.get('icd9cm_name', '') or '-')[:30],
                })

    if not dry_run:
        conn.commit()
        try:
            from minor_or_db import _set_app_setting
            _set_app_setting('skip_auto_import', '0')
        except Exception:
            pass
    conn.close()

    return {
        'inserted': inserted,
        'skipped_no_hn': skipped_no_hn,
        'skipped_no_date': skipped_no_date,
        'skipped_duplicate': skipped_duplicate,
        'enriched_sched': enriched_sched,
        'enriched_intra': enriched_intra,
        'sched_only_not_in_cost': len(sched_only),
        'sched_only_samples': sched_only[:30],
        'samples': samples,
    }


def import_historical_with_costs(sched_path: str, intra_path: str,
                                  cost_path: str = None,
                                  dry_run: bool = False) -> dict:
    """One-shot import: schedule + intraop + (optional) cost Excel.

    1. Run import_historical (sched + intraop) → create cases with
       proper case_category, room timestamps, status='discharged'
    2. If cost_path given → merge_costs_from_excel to fill
       treatment_cost / patho_cost
    3. Clear skip_auto_import flag (so future reboots can auto-import)

    Returns summary dict with all counts.
    """
    # Phase 1: cases (sched + intraop)
    n_inserted, n_skipped, results = import_historical(
        sched_path, intra_path, dry_run=dry_run)
    skip_stats = getattr(import_historical, '_last_skip_stats',
                         {'no_date': 0, 'no_hn': 0})

    out = {
        'inserted': n_inserted,
        'skipped': n_skipped,
        'skipped_no_date': skip_stats.get('no_date', 0),
        'skipped_no_hn': skip_stats.get('no_hn', 0),
        'sample_results': results[:10],
        'cost_matched': 0,
        'cost_not_found': 0,
        'cost_samples': [],
    }

    # Phase 2: cost
    if cost_path:
        try:
            cost_info = merge_costs_from_excel(cost_path, dry_run=dry_run)
            if 'error' in cost_info:
                out['cost_error'] = cost_info['error']
            else:
                out['cost_matched'] = cost_info['matched']
                out['cost_not_found'] = cost_info['not_found']
                # เก็บ NOT_FOUND ทั้งหมด (สำคัญสำหรับ debug
                # — ถ้า cut ที่ 20 อาจไม่เห็นเคสที่หาย)
                # MATCHED จำกัด 20 พอ (เพื่อ display ไม่รก)
                samples = cost_info['samples']
                not_found_all = [s for s in samples
                                 if s.get('status') == 'NOT FOUND']
                matched_first20 = [s for s in samples
                                   if s.get('status') == 'MATCHED'][:20]
                out['cost_samples'] = matched_first20 + not_found_all
        except Exception as e:
            out['cost_error'] = str(e)

    # Phase 3: clear skip_auto_import flag (we have data now!)
    if not dry_run and n_inserted > 0:
        try:
            from minor_or_db import _set_app_setting
            _set_app_setting('skip_auto_import', '0')
        except Exception:
            pass

    return out


if __name__ == '__main__':
    # Default: look for files in same directory
    base = os.path.dirname(os.path.abspath(__file__))
    sched = os.path.join(base, '111.csv')
    intra = os.path.join(base, 'รอลบ.csv')

    # --reclassify mode: fix case_category for existing cases without re-importing
    if '--reclassify' in sys.argv:
        if not os.path.exists(sched):
            print(f"Need {sched} to read reqdate values")
            sys.exit(1)
        print("=== DRY RUN: reclassify existing cases ===")
        info = reclassify_existing(sched, dry_run=True)
        for hn, od, rd, old, new in info['samples'][:30]:
            mark = '  ' if old == new else '->'
            print(f"  hn={hn:>12s} op={od} req={rd or '-':<10s}  {old or '-':<12s} {mark} {new}")
        print(f"\nWill change to Walk-in: {info['set_to_walkin']}, "
              f"to เคสนัดหมาย: {info['set_to_scheduled']}, unchanged: {info['unchanged']}, "
              f"DB rows not in CSV: {info['not_found']}")
        if input("\nApply these updates? (y/n): ").strip().lower() == 'y':
            info = reclassify_existing(sched, dry_run=False)
            print(f"Done. Updated {info['updated']} rows.")
        sys.exit(0)

    # --reimport-times mode: refresh in_or_at / op_end_at จาก roomtimein / roomtimeout
    if '--reimport-times' in sys.argv:
        if not os.path.exists(intra):
            print(f"Need {intra} to read room times")
            sys.exit(1)
        print("=== DRY RUN: re-import room timestamps ===")
        info = reimport_timestamps(intra, dry_run=True)
        for s in info['samples'][:30]:
            mark = '  ' if not s['changed'] else '->'
            print(f"  hn={s['hn']:>12s} op={s['op_date']}  "
                  f"in_or: {s['old_in_or'] or '-':<20s} {mark} {s['new_in_or'] or '-':<20s}")
        print(f"\nWill change: {info['changed']} rows, "
              f"DB rows not in CSV: {info['not_found']}")
        if input("\nApply these updates? (y/n): ").strip().lower() == 'y':
            info = reimport_timestamps(intra, dry_run=False)
            print(f"Done. Updated {info['updated']} rows.")
        sys.exit(0)

    # Normal import flow
    if not os.path.exists(sched) or not os.path.exists(intra):
        print("Please place 111.csv and รอลบ.csv in the same folder")
        sys.exit(1)

    # Dry run first
    n, s, results = import_historical(sched, intra, dry_run=True)
    print(f"\n=== DRY RUN ===")
    print(f"Would insert: {n} cases, skip (duplicate): {s}")
    for r in results:
        print(f"  {r['date']} | {r['name'][:20]:20s} | {r['proc']:15s} | {r['status']:10s} | {r['duration'] or '-':>4} min | wait {r['wait'] or '-':>3} min | Dx: {r['diag'] or '-'}")

    # Actual import
    if input("\nProceed with import? (y/n): ").strip().lower() == 'y':
        n, s, _ = import_historical(sched, intra, dry_run=False)
        print(f"\nDone! Inserted {n}, skipped {s}")
