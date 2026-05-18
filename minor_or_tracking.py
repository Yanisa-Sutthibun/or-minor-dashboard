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
    'เพชรมงกุฎ แขมคำ',  # แก้ tipo: เพชรมงกุฏ แขมดำ → เพชรมงกุฎ แขมคำ
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

    # ---- 🎬 Executive Demo toggle (สำหรับ demo ผู้บริหาร — ไม่แตะ DB) ----
    demo_l, demo_r = st.columns([4, 2])
    with demo_l:
        st.markdown(
            '<div style="font-size:13px;color:#777;padding-top:6px;">'
            '💡 <b>Executive Demo</b> — เปิดเพื่อโชว์ Live Queue + ETA '
            'พร้อมข้อมูลตัวอย่าง (ไม่บันทึก DB)</div>',
            unsafe_allow_html=True)
    with demo_r:
        exec_demo = st.toggle('🎬 Demo สำหรับผู้บริหาร',
                              key='exec_demo_mode',
                              value=st.session_state.get('exec_demo_mode', False))

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


# ════════════════════════════════════════════════════════════════════
# 🎬 EXECUTIVE DEMO — Full Flow (มีผู้ป่วย → discharge)
# ════════════════════════════════════════════════════════════════════
_DEMO_PATIENTS_FULL = [
    {'cid': 9001, 'status': 'scheduled',
     'name': 'น.ส.ปาริชาติ ม.', 'hn': '660044556', 'age': 42,
     'proc': 'Excision skin lesion at scalp', 'dx': 'Skin Lesion',
     'surgeon': 'แพทย์หญิงดารัตน์', 'estimated_time': '14:30', 'ai_pred': 25,
     'note': 'เคส elective นัดล่วงหน้า · AI ทำนาย 25 น.'},
    {'cid': 9004, 'status': 'in_or', 'room': 1,
     'name': 'นายภัทรเดช ว.', 'hn': '690009822', 'age': 36,
     'proc': 'Morpheus (Aging Face)', 'dx': 'Aging Face',
     'surgeon': 'พ.ต.อ.เฉลิมเกียรติ', 'in_or_min': 18, 'ai_pred': 30,
     'note': '🤖 AI: ใช้ห้อง 30 น. · ผ่าไป 18 น. · เหลือ ~12 น.'},
    {'cid': 9005, 'status': 'post_op__recovery',
     'name': 'นางสุปรานี เ.', 'hn': '590018522', 'age': 59,
     'proc': 'I and D abscess at back', 'dx': 'Abscess',
     'surgeon': 'แพทย์หญิงวริศฐา', 'duration_min': 16, 'recovery_min': 10,
     'note': 'ผ่าจริง 16 น. · พักฟื้น 10 น. · เตรียม discharge'},
    {'cid': 9006, 'status': 'discharged',
     'name': 'นายพลเทพ เ.', 'hn': '531479595', 'age': 82,
     'proc': 'Correction upper eyelid', 'dx': 'Ptosis upper eyelid',
     'surgeon': 'พ.ต.อ.เฉลิมเกียรติ', 'duration_min': 50, 'discharged_at': '13:25',
     'note': 'ใช้เวลาผ่า 50 น. · กลับบ้าน 13:25'},
]


def _render_demo_banner():
    st.markdown(
        '<div style="background:linear-gradient(135deg,#fff8e1,#ffe082);'
        'border-radius:8px;padding:10px 16px;margin-bottom:8px;'
        'border-left:5px solid #f57c00;">'
        '<span style="font-size:14px;font-weight:700;color:#f57c00;">'
        '🎬 EXECUTIVE DEMO</span> '
        '<span style="font-size:13px;color:#bf360c;">ข้อมูลตัวอย่าง — ไม่บันทึก DB</span></div>',
        unsafe_allow_html=True)


def _render_demo_stats_bar():
    """แถบ KPI demo: 1 sched · 3 arrived · 1 in_or · 1 recovery · 1 dc"""
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown('<div class="metric-box"><div class="metric-num">7</div>'
                '<div class="metric-lbl">ทั้งหมด</div></div>', unsafe_allow_html=True)
    c2.markdown('<div class="metric-box"><div class="metric-num" style="color:#f9a825">3</div>'
                '<div class="metric-lbl">รอผ่า</div></div>', unsafe_allow_html=True)
    c3.markdown('<div class="metric-box"><div class="metric-num" style="color:#1976d2">1</div>'
                '<div class="metric-lbl">กำลังผ่า</div></div>', unsafe_allow_html=True)
    c4.markdown('<div class="metric-box"><div class="metric-num" style="color:#388e3c">2</div>'
                '<div class="metric-lbl">เสร็จ</div></div>', unsafe_allow_html=True)
    c5.markdown('<div class="metric-box"><div class="metric-num" style="color:#e53935">0</div>'
                '<div class="metric-lbl">ยกเลิก</div></div>', unsafe_allow_html=True)
    st.markdown("")


