"""
Minor OR Tracking v2 — Step-by-step Workflow
Upload CSV >> รับผู้ป่วย (timer) >> เข้าห้องผ่าตัด >> ผ่าเสร็จ >> Discharge >> โทรเยี่ยม
"""
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, timezone
_BKK = timezone(timedelta(hours=7))

def _now_bkk():
    """Return current datetime in Bangkok timezone (naive, for comparisons with stored timestamps)."""
    return datetime.now(_BKK).replace(tzinfo=None)
from minor_or_db import (
    init_db, import_schedule, add_walkin_case, get_cases,
    get_pending_calls, get_summary, update_case, update_checkbox,
    update_postcall, cancel_case,
    mark_arrived, mark_in_or, mark_in_or_with_nurses, mark_op_end, mark_discharged,
    get_db_stats, DIVISIONS, div_name, DIV_CODE_MAP,
    lookup_cost, PROCEDURE_COSTS, PATHO_COSTS,
    export_summary_excel,
)


# ============================================================================
# CSS
# ============================================================================

_CSS = """
<style>
.case-card {
    border-radius: 12px; padding: 14px 16px; margin: 10px 0;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
}
.card-scheduled  { background:#fff;    border-left:5px solid #95a5a6; }
.card-arrived    { background:#fffde7; border-left:5px solid #f9a825; }
.card-in-or      { background:#e3f2fd; border-left:5px solid #1976d2; }
.card-post-op    { background:#e8f5e9; border-left:5px solid #388e3c; }
.card-discharged { background:#f1f8e9; border-left:5px solid #7cb342; }
.card-cancelled  { background:#f5f5f5; border-left:5px solid #bdbdbd; opacity:.5; }

.pill { display:inline-block; padding:2px 10px; border-radius:20px;
        font-size:12px; font-weight:600; margin-right:4px; }
.pill-sched  { background:#eceff1; color:#546e7a; }
.pill-arrive { background:#fff9c4; color:#f57f17; }
.pill-inor   { background:#bbdefb; color:#1565c0; }
.pill-postop { background:#c8e6c9; color:#2e7d32; }
.pill-dc     { background:#dcedc8; color:#558b2f; }
.pill-cancel { background:#f5f5f5; color:#9e9e9e; }
.pill-set    { background:#e0f2f1; color:#00695c; }
.pill-walkin { background:#e3f2fd; color:#1565c0; }
.pill-opd    { background:#e0f7fa; color:#00838f; }
.pill-ipd    { background:#fff3e0; color:#e65100; }
.pill-after  { background:#fce4ec; color:#c62828; }

.pt-name { font-size:16px; font-weight:700; color:#212121; }
.pt-hn   { font-size:12px; color:#9e9e9e; margin-left:6px; }
.pt-proc { font-size:14px; color:#424242; margin-top:4px; }
.pt-meta { font-size:12px; color:#9e9e9e; margin-top:2px; }

.timer-normal { font-size:18px; font-weight:700; color:#f9a825; }
.timer-danger { font-size:18px; font-weight:700; color:#d32f2f; }

.metric-box { text-align:center; background:#f8f9fa; border-radius:10px; padding:12px 8px; }
.metric-num { font-size:28px; font-weight:800; color:#2c3e50; }
.metric-lbl { font-size:12px; color:#7f8c8d; }

.timeline { font-size:12px; color:#616161; margin-top:4px; }
.timeline b { color:#212121; }

.call-card { background:#fff8e1; border-left:4px solid #ffa000;
             border-radius:0 10px 10px 0; padding:10px 14px; margin:6px 0; }
.ai-badge { display:inline-block; background:linear-gradient(135deg,#e8eaf6,#c5cae9);
            color:#283593; font-size:12px; font-weight:700; padding:3px 10px;
            border-radius:12px; margin-top:4px; }
</style>
"""


# รายชื่อพยาบาลห้องผ่าตัดเล็ก — ใช้เลือก Scrub / Circulating ในหน้าห้องผ่าตัด
OR_NURSE_LIST = [
    'ศิวพร ม่วงไทย',
    'วิไล ภู่หลำ',
    'อโณทัย คำอ้วน',
    'ธัญญาภรณ์ ธรรมวาสี',
    'ญาณิศา สุทธิบูรณ์',
    'พิมพ์ชนก ตั๊นประเสริฐ',
    'ศตพร แย้มชื่น',
    'เพชรมงกุฏ แขมดำ',
    'พรสุภา ญาณะวัฒน์',
]


# ============================================================================
# Price CSV — Fuzzy Lookup
# ============================================================================

import os as _os

@st.cache_data(ttl=3600)
def _load_price_csv():
    """Load or_minor_price.csv → list of dicts."""
    csv_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'or_minor_price.csv')
    if not _os.path.exists(csv_path):
        return []
    df = pd.read_csv(csv_path)
    return df.to_dict('records')


def _fuzzy_price_lookup(procedure_name: str):
    """Fuzzy match procedure name against price CSV.
    Returns list of matching dicts: [{procedure_name, procedure_name_th, new_price_thb}, ...]
    """
    prices = _load_price_csv()
    if not prices or not procedure_name:
        return []
    p = procedure_name.strip().upper()

    # 1. Exact match
    exact = [r for r in prices if r['procedure_name'].strip().upper() == p]
    if exact:
        return exact

    # 2. Keyword contain — procedure_name from CSV is contained in input, or vice versa
    contains = []
    for r in prices:
        csv_name = r['procedure_name'].strip().upper()
        if csv_name in p or p in csv_name:
            contains.append(r)
    if contains:
        return contains

    # 3. First-word / keyword group match — extract first word from input
    first_word = p.split()[0] if p.split() else ''
    if first_word and len(first_word) >= 3:
        group = [r for r in prices if r['procedure_name'].strip().upper().startswith(first_word)]
        if group:
            return group

    # 4. No match
    return []


_NONE_LABEL = '— ไม่ระบุ —'
_SKIP_VALUES = {_NONE_LABEL}


def _build_nurse_options(room_no: int) -> list:
    """สร้าง list ตัวเลือกพยาบาล — พยาบาลประจำห้องขึ้นก่อน แล้วตามด้วยคนอื่น.
    ดึง scrub/circ (lists) จาก Room Settings > ถ้าไม่มีก็ใช้ OR_NURSE_LIST ทั้งหมด.
    ไม่มี separator ที่เลือกได้ — ใช้หมวดหมู่แทน."""
    settings = st.session_state.get('room_settings', {})
    room = settings.get(room_no, {})
    scrub_raw = room.get('scrub', [])
    circ_raw = room.get('circ', [])
    if isinstance(scrub_raw, str):
        scrub_raw = [scrub_raw]
    if isinstance(circ_raw, str):
        circ_raw = [circ_raw]
    # รวมชื่อที่ไม่ว่างและไม่ซ้ำ (preserve order)
    seen = set()
    room_nurses = []
    for n in list(scrub_raw) + list(circ_raw):
        if n and isinstance(n, str) and n not in seen:
            room_nurses.append(n)
            seen.add(n)

    if room_nurses:
        others = [n for n in OR_NURSE_LIST if n not in seen]
        # ไม่ใส่ separator — ใส่แค่ชื่อจริงเท่านั้น
        return [_NONE_LABEL] + room_nurses + others
    else:
        return [_NONE_LABEL] + OR_NURSE_LIST


def _inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def _read_csv(uploaded):
    for enc in ['utf-8-sig', 'utf-16', 'tis-620', 'cp874', 'latin-1']:
        uploaded.seek(0)
        try:
            df = pd.read_csv(uploaded, encoding=enc)
            if not df.empty and len(df.columns) >= 2:
                return df
        except Exception:
            pass
    return None


# ============================================================================
# MAIN ENTRY
# ============================================================================

