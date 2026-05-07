"""
Minor OR Core — ML Engine v3 + Helpers (16 features, optimized)
Aligned with Major OR (pro09.py) + 5 strong Minor-specific features
"""
import os
import pickle
import re
import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime
from difflib import get_close_matches

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

TURNOVER_MINOR = 10
WORK_START = 8
WORK_END = 16
WORK_MINUTES = 480

# ============================================================================
# ML MODEL LOADER
# ============================================================================

@st.cache_resource
def load_ml_assets():
    assets = {'model': None, 'model_data': None, 'model_loaded': False}
    model_path = os.path.join(_SCRIPT_DIR, 'minor_or_model.pkl')
    try:
        if os.path.exists(model_path):
            with open(model_path, 'rb') as f:
                data = pickle.load(f)
            assets['model'] = data['model']
            assets['model_data'] = data
            assets['model_loaded'] = True
    except Exception as e:
        print(f"Warning: Cannot load model: {e}")
    return assets

# ============================================================================
# FUZZY RESOLVE
# ============================================================================

def fuzzy_resolve(query, candidates, cutoff=0.65):
    """Resolve: exact -> contains -> reverse contains -> fuzzy."""
    if not query or not candidates:
        return None, None
    if query in candidates:
        return query, 'exact'
    contains = [c for c in candidates if query in c]
    if contains:
        return min(contains, key=len), 'contains'
    rev = [c for c in candidates if c and c in query]
    if rev:
        return max(rev, key=len), 'contains_rev'
    matches = get_close_matches(query, candidates, n=1, cutoff=cutoff)
    if matches:
        return matches[0], 'fuzzy'
    return None, None

# ============================================================================
# PREDICTION ENGINE v3 (16 features — optimized, Major-OR compatible)
# Feature order MUST match training:
#   [proc_enc, surgeon_enc, division_enc, age, op_hour, day_of_week, month,
#    timeslot_enc, optype_enc, surgeon_avg_duration, proc_avg_duration,
#    surg_proc_avg, scrub_enc, circ_enc, wait_min, month_avg]
# ============================================================================