def _render_executive_demo_receive():
    """🎬 Demo: รับผู้ป่วย — แสดง 1 เคส scheduled"""
    _render_demo_banner()
    _render_demo_stats_bar()
    with st.expander("💡 หน้านี้คืออะไร?", expanded=False):
        st.markdown("""
**🧑 รับผู้ป่วย** = จุดเริ่มต้น flow ในห้องผ่าตัดเล็ก

- พยาบาลเช็คอินผู้ป่วยที่มาตามนัด หรือ Walk-in
- ระบบเก็บข้อมูล: HN, ชื่อ, อายุ, หัตถการ, แพทย์, เวลานัด
- **🤖 AI ทำนายเวลาผ่าตัด** จะแสดงเลยตั้งแต่ขั้นนี้
- พอกดปุ่ม "รับผู้ป่วย" → เคสเลื่อนไปแท็บ "⏳ รอผ่าตัด"
""")
    p = next(x for x in _DEMO_PATIENTS_FULL if x['status'] == 'scheduled')
    st.markdown(f"""
    <div class="case-card" style="background:#f3e5f5;border-left:5px solid #9c27b0;">
        <div><span class="pill" style="background:#9c27b0;color:white;">🆕 รอตรวจรับ</span>
        <span class="pill" style="background:#e1bee7;color:#6a1b9a;">นัดล่วงหน้า</span></div>
        <div style="margin-top:6px;">
            <span class="pt-name">{p['name']}</span>
            <span class="pt-hn">HN: {p['hn']} · อายุ {p['age']}</span>
        </div>
        <div style="color:#555;font-size:12px;margin-top:2px;">Dx: {p['dx']}</div>
        <div class="pt-proc">{p['proc']}</div>
        <div class="pt-meta">แพทย์: {p['surgeon']} · นัด {p['estimated_time']} น. · 🤖 AI {p['ai_pred']} น.</div>
        <div style="margin-top:8px;padding:6px 10px;background:#fce4ec;border-radius:4px;
                    font-size:11px;color:#6a1b9a;font-style:italic;">💡 {p['note']}</div>
    </div>""", unsafe_allow_html=True)
    st.button('🧑 รับผู้ป่วย (Demo)', key='demo_recv_btn',
              use_container_width=True, disabled=True,
              help='ปุ่มถูก disable ใน demo mode')


def _render_executive_demo_or():
    """🎬 Demo: ห้องผ่าตัด — แสดง 1 เคสกำลังผ่า + AI progress bar"""
    _render_demo_banner()
    _render_demo_stats_bar()
    with st.expander("💡 หน้านี้คืออะไร?", expanded=False):
        st.markdown("""
**🔪 ห้องผ่าตัด** = เคสที่กำลังอยู่ในห้องผ่าตัด real-time

- เห็นชื่อ + หัตถการ + แพทย์ที่กำลังผ่า
- **🤖 AI progress bar** = บอกความคืบหน้า เทียบกับเวลาที่ AI ทำนาย
  - 🔵 น้อยกว่า 90% → ปกติ ตรงเวลา
  - 🟠 90-110% → ใกล้เสร็จแล้ว
  - 🔴 เกิน 110% → นานกว่าที่ทำนาย ควรตรวจสอบ
- พอผ่าเสร็จ → กดปุ่ม "ผ่าเสร็จ" → เคสเลื่อนไป "🛏️ ห้องพักฟื้น"
""")
    p = next(x for x in _DEMO_PATIENTS_FULL if x['status'] == 'in_or')
    pct = round(p['in_or_min'] / p['ai_pred'] * 100)
    bar_w = min(pct, 100)
    bar_color = '#1976d2' if pct < 90 else '#e65100' if pct < 110 else '#c62828'
    st.markdown(f"""
    <div class="case-card" style="background:#e3f2fd;border-left:5px solid #1976d2;">
        <div><span class="pill" style="background:#1976d2;color:white;">🔪 กำลังผ่าตัด · ห้อง {p['room']}</span></div>
        <div style="margin-top:6px;">
            <span class="pt-name">{p['name']}</span>
            <span class="pt-hn">HN: {p['hn']} · อายุ {p['age']}</span>
        </div>
        <div style="color:#555;font-size:12px;margin-top:2px;">Dx: {p['dx']}</div>
        <div class="pt-proc">{p['proc']}</div>
        <div class="pt-meta">แพทย์: {p['surgeon']}</div>
        <div style="margin-top:8px;padding:8px 12px;background:#e3f2fd;border-radius:6px;">
            <div style="font-size:12px;color:#1565c0;margin-bottom:4px;">
                🤖 AI ทำนายเวลาใช้ห้อง: ~{p['ai_pred']} นาที · ใช้ไป <b>{p['in_or_min']}</b> น.
            </div>
            <div style="background:#bbdefb;height:6px;border-radius:3px;overflow:hidden;">
                <div style="background:{bar_color};height:100%;width:{bar_w}%;"></div>
            </div>
            <div style="font-size:11px;color:#1565c0;margin-top:4px;text-align:right;">{pct}% · เหลือ ~{p['ai_pred']-p['in_or_min']} น.</div>
        </div>
        <div style="margin-top:6px;padding:6px 10px;background:#e3f2fd;border-radius:4px;
                    font-size:11px;color:#1565c0;font-style:italic;">💡 {p['note']}</div>
    </div>""", unsafe_allow_html=True)


def _render_executive_demo_recovery():
    """🎬 Demo: ห้องพักฟื้น"""
    _render_demo_banner()
    _render_demo_stats_bar()
    with st.expander("💡 หน้านี้คืออะไร?", expanded=False):
        st.markdown("""
**🛏️ ห้องพักฟื้น** = เคสที่ผ่าตัดเสร็จแล้ว กำลังพักฟื้นก่อนกลับบ้าน

- ระยะเวลาพักฟื้นทั่วไป **10-30 นาที**
- พยาบาลสังเกตอาการ — สัญญาณชีพ, อาการแพ้ยา, แผล
- เมื่อพร้อม → กดปุ่ม **"ส่งห้องรับส่ง"** หรือ **"Discharge"** ทันที
- ข้อมูลที่บันทึก: เวลาผ่าตัดจริง, เวลาพักฟื้น
""")
    p = next(x for x in _DEMO_PATIENTS_FULL if x['status'] == 'post_op__recovery')
    st.markdown(f"""
    <div class="case-card" style="background:#fff8e1;border-left:5px solid #f57c00;">
        <div><span class="pill" style="background:#f57c00;color:white;">🛏️ พักฟื้น</span>
        <span class="pill" style="background:#ffe082;color:#bf360c;">{p['recovery_min']} นาที</span></div>
        <div style="margin-top:6px;">
            <span class="pt-name">{p['name']}</span>
            <span class="pt-hn">HN: {p['hn']} · อายุ {p['age']}</span>
        </div>
        <div style="color:#555;font-size:12px;margin-top:2px;">Dx: {p['dx']}</div>
        <div class="pt-proc">{p['proc']}</div>
        <div class="pt-meta">แพทย์: {p['surgeon']} · ผ่าจริง {p['duration_min']} นาที</div>
        <div style="margin-top:8px;padding:6px 10px;background:#ffecb3;border-radius:4px;
                    font-size:11px;color:#bf360c;font-style:italic;">💡 {p['note']}</div>
    </div>""", unsafe_allow_html=True)
    cdc1, cdc2 = st.columns(2)
    cdc1.button('🛗 ส่งห้องรับส่ง (Demo)', key='demo_to_dc', disabled=True,
                use_container_width=True)
    cdc2.button('🏠 Discharge (Demo)', key='demo_dc_direct', disabled=True,
                use_container_width=True)