def page_tracking():
    _inject_css()

    st.markdown(
        '<div style="background:linear-gradient(135deg,#e3f2fd 0%,#bbdefb 100%);'
        'border-radius:12px;padding:18px 24px;margin-bottom:10px;">'
        '<h2 style="margin:0;color:#1565c0;font-size:26px;">🏥 Minor OR — Operating Room Management</h2>'
        '<p style="margin:4px 0 0;color:#1976d2;font-size:14px;">'
        'ระบบจัดการห้องผ่าตัดเล็ก (ทดลองใช้)</p></div>',
        unsafe_allow_html=True,
    )

    # ---- Date + Upload + Refresh ----
    col_d, col_u, col_r = st.columns([4, 4, 1])
    with col_d:
        view_date = st.date_input("📅 วันที่",
                                   value=_now_bkk().date(),
                                   label_visibility='collapsed')
        view_date_str = view_date.strftime('%Y-%m-%d')
    with col_u:
        uploaded = st.file_uploader("นำเข้าตาราง CSV",
                                     type=['csv'], key='csv_up',
                                     label_visibility='collapsed')
    with col_r:
        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
        if st.button('🔄 Refresh', key='btn_refresh', use_container_width=True):
            st.rerun()

    if uploaded:
        df_up = _read_csv(uploaded)
        if df_up is None:
            st.error("อ่านไฟล์ไม่ได้ — ลอง save เป็น UTF-8")
        else:
            n = import_schedule(df_up, view_date_str)
            if n > 0:
                st.toast(f"นำเข้าสำเร็จ {n} เคส", icon="✅")
                st.rerun()
            else:
                st.warning("ไม่พบเคสใหม่ (อาจนำเข้าแล้ว)")
                with st.expander("ดูรายละเอียด", expanded=False):
                    mapped = getattr(import_schedule, '_last_mapped', {})
                    for k, v in mapped.items():
                        st.caption(f"{k} → {v}")

    # ---- Tabs ----
    tab_recv, tab_wait, tab_or, tab_recov, tab_dc, tab_sum = st.tabs([
        "🧑 รับผู้ป่วย",
        "⏳ รอผ่าตัด",
        "🔪 ห้องผ่าตัด",
        "🛏️ ห้องพักฟื้น",
        "🛗 ห้องรับส่ง",
        "📊 สรุปยอด",
    ])

    with tab_recv:
        _tab_station(view_date_str, 'receive')
    with tab_wait:
        _tab_waiting_room(view_date_str)
    with tab_or:
        _tab_station(view_date_str, 'or')
    with tab_recov:
        _tab_station(view_date_str, 'recovery')
    with tab_dc:
        _tab_station(view_date_str, 'discharge')
    with tab_sum:
        _tab_summary()


# ============================================================================
# TAB: ห้องรอผ่าตัด — Waiting Room (grouped by OR room)
# ============================================================================

# Keywords สำหรับจัด room อัตโนมัติ
_ROOM1_KEYWORDS = ['laser', 'morpheus', 'scaret', 'emsculpt', 'cooltect', 'q-switch',
                    'q switch', 'qswitch']
_ROOM3_KEYWORDS = ['eswl']


def _assign_waiting_room(procedure_name: str) -> str:
    """จัดห้องผ่าตัดอัตโนมัติจากชื่อหัตถการ."""
    if not procedure_name:
        return 'room45'
    p = procedure_name.strip().upper()
    for kw in _ROOM1_KEYWORDS:
        if kw.upper() in p:
            return 'room1'
    for kw in _ROOM3_KEYWORDS:
        if kw.upper() in p:
            return 'room3'
    return 'room45'


def _tab_waiting_room(view_date_str):
    """Tab รอผ่าตัด — แสดงผู้ป่วยที่กดรับแล้ว (arrived) แบ่งตามห้อง."""
    df = get_cases(op_date=view_date_str)

    if df.empty:
        st.markdown(
            '<div style="text-align:center;padding:40px 0;">'
            '<p style="font-size:48px;">⏳</p>'
            '<p style="color:#9e9e9e;font-size:16px;">ยังไม่มีผู้ป่วยรอผ่าตัด</p>'
            '<p style="color:#bdbdbd;font-size:13px;">กด "รับผู้ป่วย" ใน tab แรกก่อน</p></div>',
            unsafe_allow_html=True,
        )
        return

    # Filter เฉพาะ arrived
    waiting = df[df['status'] == 'arrived'].copy()

    if waiting.empty:
        st.info("ไม่มีผู้ป่วยรอผ่าตัดขณะนี้")
        return

    # จัด room
    waiting['_wait_room'] = waiting['procedure_name'].apply(_assign_waiting_room)

    # Sort by arrived_at (รอนานสุดขึ้นก่อน)
    waiting = waiting.sort_values('arrived_at', ascending=True, na_position='last')

    # Room definitions
    rooms = [
        ('room1', '🔬 ห้องผ่าตัด 1', 'Laser / Morpheus / Scaret / Emsculpt / Cooltect / Q-Switch'),
        ('room3', '🔧 ห้องผ่าตัด 3', 'ESWL'),
        ('room45', '🏥 ห้องผ่าตัด 4-5', 'เคสทั่วไป'),
    ]

    for room_key, room_label, room_desc in rooms:
        room_df = waiting[waiting['_wait_room'] == room_key]
        count = len(room_df)

        # Room header
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#e3f2fd,#bbdefb);'
            f'border-radius:10px;padding:12px 16px;margin:16px 0 8px;">'
            f'<span style="font-size:18px;font-weight:700;color:#1565c0;">{room_label}</span>'
            f'<span style="font-size:13px;color:#1976d2;margin-left:8px;">{room_desc}</span>'
            f'<span style="float:right;background:#1565c0;color:#fff;border-radius:20px;'
            f'padding:2px 12px;font-size:14px;font-weight:600;">{count} คน</span></div>',
            unsafe_allow_html=True,
        )

        if room_df.empty:
            st.caption("    — ว่าง —")
        else:
            for _, row in room_df.iterrows():
                _render_waiting_card(row)

        st.markdown("")


def _render_waiting_card(row):
    """แสดง card ผู้ป่วยในห้องรอผ่าตัด พร้อม timer + ปุ่มเข้าห้องผ่าตัด."""
    cid = int(row['case_id'])
    name_d = row['name'] or '-'
    hn_d = row['hn'] or '-'
    proc_d = row['procedure_name'] or '-'
    surg_d = row['surgeon_name'] or '-'

    st.markdown(f"""
    <div class="case-card card-arrived">
        <div>
            <span class="pill pill-arrive">⏳ รอผ่าตัด</span>
        </div>
        <div style="margin-top:6px;">
            <span class="pt-name">{name_d}</span>
            <span class="pt-hn">HN: {hn_d}</span>
        </div>
        <div class="pt-proc">{proc_d}</div>
        <div class="pt-meta">แพทย์: {surg_d}</div>
    </div>""", unsafe_allow_html=True)

    # Timer
    if row['arrived_at']:
        _render_timer(cid, row['arrived_at'])

    # ปุ่ม เข้าห้องผ่าตัด / ยกเลิก
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔪 เข้าห้องผ่าตัด", key=f"wait_ior_{cid}",
                     type='primary', use_container_width=True):
            rm_no = int(row.get('room_no', 1) or 1)
            rm_settings = st.session_state.get('room_settings', {}).get(rm_no, {})
            scrub_raw = rm_settings.get('scrub', [])
            circ_raw = rm_settings.get('circ', [])
            if isinstance(scrub_raw, str):
                scrub_raw = [scrub_raw]
            if isinstance(circ_raw, str):
                circ_raw = [circ_raw]
            auto_scrub = ', '.join(n for n in scrub_raw if n and isinstance(n, str))
            auto_circ = ', '.join(n for n in circ_raw if n and isinstance(n, str))
            mark_in_or_with_nurses(cid, auto_scrub, auto_circ)
            st.rerun()
    with b2:
        if st.button("❌ ยกเลิก", key=f"wait_canc_{cid}",
                     use_container_width=True):
            st.session_state[f'cancelling_{cid}'] = True

    # Cancel confirmation
    if st.session_state.get(f'cancelling_{cid}'):
        st.warning(f"⚠️ ยืนยันยกเลิกเคส **{name_d}** — {proc_d} ?")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("✅ ยืนยันยกเลิก", key=f"wait_cc_{cid}",
                         type='primary', use_container_width=True):
                cancel_case(cid)
                del st.session_state[f'cancelling_{cid}']
                st.rerun()
        with cc2:
            if st.button("↩️ ไม่ใช่", key=f"wait_cx_{cid}",
                         use_container_width=True):
                del st.session_state[f'cancelling_{cid}']
                st.rerun()


# ============================================================================
# TAB: Station-based views
# ============================================================================

_STATION_FILTER = {
    'receive':   ['scheduled'],
    'or':        ['in_or'],
    'recovery':  ['post_op__recovery'],
    'discharge': ['post_op__transfer', 'discharged'],
}

_STATION_EMPTY = {
    'receive':   ('🧑', 'ยังไม่มีเคสรอรับ', 'อัพโหลด CSV หรือเพิ่ม Walk-in ด้านล่าง'),
    'or':        ('🔪', 'ไม่มีเคสในห้องผ่าตัด', 'กดเข้าห้องผ่าตัดใน tab "รับผู้ป่วย" ก่อน'),
    'recovery':  ('🛏️', 'ห้องพักฟื้นว่าง', 'ยังไม่มีเคสที่ส่งพักฟื้น'),
    'discharge': ('🛗', 'ไม่มีเคสรอ Discharge', 'กด "ผ่าเสร็จ" แล้วเลือก "รับส่ง" ก่อน'),
}


