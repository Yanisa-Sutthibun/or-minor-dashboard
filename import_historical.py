"""
import_historical.py — รวม scheduling CSV + intraop CSV แล้ว import เข้า cases table
ใช้สำหรับข้อมูลย้อนหลังที่ผ่าตัดเสร็จแล้ว (status = discharged)

Case category logic:
    เคสนัดหมาย = reqdate < opedate (booked at least 1 day in advance)
    Walk-in    = reqdate == opedate, missing reqdate, or reqdate > opedate
"""
import pandas as pd
import sqlite3
import os
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'minor_or.db')


def _classify_case_category(req_iso, op_iso):
    """Return 'เคสนัดหมาย' if booked in advance, else 'Walk-in'.

    req_iso, op_iso: 'YYYY-MM-DD' strings (output of _norm_date) or None.
    """
    if not req_iso or not op_iso:
        return 'Walk-in'
    # ISO date strings compare lexicographically
    return 'เคสนัดหมาย' if req_iso < op_iso else 'Walk-in'


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

    for _, s in sched.iterrows():
        hn = str(s['hn']).strip()
        op_date = s['_date']
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
            in_or_at = _make_timestamp(op_date, i.get('opesttime'))
            op_end_at = _make_timestamp(op_date, i.get('opendtime'))
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
        if not op_date:
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
