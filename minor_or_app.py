"""
Minor OR Management Dashboard - Trial Version
ระบบจัดการห้องผ่าตัดเล็ก — AI ทำนายเวลาผ่าตัด
โครงสร้าง UI เหมือน pro09.py (ห้องผ่าตัดใหญ่)

Author: Mukky — Master's Thesis, Nursing Administration
Institution: Chulalongkorn University
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import json
from collections import defaultdict
import uuid

from minor_or_core import (
    init_session_state, load_ml_assets, predict_surgical_time,
    parse_opetime_full, parse_opetime,
    TURNOVER_MINOR, WORK_START, WORK_END, WORK_MINUTES
)
from minor_or_pages import page_or_board, page_statistics
from minor_or_tracking import page_tracking
from minor_or_admin import page_admin
from minor_or_db import init_db, get_db_stats, save_room_settings, load_room_settings

# ============================================================================
# PAGE CONFIG & CSS
# ============================================================================

st.set_page_config(
    page_title="ห้องผ่าตัดเล็ก Dashboard",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap');
    * { font-family: 'Sarabun', sans-serif; }
    .card { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); border-radius: 12px; padding: 20px; margin: 10px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border-left: 5px solid #3498db; }
    .card-waiting { background: linear-gradient(135deg, #fff9e6 0%, #ffe680 100%); border-left-color: #f1c40f; }
    .card-inor { background: linear-gradient(135deg, #e3f2fd 0%, #90caf9 100%); border-left-color: #2196f3; }
    .card-recovery { background: linear-gradient(135deg, #e8f5e9 0%, #81c784 100%); border-left-color: #4caf50; }
    .card-emergency { background: linear-gradient(135deg, #ffebee 0%, #ef5350 100%); border-left-color: #f44336; border: 2px solid #f44336; }
    .or-room-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center; min-height: 300px; border-top: 4px solid #3498db; }
    .or-room-empty { border-top-color: #95a5a6; background: linear-gradient(135deg, #ecf0f1 0%, #bdc3c7 100%); }
    .or-room-active { border-top-color: #2196f3; background: linear-gradient(135deg, #e3f2fd 0%, #e1f5fe 100%); }
    .timer { font-size: 32px; font-weight: bold; color: #e74c3c; font-family: 'Courier New', monospace; }
    .metric-box { background: white; border-radius: 12px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }
    .stat-title { color: #7f8c8d; font-size: 14px; font-weight: 600; margin-bottom: 10px; }
    .stat-value { color: #2c3e50; font-size: 32px; font-weight: bold; }
    .header-title { color: #2c3e50; font-size: 28px; font-weight: 700; margin-bottom: 20px; }
    .subheader { color: #34495e; font-size: 18px; font-weight: 600; margin-top: 20px; margin-bottom: 15px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

init_session_state()

# Restore room settings from DB on first load (fix #1: persist across restarts)
if not st.session_state.get('_room_settings_loaded'):
    try:
        db_settings = load_room_settings()
        for rm_no, data in db_settings.items():
            if rm_no in st.session_state.room_settings:
                st.session_state.room_settings[rm_no]['enabled'] = data['enabled']
                st.session_state.room_settings[rm_no]['scrub'] = data['scrub']
                st.session_state.room_settings[rm_no]['circ'] = data['circ']
                st.session_state.room_settings[rm_no]['nurses'] = [n for n in data['scrub'] + data['circ'] if n]
    except Exception:
        pass
    st.session_state['_room_settings_loaded'] = True

# ============================================================================
# PAGE 1: ROOM SETTINGS
# ============================================================================

def page_room_settings():
    from minor_or_tracking import OR_NURSE_LIST

    ROOM_INFO = {
        1: {'label': 'ห้อง 1 — Morpheus / Laser / Cooltech', 'desc': 'สำหรับทำ Morpheus, Laser, Cooltech'},
        3: {'label': 'ห้อง 3 — ESWL', 'desc': 'สำหรับ ESWL (สลายนิ่ว)'},
        4: {'label': 'ห้อง 4 — ผ่าตัดทั่วไป', 'desc': 'เคสผ่าตัดอื่นๆ'},
        5: {'label': 'ห้อง 5 — ผ่าตัดทั่วไป', 'desc': 'เคสผ่าตัดอื่นๆ'},
    }

    st.markdown('<h1 class="header-title">⚙️ ตั้งค่าห้องผ่าตัดเล็ก</h1>', unsafe_allow_html=True)
    st.markdown('<p style="color:#7f8c8d;font-size:14px;">ตั้งค่าสถานะ + พยาบาล Scrub / Circulating ประจำห้อง — เคสที่เข้าห้องจะ auto-fill ให้</p>', unsafe_allow_html=True)

    nurse_options = ['— ยังไม่ระบุ —'] + OR_NURSE_LIST
    _NONE = '— ยังไม่ระบุ —'
    all_inputs = {}

    for rm in [1, 3, 4, 5]:
        info = ROOM_INFO[rm]
        # Ensure room exists in session state
        if rm not in st.session_state.room_settings:
            st.session_state.room_settings[rm] = {
                'enabled': True, 'name': info['label'].split(' — ')[0],
                'specialty': info['desc'], 'scrub': ['', ''], 'circ': ['', '', '', ''],
                'nurses': [],
            }
        # Migrate old format → list (handles missing key, string, or short list)
        settings = st.session_state.room_settings[rm]
        raw_s = settings.get('scrub')
        if raw_s is None or isinstance(raw_s, str):
            settings['scrub'] = [raw_s or '', '']
        while len(settings['scrub']) < 2:
            settings['scrub'].append('')
        raw_c = settings.get('circ')
        if raw_c is None or isinstance(raw_c, str):
            settings['circ'] = [raw_c or '', '', '', '']
        while len(settings['circ']) < 4:
            settings['circ'].append('')

        if rm not in st.session_state.or_rooms:
            st.session_state.or_rooms[rm] = {
                'status': 'ว่าง', 'current_case': None, 'start_time': None,
                'predicted_time': None, 'override_time': None, 'is_emergency': False,
                'staff': {'scrub': '', 'circulating': ''},
                'name': info['label'].split(' — ')[0], 'specialty': info['desc'],
            }

        st.markdown(f'<div style="background:#f8f9fa;padding:12px 16px;border-radius:10px;border-left:4px solid #3498db;margin:8px 0;"><b>{info["label"]}</b><br><span style="color:#7f8c8d;font-size:12px;">{info["desc"]}</span></div>', unsafe_allow_html=True)

        enabled = st.toggle(f"เปิดใช้งาน {info['label'].split(' — ')[0]}", value=settings.get('enabled', True), key=f"toggle_room_{rm}")

        room_inputs = {'enabled': enabled, 'scrub': ['', ''], 'circ': ['', '', '', '']}

        if enabled:
            st.markdown("🧤 **Scrub Nurse** (2 ตำแหน่ง)")
            sc1, sc2 = st.columns(2)
            for si, col in enumerate([sc1, sc2]):
                with col:
                    cur = settings['scrub'][si] if si < len(settings['scrub']) else ''
                    idx = nurse_options.index(cur) if cur in nurse_options else 0
                    room_inputs['scrub'][si] = st.selectbox(
                        f"Scrub #{si+1}", nurse_options, index=idx,
                        key=f"set_scrub_{rm}_{si}", label_visibility='collapsed')

            st.markdown("📋 **Circulating Nurse** (4 ตำแหน่ง)")
            cc1, cc2, cc3, cc4 = st.columns(4)
            for ci, col in enumerate([cc1, cc2, cc3, cc4]):
                with col:
                    cur = settings['circ'][ci] if ci < len(settings['circ']) else ''
                    idx = nurse_options.index(cur) if cur in nurse_options else 0
                    room_inputs['circ'][ci] = st.selectbox(
                        f"Circ #{ci+1}", nurse_options, index=idx,
                        key=f"set_circ_{rm}_{ci}", label_visibility='collapsed')
        else:
            st.caption("🔒 ห้องปิด — ไม่สามารถตั้งค่าได้")

        all_inputs[rm] = room_inputs
        st.markdown("---")

    if st.button("💾 บันทึกการตั้งค่า", type="primary", use_container_width=True):
        for rm, room_inputs in all_inputs.items():
            settings = st.session_state.room_settings[rm]
            room = st.session_state.or_rooms[rm]
            settings['enabled'] = room_inputs['enabled']
            # Clean: replace placeholder with empty string
            settings['scrub'] = [v if v != _NONE else '' for v in room_inputs['scrub']]
            settings['circ'] = [v if v != _NONE else '' for v in room_inputs['circ']]
            # Deduplicate: ป้องกันเลือกคนซ้ำ (fix #3)
            seen = set()
            for i, n in enumerate(settings['scrub']):
                if n and n in seen:
                    settings['scrub'][i] = ''
                elif n:
                    seen.add(n)
            for i, n in enumerate(settings['circ']):
                if n and n in seen:
                    settings['circ'][i] = ''
                elif n:
                    seen.add(n)
            # backward compat: nurses list = all non-empty
            settings['nurses'] = [n for n in settings['scrub'] + settings['circ'] if n]
            if room_inputs['enabled']:
                if room['status'] == 'ปิด':
                    room['status'] = 'ว่าง'
            else:
                room['status'] = 'ปิด'
                settings['scrub'] = ['', '']
                settings['circ'] = ['', '', '', '']
                settings['nurses'] = []
            # Persist to DB (fix #1)
            save_room_settings(rm, settings['enabled'], settings['scrub'], settings['circ'])
        st.success("✅ บันทึกการตั้งค่าสำเร็จ! (บันทึกลง DB แล้ว)")
        st.rerun()

    st.markdown("### 👥 สรุปบุคลากรที่ตั้งค่าไว้")
    for rm in [1, 3, 4, 5]:
        info = ROOM_INFO[rm]
        settings = st.session_state.room_settings[rm]
        scrub_list = [n for n in (settings.get('scrub') or []) if n]
        circ_list = [n for n in (settings.get('circ') or []) if n]
        if scrub_list or circ_list:
            badges = ''
            for s in scrub_list:
                badges += f'<span style="background:#e8eaf6;color:#3949ab;padding:3px 10px;border-radius:12px;font-size:12px;margin:2px;display:inline-block;">🧤 {s}</span>'
            for c in circ_list:
                badges += f'<span style="background:#e0f2f1;color:#00695c;padding:3px 10px;border-radius:12px;font-size:12px;margin:2px;display:inline-block;">📋 {c}</span>'
            st.markdown(f'<b>{info["label"].split(" — ")[0]}:</b> {badges}', unsafe_allow_html=True)
        else:
            st.caption(f'{info["label"].split(" — ")[0]}: ยังไม่ได้ตั้งค่า')


# ============================================================================
# PAGE 2: PLAN SCHEDULE
# ============================================================================

def page_plan_schedule():
    st.markdown('<h1 class="header-title">📋 วางแผนตาราง</h1>', unsafe_allow_html=True)

    st.markdown('<div style="background:#e3f2fd;padding:12px 16px;border-radius:10px;border-left:4px solid #1976d2;margin-bottom:16px;"><b>📋 วิธีใช้</b><br>1. อัพโหลดไฟล์ CSV ตารางผ่าตัด → ระบบทำนายเวลาอัตโนมัติ<br>2. ตรวจสอบรายการ ลบเคสที่ไม่ต้องการ<br>3. กด <b>"📤 ส่งเข้า OR Board"</b><br><span style="font-size:12px;color:#666;">⚠️ Upload ซ้ำได้ — ระบบจะอัพเดทเฉพาะเคสที่ยังไม่เข้า flow</span></div>', unsafe_allow_html=True)

    uploaded_file = st.file_uploader("เลือกไฟล์ CSV ตารางผ่าตัด", type=["csv"], help="รองรับหลาย encoding")

    if uploaded_file is not None:
        encodings = ['utf-8-sig', 'utf-8', 'utf-16', 'tis-620', 'cp874']
        df_raw = None
        for enc in encodings:
            try:
                uploaded_file.seek(0)
                df_raw = pd.read_csv(uploaded_file, encoding=enc)
                break
            except (ValueError, TypeError, AttributeError):
                continue
        if df_raw is None:
            st.error("❌ ไม่สามารถอ่านไฟล์ได้")
            return

        st.markdown(f"**พบ {len(df_raw)} แถว, {len(df_raw.columns)} คอลัมน์**")

        col_map = {}
        cols_lower = {c.lower(): c for c in df_raw.columns}
        def find_col(*kws):
            for kw in kws:
                for cl, co in cols_lower.items():
                    if kw.lower() in cl:
                        return co
            return None

        col_map['hn'] = find_col('hn')
        col_map['name'] = find_col('dspname','name')
        col_map['age'] = find_col('age')
        col_map['procedure'] = find_col('icd9cm_name','procedure','icd9')
        col_map['surgeon'] = find_col('surgstfnm','surgeon')
        col_map['date'] = find_col('opedate','date')
        col_map['estmtime'] = find_col('estmtime','opetime','time')
        col_map['order'] = find_col('ororder','order')
        col_map['diagnosis'] = find_col('icd10name','icd10','diag')
        col_map['anesthesia'] = find_col('anestechnm','anestype','anes')
        col_map['division'] = find_col('division')
        col_map['casetype'] = find_col('optype','casetype')
        col_map['procnote'] = find_col('procnote','note')

        with st.expander("🔧 ปรับ Column Mapping", expanded=False):
            all_cols = ['(ไม่มี)'] + list(df_raw.columns)
            c1, c2, c3 = st.columns(3)
            with c1:
                for k in ['hn','name','age','procedure']:
                    col_map[k] = st.selectbox(k.upper(), all_cols, index=all_cols.index(col_map[k]) if col_map[k] in all_cols else 0, key=f"map_{k}")
            with c2:
                for k in ['surgeon','date','estmtime','order']:
                    col_map[k] = st.selectbox(k.upper(), all_cols, index=all_cols.index(col_map[k]) if col_map[k] in all_cols else 0, key=f"map_{k}")
            with c3:
                for k in ['diagnosis','anesthesia','casetype','procnote']:
                    col_map[k] = st.selectbox(k.upper(), all_cols, index=all_cols.index(col_map[k]) if col_map[k] in all_cols else 0, key=f"map_{k}")

        if st.button("✅ โหลดรายการ + ทำนายเวลา", type="primary", use_container_width=True):
            new_cases = []
            for _, row in df_raw.iterrows():
                def get(key):
                    c = col_map.get(key)
                    if c and c != '(ไม่มี)' and c in row.index:
                        v = row[c]
                        return v if pd.notna(v) else None
                    return None

                raw_time = get('estmtime')
                try:
                    estm_val = int(float(raw_time)) if raw_time is not None else 0
                except (ValueError, TypeError, AttributeError):
                    estm_val = 0
                is_tf = (estm_val == 0)
                sched_h, sched_m = (23, 55) if is_tf else parse_opetime_full(raw_time)

                raw_note = str(get('procnote') or '')
                raw_date = get('date')
                try:
                    sched_date = pd.to_datetime(str(raw_date)).date()
                except (ValueError, TypeError, AttributeError):
                    sched_date = datetime.now().date()

                case = {
                    'id': f"CSV_{uuid.uuid4().hex[:8]}",
                    'hn': str(get('hn') or ''), 'name': str(get('name') or 'ไม่ระบุ'),
                    'age': int(float(get('age'))) if get('age') else 50,
                    'diagnosis': str(get('diagnosis') or '-'),
                    'procedure': str(get('procedure') or 'UNKNOWN').strip().upper(),
                    'anesthesia': str(get('anesthesia') or '-'),
                    'surgeon': str(get('surgeon') or ''), 'room': 1,
                    'division': str(get('division') or '75'),
                    'ororder': int(float(get('order'))) if get('order') else 1,
                    'case_type': str(get('casetype') or 'Elective').capitalize(),
                    'sched_date': sched_date, 'sched_hour': sched_h, 'sched_min': sched_m,
                    'is_tf': is_tf, 'is_after_note': 'นอกเวลา' in raw_note,
                    'procnote': raw_note, 'predicted_min': None, 'confidence': None,
                }
                new_cases.append(case)

            in_flow_hns = {pc['hn'] for pc in st.session_state.patient_cases if pc.get('status') != 'not_arrived' and pc.get('hn')}
            st.session_state.patient_cases = [pc for pc in st.session_state.patient_cases if pc.get('status') != 'not_arrived']
            filtered = [c for c in new_cases if not (c['hn'] and c['hn'] in in_flow_hns)]
            skipped = len(new_cases) - len(filtered)

            with st.spinner("กำลังทำนายเวลาผ่าตัด..."):
                for case in filtered:
                    pred = predict_surgical_time(case['procedure'], case['age'], case['surgeon'], case['division'], case['sched_hour'] if case['sched_hour'] < 23 else 9)
                    case['predicted_min'] = pred['predicted_min']
                    case['confidence'] = pred['confidence']
                    case['pred_method'] = pred['method']
                    case['proc_n'] = pred.get('proc_n', 0)
                    case['surg_n'] = pred.get('surg_n', 0)

            st.session_state.uploaded_cases = filtered
            msg = [f"✅ โหลด + ทำนายสำเร็จ {len(filtered)} เคส"]
            if skipped > 0:
                msg.append(f"ข้าม {skipped} เคสที่เข้า flow แล้ว")
            st.success(" | ".join(msg))
            st.rerun()

    # Manual entry
    with st.expander("➕ เพิ่มเคสด้วยตนเอง"):
        c1, c2 = st.columns(2)
        with c1:
            hn_m = st.text_input("HN *", key="m_hn")
            name_m = st.text_input("ชื่อ-สกุล *", key="m_name")
            proc_m = st.text_input("หัตถการ *", key="m_proc")
            surg_m = st.text_input("แพทย์ *", key="m_surg")
        with c2:
            age_m = st.number_input("อายุ", 0, 120, 50, key="m_age")
            time_m = st.time_input("เวลาผ่าตัด", key="m_time")
            order_m = st.number_input("ลำดับ", 1, 30, 1, key="m_order")
            div_m = st.selectbox("แผนก", ['75','74','78','76','701','77','72'], key="m_div")

        if st.button("✅ เพิ่มเคส + ทำนาย", use_container_width=True):
            if hn_m and name_m and proc_m and surg_m:
                case = {
                    'id': f"MANUAL_{uuid.uuid4().hex[:8]}", 'hn': hn_m, 'name': name_m,
                    'age': age_m, 'diagnosis': '-', 'procedure': proc_m.strip().upper(),
                    'anesthesia': '-', 'surgeon': surg_m, 'room': 1, 'division': div_m,
                    'ororder': order_m, 'case_type': 'Elective', 'sched_date': datetime.now().date(),
                    'sched_hour': time_m.hour, 'sched_min': time_m.minute,
                    'is_tf': False, 'is_after_note': False, 'procnote': '',
                    'predicted_min': None, 'confidence': None,
                }
                pred = predict_surgical_time(case['procedure'], case['age'], case['surgeon'], case['division'], case['sched_hour'])
                case.update({'predicted_min': pred['predicted_min'], 'confidence': pred['confidence'], 'pred_method': pred['method'], 'proc_n': pred.get('proc_n',0), 'surg_n': pred.get('surg_n',0)})
                st.session_state.uploaded_cases.append(case)
                st.success(f"✅ ทำนาย {pred['predicted_min']} นาที")
                st.rerun()
            else:
                st.error("กรุณากรอก HN, ชื่อ, หัตถการ, แพทย์")

    # Display uploaded cases
    if st.session_state.uploaded_cases:
        st.markdown("---")
        total = len(st.session_state.uploaded_cases)
        tf_n = sum(1 for c in st.session_state.uploaded_cases if c.get('is_tf'))
        after_n = sum(1 for c in st.session_state.uploaded_cases if c.get('is_after_note') or ((not c.get('is_tf')) and c['sched_hour'] >= WORK_END))

        m1, m2, m3 = st.columns(3)
        m1.metric("เคสทั้งหมด", total)
        m2.metric("TF", tf_n)
        m3.metric("นอกเวลา", after_n)

        st.markdown("### 📋 OR Schedule — ห้องผ่าตัดเล็ก")
        sorted_cases = sorted(enumerate(st.session_state.uploaded_cases), key=lambda x: (1 if x[1].get('is_tf') else 0, x[1]['sched_hour'], x[1]['sched_min'], x[1]['ororder']))

        st.markdown(f'<div style="background:linear-gradient(135deg,#2c3e50,#3498db);color:white;padding:10px 16px;border-radius:8px 8px 0 0;margin-top:16px;font-size:16px;font-weight:700;">🏥 ห้องผ่าตัดเล็ก 1 ({total} เคส)</div>', unsafe_allow_html=True)

        to_delete = []
        for idx, case in sorted_cases:
            time_d = "TF" if case.get('is_tf') else f'{case["sched_hour"]:02d}:{case["sched_min"]:02d}'
            pred_html = ""
            if case.get('predicted_min'):
                conf = case.get('confidence', '-')
                conf_color = '#27ae60' if conf == 'สูง' else ('#f39c12' if conf == 'ปานกลาง' else '#e74c3c')
                pred_html = f'<br><span style="font-size:12px;">🤖 <b style="color:#2980b9;">{case["predicted_min"]} นาที</b> | ความเชื่อมั่น <b style="color:{conf_color};">{conf}</b></span>'

            col_i, col_d = st.columns([11, 1])
            with col_i:
                st.markdown(f'<div style="border-left:4px solid #eee;background:#fafafa;padding:10px 14px;border-radius:0 4px 4px 0;margin:1px 0;"><span style="font-weight:700;color:#2c3e50;">#{case["ororder"]}</span> <span style="color:#2980b9;font-weight:600;">{time_d}</span> &nbsp; <b>{case["name"]}</b> <span style="color:#7f8c8d;font-size:12px;">HN: {case["hn"] or "-"} | อายุ {case["age"]} ปี</span><br><span style="font-size:12px;color:#2c3e50;"><span style="color:#c0392b;">Op:</span> {case["procedure"]} | <span style="color:#2980b9;">Surg:</span> {case["surgeon"] or "-"}</span>{pred_html}</div>', unsafe_allow_html=True)
            with col_d:
                if st.button("❌", key=f"del_{idx}"):
                    to_delete.append(idx)

        if to_delete:
            st.session_state.uploaded_cases = [c for i, c in enumerate(st.session_state.uploaded_cases) if i not in to_delete]
            st.rerun()

        c_clr, c_send = st.columns([1, 2])
        with c_clr:
            if st.button("🗑️ ล้างทั้งหมด", use_container_width=True):
                st.session_state.uploaded_cases = []
                st.rerun()
        with c_send:
            if st.button("📤 ส่งเข้า OR Board", type="primary", use_container_width=True):
                new_n = 0
                existing_ids = {c['id'] for c in st.session_state.patient_cases}
                for case in st.session_state.uploaded_cases:
                    if case['id'] not in existing_ids:
                        p = dict(case)
                        p.update({'status': 'not_arrived', 'ai_predicted_min': case.get('predicted_min', 30), 'user_override_min': None, 'effective_min': case.get('predicted_min', 30), 'or_room_assigned': 1, 'time_arrived_holding': None, 'time_entered_or': None, 'time_exited_or': None, 'time_discharged': None, 'actual_duration_min': None})
                        st.session_state.patient_cases.append(p)
                        new_n += 1
                st.success(f"✅ ส่ง {new_n} เคส" if new_n else "ℹ️ เคสทั้งหมดอยู่ใน OR Board แล้ว")
                st.rerun()

        # HP Bar
        pred_cases = [c for c in st.session_state.uploaded_cases if c.get('predicted_min')]
        if pred_cases:
            st.markdown("---")
            in_time = [c for c in pred_cases if not (c.get('is_after_note') or ((not c.get('is_tf')) and c['sched_hour'] >= WORK_END))]
            op_min = sum(c['predicted_min'] for c in in_time)
            to_min = TURNOVER_MINOR * len(in_time)
            total_min = op_min + to_min

            st.markdown("### 🎮 เวลาใช้ห้องผ่าตัด (ในเวลาราชการ)")
            op_pct = min(100, op_min / WORK_MINUTES * 100)
            to_pct = min(100 - op_pct, to_min / WORK_MINUTES * 100)
            total_pct = op_pct + to_pct
            bar_c = '#27ae60' if total_pct <= 80 else ('#f39c12' if total_pct <= 100 else '#e74c3c')
            overflow = total_min > WORK_MINUTES
            st.markdown(f'<div style="margin:6px 0;"><div style="display:flex;align-items:center;margin-bottom:2px;"><span style="font-weight:700;font-size:14px;width:120px;">ผ่าตัดเล็ก 1</span><span style="font-size:12px;color:#7f8c8d;">{len(in_time)} เคส | Op {op_min} + TO {to_min} = <b style="color:{bar_c};">{total_min} นาที</b>{"⚠️ เกิน!" if overflow else ""}</span></div><div style="background:#ecf0f1;border-radius:6px;height:22px;width:100%;position:relative;overflow:visible;"><div style="background:#3498db;height:100%;width:{op_pct}%;border-radius:6px 0 0 6px;float:left;"></div><div style="background:#bdc3c7;height:100%;width:{to_pct}%;float:left;"></div><div style="position:absolute;left:100%;top:-2px;height:26px;width:2px;background:#e74c3c;"></div></div></div>', unsafe_allow_html=True)


# ============================================================================
# MAIN
# ============================================================================

def main():
    # Initialize DB on startup
    init_db()

    with st.sidebar:
        st.markdown(
            '<div style="text-align:center;padding:20px 0;">'
            '<h2 style="color:#2c3e50;margin:0;">🏥 Minor OR</h2>'
            '<p style="color:#7f8c8d;font-size:12px;margin-top:5px;">'
            'ระบบจัดการห้องผ่าตัดเล็ก — Trial</p></div>',
            unsafe_allow_html=True,
        )
        st.markdown("---")

        page = st.radio(
            "📋 เมนูหลัก",
            ["📋 ตารางผ่าตัด", "📊 บริหารจัดการ", "⚙️ ตั้งค่า"],
        )

        st.markdown("---")

        # DB-backed sidebar stats
        db_stats = get_db_stats()
        st.markdown('<h4 style="color:#2c3e50;">📊 สรุปด่วน</h4>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.metric("เคสวันนี้", db_stats['today'])
        c2.metric("ผ่าแล้ว", db_stats['today_done'])
        c3, _ = st.columns(2)
        c3.metric("เคสสะสม", db_stats['total_all'])

        st.markdown("---")
        assets = load_ml_assets()
        if assets['model_loaded']:
            d = assets['model_data']
            res = d.get('results', {}).get(d.get('model_name', ''), {})
            st.markdown(
                f'<div style="background:#e8f5e9;padding:8px;border-radius:8px;text-align:center;">'
                f'<p style="margin:0;font-size:11px;color:#2e7d32;">'
                f'🤖 <b>AI Model: Active</b><br>'
                f'{d.get("model_name","?")} (MAE={res.get("MAE","?")})<br>'
                f'หัตถการ {len(d.get("procedures",[]))} | แพทย์ {len(d.get("surgeons",[]))}'
                f'</p></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="background:#ffebee;padding:8px;border-radius:8px;text-align:center;">'
                '<p style="margin:0;font-size:11px;color:#c62828;">'
                '⚠️ <b>ไม่พบ Model</b><br>ตรวจสอบ minor_or_model.pkl</p></div>',
                unsafe_allow_html=True,
            )

        if st.button('🔄 Reload Model', use_container_width=True):
            load_ml_assets.clear()
            st.rerun()

        st.markdown('---')
        st.markdown(
            '<p style="font-size:11px;color:#95a5a6;text-align:center;">'
            '<b>Trial Version</b><br>Minor OR Management<br>'
            'ML Surgical Time Prediction<br>Chulalongkorn University</p>',
            unsafe_allow_html=True,
        )

    # Route pages
    if page == '📋 ตารางผ่าตัด':
        page_tracking()
    elif page == '📊 บริหารจัดการ':
        page_admin()
    elif page == '⚙️ ตั้งค่า':
        page_room_settings()



if __name__ == "__main__":
    main()