def _tab_station(view_date_str, station):
    df = get_cases(op_date=view_date_str)

    if df.empty:
        icon, title, sub = _STATION_EMPTY.get(station, ('📂', 'ไม่มีเคส', ''))
        st.markdown(
            f'<div style="text-align:center;padding:40px 0;">'
            f'<p style="font-size:48px;">{icon}</p>'
            f'<p style="color:#9e9e9e;font-size:16px;">{title}</p>'
            f'<p style="color:#bdbdbd;font-size:13px;">{sub}</p></div>',
            unsafe_allow_html=True,
        )
        # Walk-in form even when empty (receive only)
        if station == 'receive':
            _render_walkin(view_date_str)
    else:
        # Stats bar (always show full picture)
        n_total = len(df)
        n_dc = len(df[df['status'] == 'discharged'])
        n_cancel = len(df[df['status'] == 'cancelled'])
        n_inor = len(df[df['status'] == 'in_or'])
        n_arrived = len(df[df['status'] == 'arrived'])
        n_postop = len(df[df['status'] == 'post_op'])
        n_sched = len(df[df['status'] == 'scheduled'])

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.markdown(f'<div class="metric-box"><div class="metric-num">{n_total}</div>'
                    f'<div class="metric-lbl">ทั้งหมด</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="metric-box"><div class="metric-num" style="color:#f9a825">{n_arrived}</div>'
                    f'<div class="metric-lbl">รอผ่า</div></div>', unsafe_allow_html=True)
        c3.markdown(f'<div class="metric-box"><div class="metric-num" style="color:#1976d2">{n_inor}</div>'
                    f'<div class="metric-lbl">กำลังผ่า</div></div>', unsafe_allow_html=True)
        c4.markdown(f'<div class="metric-box"><div class="metric-num" style="color:#388e3c">{n_dc + n_postop}</div>'
                    f'<div class="metric-lbl">เสร็จ</div></div>', unsafe_allow_html=True)
        c5.markdown(f'<div class="metric-box"><div class="metric-num" style="color:#e53935">{n_cancel}</div>'
                    f'<div class="metric-lbl">ยกเลิก</div></div>', unsafe_allow_html=True)

        st.markdown("")

        # Walk-in form at top of receive tab
        if station == 'receive':
            _render_walkin(view_date_str)
            st.markdown("---")

        # Filter by station (post_op split by dest)
        rules = _STATION_FILTER[station]
        mask = pd.Series(False, index=df.index)
        for rule in rules:
            if '__' in rule:
                st_part, dest_part = rule.split('__', 1)
                mask = mask | ((df['status'] == st_part) & (df['post_op_dest'] == dest_part))
            else:
                mask = mask | (df['status'] == rule)
        filtered = df[mask]

        # ---- RECEIVE TAB: แยก ในเวลา / นอกเวลา ----
        if station == 'receive':
            in_hours = filtered[filtered['patient_type'] != 'นอกเวลา']
            after_hours = filtered[filtered['patient_type'] == 'นอกเวลา']

            # === Section: ในเวลา (full OR flow + AI) ===
            st.markdown(
                '<div style="background:linear-gradient(135deg,#e8f5e9,#c8e6c9);'
                'border-radius:10px;padding:10px 16px;margin:10px 0 6px;">'
                '<span style="font-size:16px;font-weight:700;color:#2e7d32;">'
                '🏥 เคสในเวลา</span>'
                '<span style="font-size:12px;color:#388e3c;margin-left:8px;">'
                'Full OR Flow + AI Prediction</span></div>',
                unsafe_allow_html=True,
            )
            if in_hours.empty:
                st.info("ไม่มีเคสในเวลา")
            else:
                for _, row in in_hours.iterrows():
                    _render_case(row)

            st.markdown("")
            st.markdown("")

            # === Section: นอกเวลา (simplified: ยืนยัน / ยกเลิก) ===
            st.markdown(
                '<div style="background:linear-gradient(135deg,#fce4ec,#f8bbd0);'
                'border-radius:10px;padding:10px 16px;margin:10px 0 6px;">'
                '<span style="font-size:16px;font-weight:700;color:#c62828;">'
                '🌙 เคสนอกเวลา</span>'
                '<span style="font-size:12px;color:#d32f2f;margin-left:8px;">'
                'ยืนยัน / ยกเลิก เท่านั้น (ไม่เข้า OR Flow)</span></div>',
                unsafe_allow_html=True,
            )
            if after_hours.empty:
                st.info("ไม่มีเคสนอกเวลา")
            else:
                for _, row in after_hours.iterrows():
                    _render_after_hours_card(row)
        else:
            # Non-receive tabs: render normally
            if filtered.empty:
                icon, title, sub = _STATION_EMPTY.get(station, ('📂', 'ไม่มีเคส', ''))
                st.info(title)
            else:
                for _, row in filtered.iterrows():
                    _render_case(row)


def _render_after_hours_card(row):
    """Simplified card for after-hours cases: ยืนยัน (confirm+price) or ยกเลิก only."""
    cid = int(row['case_id'])
    status = row['status'] or 'scheduled'
    name_d = row['name'] or '-'
    hn_d = row['hn'] or '-'
    proc_d = row['procedure_name'] or '-'
    surg_d = row['surgeon_name'] or '-'
    diag_d = row.get('diagnosis') or ''
    _aft_diag = f'<div style="color:#555;font-size:12px;margin-top:2px;">Dx: {diag_d}</div>' if diag_d and diag_d.strip() not in ('', '-') else ''

    # If already discharged (confirmed) — show green "done" card
    if status == 'discharged':
        cost_d = int(row.get('treatment_cost') or 0)
        st.markdown(f"""<div class="case-card" style="background:#e8f5e9;border-left:5px solid #4caf50;">
<div><span class="pill pill-dc">✅ ยืนยันแล้ว</span>
<span class="pill pill-after">นอกเวลา</span></div>
<div style="margin-top:6px;">
<span class="pt-name">{name_d}</span>
<span class="pt-hn">HN: {hn_d}</span>
</div>{_aft_diag}
<div class="pt-proc">{proc_d}</div>
<div class="pt-meta">แพทย์: {surg_d} · ค่าหัตถการ: {cost_d:,} ฿</div>
</div>""", unsafe_allow_html=True)
        return

    # If cancelled — show faded card
    if status == 'cancelled':
        st.markdown(f"""<div class="case-card card-cancelled">
<div><span class="pill pill-cancel">❌ ยกเลิก</span>
<span class="pill pill-after">นอกเวลา</span></div>
<div style="margin-top:6px;text-decoration:line-through;">
<span class="pt-name">{name_d}</span>
<span class="pt-hn">HN: {hn_d}</span>
</div>{_aft_diag}
<div class="pt-proc" style="text-decoration:line-through;">{proc_d}</div>
</div>""", unsafe_allow_html=True)
        if st.button("🔄 กู้คืนเคส", key=f"aft_restore_{cid}", use_container_width=True):
            update_case(cid, status='scheduled', cancel_reason=None)
            st.rerun()
        return

    # Active card (scheduled/arrived) — show ยืนยัน / ยกเลิก buttons
    st.markdown(f"""<div class="case-card" style="background:#fff0f3;border-left:5px solid #c62828;">
<div><span class="pill pill-after">🌙 นอกเวลา</span></div>
<div style="margin-top:6px;">
<span class="pt-name">{name_d}</span>
<span class="pt-hn">HN: {hn_d}</span>
</div>{_aft_diag}
<div class="pt-proc">{proc_d}</div>
<div class="pt-meta">แพทย์: {surg_d}</div>
</div>""", unsafe_allow_html=True)

    # === ยืนยัน flow (expanded) ===
    if st.session_state.get(f'aft_confirming_{cid}'):
        st.markdown("---")
        st.markdown("**💰 ยืนยันเคส — เลือกราคาค่าหัตถการ**")

        # Fuzzy price lookup
        matches = _fuzzy_price_lookup(proc_d)
        cost_val = 0

        if len(matches) == 1:
            m = matches[0]
            st.markdown(
                f'<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
                f'border-radius:12px;font-size:12px;">match: {m["procedure_name"]}</span>',
                unsafe_allow_html=True)
            cost_val = int(m['new_price_thb'])
        elif len(matches) > 1:
            st.markdown(
                f'<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
                f'border-radius:12px;font-size:12px;">พบ {len(matches)} รายการ</span>',
                unsafe_allow_html=True)
            options_display = [
                f"{r['procedure_name_th']} — {int(r['new_price_thb']):,} ฿"
                for r in matches
            ]
            sel = st.selectbox("เลือกรายการ", options_display, key=f"aftpick_{cid}")
            sel_idx = options_display.index(sel)
            cost_val = int(matches[sel_idx]['new_price_thb'])
        cost_val = st.number_input("ค่าหัตถการ (บาท)", min_value=0,
                                    value=cost_val, step=100, key=f"aftcost_{cid}")

        # แพทย์ที่ทำ (editable)
        aft_surg = st.text_input("แพทย์ที่ทำ", value=surg_d, key=f"aftsurg_{cid}")

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("✅ ยืนยันบันทึก", key=f"aft_save_{cid}",
                         type='primary', use_container_width=True):
                # Save: set status=discharged, treatment_cost, surgeon_name
                update_case(cid, status='discharged',
                            treatment_cost=cost_val,
                            surgeon_name=aft_surg.strip() if aft_surg else surg_d,
                            discharged_at=_now_bkk().strftime('%Y-%m-%d %H:%M:%S'))
                if f'aft_confirming_{cid}' in st.session_state:
                    del st.session_state[f'aft_confirming_{cid}']
                st.rerun()
        with bc2:
            if st.button("↩️ ยกเลิก", key=f"aft_back_{cid}",
                         use_container_width=True):
                del st.session_state[f'aft_confirming_{cid}']
                st.rerun()

    # === ยกเลิก flow (confirm popup) ===
    elif st.session_state.get(f'aft_cancelling_{cid}'):
        st.warning(f"⚠️ ยืนยันยกเลิกเคส **{name_d}** — {proc_d} ?")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("✅ ยืนยันยกเลิก", key=f"aft_cc_{cid}",
                         type='primary', use_container_width=True):
                cancel_case(cid)
                del st.session_state[f'aft_cancelling_{cid}']
                st.rerun()
        with cc2:
            if st.button("↩️ ไม่ใช่", key=f"aft_cx_{cid}",
                         use_container_width=True):
                del st.session_state[f'aft_cancelling_{cid}']
                st.rerun()

    # === Default: show 2 buttons ===
    else:
        b1, b2 = st.columns(2)
        with b1:
            if st.button("✅ ยืนยัน", key=f"aft_conf_{cid}",
                         type='primary', use_container_width=True):
                st.session_state[f'aft_confirming_{cid}'] = True
                st.rerun()
        with b2:
            if st.button("❌ ยกเลิก", key=f"aft_canc_{cid}",
                         use_container_width=True):
                st.session_state[f'aft_cancelling_{cid}'] = True
                st.rerun()