def _render_executive_demo_discharge():
    """🎬 Demo: ห้องรับส่ง / Discharged"""
    _render_demo_banner()
    _render_demo_stats_bar()
    with st.expander("💡 หน้านี้คืออะไร?", expanded=False):
        st.markdown("""
**🛗 ห้องรับส่ง / Discharge** = ขั้นสุดท้าย — ผู้ป่วยพร้อมกลับบ้าน

- ระบบบันทึก: **เวลา Discharge**, ระยะเวลาผ่าตัดจริง
- ผู้ป่วยที่เป็น IPD → ส่งกลับ ward
- OPD → ออกจากโรงพยาบาลได้ทันที
- ข้อมูลเหล่านี้จะไปอัปเดต **AI model** เพื่อเรียนรู้ความแม่นยำต่อ
""")
    p = next(x for x in _DEMO_PATIENTS_FULL if x['status'] == 'discharged')
    st.markdown(f"""
    <div class="case-card" style="background:#e8f5e9;border-left:5px solid #4caf50;">
        <div><span class="pill" style="background:#4caf50;color:white;">✅ Discharge แล้ว</span></div>
        <div style="margin-top:6px;">
            <span class="pt-name">{p['name']}</span>
            <span class="pt-hn">HN: {p['hn']} · อายุ {p['age']}</span>
        </div>
        <div style="color:#555;font-size:12px;margin-top:2px;">Dx: {p['dx']}</div>
        <div class="pt-proc">{p['proc']}</div>
        <div class="pt-meta">แพทย์: {p['surgeon']} · ใช้เวลาผ่า {p['duration_min']} นาที · กลับบ้าน {p['discharged_at']}</div>
        <div style="margin-top:8px;padding:6px 10px;background:#c8e6c9;border-radius:4px;
                    font-size:11px;color:#1b5e20;font-style:italic;">💡 {p['note']}</div>
    </div>""", unsafe_allow_html=True)


def _render_executive_demo_summary():
    """🎬 Demo: สรุปยอด — แสดงตัวเลขสมมติ"""
    _render_demo_banner()
    st.markdown('<h3 style="color:#1565c0;">📊 สรุปยอดวันนี้ (Demo)</h3>',
                unsafe_allow_html=True)
    with st.expander("💡 หน้านี้คืออะไร?", expanded=False):
        st.markdown("""
**📊 สรุปยอด** = ภาพรวมการทำงานวันนี้ทั้งหมด

- **เคสทั้งหมด/ผ่าเสร็จ/ยกเลิก** — ตัวเลขสำคัญที่ต้องดู
- **OPD/IPD** — ประเภทผู้ป่วย
- **Walk-in/Elective/Urgent** — ความเร่งด่วน
- **เวลารอเฉลี่ย** — KPI หลักของ patient experience
- **Turnover** — ประสิทธิภาพการเปลี่ยนเคส (ยิ่งน้อยยิ่งดี)
- **🤖 AI accuracy** — บอกว่า model แม่นแค่ไหน (±10 นาที)
""")

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("เคสทั้งหมด", "7")
    r2.metric("ผ่าตัดสำเร็จ", "2")
    r3.metric("ยกเลิก", "0", "อัตรา 0%")
    r4.metric("Walk-in", "3", "43%")

    r5, r6, r7, r8 = st.columns(4)
    r5.metric("OPD", "6")
    r6.metric("IPD", "1")
    r7.metric("Elective", "5")
    r8.metric("Urgent", "1")

    st.markdown("---")
    st.markdown('<div style="font-size:14px;font-weight:500;margin-bottom:6px;">'
                '⏱️ เวลาที่น่าสนใจ (Demo)</div>', unsafe_allow_html=True)
    t1, t2, t3 = st.columns(3)
    t1.metric("เวลารอเฉลี่ย", "23 นาที", "-12 vs สัปดาห์ก่อน")
    t2.metric("Turnover เฉลี่ย", "16 นาที", "เป้า ≤15")
    t3.metric("AI accuracy", "78%", "±10 นาที")

    st.markdown(
        '<div style="background:#e8f5e9;border-radius:8px;padding:12px 16px;'
        'margin-top:16px;border-left:4px solid #2e7d32;">'
        '<div style="font-size:14px;font-weight:700;color:#2e7d32;margin-bottom:6px;">'
        '💡 Highlights วันนี้</div>'
        '<ul style="font-size:13px;color:#1b5e20;margin:0;padding-left:20px;line-height:1.7;">'
        '<li>✅ เคสผ่าตัดเสร็จ 2 เคส · ไม่มี cancel</li>'
        '<li>⏱️ เวลารอลดลง 12 นาที vs สัปดาห์ที่แล้ว</li>'
        '<li>🤖 AI ทำนายแม่น 78% (±10 นาที) — แจ้งญาติได้แม่นยำ</li>'
        '<li>🔄 Turnover 16 น. ใกล้เป้า 15 น. — efficient</li>'
        '</ul></div>',
        unsafe_allow_html=True,
    )