def predict_surgical_time(procedure: str, age: int, surgeon: str = "",
                          division: str = "75", op_hour: int = 9,
                          optype: str = "elective",
                          anesthesia: str = "UNKNOWN",  # kept for API compat (unused in v3)
                          scrub_nurse: str = "UNKNOWN",
                          circ_nurse: str = "UNKNOWN",
                          has_assistant: int = 0,        # kept for API compat (unused in v3)
                          wait_min: int = 0,
                          op_date: datetime = None) -> dict:
    assets = load_ml_assets()
    now = op_date if op_date else datetime.now()
    procedure_clean = procedure.strip().upper() if procedure else "UNKNOWN"
    surgeon_clean = surgeon.strip() if surgeon else 'UNKNOWN'

    # ──────────────────────────────────────────────────────────────────
    # ลำดับที่ 1: ลองใช้ local history ก่อน (เคสจริงในห้องเล็ก)
    # ----------------------------------------------------------------------
    # Smart fallback strategy:
    #   Tier 1: surgeon × procedure (≥3 เคส) → confidence "สูงมาก"
    #   Tier 2: procedure only (≥3 เคส)      → confidence "สูง"
    #   Tier 3 (ลำดับ 2 ใน function): ML model (เดิม)
    # ----------------------------------------------------------------------
    # เหตุผล: training data เก็บชื่อหัตถการเป็น ICD-9 ภาษาอังกฤษเต็ม
    # แต่ user กรอกชื่อสั้นเช่น "ESWL", "Morpheus", "PERM cath"
    # → fuzzy match ฝั่ง ML ล้มเหลว fallback global_avg=35.5 เสมอ
    # → ใช้ local history ที่ user กรอกเอง = ตรงกับ workflow จริง = แม่นกว่า
    # ──────────────────────────────────────────────────────────────────
    try:
        from minor_or_db import predict_from_local_history
        local = predict_from_local_history(procedure, surgeon)
        if local is not None:
            return {
                'predicted_min': local['predicted_min'],
                'confidence': local['confidence'],
                'method': local['method_label'],
                'details': (f'median ของ {local["n_cases"]} เคส '
                            f'(min={local["min_dur"]} / max={local["max_dur"]} นาที) '
                            f'• กลุ่ม "{local["canonical"]}"'),
                'proc_n': local['n_cases'],
                'surg_n': local['n_cases'] if local['tier'] == 1 else 0,
                'source': 'local_history',
                'tier': local['tier'],
            }
    except Exception:
        pass  # ถ้า DB ผิดพลาด ให้ fallback ML model ต่อไป

    # ลำดับที่ 2: ML model (เดิม)
    if not assets['model_loaded'] or assets['model_data'] is None:
        return {
            'predicted_min': 30, 'confidence': 'ต่ำ',
            'method': 'ค่าเริ่มต้น (ไม่มี Model)',
            'details': 'ไม่พบ minor_or_model.pkl และไม่มีประวัติเคสในห้องเล็กพอ',
            'proc_n': 0, 'surg_n': 0,
            'source': 'default',
        }

    data = assets['model_data']
    model = data['model']
    global_avg = data.get('global_avg', 35.5)

    def safe_encode(le, value):
        try:
            return int(le.transform([value])[0])
        except (ValueError, KeyError):
            return 0

    # --- Resolve procedure & surgeon via fuzzy ---
    proc_known = list(data['le_proc'].classes_)
    proc_resolved, proc_method = fuzzy_resolve(procedure_clean, proc_known)
    proc_fuzzy_used = proc_method not in (None, 'exact')
    if proc_resolved is None:
        proc_resolved = procedure_clean

    surg_known = list(data['le_surgeon'].classes_)
    surg_resolved, surg_method = fuzzy_resolve(surgeon_clean, surg_known)
    surg_fuzzy_used = surg_method not in (None, 'exact')
    if surg_resolved is None:
        surg_resolved = surgeon_clean

    # --- Encode categoricals (only those v3 needs) ---
    proc_enc = safe_encode(data['le_proc'], proc_resolved)
    surg_enc = safe_encode(data['le_surgeon'], surg_resolved)
    div_enc = safe_encode(data['le_division'], str(division).strip())
    scrub_enc = safe_encode(data['le_scrub'], scrub_nurse.strip() if scrub_nurse else 'UNKNOWN')
    circ_enc = safe_encode(data['le_circ'], circ_nurse.strip() if circ_nurse else 'UNKNOWN')
    optype_enc = safe_encode(data['le_optype'], str(optype).strip().lower())

    # --- Time slot ---
    if op_hour < 12:
        ts = 'morning'
    elif op_hour < 16:
        ts = 'afternoon'
    else:
        ts = 'evening'
    ts_enc = safe_encode(data['le_timeslot'], ts)

    # --- Time features from date ---
    dow = now.weekday()
    month = now.month

    # --- Historical averages ---
    surgeon_avg = data['surgeon_avg'].get(surg_resolved, global_avg)
    proc_avg = data['proc_avg'].get(proc_resolved, global_avg)

    # ⭐ Surgeon × Procedure interaction (dominant feature)
    surg_proc_key = f'{surg_resolved}||{proc_resolved}'
    surg_proc_avg_val = data.get('surg_proc_avg', {}).get(surg_proc_key, global_avg)
    surg_proc_count_val = data.get('surg_proc_count', {}).get(surg_proc_key, 0)

    # Group average (month)
    month_avg_val = data.get('month_avg', {}).get(month, global_avg)

    # --- Build feature vector (16 features — order MUST match training) ---
    features = np.array([[
        proc_enc, surg_enc, div_enc,           # 3 categorical core
        age, op_hour, dow, month,              # 4 numeric patient/time
        ts_enc, optype_enc,                    # 2 categorical time/op
        surgeon_avg, proc_avg,                 # 2 historical averages
        surg_proc_avg_val,                     # 1 interaction ⭐
        scrub_enc, circ_enc,                   # 2 nurse
        wait_min,                              # 1 wait
        month_avg_val,                         # 1 group avg
    ]])

    try:
        pred = float(model.predict(features)[0])
        pred_min = max(5, int(round(pred)))

        # Confidence based on data availability
        proc_in = proc_resolved in data['proc_avg']
        surg_in = surg_resolved in data['surgeon_avg']
        surg_proc_n = surg_proc_count_val

        if surg_proc_n >= 3:
            confidence = "สูงมาก"
        elif proc_in and surg_in:
            confidence = "สูง"
        elif proc_in or surg_in:
            confidence = "ปานกลาง"
        else:
            confidence = "ต่ำ"

        detail_parts = []
        mae_val = data.get('results', {}).get(data.get('model_name', ''), {}).get('MAE', '?')
        detail_parts.append(f'MAE={mae_val} นาที')
        detail_parts.append(confidence)
        if surg_proc_n >= 1:
            detail_parts.append(f'ประวัติ surgeon×proc: {int(surg_proc_n)} ครั้ง')
        if proc_fuzzy_used:
            detail_parts.append(f'fuzzy proc→{proc_resolved[:30]}')
        if surg_fuzzy_used:
            detail_parts.append(f'fuzzy surg→{surg_resolved[:20]}')

        return {
            'predicted_min': pred_min, 'confidence': confidence,
            'method': f'AI Model v3 ({data["model_name"]})',
            'details': ' | '.join(detail_parts),
            'proc_n': int(data.get('proc_count', {}).get(proc_resolved, 0)) if proc_in else 0,
            'surg_n': int(data.get('surgeon_count', {}).get(surg_resolved, 0)) if surg_in else 0,
            'surg_proc_n': int(surg_proc_n),
            'proc_resolved': proc_resolved if proc_fuzzy_used else None,
            'surg_resolved': surg_resolved if surg_fuzzy_used else None,
            'source': 'ml_model',
            'tier': 3,
        }
    except Exception as e:
        return {
            'predicted_min': int(round(proc_avg)), 'confidence': 'ต่ำ',
            'method': 'Fallback (proc_avg)',
            'details': f'Model predict error: {str(e)[:40]}',
            'proc_n': 0, 'surg_n': 0,
            'source': 'fallback_proc_avg',
            'tier': 3,
        }