def _render_case(row):
    cid = int(row['case_id'])
    status = row['status'] or 'scheduled'
    pt_type = row['patient_type'] or 'OPD'
    cat = row['case_category'] or ''
    is_ipd = pt_type == 'IPD'

    # Card class
    card_cls = {
        'scheduled': 'card-scheduled',
        'arrived': 'card-arrived',
        'in_or': 'card-in-or',
        'post_op': 'card-post-op',
        'discharged': 'card-discharged',
        'cancelled': 'card-cancelled',
    }.get(status, 'card-scheduled')

    # Status pill
    status_pills = {
        'scheduled': ('⏳ รอดำเนินการ', 'pill-sched'),
        'arrived': ('🧑 ผู้ป่วยมาแล้ว', 'pill-arrive'),
        'in_or': ('🔪 กำลังผ่าตัด', 'pill-inor'),
        'post_op': ('✅ ผ่าเสร็จ — รอ D/C', 'pill-postop'),
        'discharged': ('🏠 Discharge แล้ว', 'pill-dc'),
        'cancelled': ('❌ ยกเลิก', 'pill-cancel'),
    }
    sp_text, sp_cls = status_pills.get(status, ('⏳', 'pill-sched'))

    pills = [f'<span class="pill {sp_cls}">{sp_text}</span>']
    if cat == 'เคสนัดหมาย' or cat == 'SET':
        pills.append('<span class="pill pill-set">เคสนัดหมาย</span>')
    elif cat == 'Walk-in' or cat == 'WALK-IN':
        pills.append('<span class="pill pill-walkin">Walk-in</span>')
    if pt_type == 'OPD':
        pills.append('<span class="pill pill-opd">OPD</span>')
    elif pt_type == 'IPD':
        an_txt = f" {row['an']}" if row['an'] else ''
        pills.append(f'<span class="pill pill-ipd">IPD{an_txt}</span>')
    elif pt_type == 'นอกเวลา':
        pills.append('<span class="pill pill-after">นอกเวลา</span>')

    pill_html = ' '.join(pills)
    text_deco = 'line-through' if status == 'cancelled' else 'none'
    name_d = row['name'] or '-'
    hn_d = row['hn'] or '-'
    proc_d = row['procedure_name'] or '-'
    surg_d = row['surgeon_name'] or '-'
    div_d = div_name(row['division_code'])

    # Timeline info
    timeline = _build_timeline(row)

    diag_d = row.get('diagnosis') or ''
    diag_html = f'<div class="pt-diag" style="text-decoration:{text_deco};color:#555;font-size:12px;margin-top:2px;">Dx: {diag_d}</div>' if diag_d and diag_d.strip() not in ('', '-') else ''

    st.markdown(f"""<div class="case-card {card_cls}">
<div>{pill_html}</div>
<div style="margin-top:6px;text-decoration:{text_deco};">
<span class="pt-name">{name_d}</span>
<span class="pt-hn">HN: {hn_d}</span>
</div>{diag_html}
<div class="pt-proc" style="text-decoration:{text_deco};">{proc_d}</div>
<div class="pt-meta">แพทย์: {surg_d}  ·  สาขา: {div_d}</div>
{_ai_badge(row)}
</div>""", unsafe_allow_html=True)

    # Timeline as separate markdown (avoid Streamlit HTML sanitization)
    if timeline:
        st.markdown(timeline, unsafe_allow_html=True)

    # ---- Cancelled: show reason + restore ----
    if status == 'cancelled':
        if row['cancel_reason']:
            st.caption(f"    เหตุผล: {row['cancel_reason']}")
        if st.button("🔄 กู้คืนเคส", key=f"restore_{cid}",
                     use_container_width=True):
            update_case(cid, status='scheduled', cancel_reason=None)
            st.rerun()
        return

    # ---- Discharged: show restore button ----
    if status == 'discharged':
        if st.button("⬅️ ย้อนกลับ (ยกเลิก D/C)", key=f"back_{cid}",
                     use_container_width=True):
            update_case(cid, status='post_op', discharged_at=None)
            st.rerun()

    # ---- Timer for arrived status ----
    if status == 'arrived' and row['arrived_at']:
        _render_timer(cid, row['arrived_at'])

    # ---- Checkboxes: OSS + OR เยี่ยม ----
    if status in ('scheduled', 'arrived', 'in_or'):
        oss_by_or = row['oss_by_or'] == 1
        cols = st.columns([1, 1])
        with cols[0]:
            if oss_by_or:
                st.markdown("✅ OSS *(OR เยี่ยมเอง)*")
            else:
                v = st.toggle("OSS เยี่ยม", value=bool(row['oss_visited']),
                              key=f"oss_{cid}")
                if v != bool(row['oss_visited']):
                    update_checkbox(cid, 'oss_visited', int(v))
                    st.rerun()
        with cols[1]:
            if oss_by_or:
                st.markdown("✅ OR เยี่ยม")
            else:
                v = st.toggle("OR เยี่ยม", value=bool(row['or_pre_visit']),
                              key=f"orv_{cid}")
                if v != bool(row['or_pre_visit']):
                    update_checkbox(cid, 'or_pre_visit', int(v))
                    st.rerun()

    # ---- Action buttons per status ----
    _render_actions(cid, status, row)


def _ai_badge(row):
    """Return HTML badge showing AI predicted time."""
    ai = row.get('ai_predicted_min')
    if ai is None or (isinstance(ai, float) and (ai != ai)):  # NaN check
        return ''
    try:
        ai = int(float(ai))
    except (ValueError, TypeError):
        return ''
    if ai <= 0:
        return ''
    return f'<div class="ai-badge">🤖 AI ทำนาย: ~{ai} นาที</div>'