def _render_executive_demo_queue():
    """🎬 Executive Demo — แสดง Live Queue ตัวอย่างพร้อม ETA หลากสี (ไม่แตะ DB)"""
    st.markdown(
        '<div style="background:linear-gradient(135deg,#fff8e1,#ffe082);'
        'border-radius:8px;padding:10px 16px;margin-bottom:8px;'
        'border-left:5px solid #f57c00;">'
        '<span style="font-size:14px;font-weight:700;color:#f57c00;">'
        '🎬 EXECUTIVE DEMO MODE</span> '
        '<span style="font-size:13px;color:#bf360c;">ข้อมูลตัวอย่าง — สาธิตเท่านั้น ไม่บันทึก DB</span></div>',
        unsafe_allow_html=True,
    )

    with st.expander("💡 หน้านี้คืออะไร? (Live Queue + AI ETA)", expanded=False):
        st.markdown("""
**⏳ รอผ่าตัด** = หน้า **หัวใจของระบบ** — Live Queue ผู้ป่วยรอเข้าห้องผ่าตัด

### 🎨 อ่านสีง่ายๆ
- 🟢 **เขียว** = รอ ≤30 นาที (ปกติ)
- 🟡 **เหลือง** = รอ 30-60 นาที (เริ่มนาน)
- 🔴 **แดง** = รอ >60 นาที (ต้องรีบเข้าห้อง / แจ้งญาติ)

### 🤖 AI ETA (Expected Time of Arrival to OR)
- AI คำนวณว่าผู้ป่วยจะได้เข้าห้องผ่าตัดประมาณกี่โมง
- ใช้ข้อมูล: เคสกำลังผ่า + AI ทำนายเวลาเสร็จ + คิวรอ + 5 น. turnover
- **ประโยชน์:** แจ้งญาติได้แม่นยำ ลดความวิตกกังวล

### 💼 Value สำหรับ Executive
- 👥 **ลด workload พยาบาล** — ไม่ต้องตอบคำถามญาติบ่อย
- 🏥 **ปรับ flow** — ห้องไหนว่าง รับเคสได้เพิ่ม
- 📊 **measure ได้** — เวลารอจริงจะลดลง วัด KPI ได้
""")

    # Live Queue banner
    st.markdown(
        '<div style="background:linear-gradient(135deg,#fff3e0,#ffe0b2);'
        'border-radius:10px;padding:10px 16px;margin-bottom:8px;'
        'border-left:5px solid #e65100;">'
        '<span style="font-size:14px;font-weight:700;color:#e65100;">🚦 Live Queue</span> '
        '<span style="font-size:13px;color:#bf360c;">รอ 4 คน · '
        'นานสุด 67 นาที · 🤖 ETA จาก AI prediction</span></div>',
        unsafe_allow_html=True,
    )

    # ── Room headers + fake patients ──
    demo_rooms = [
        ('🔬 ห้องผ่าตัด 1', 'Laser / Morpheus / Scaret / Emsculpt / Cooltect / Q-Switch', 2,
         [
            {'name': 'นายภัทรเดช ว.', 'hn': '690009822',
             'proc': 'Morpheus (Aging Face)', 'surgeon': 'พ.ต.อ.เฉลิมเกียรติ',
             'wait_min': 67, 'color': '#c62828', 'emoji': '🔴', 'level': 'รอนาน',
             'eta': '14:35', 'time_to_eta': 'อีก 12 น.',
             'note': 'AI: เคสกำลังผ่าใช้ 25 น. (เหลือ 12 น.)'},
            {'name': 'น.ส.รัฐธีร์ ไ.', 'hn': '690011149',
             'proc': 'Morpheus (Aging Face)', 'surgeon': 'พ.ต.อ.เฉลิมเกียรติ',
             'wait_min': 28, 'color': '#e65100', 'emoji': '🟡', 'level': 'รอ',
             'eta': '15:05', 'time_to_eta': 'อีก 42 น.',
             'note': 'AI: คาดผ่าเสร็จเคสก่อน 14:35 + 30 น. ของเคสนี้'},
         ]),
        ('🔧 ห้องผ่าตัด 3', 'ESWL', 1,
         [
            {'name': 'นางสาวสุดารัตน์ ก.', 'hn': '670022345',
             'proc': 'ESWL Lt. Renal stone', 'surgeon': 'พ.ต.ท.พงศ์ธร',
             'wait_min': 15, 'color': '#2e7d32', 'emoji': '🟢', 'level': 'พึ่งมา',
             'eta': '14:50', 'time_to_eta': 'อีก 27 น.',
             'note': 'AI: ห้องว่างทันที + setup 5 น.'},
         ]),
        ('🏥 ห้องผ่าตัด 4-5', 'เคสทั่วไป (Excision / I&D / Stitch off)', 2,
         [
            {'name': 'น.ส.มาลี ส.', 'hn': '680033456',
             'proc': 'I&D abscess at back', 'surgeon': 'แพทย์หญิงวริศฐา',
             'wait_min': 5, 'color': '#2e7d32', 'emoji': '🟢', 'level': 'พึ่งมา',
             'eta': '14:30', 'time_to_eta': 'อีก 7 น.',
             'note': 'AI: ห้อง 4 ว่าง — พร้อมรับ'},
         ]),
    ]

    for room_label, room_desc, capacity, patients in demo_rooms:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#e3f2fd,#bbdefb);'
            f'border-radius:10px;padding:12px 16px;margin:16px 0 8px;">'
            f'<span style="font-size:18px;font-weight:700;color:#1565c0;">{room_label}</span>'
            f'<span style="font-size:13px;color:#1976d2;margin-left:8px;">{room_desc}</span>'
            f'<span style="float:right;background:#1565c0;color:#fff;border-radius:20px;'
            f'padding:2px 12px;font-size:14px;font-weight:600;">{len(patients)} คน</span></div>',
            unsafe_allow_html=True,
        )

        for p in patients:
            card_html = (
                f'<div class="case-card card-arrived">'
                f'<div><span class="pill pill-arrive">⏳ รอผ่าตัด</span></div>'
                f'<div style="margin-top:6px;">'
                f'<span class="pt-name">{p["name"]}</span>'
                f'<span class="pt-hn">HN: {p["hn"]}</span></div>'
                f'<div class="pt-proc">{p["proc"]}</div>'
                f'<div class="pt-meta">แพทย์: {p["surgeon"]}</div>'
                f'<div style="margin-top:8px;padding:10px 12px;'
                f'background:{p["color"]}15;border-left:4px solid {p["color"]};'
                f'border-radius:6px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<div style="font-size:12px;color:{p["color"]};">'
                f'{p["emoji"]} <b>{p["level"]} {p["wait_min"]} นาที</b></div>'
                f'<div style="font-size:13px;color:{p["color"]};font-weight:600;">'
                f'🤖 ETA <b>{p["eta"]}</b> ({p["time_to_eta"]})</div>'
                f'</div>'
                f'<div style="font-size:11px;color:#666;margin-top:4px;font-style:italic;">'
                f'💡 {p["note"]}</div>'
                f'</div></div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

    # Insight footer
    st.markdown(
        '<div style="background:#e8f5e9;border-radius:8px;padding:12px 16px;'
        'margin-top:16px;border-left:4px solid #2e7d32;">'
        '<div style="font-size:14px;font-weight:700;color:#2e7d32;margin-bottom:6px;">'
        '💡 Insight ที่ Dashboard ช่วย</div>'
        '<ul style="font-size:13px;color:#1b5e20;margin:0;padding-left:20px;line-height:1.7;">'
        '<li><b>นายภัทรเดช</b> รอ 67 นาที (🔴) → พยาบาลรู้ต้องอธิบายญาติทันที</li>'
        '<li><b>AI ทำนาย ETA</b> ทุกเคส → แจ้งญาติได้ว่าจะถึงคิวเมื่อไหร่</li>'
        '<li><b>ห้อง 4-5</b> ว่างทันที → จัดเคสเข้าได้เลย ลด idle time</li>'
        '<li><b>Throughput ดีขึ้น</b> → รับเคสเพิ่ม + ลดเวลารอผู้ป่วย</li>'
        '</ul></div>',
        unsafe_allow_html=True,
    )


def _compute_live_queue_eta(view_date_str):
    """🚦 Live Queue + ETA — คำนวณเวลาเข้าห้องผ่าตัดที่ทำนายสำหรับแต่ละเคสที่รอ

    Logic:
      1. หาเคสที่กำลังผ่าตัด (in_or) ในแต่ละห้อง → คำนวณเวลาที่ห้องจะว่าง
      2. เรียงเคสรอ (arrived) ในห้องเดียวกันตาม arrived_at
      3. ETA = นาทีที่ห้องว่างเร็วสุด + turnover 5 นาที
      4. สีตาม wait_time: 🟢 ≤30 · 🟡 30-60 · 🔴 >60 นาที

    Returns: dict {case_id: {'eta_str', 'time_to_eta', 'wait_min', 'color', 'emoji'}}
    """
    df = get_cases(op_date=view_date_str)
    if df.empty:
        return {}

    now = _now_bkk()
    df = df.copy()
    df['_wait_room'] = df['procedure_name'].apply(_assign_waiting_room)

    eta_dict = {}

    for room_key in ['room1', 'room3', 'room45']:
        # หา expected free times ของเคสที่กำลังผ่าในห้องนี้
        room_active = df[(df['status'] == 'in_or') &
                         (df['_wait_room'] == room_key)]
        free_times = []
        for _, ac in room_active.iterrows():
            in_or_at = ac.get('in_or_at')
            ai_pred = ac.get('ai_predicted_min')
            if in_or_at and ai_pred:
                try:
                    in_or_dt = datetime.strptime(in_or_at, '%Y-%m-%d %H:%M:%S')
                    expected_free = in_or_dt + timedelta(minutes=int(ai_pred))
                    # ถ้าคำนวณแล้วผ่านเวลาปัจจุบันแล้ว → ใช้ now
                    free_times.append(max(expected_free, now))
                except (ValueError, TypeError):
                    pass

        # ห้อง 4-5 มี 2 ห้อง — สมมติทั้งคู่ว่างถ้าไม่มี active case
        capacity = 2 if room_key == 'room45' else 1
        while len(free_times) < capacity:
            free_times.append(now)

        # เคสที่รอในห้องเดียวกัน เรียงตามเวลาเข้ามารอ
        room_waiting = df[(df['status'] == 'arrived') &
                          (df['_wait_room'] == room_key)].copy()
        if room_waiting.empty:
            continue
        room_waiting = room_waiting.sort_values('arrived_at')

        for _, w in room_waiting.iterrows():
            cid = int(w['case_id'])
            # ห้องที่ว่างเร็วสุด
            next_free = min(free_times)
            # ETA = ห้องว่าง + 5 นาที turnover
            eta = next_free + timedelta(minutes=5)
            # คำนวณ wait_min (ตอนนี้ - arrived_at)
            arrived_at = w.get('arrived_at')
            wait_min = 0
            if arrived_at:
                try:
                    arr_dt = datetime.strptime(arrived_at, '%Y-%m-%d %H:%M:%S')
                    wait_min = max(0, int((now - arr_dt).total_seconds() / 60))
                except (ValueError, TypeError):
                    pass

            # สีตามเวลารอ
            if wait_min > 60:
                color, emoji, level = '#c62828', '🔴', 'รอนาน'
            elif wait_min > 30:
                color, emoji, level = '#e65100', '🟡', 'รอ'
            else:
                color, emoji, level = '#2e7d32', '🟢', 'พึ่งมา'

            # ETA in อีก... นาที / ชั่วโมง
            time_to_eta_min = int((eta - now).total_seconds() / 60)
            if time_to_eta_min <= 0:
                time_to_eta_str = 'ใกล้คิว!'
            elif time_to_eta_min < 60:
                time_to_eta_str = f'อีก {time_to_eta_min} น.'
            else:
                time_to_eta_str = f'อีก {time_to_eta_min//60} ชม. {time_to_eta_min%60} น.'

            eta_dict[cid] = {
                'eta_str': eta.strftime('%H:%M'),
                'time_to_eta': time_to_eta_str,
                'wait_min': wait_min,
                'color': color,
                'emoji': emoji,
                'level': level,
            }

            # update free_times: ลบห้องที่ใช้ไป + เพิ่มเวลาเสร็จใหม่
            ai_pred_w = int(w.get('ai_predicted_min') or 30)
            new_finish = eta + timedelta(minutes=ai_pred_w)
            free_times.remove(min(free_times))
            free_times.append(new_finish)

    return eta_dict


def _tab_waiting_room(view_date_str):
    """Tab รอผ่าตัด — Live Queue + ETA สำหรับผู้ป่วยที่รอ (status='arrived')."""
    # 🎬 Executive Demo override
    if st.session_state.get('exec_demo_mode'):
        _render_executive_demo_queue()
        return

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

    # คำนวณ ETA สำหรับทุกเคสที่รอ
    eta_dict = _compute_live_queue_eta(view_date_str)
    # เก็บใน session_state เพื่อใช้ใน _render_waiting_card
    st.session_state['_live_eta'] = eta_dict

    # Live Queue summary banner
    n_wait = len(waiting)
    longest = max([info['wait_min'] for info in eta_dict.values()], default=0)
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#fff3e0,#ffe0b2);'
        f'border-radius:10px;padding:10px 16px;margin-bottom:8px;'
        f'border-left:5px solid #e65100;">'
        f'<span style="font-size:14px;font-weight:700;color:#e65100;">🚦 Live Queue</span> '
        f'<span style="font-size:13px;color:#bf360c;">รอ {n_wait} คน · '
        f'นานสุด {longest} นาที · refresh 1 นาที</span></div>',
        unsafe_allow_html=True,
    )

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
    """แสดง card ผู้ป่วยในห้องรอผ่าตัด พร้อม timer + ETA + ปุ่มเข้าห้องผ่าตัด."""
    cid = int(row['case_id'])
    name_d = row['name'] or '-'
    hn_d = row['hn'] or '-'
    proc_d = row['procedure_name'] or '-'
    surg_d = row['surgeon_name'] or '-'

    # ETA info จาก _compute_live_queue_eta (ผ่าน session_state)
    eta_info = st.session_state.get('_live_eta', {}).get(cid)
    eta_badge_html = ''
    if eta_info:
        eta_badge_html = (
            f'<div style="margin-top:8px;padding:8px 12px;'
            f'background:{eta_info["color"]}15;border-left:4px solid {eta_info["color"]};'
            f'border-radius:6px;display:flex;justify-content:space-between;align-items:center;">'
            f'<div style="font-size:12px;color:{eta_info["color"]};">'
            f'{eta_info["emoji"]} <b>{eta_info["level"]} {eta_info["wait_min"]} นาที</b></div>'
            f'<div style="font-size:13px;color:{eta_info["color"]};font-weight:600;">'
            f'🤖 ETA <b>{eta_info["eta_str"]}</b> ({eta_info["time_to_eta"]})</div>'
            f'</div>'
        )

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
        {eta_badge_html}
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
    # 🎬 Executive Demo override
    if st.session_state.get('exec_demo_mode'):
        if station == 'receive':
            _render_executive_demo_receive()
        elif station == 'or':
            _render_executive_demo_or()
        elif station == 'recovery':
            _render_executive_demo_recovery()
        elif station == 'discharge':
            _render_executive_demo_discharge()
        return

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
        # NOTE (thesis mode): ซ่อนค่าหัตถการ — เปิดกลับโดย uncomment cost_d + เพิ่มกลับใน pt-meta
        # cost_d = int(row.get('treatment_cost') or 0)
        st.markdown(f"""<div class="case-card" style="background:#e8f5e9;border-left:5px solid #4caf50;">
<div><span class="pill pill-dc">✅ ยืนยันแล้ว</span>
<span class="pill pill-after">นอกเวลา</span></div>
<div style="margin-top:6px;">
<span class="pt-name">{name_d}</span>
<span class="pt-hn">HN: {hn_d}</span>
</div>{_aft_diag}
<div class="pt-proc">{proc_d}</div>
<div class="pt-meta">แพทย์: {surg_d}</div>
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
        # NOTE (thesis mode): ซ่อน UI เลือกราคา — เก็บโค้ดราคาไว้ใน comment block ด้านล่าง
        # คงไว้แค่ "แพทย์ที่ทำ" + "ยืนยันบันทึก" — บันทึกค่าราคา = 0 ก่อน
        st.markdown("**✅ ยืนยันเคสนอกเวลา**")

        # ── [HIDDEN — thesis mode] Fuzzy price lookup ──
        # matches = _fuzzy_price_lookup(proc_d)
        # cost_val = 0
        # if len(matches) == 1:
        #     m = matches[0]
        #     st.markdown(
        #         f'<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
        #         f'border-radius:12px;font-size:12px;">match: {m["procedure_name"]}</span>',
        #         unsafe_allow_html=True)
        #     cost_val = int(m['new_price_thb'])
        # elif len(matches) > 1:
        #     st.markdown(
        #         f'<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
        #         f'border-radius:12px;font-size:12px;">พบ {len(matches)} รายการ</span>',
        #         unsafe_allow_html=True)
        #     options_display = [
        #         f"{r['procedure_name_th']} — {int(r['new_price_thb']):,} ฿"
        #         for r in matches
        #     ]
        #     sel = st.selectbox("เลือกรายการ", options_display, key=f"aftpick_{cid}")
        #     sel_idx = options_display.index(sel)
        #     cost_val = int(matches[sel_idx]['new_price_thb'])
        # cost_val = st.number_input("ค่าหัตถการ (บาท)", min_value=0,
        #                             value=cost_val, step=100, key=f"aftcost_{cid}")
        cost_val = 0  # default — thesis mode

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
    """Return HTML badge showing AI predicted time + progress bar (when in_or)."""
    ai = row.get('ai_predicted_min')
    if ai is None or (isinstance(ai, float) and (ai != ai)):  # NaN check
        return ''
    try:
        ai = int(float(ai))
    except (ValueError, TypeError):
        return ''
    if ai <= 0:
        return ''

    status = row.get('status', '')
    in_or_at = row.get('in_or_at')

    # When กำลังผ่า → แสดง progress bar
    if status == 'in_or' and in_or_at:
        try:
            start = datetime.strptime(str(in_or_at), '%Y-%m-%d %H:%M:%S')
            elapsed_min = max(0, int(
                (_now_bkk() - start).total_seconds() / 60))
            pct = int((elapsed_min / ai) * 100) if ai > 0 else 0
            bar_width = min(pct, 100)
            bar_color = '#26a69a' if pct <= 100 else '#ef5350'
            # Build single-line HTML (no newlines/indent — Streamlit
            # markdown treats indented multi-line as code block)
            return (
                f'<div class="ai-badge">🤖 AI ทำนายเวลาใช้ห้อง: ~{ai} นาที '
                f'· ใช้ไป <b>{elapsed_min}</b> น.</div>'
                f'<div style="background:#e0e0e0;border-radius:8px;'
                f'height:18px;margin-top:4px;overflow:hidden;'
                f'position:relative;">'
                f'<div style="background:{bar_color};height:100%;'
                f'width:{bar_width}%;transition:width 1s ease;'
                f'border-radius:8px;"></div>'
                f'<div style="position:absolute;top:0;left:0;right:0;'
                f'bottom:0;display:flex;align-items:center;'
                f'justify-content:center;font-size:11px;font-weight:700;'
                f'color:#333;">{pct}%</div>'
                f'</div>'
            )
        except (ValueError, TypeError):
            pass

    # Other statuses — แสดงแค่ badge text
    return (f'<div class="ai-badge">🤖 AI ทำนายเวลาใช้ห้อง: '
            f'~{ai} นาที</div>')


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

        # NOTE (thesis mode): ซ่อน expander แก้ไขราคา/patho — เปิดกลับโดยลบ False and
        # แก้หัตถการ + ค่ารักษา (เฉพาะ transfer = ห้องรับส่ง)
        if False and dest_val == 'transfer':  # disabled for thesis mode
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

    # NOTE (thesis mode): ซ่อน Revenue + patho row — เปิดกลับโดย uncomment
    # rv1, rv2, rv3, rv4 = st.columns(4)
    # rv1.metric("💰 ค่าหัตถการ", f"{s['total_treatment']:,} ฿")
    # rv2.metric("💵 รายได้รวม", f"{s['total_revenue']:,} ฿")
    # rv3.metric("🧬 ส่งชิ้นเนื้อ", f"{s['n_patho_sent']} ราย")
    # rv4.metric("🔬 ค่าชิ้นเนื้อ", f"{s['total_patho']:,} ฿")

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
        m3.metric("AI ทำนายเวลาใช้ห้อง (เฉลี่ย)", "%.0f นาที" % avg_pred)

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
    # 🎬 Executive Demo override
    if st.session_state.get('exec_demo_mode'):
        _render_executive_demo_summary()
        return

    today = _now_bkk().strftime('%Y-%m-%d')
    _thai_months = ['','ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.']
    _t = _now_bkk()
    _thai_date = f"{_t.day} {_thai_months[_t.month]} {_t.year + 543}"

    s = get_summary(date_from=today, date_to=today)
    df_today = get_cases(op_date=today)

    # ── Header ──
    st.markdown(f"""<div style="background:linear-gradient(135deg,#1565c0,#1976d2);color:#fff;padding:16px 20px;border-radius:12px;margin-bottom:16px;">
<div style="font-size:20px;font-weight:700;">📋 สรุปยอดวันนี้</div>
<div style="font-size:13px;opacity:.85;">{_thai_date} · {_t.strftime('%H:%M')} น.</div>
</div>""", unsafe_allow_html=True)

    # ── 📊 ภาพรวม — 4 cards row ──
    st.markdown(
        '<div style="font-size:14px;font-weight:600;color:#546e7a;margin:14px 0 6px;">'
        '📊 ภาพรวม</div>', unsafe_allow_html=True)
    cancel_rate = s['cancelled'] / s['total'] * 100 if s['total'] > 0 else 0
    opd_pct = (s['n_opd'] / s['total'] * 100) if s['total'] > 0 else 0
    ipd_pct = (s['n_ipd'] / s['total'] * 100) if s['total'] > 0 else 0

    def _sum_card(label, value, sub_text, value_color='#212121'):
        return (
            f'<div style="background:#f5f5f5;border-radius:8px;padding:14px;">'
            f'<div style="font-size:12px;color:#757575;margin-bottom:4px;">{label}</div>'
            f'<div style="font-size:26px;font-weight:500;line-height:1;color:{value_color};">{value}</div>'
            f'<div style="font-size:11px;color:#9e9e9e;margin-top:4px;">{sub_text}</div>'
            f'</div>'
        )
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        st.markdown(_sum_card("📊 เคสทั้งหมด", s['total'],
                              f"✓ ผ่าตัดสำเร็จ {s['completed']}",
                              value_color='#1565c0'), unsafe_allow_html=True)
    with r2:
        st.markdown(_sum_card("🏥 OPD", s['n_opd'], f"{opd_pct:.1f}%"),
                    unsafe_allow_html=True)
    with r3:
        st.markdown(_sum_card("🏨 IPD", s['n_ipd'], f"{ipd_pct:.1f}%"),
                    unsafe_allow_html=True)
    with r4:
        st.markdown(_sum_card("⚠️ ยกเลิก", s['cancelled'],
                              f"อัตรา {cancel_rate:.0f}%",
                              value_color='#c62828'), unsafe_allow_html=True)

    # ── ⚠️ ระดับความเร่งด่วน — Elective (+breakdown) / Urgent / Emergency ──
    # คำนวณเสมอ — ถ้าไม่มีข้อมูล จะเป็น 0 ทั้งหมด
    n_elec = n_urg = n_emer = 0
    n_elec_set = n_elec_walkin = 0
    if not df_today.empty and 'op_type' in df_today.columns:
        op_norm = (df_today['op_type'].fillna('elective')
                   .astype(str).str.lower().str.strip()
                   .replace('', 'elective'))
        n_elec = int((op_norm == 'elective').sum())
        n_urg = int((op_norm == 'urgent').sum())
        n_emer = int((op_norm == 'emergency').sum())
        if 'case_category' in df_today.columns:
            mask_elec = (op_norm == 'elective')
            n_elec_set = int(((df_today['case_category'] == 'เคสนัดหมาย') & mask_elec).sum())
            n_elec_walkin = int(((df_today['case_category'] == 'Walk-in') & mask_elec).sum())

    st.markdown(
        '<div style="font-size:14px;font-weight:600;color:#546e7a;margin:14px 0 6px;">'
        '⚠️ ระดับความเร่งด่วน</div>', unsafe_allow_html=True)
    if True:
        ko1, ko2, ko3 = st.columns(3)
        with ko1:
            st.markdown(
                f'<div style="background:#f5f5f5;border-radius:8px;padding:14px 16px;">'
                f'<div style="font-size:13px;color:#666;margin-bottom:4px;">📋 Elective</div>'
                f'<div style="font-size:28px;font-weight:500;line-height:1.1;margin-bottom:8px;">{n_elec}</div>'
                f'<div style="font-size:12px;color:#888;display:flex;gap:10px;">'
                f'<span>นัดหมาย <b style="color:#444;font-weight:500;">{n_elec_set}</b></span>'
                f'<span style="color:#ccc;">|</span>'
                f'<span>Walk-in <b style="color:#444;font-weight:500;">{n_elec_walkin}</b></span>'
                f'</div></div>', unsafe_allow_html=True)
        with ko2:
            st.markdown(
                f'<div style="background:#f5f5f5;border-radius:8px;padding:14px 16px;">'
                f'<div style="font-size:13px;color:#666;margin-bottom:4px;">⚡ Urgent</div>'
                f'<div style="font-size:28px;font-weight:500;line-height:1.1;">{n_urg}</div>'
                f'</div>', unsafe_allow_html=True)
        with ko3:
            st.markdown(
                f'<div style="background:#f5f5f5;border-radius:8px;padding:14px 16px;">'
                f'<div style="font-size:13px;color:#666;margin-bottom:4px;">🚨 Emergency</div>'
                f'<div style="font-size:28px;font-weight:500;line-height:1.1;">{n_emer}</div>'
                f'</div>', unsafe_allow_html=True)

    # ── Charts ──
    if not df_today.empty:
        df_in = df_today[(df_today['status'] != 'cancelled') &
                         (df_today['patient_type'] != 'นอกเวลา')].copy()

        if not df_in.empty:
            st.markdown('<div style="margin-top:12px;"></div>', unsafe_allow_html=True)

            pc1, pc2 = st.columns(2)
            with pc1:
                st.markdown('<div style="background:#f1f8e9;border-radius:10px;padding:12px 16px;"><b>🩺 หัตถการวันนี้</b></div>', unsafe_allow_html=True)
                proc_counts = df_in['procedure_name'].str.upper().value_counts()
                for proc_name, n in proc_counts.items():
                    st.markdown(f"&nbsp;&nbsp;**{proc_name}** — {n} ราย", unsafe_allow_html=True)
            with pc2:
                st.markdown('<div style="background:#e3f2fd;border-radius:10px;padding:12px 16px;"><b>🏷️ สาขาที่ set วันนี้</b></div>', unsafe_allow_html=True)
                div_counts = df_in['division_code'].apply(div_name).value_counts().reset_index()
                div_counts.columns = ['สาขา', 'จำนวน']
                fig_div = px.pie(div_counts, names='สาขา', values='จำนวน', hole=0.35)
                fig_div.update_layout(margin=dict(t=10, b=10, l=10, r=10),
                                      height=280, showlegend=True,
                                      legend=dict(font=dict(size=11)))
                fig_div.update_traces(textposition='inside', textinfo='value+label')
                st.plotly_chart(fig_div, use_container_width=True)

            # Scatter: ช่วงเวลาผ่าตัด
            st.markdown('<div style="background:#fff3e0;border-radius:10px;padding:12px 16px;margin-top:8px;"><b>⏰ ช่วงเวลาผ่าตัด (เช้า / บ่าย)</b></div>', unsafe_allow_html=True)
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

            # Top 5 แพทย์
            st.markdown('<div style="background:#fce4ec;border-radius:10px;padding:12px 16px;margin-top:8px;"><b>🏅 Top 5 แพทย์ที่ set มากสุด</b></div>', unsafe_allow_html=True)
            surg_counts = df_in['surgeon_name'].value_counts().head(5)
            for i, (surg, n) in enumerate(surg_counts.items()):
                medal = ["🥇", "🥈", "🥉", "4.", "5."][i]
                st.markdown(f"&nbsp;&nbsp;**{medal} {surg}** — {n} ราย", unsafe_allow_html=True)

    # ── นอกเวลา ──
    st.markdown('<div style="margin-top:16px;"></div>', unsafe_allow_html=True)
    st.markdown("""<div style="background:linear-gradient(135deg,#f3e5f5,#e1bee7);border-radius:10px;padding:12px 16px;">
<span style="font-size:16px;font-weight:700;color:#6a1b9a;">🌙 เคสนอกเวลา</span></div>""", unsafe_allow_html=True)
    _render_after_hours_summary(df_today, prefix="today")

    # ── Download ──
    st.markdown('<div style="margin-top:16px;"></div>', unsafe_allow_html=True)
    if s['total'] > 0 or (not df_today.empty):
        dl1, dl2 = st.columns(2)
        with dl1:
            xlsx_data = export_summary_excel(today, today)
            st.download_button(
                '📊 ดาวน์โหลดสรุปวันนี้ (Excel)',
                xlsx_data,
                f'minor_or_summary_{today}.xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                use_container_width=True,
                key='dl_today_xlsx',
            )
        with dl2:
            csv_data = df_today.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                '📥 ดาวน์โหลดข้อมูลดิบ (CSV)',
                csv_data,
                f'minor_or_cases_{today}.csv',
                'text/csv',
                use_container_width=True,
                key='dl_today_csv',
            )

    st.caption("💡 ดูสถิติสะสมย้อนหลังได้ที่หน้า 📊 บริหารจัดการ")