# ============================================================================
# HELPERS
# ============================================================================

def parse_opetime_full(val) -> tuple:
    try:
        t = int(float(val))
        return (t // 10000, (t % 10000) // 100)
    except:
        return (8, 0)

def parse_opetime(val) -> int:
    try:
        return int(float(val)) // 10000
    except:
        return 8

# ============================================================================
# PERSISTENT CASE HISTORY (for Top 5/10 statistics)
# ============================================================================

HISTORY_FILE = os.path.join(_SCRIPT_DIR, 'case_history.csv')

HISTORY_COLUMNS = [
    'timestamp', 'case_id', 'procedure', 'surgeon', 'division',
    'age', 'op_hour', 'scrub', 'circ',
    'ai_predicted_min', 'user_override_min', 'actual_duration_min',
    'abs_error', 'signed_error', 'wait_min', 'room',
]

def load_case_history() -> pd.DataFrame:
    """Load persistent case history CSV (returns empty DF if missing)."""
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        df = pd.read_csv(HISTORY_FILE, encoding='utf-8-sig')
        for col in HISTORY_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df
    except Exception as e:
        print(f"Warning: cannot read history: {e}")
        return pd.DataFrame(columns=HISTORY_COLUMNS)

def append_case_history(record: dict) -> bool:
    """Append one completed case to persistent CSV. Computes errors automatically."""
    try:
        ai = record.get('ai_predicted_min')
        actual = record.get('actual_duration_min')
        if ai is not None and actual is not None:
            record['signed_error'] = actual - ai
            record['abs_error'] = abs(actual - ai)
        row = {c: record.get(c) for c in HISTORY_COLUMNS}
        df_new = pd.DataFrame([row])
        header = not os.path.exists(HISTORY_FILE)
        df_new.to_csv(HISTORY_FILE, mode='a', header=header,
                      index=False, encoding='utf-8-sig')
        return True
    except Exception as e:
        print(f"Warning: cannot append history: {e}")
        return False

def top_n_procedures(df: pd.DataFrame, by: str = 'volume', n: int = 10) -> pd.DataFrame:
    """by = 'volume' | 'avg_duration' | 'mae' | 'bias'"""
    if df.empty:
        return pd.DataFrame()
    g = df.groupby('procedure').agg(
        n_cases=('procedure', 'size'),
        avg_duration=('actual_duration_min', 'mean'),
        median_duration=('actual_duration_min', 'median'),
        mae=('abs_error', 'mean'),
        bias=('signed_error', 'mean'),
    ).reset_index()
    g = g[g['n_cases'] >= 1]
    sort_key = {'volume': 'n_cases', 'avg_duration': 'avg_duration',
                'mae': 'mae', 'bias': 'bias'}.get(by, 'n_cases')
    ascending = (by == 'mae')
    return g.sort_values(sort_key, ascending=ascending).head(n).round(1)

def top_n_surgeons(df: pd.DataFrame, by: str = 'volume', n: int = 10) -> pd.DataFrame:
    """by = 'volume' | 'avg_duration' | 'mae'"""
    if df.empty:
        return pd.DataFrame()
    g = df.groupby('surgeon').agg(
        n_cases=('surgeon', 'size'),
        avg_duration=('actual_duration_min', 'mean'),
        mae=('abs_error', 'mean'),
    ).reset_index()
    sort_key = {'volume': 'n_cases', 'avg_duration': 'avg_duration',
                'mae': 'mae'}.get(by, 'n_cases')
    ascending = (by == 'mae')
    return g.sort_values(sort_key, ascending=ascending).head(n).round(1)

def top_n_surg_proc(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Top surgeon x procedure combos by volume."""
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(['surgeon', 'procedure']).agg(
        n_cases=('procedure', 'size'),
        avg_duration=('actual_duration_min', 'mean'),
        mae=('abs_error', 'mean'),
    ).reset_index()
    return g.sort_values('n_cases', ascending=False).head(n).round(1)

def top_n_nurses(df: pd.DataFrame, role: str = 'scrub', n: int = 10) -> pd.DataFrame:
    """role = 'scrub' | 'circ'"""
    if df.empty or role not in df.columns:
        return pd.DataFrame()
    g = df.groupby(role).agg(
        n_cases=(role, 'size'),
        avg_duration=('actual_duration_min', 'mean'),
    ).reset_index()
    g = g[g[role].notna() & (g[role].astype(str).str.strip() != '')]
    return g.sort_values('n_cases', ascending=False).head(n).round(1)

# ============================================================================
# SESSION STATE
# ============================================================================

def init_session_state():
    if 'patient_cases' not in st.session_state:
        st.session_state.patient_cases = []
    if 'my_room' not in st.session_state:
        st.session_state.my_room = 'หัวหน้า (ทุกห้อง)'
    if 'or_rooms' not in st.session_state:
        _room_tpl = lambda name, spec: {
            'status': 'ว่าง', 'current_case': None, 'start_time': None,
            'predicted_time': None, 'override_time': None, 'is_emergency': False,
            'staff': {'scrub': '', 'circulating': ''},
            'name': name, 'specialty': spec,
        }
        st.session_state.or_rooms = {
            1: _room_tpl('ห้อง 1', 'Morpheus / Laser / Cooltech'),
            3: _room_tpl('ห้อง 3', 'ESWL'),
            4: _room_tpl('ห้อง 4', 'ผ่าตัดทั่วไป'),
            5: _room_tpl('ห้อง 5', 'ผ่าตัดทั่วไป'),
        }
    if 'statistics' not in st.session_state:
        st.session_state.statistics = {
            'total_cases': 0, 'completed_cases': 0, 'cancelled_cases': 0,
            'case_history': [], 'predictions_history': []
        }
    if 'room_settings' not in st.session_state:
        _empty_scrub = ['', '']
        _empty_circ = ['', '', '', '']
        st.session_state.room_settings = {
            1: {'enabled': True, 'name': 'ห้อง 1', 'specialty': 'Morpheus / Laser / Cooltech', 'scrub': list(_empty_scrub), 'circ': list(_empty_circ), 'nurses': []},
            3: {'enabled': True, 'name': 'ห้อง 3', 'specialty': 'ESWL', 'scrub': list(_empty_scrub), 'circ': list(_empty_circ), 'nurses': []},
            4: {'enabled': True, 'name': 'ห้อง 4', 'specialty': 'ผ่าตัดทั่วไป', 'scrub': list(_empty_scrub), 'circ': list(_empty_circ), 'nurses': []},
            5: {'enabled': True, 'name': 'ห้อง 5', 'specialty': 'ผ่าตัดทั่วไป', 'scrub': list(_empty_scrub), 'circ': list(_empty_circ), 'nurses': []},
        }
    if 'uploaded_cases' not in st.session_state:
        st.session_state.uploaded_cases = []
    if 'schedule' not in st.session_state:
        st.session_state.schedule = []