def _ts(val):
    """Safely extract HH:MM from a timestamp string, return None if not valid."""
    if not val or not isinstance(val, str) or len(val) < 8:
        return None
    try:
        hhmm = val[-8:-3]  # e.g. "2026-05-02 09:30:00" → "09:30"
        if len(hhmm) == 5 and hhmm[2] == ':':
            return hhmm
        return None
    except Exception:
        return None


def _build_timeline(row):
    """Build timeline HTML string from timestamps."""
    parts = []
    t = _ts(row.get('arrived_at'))
    if t:
        parts.append(f"🧑 มา <b>{t}</b>")
    try:
        w = int(float(row.get('wait_min', 0) or 0))
        if w > 0:
            color = '#d32f2f' if w >= 60 else '#f57f17'
            parts.append(f"⏱ รอ <b style='color:{color}'>{w} นาที</b>")
    except (ValueError, TypeError):
        pass
    t = _ts(row.get('in_or_at'))
    if t:
        parts.append(f"🔪 เข้าห้อง <b>{t}</b>")
    try:
        d = int(float(row.get('actual_duration_min', 0) or 0))
        if d > 0:
            parts.append(f"ผ่า <b>{d} นาที</b>")
    except (ValueError, TypeError):
        pass
    t = _ts(row.get('discharged_at'))
    if t:
        parts.append(f"🏠 D/C <b>{t}</b>")

    if not parts:
        return ''
    return '<div class="timeline">' + '  →  '.join(parts) + '</div>'


def _render_timer(cid, arrived_at_str):
    """JS-based live timer for waiting time."""
    components.html(f"""
    <div id="timer_{cid}" style="font-size:18px;font-weight:700;color:#f9a825;
         padding:4px 0;">
        ⏱ กำลังคำนวณ...
    </div>
    <script>
    (function() {{
        var start = new Date("{arrived_at_str}").getTime();
        function update() {{
            var now = Date.now();
            var diff = Math.floor((now - start) / 1000);
            var min = Math.floor(diff / 60);
            var sec = diff % 60;
            var el = document.getElementById("timer_{cid}");
            if (!el) return;
            var txt = "⏱ รอ " + min + " นาที " + (sec < 10 ? "0" : "") + sec + " วินาที";
            el.textContent = txt;
            if (min >= 60) {{
                el.style.color = "#d32f2f";
                el.style.fontSize = "22px";
            }} else if (min >= 30) {{
                el.style.color = "#e65100";
            }}
        }}
        update();
        setInterval(update, 1000);
    }})();
    </script>
    """, height=35)


def _render_actions(cid, status, row=None):
    """Show action buttons based on current status."""

    if status == 'scheduled':
        b1, b2 = st.columns(2)
        with b1:
            if st.button("🧑 รับผู้ป่วย", key=f"arr_{cid}",
                         type='primary', use_container_width=True):
                mark_arrived(cid)
                st.rerun()
        with b2:
            if st.button("❌ ยกเลิกเคส", key=f"canc_{cid}",
                         use_container_width=True):
                st.session_state[f'cancelling_{cid}'] = True

    elif status == 'arrived':
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("🔪 เข้าห้องผ่าตัด", key=f"ior_{cid}",
                         type='primary', use_container_width=True):
                # Auto-fill scrub/circ ทั้งหมดจาก Room Settings (atomic transaction)
                rm_no = int(row.get('room_no', 1) or 1)
                rm_settings = st.session_state.get('room_settings', {}).get(rm_no, {})
                scrub_raw = rm_settings.get('scrub', [])
                circ_raw = rm_settings.get('circ', [])
                if isinstance(scrub_raw, str):
                    scrub_raw = [scrub_raw]
                if isinstance(circ_raw, str):
                    circ_raw = [circ_raw]
                auto_scrub = ', '.join(n for n in scrub_raw if n and isinstance(n, str))
                auto_circ = ', '.join(n for n in circ_raw if n and isinstance(n, str))
                # Atomic: set nurses + mark in_or ในคำสั่งเดียว
                mark_in_or_with_nurses(cid, auto_scrub, auto_circ)
                # Feedback: ���จ้ง user ว่าเติมใครบ้าง
                parts = []
                if auto_scrub:
                    parts.append(f"🧤 Scrub: {auto_scrub}")
                if auto_circ:
                    parts.append(f"📋 Circ: {auto_circ}")
                if parts:
                    st.toast(f"Auto-fill: {' | '.join(parts)}")
                st.rerun()
        with b2:
            if st.button("⬅️ ย้อนกลับ", key=f"back_{cid}",
                         use_container_width=True):
                update_case(cid, status='scheduled', arrived_at=None)
                st.rerun()
        with b3:
            if st.button("❌ ยกเลิก", key=f"canc_{cid}",
                         use_container_width=True):
                st.session_state[f'cancelling_{cid}'] = True

    elif status == 'in_or':
        # ---- เลือก / แก้ไข Scrub (2) & Circulating (4) Nurse ----
        nurse_opts = _build_nurse_options(int(row.get('room_no', 1) or 1))

        # Parse comma-separated → list, pad to required length (normalize spaces)
        cur_s_str = row.get('scrub_nurse') or ''
        cur_c_str = row.get('circ_nurse') or ''
        cur_scrubs = [n.strip() for n in cur_s_str.split(',') if n.strip()] if cur_s_str else []
        cur_circs = [n.strip() for n in cur_c_str.split(',') if n.strip()] if cur_c_str else []
        while len(cur_scrubs) < 2:
            cur_scrubs.append('')
        while len(cur_circs) < 4:
            cur_circs.append('')

        st.markdown("🧤 **Scrub Nurse**")
        sc1, sc2 = st.columns(2)
        new_scrubs = ['', '']
        for si, col in enumerate([sc1, sc2]):
            with col:
                cur = cur_scrubs[si] if si < len(cur_scrubs) else ''
                idx2 = nurse_opts.index(cur) if cur in nurse_opts else 0
                new_scrubs[si] = st.selectbox(
                    f"Scrub #{si+1}", nurse_opts, index=idx2,
                    key=f"scrub_{cid}_{si}", label_visibility='collapsed')

        st.markdown("📋 **Circulating Nurse**")
        cc1, cc2, cc3, cc4 = st.columns(4)
        new_circs = ['', '', '', '']
        for ci, col in enumerate([cc1, cc2, cc3, cc4]):
            with col:
                cur = cur_circs[ci] if ci < len(cur_circs) else ''
                idx2 = nurse_opts.index(cur) if cur in nurse_opts else 0
                new_circs[ci] = st.selectbox(
                    f"Circ #{ci+1}", nurse_opts, index=idx2,
                    key=f"circ_{cid}_{ci}", label_visibility='collapsed')

        if st.button("💾 บันทึกพยาบาล", key=f"save_nurse_{cid}", use_container_width=True):
            # Filter: remove placeholder + deduplicate (fix #3)
            s_clean = list(dict.fromkeys(n for n in new_scrubs if n and n not in _SKIP_VALUES))
            c_clean = list(dict.fromkeys(n for n in new_circs if n and n not in _SKIP_VALUES))
            # Validate: ≥1 nurse (fix #6)
            if not s_clean and not c_clean:
                st.warning("⚠️ กรุณาเลือกพยาบาลอย่างน้อย 1 คน")
            else:
                # Normalize comma-separated (fix #7)
                sv = ', '.join(s_clean)
                cv = ', '.join(c_clean)
                # Normalize current for fair comparison
                cur_s_norm = ', '.join(n.strip() for n in cur_s_str.split(',') if n.strip())
                cur_c_norm = ', '.join(n.strip() for n in cur_c_str.split(',') if n.strip())
                updates = {}
                if sv != cur_s_norm:
                    updates['scrub_nurse'] = sv
                if cv != cur_c_norm:
                    updates['circ_nurse'] = cv
                if updates:
                    update_case(cid, **updates)
                    st.success("✅ บันทึกเรียบร้อย")
                    st.rerun()
                else:
                    st.info("ℹ️ ไม่มีการเปลี่ยนแปลง")

        dest = st.radio("หลังผ่าเสร็จ ส่งไป:",
                        ["🛗 รับส่ง", "🛏️ ห้องพักฟื้น"],
                        index=0, horizontal=True, key=f"dest_{cid}",
                        label_visibility='collapsed')
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("✅ ผ่าเสร็จแล้ว", key=f"opend_{cid}",
                         type='primary', use_container_width=True):
                d = 'transfer' if 'รับส่ง' in dest else 'recovery'
                mark_op_end(cid, d)
                st.rerun()
        with b2:
            if st.button("⬅️ ย้อนกลับ", key=f"back_{cid}",
                         use_container_width=True):
                update_case(cid, status='arrived', in_or_at=None)
                st.rerun()
        with b3:
            if st.button("❌ ยกเลิก", key=f"canc_{cid}",
                         use_container_width=True):
                st.session_state[f'cancelling_{cid}'] = True

    elif status == 'post_op':
        dest_val = row.get('post_op_dest', 'transfer') if row is not None else 'transfer'

        # แก้หัตถการ + ค่ารักษา (เฉพาะ transfer = ห้องรับส่ง)
        if dest_val == 'transfer':
            with st.expander("💰 แก้ไขหัตถการ / ค่ารักษา", expanded=False):
                cur_proc = (row['procedure_name'] or '').strip()
                cur_cost = int(row.get('treatment_cost') or 0)

                # --- Fuzzy price lookup จาก CSV ---
                matches = _fuzzy_price_lookup(cur_proc)

                new_proc = cur_proc  # default

                if len(matches) == 1:
                    # ราคาเดียว — auto-fill
                    m = matches[0]
                    st.markdown(f"**หัตถการ:** {cur_proc}")
                    st.markdown(
                        f'<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
                        f'border-radius:12px;font-size:12px;">match: {m["procedure_name"]}</span>',
                        unsafe_allow_html=True)
                    cost1 = int(m['new_price_thb'])

                elif len(matches) > 1:
                    # หลายราคา — dropdown ให้เลือก
                    st.markdown(f"**หัตถการ:** {cur_proc}")
                    st.markdown(
                        f'<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
                        f'border-radius:12px;font-size:12px;">พบ {len(matches)} รายการ</span>',
                        unsafe_allow_html=True)
                    options_display = [
                        f"{r['procedure_name_th']} — {int(r['new_price_thb']):,} ฿"
                        for r in matches
                    ]
                    # Find default index if current cost matches
                    default_sel = 0
                    for i, r in enumerate(matches):
                        if int(r['new_price_thb']) == cur_cost:
                            default_sel = i
                            break
                    sel = st.selectbox("เลือกรายการ", options_display,
                                       index=default_sel, key=f"pricepick_{cid}")
                    sel_idx = options_display.index(sel)
                    cost1 = int(matches[sel_idx]['new_price_thb'])
                    new_proc = matches[sel_idx]['procedure_name']

                else:
                    # ไม่เจอ
                    st.markdown(f"**หัตถการ:** {cur_proc}")
                    st.markdown(
                        '<span style="background:#fff3e0;color:#e65100;padding:2px 8px;'
                        'border-radius:12px;font-size:12px;">ไม่พบราคาอัตโนมัติ</span>',
                        unsafe_allow_html=True)
                    cost1 = cur_cost
                # ช่องแก้ไขค่าหัตถการ (แก้ได้เสมอ เผื่อ autofill พลาด)
                cost1 = st.number_input("ค่าหัตถการ (บาท)", min_value=0,
                                        value=cost1, step=100, key=f"cost_{cid}")

                st.markdown("---")

                # --- ค่าชิ้นเนื้อ Patho ---
                cur_patho = int(row.get('patho_cost') or 0)
                patho_options = [0] + PATHO_COSTS
                patho_default = 0
                if cur_patho in patho_options:
                    patho_default = patho_options.index(cur_patho)

                st.markdown("**ค่าชิ้นเนื้อ (Pathology)**")
                patho_val = st.radio(
                    "ส่งชิ้นเนื้อ", patho_options,
                    format_func=lambda x: "ไม่ส่ง" if x == 0 else f"{x:,} บาท",
                    index=patho_default, horizontal=True,
                    key=f"patho_{cid}", label_visibility="collapsed",
                )

                st.markdown("---")
                total_cost = cost1 + patho_val
                st.markdown(f"### รวมค่ารักษา: {total_cost:,} บาท")

                if st.button("💾 บันทึก", key=f"savecost_{cid}"):
                    updates = {
                        'treatment_cost': cost1,
                        'patho_cost': patho_val,
                    }
                    if new_proc.strip().upper() != cur_proc.strip().upper():
                        updates['procedure_name'] = new_proc.strip()
                    update_case(cid, **updates)
                    st.success("✅ บันทึกเรียบร้อย")

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button(f"🏠 Discharge ({_now_bkk().strftime('%H:%M')} น.)",
                         key=f"dc_{cid}", type='primary', use_container_width=True):
                mark_discharged(cid)
                st.rerun()
        with b2:
            if st.button("⬅️ ย้อนกลับ", key=f"back_{cid}",
                         use_container_width=True):
                update_case(cid, status='in_or', op_end_at=None,
                            actual_duration_min=None, post_op_dest=None)
                st.rerun()
        with b3:
            if st.button("❌ ยกเลิก", key=f"canc_{cid}",
                         use_container_width=True):
                st.session_state[f'cancelling_{cid}'] = True

    # Cancel confirmation dialog
    if st.session_state.get(f'cancelling_{cid}'):
        st.warning(f"⚠️ ยืนยันยกเลิกเคส **{row.get('patient_name','')}** — {row.get('procedure_name','')} ?")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("✅ ยืนยันยกเลิก", key=f"cc_{cid}",
                         type='primary', use_container_width=True):
                cancel_case(cid)
                del st.session_state[f'cancelling_{cid}']
                st.rerun()
        with cc2:
            if st.button("↩️ ไม่ใช่", key=f"cx_{cid}",
                         use_container_width=True):
                del st.session_state[f'cancelling_{cid}']
                st.rerun()


# ============================================================================
# Walk-in
# ============================================================================

def _render_walkin(view_date_str):
    with st.expander("➕ เพิ่มเคส Walk-in"):
        w1, w2 = st.columns(2)
        with w1:
            wi_name = st.text_input("ชื่อผู้ป่วย", key="wi_name",
                                    placeholder="นายสมชาย ใจดี")
            wi_hn = st.text_input("HN", key="wi_hn")
            wi_proc = st.text_input("หัตถการ", key="wi_proc",
                                    placeholder="Excision mass")
        with w2:
            wi_surg = st.text_input("แพทย์ผ่าตัด", key="wi_surg")
            wi_div = st.selectbox("สาขา", DIVISIONS, index=0, key="wi_div")
            wi_pt = st.selectbox("ประเภทผู้ป่วย",
                                 ['OPD', 'IPD'], key="wi_pt")
            wi_an = None
            if wi_pt == 'IPD':
                wi_an = st.text_input("AN", key="wi_an")

        if st.button("💾 บันทึก Walk-in", type='primary',
                     use_container_width=True):
            if not wi_proc:
                st.warning("กรุณากรอกชื่อหัตถการ")
            else:
                add_walkin_case(view_date_str,
                                wi_name or '-', wi_hn or '-',
                                wi_proc, wi_surg or '-',
                                wi_div, wi_pt, wi_an)
                st.toast("เพิ่ม Walk-in สำเร็จ", icon="✅")
                st.rerun()


# ============================================================================
# TAB 2: รอโทรเยี่ยม
# ============================================================================

def _tab_pending_calls():
    days = st.selectbox("ย้อนหลัง", [3, 7, 14], index=1,
                        format_func=lambda x: f"{x} วัน", key='call_days')
    df = get_pending_calls(days_back=days)

    if df.empty:
        st.markdown(
            '<div style="text-align:center;padding:40px 0;">'
            '<p style="font-size:48px;">🎉</p>'
            '<p style="color:#388e3c;font-size:16px;font-weight:600;">'
            'โทรเยี่ยมครบแล้ว!</p></div>',
            unsafe_allow_html=True,
        )
        return

    st.warning(f"ค้างโทรเยี่ยม {len(df)} เคส")

    for op_date in df['op_date'].unique():
        day_df = df[df['op_date'] == op_date]
        days_ago = (_now_bkk().date() - pd.to_datetime(op_date).date()).days
        icon = "🔴" if days_ago >= 3 else "🟡" if days_ago >= 1 else "🟢"
        st.markdown(f"**{icon} วันที่ {op_date}** ({days_ago} วันที่แล้ว)")

        for _, r in day_df.iterrows():
            cid = int(r['case_id'])
            st.markdown(f"""
            <div class="call-card">
                <span class="pt-name">{r["name"] or "-"}</span>
                <span class="pt-hn">HN: {r["hn"] or "-"}</span>
                <div class="pt-proc">{r["procedure_name"]}</div>
                <div class="pt-meta">แพทย์: {r["surgeon_name"] or "-"}</div>
            </div>""", unsafe_allow_html=True)

            b1, b2 = st.columns(2)
            with b1:
                if st.button("✅ เยี่ยมได้", key=f"pok_{cid}",
                             use_container_width=True, type='primary'):
                    update_postcall(cid, 'เยี่ยมได้')
                    st.rerun()
            with b2:
                if st.button("📵 ไม่รับสาย", key=f"pmiss_{cid}",
                             use_container_width=True):
                    update_postcall(cid, 'ไม่รับสาย')
                    st.rerun()


# ============================================================================
# TAB 3: สรุปยอด
# ============================================================================

def _render_summary_section(s, label, key_prefix):
    """Render a summary section (reusable for today / cumulative)."""
    # Overview metrics
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("เคสทั้งหมด", s['total'])
    r2.metric("ผ่าเสร็จ", s['completed'])
    r3.metric("ยกเลิก", s['cancelled'])
    cancel_rate = s['cancelled'] / s['total'] * 100 if s['total'] > 0 else 0
    r4.metric("อัตรายกเลิก", "%.0f%%" % cancel_rate)

    r5, r6, r7, r8 = st.columns(4)
    r5.metric("OPD", s['n_opd'])
    r6.metric("IPD", s['n_ipd'])
    r7.metric("เคสนัดหมาย", s['n_set'])
    r8.metric("Walk-in", s['n_walkin'])

    # Revenue + patho row
    rv1, rv2, rv3, rv4 = st.columns(4)
    rv1.metric("💰 ค่าหัตถการ", f"{s['total_treatment']:,} ฿")
    rv2.metric("💵 รายได้รวม", f"{s['total_revenue']:,} ฿")
    rv3.metric("🧬 ส่งชิ้นเนื้อ", f"{s['n_patho_sent']} ราย")
    rv4.metric("🔬 ค่าชิ้นเนื้อ", f"{s['total_patho']:,} ฿")

    st.markdown("---")

    # Top 5 หัตถการ
    st.markdown("#### Top 5 หัตถการที่ทำบ่อย")
    if not s['top_procs'].empty:
        max_n = s['top_procs']['n'].max()
        for i, (_, p) in enumerate(s['top_procs'].iterrows()):
            medal = ["🥇", "🥈", "🥉", "4.", "5."][i] if i < 5 else f"{i+1}."
            proc_name = p['procedure_name'] or '-'
            proc_n = int(p['n'])
            pct = proc_n / max_n if max_n > 0 else 0
            st.markdown(f"**{medal} {proc_name}** — {proc_n} ครั้ง")
            st.progress(min(pct, 1.0))
    else:
        st.info("ยังไม่มีข้อมูล")

    st.markdown("---")

    # Top 5 สาขา
    st.markdown("#### Top 5 สาขาที่ทำบ่อย")
    if not s['div_stats'].empty:
        top_div = s['div_stats'].head(5)
        max_n = top_div['n'].max()
        for i, (_, d) in enumerate(top_div.iterrows()):
            medal = ["🥇", "🥈", "🥉", "4.", "5."][i] if i < 5 else f"{i+1}."
            dname = div_name(d['division_code'])
            dn = int(d['n'])
            pct = dn / max_n if max_n > 0 else 0
            st.markdown(f"**{medal} {dname}** — {dn} ครั้ง")
            st.progress(min(pct, 1.0))
    else:
        st.info("ยังไม่มีข้อมูล")

    st.markdown("---")

    # AI ทำนาย vs เวลาจริง
    st.markdown("#### AI ทำนาย vs เวลาจริง")
    ai_df = s.get('ai_df')
    if ai_df is not None and not ai_df.empty:
        ai_df = ai_df.copy()
        ai_df['error'] = ai_df['ai_predicted_min'] - ai_df['actual_duration_min']
        ai_df['abs_error'] = ai_df['error'].abs()
        n_cases = len(ai_df)
        mae = ai_df['abs_error'].mean()
        within_10 = (ai_df['abs_error'] <= 10).sum() / n_cases * 100
        within_15 = (ai_df['abs_error'] <= 15).sum() / n_cases * 100
        avg_pred = ai_df['ai_predicted_min'].mean()
        avg_actual = ai_df['actual_duration_min'].mean()

        m1, m2, m3 = st.columns(3)
        m1.metric("เคสที่มีข้อมูล", n_cases)
        m2.metric("เวลาจริงเฉลี่ย", "%.0f นาที" % avg_actual)
        m3.metric("AI ทำนายเฉลี่ย", "%.0f นาที" % avg_pred)

        m4, m5, m6 = st.columns(3)
        m4.metric("ค่าผิดพลาดเฉลี่ย (MAE)", "%.1f นาที" % mae)
        m5.metric("ถูกภายใน ±10 นาที", "%.0f%%" % within_10)
        m6.metric("ถูกภายใน ±15 นาที", "%.0f%%" % within_15)

        st.markdown("**เคสที่ AI ทำนายคลาดเคลื่อนมากสุด**")
        worst = ai_df.nlargest(5, 'abs_error')
        for _, row in worst.iterrows():
            pred = int(row['ai_predicted_min'])
            actual = int(row['actual_duration_min'])
            err = int(row['error'])
            sign = "+" if err > 0 else ""
            proc = (row.get('procedure_name') or '-')[:40]
            color = "#d32f2f" if abs(err) > 15 else "#f57f17"
            st.markdown(
                f"- **{proc}** — AI: {pred} นาที, จริง: {actual} นาที "
                f"(<span style='color:{color};font-weight:700'>{sign}{err} นาที</span>)",
                unsafe_allow_html=True
            )
    else:
        st.info("ยังไม่มีเคสที่ผ่าเสร็จ + มีค่า AI ทำนาย — กด 'ผ่าเสร็จ' เพื่อเริ่มเก็บสถิติ")


def _render_after_hours_summary(df_cases, prefix=""):
    """แสดง section สรุปนอกเวลา (จำนวน + รายได้ + แพทย์)."""
    if df_cases is None or df_cases.empty:
        st.info("ไม่มีเคสนอกเวลา")
        return

    aft = df_cases[df_cases['patient_type'] == 'นอกเวลา'].copy()
    if aft.empty:
        st.info("ไม่มีเคสนอกเวลา")
        return

    n_total = len(aft)
    n_done = len(aft[aft['status'] == 'discharged'])
    n_cancel = len(aft[aft['status'] == 'cancelled'])
    revenue = int(aft['treatment_cost'].fillna(0).sum())

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("เคสนอกเวลา", n_total)
    a2.metric("ยืนยันแล้ว", n_done)
    a3.metric("ยกเลิก", n_cancel)
    a4.metric("💰 รายได้", f"{revenue:,} ฿")

    # Top procedures
    done_aft = aft[aft['status'] == 'discharged']
    if not done_aft.empty:
        st.markdown("**หัตถการนอกเวลาที่ทำ**")
        proc_counts = done_aft['procedure_name'].str.upper().value_counts().head(5)
        for proc_name, n in proc_counts.items():
            st.markdown(f"- {proc_name} — {n} ราย")

        # Top doctors
        surg_counts = done_aft['surgeon_name'].value_counts().head(5)
        if not surg_counts.empty:
            st.markdown("**แพทย์นอกเวลา**")
            for surg, n in surg_counts.items():
                st.markdown(f"- {surg} — {n} ราย")


def _tab_summary():
    today = _now_bkk().strftime('%Y-%m-%d')

    # === 2 Sub-tabs ===
    sub_today, sub_stats = st.tabs(["📋 สรุปยอดวันนี้", "📈 สรุปยอดสถิติ"])

    # =============================================
    # SUB-TAB 1: สรุปยอดวันนี้
    # =============================================
    with sub_today:
        s_today = get_summary(date_from=today, date_to=today)
        df_today = get_cases(op_date=today)

        # --- Section: ในเวลา ---
        st.markdown(
            '<div style="background:linear-gradient(135deg,#e8f5e9,#c8e6c9);'
            'border-radius:10px;padding:10px 16px;margin:8px 0;">'
            '<span style="font-size:16px;font-weight:700;color:#2e7d32;">'
            '🏥 เคสในเวลา</span></div>',
            unsafe_allow_html=True,
        )

        # Metrics
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("เคสทั้งหมด", s_today['total'])
        r2.metric("ผ่าเสร็จ", s_today['completed'])
        r3.metric("ยกเลิก", s_today['cancelled'])
        cancel_rate = s_today['cancelled'] / s_today['total'] * 100 if s_today['total'] > 0 else 0
        r4.metric("อัตรายกเลิก", "%.0f%%" % cancel_rate)

        r5, r6, r7, r8 = st.columns(4)
        r5.metric("OPD", s_today['n_opd'])
        r6.metric("IPD", s_today['n_ipd'])
        r7.metric("เคสนัดหมาย", s_today['n_set'])
        r8.metric("Walk-in", s_today['n_walkin'])

        # Revenue
        rv1, rv2, rv3, rv4 = st.columns(4)
        rv1.metric("💰 ค่าหัตถการ", f"{s_today['total_treatment']:,} ฿")
        rv2.metric("💵 รายได้รวม", f"{s_today['total_revenue']:,} ฿")
        rv3.metric("🧬 ส่งชิ้นเนื้อ", f"{s_today['n_patho_sent']} ราย")
        rv4.metric("🔬 ค่าชิ้นเนื้อ", f"{s_today['total_patho']:,} ฿")

        # Charts (in-hours only)
        if not df_today.empty:
            df_in = df_today[(df_today['status'] != 'cancelled') &
                             (df_today['patient_type'] != 'นอกเวลา')].copy()

            if not df_in.empty:
                st.markdown("---")
                pc1, pc2 = st.columns(2)
                with pc1:
                    st.markdown("#### หัตถการวันนี้")
                    proc_counts = df_in['procedure_name'].str.upper().value_counts()
                    for proc_name, n in proc_counts.items():
                        st.markdown(f"**{proc_name}** — {n} ราย")
                with pc2:
                    st.markdown("#### สาขาที่ set วันนี้")
                    div_counts = df_in['division_code'].apply(div_name).value_counts().reset_index()
                    div_counts.columns = ['สาขา', 'จำนวน']
                    fig_div = px.pie(div_counts, names='สาขา', values='จำนวน', hole=0.35)
                    fig_div.update_layout(margin=dict(t=10, b=10, l=10, r=10),
                                          height=300, showlegend=True,
                                          legend=dict(font=dict(size=11)))
                    fig_div.update_traces(textposition='inside', textinfo='value+label')
                    st.plotly_chart(fig_div, use_container_width=True)

                st.markdown("---")

                # Scatter: ช่วงเวลาผ่าตัด
                st.markdown("#### ช่วงเวลาผ่าตัด (เช้า / บ่าย)")
                df_time = df_in.copy()
                if 'in_or_at' in df_time.columns:
                    df_time['_time'] = df_time['in_or_at'].fillna(df_time.get('arrived_at'))
                elif 'arrived_at' in df_time.columns:
                    df_time['_time'] = df_time['arrived_at']
                else:
                    df_time['_time'] = None
                df_time = df_time[df_time['_time'].notna()].copy()
                if not df_time.empty:
                    df_time['เวลาเข้าห้อง'] = pd.to_datetime(df_time['_time'])
                    df_time['ชั่วโมง'] = df_time['เวลาเข้าห้อง'].dt.hour + df_time['เวลาเข้าห้อง'].dt.minute / 60
                    df_time['ช่วงเวลา'] = df_time['ชั่วโมง'].apply(
                        lambda h: 'เช้า (08-12)' if h < 12 else 'บ่าย (12-16)')
                    df_time['proc_upper'] = df_time['procedure_name'].str.upper()

                    fig_sc = px.scatter(df_time, x='ชั่วโมง', y='proc_upper',
                                        color='ช่วงเวลา',
                                        color_discrete_map={
                                            'เช้า (08-12)': '#1976d2',
                                            'บ่าย (12-16)': '#e65100'},
                                        labels={'proc_upper': 'หัตถการ'},
                                        hover_data=['surgeon_name'])
                    h_min = max(7, int(df_time['ชั่วโมง'].min()) - 1)
                    h_max = min(24, int(df_time['ชั่วโมง'].max()) + 1)
                    fig_sc.update_layout(
                        height=max(280, len(df_time['proc_upper'].unique()) * 50),
                        margin=dict(t=10, b=30, l=10, r=10),
                        xaxis=dict(range=[h_min, h_max], dtick=1, title='เวลา (ชั่วโมง)'),
                        yaxis=dict(title=''),
                        showlegend=True)
                    fig_sc.update_traces(marker=dict(size=18, line=dict(width=1, color='white')))
                    st.plotly_chart(fig_sc, use_container_width=True)
                    mc = (df_time['ชั่วโมง'] < 12).sum()
                    ac = (df_time['ชั่วโมง'] >= 12).sum()
                    st.caption(f"เช้า {mc} ราย | บ่าย {ac} ราย")

                st.markdown("---")
                # Top 5 แพทย์
                st.markdown("#### Top 5 แพทย์ที่ set มากสุด")
                surg_counts = df_in['surgeon_name'].value_counts().head(5)
                for i, (surg, n) in enumerate(surg_counts.items()):
                    medal = ["🥇", "🥈", "🥉", "4.", "5."][i]
                    st.markdown(f"**{medal} {surg}** — {n} ราย")

        st.markdown("")
        st.markdown("")

        # --- Section: นอกเวลา ---
        st.markdown(
            '<div style="background:linear-gradient(135deg,#fce4ec,#f8bbd0);'
            'border-radius:10px;padding:10px 16px;margin:8px 0;">'
            '<span style="font-size:16px;font-weight:700;color:#c62828;">'
            '🌙 เคสนอกเวลา</span></div>',
            unsafe_allow_html=True,
        )
        _render_after_hours_summary(df_today, prefix="today")

    # =============================================
    # SUB-TAB 2: สรุปยอดสถิติ (สะสม)
    # =============================================
    with sub_stats:
        today_dt = _now_bkk().date()

        # --- Quick preset buttons ---
        preset = st.radio(
            "ช่วงเวลา", ["7 วัน", "30 วัน", "90 วัน", "กำหนดเอง"],
            horizontal=True, key='sum_period', label_visibility='collapsed',
        )

        if preset == "7 วัน":
            default_from = today_dt - timedelta(days=6)
            default_to = today_dt
        elif preset == "30 วัน":
            default_from = today_dt - timedelta(days=29)
            default_to = today_dt
        elif preset == "90 วัน":
            default_from = today_dt - timedelta(days=89)
            default_to = today_dt
        else:
            default_from = today_dt - timedelta(days=29)
            default_to = today_dt

        # --- Date range picker ---
        col_from, col_to = st.columns(2)
        with col_from:
            sel_from = st.date_input("📅 วันที่เริ่มต้น", value=default_from,
                                     max_value=today_dt, key="stats_from")
        with col_to:
            sel_to = st.date_input("📅 วันที่สิ้นสุด", value=default_to,
                                   max_value=today_dt, key="stats_to")

        if sel_from > sel_to:
            st.warning("⚠️ วันที่เริ่มต้นต้องไม่เกินวันที่สิ้นสุด")
            return

        d_from = sel_from.strftime('%Y-%m-%d')
        d_to = sel_to.strftime('%Y-%m-%d')

        s_all = get_summary(date_from=d_from, date_to=d_to)

        # --- Section: ในเวลา ---
        st.markdown(
            '<div style="background:linear-gradient(135deg,#e8f5e9,#c8e6c9);'
            'border-radius:10px;padding:10px 16px;margin:8px 0;">'
            '<span style="font-size:16px;font-weight:700;color:#2e7d32;">'
            '🏥 เคสในเวลา (สะสม)</span></div>',
            unsafe_allow_html=True,
        )
        _render_summary_section(s_all, "สะสม", "all")

        st.markdown("")
        st.markdown("")

        # --- Section: นอกเวลา ---
        st.markdown(
            '<div style="background:linear-gradient(135deg,#fce4ec,#f8bbd0);'
            'border-radius:10px;padding:10px 16px;margin:8px 0;">'
            '<span style="font-size:16px;font-weight:700;color:#c62828;">'
            '🌙 เคสนอกเวลา (สะสม)</span></div>',
            unsafe_allow_html=True,
        )
        df_all_cases = get_cases()
        df_all_cases = df_all_cases[
            (df_all_cases['op_date'] >= d_from) &
            (df_all_cases['op_date'] <= d_to)
        ]
        _render_after_hours_summary(df_all_cases, prefix="stats")

        st.markdown("---")

        # ---- Download ----
        if s_all['total'] > 0:
            dl1, dl2 = st.columns(2)
            with dl1:

                xlsx_data = export_summary_excel(d_from, d_to)
                st.download_button(
                    '📊 ดาวน์โหลดสรุปสถิติ (Excel+กราฟ)',
                    xlsx_data,
                    'minor_or_summary.xlsx',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True,
                )
            with dl2:
                df_export = get_cases()
                csv_data = df_export.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    '📥 ดาวน์โหลดข้อมูลดิบ (CSV)',
                    csv_data,
                    'minor_or_cases.csv',
                    'text/csv',
                    use_container_width=True,
                )
