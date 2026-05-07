"""
Minor OR Admin Dashboard — หน้าบริหารจัดการสำหรับหัวหน้า/ผู้บริหาร
ดูอย่างเดียว ไม่ต้องกดอะไร — เปิดมาเห็นภาพรวมทันที
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
_BKK = timezone(timedelta(hours=7))

def _now_bkk():
    """Return current datetime in Bangkok timezone (naive, for comparisons with stored timestamps)."""
    return datetime.now(_BKK).replace(tzinfo=None)
from minor_or_db import (
    get_room_status, get_kpi, get_delay_alerts, get_workload,
    get_summary, get_nurse_stats, div_name, DIV_CODE_MAP,
    get_historical_analytics, export_cases_csv, export_summary_excel, get_cases,
    get_wait_stats, get_handover_stats,
)
import numpy as np
import re
from difflib import SequenceMatcher


# ============================================================================
# Procedure name fuzzy grouping
# ----------------------------------------------------------------------------
# รวม "หัตถการ" ที่เขียนต่างกันแต่หมายถึงสิ่งเดียวกัน เช่น
#   - off PERM cath / off TCC Rt IJV  →  "Off catheter (PERM/TCC/IJV)"
#   - right big toe partial nail extraction / partial nail extraction  →  "Nail extraction"
#   - excision / Excision  →  "Excision" (case-insensitive)
# วิธีการ: rule-based (regex) ก่อน → fuzzy similarity (SequenceMatcher) ทีหลัง
# ============================================================================

# (compiled regex pattern, canonical name)  — ลำดับสำคัญ! pattern แรกที่ match จะถูกใช้
_PROC_RULES = [
    # Off catheter (PERM cath / TCC / IJV)
    (re.compile(
        r'\boff\b.*\b(perm\s*cath|perm|tcc|ijv|hd\s*cath|cath(eter)?)\b',
        re.I), 'Off catheter (PERM/TCC/IJV)'),
    # "remove cath" / "removal of catheter" (removal-first order)
    (re.compile(r'\b(remove|removal)\b.*\bcath(eter)?\b', re.I),
        'Off catheter (PERM/TCC/IJV)'),
    # "PERM/TCC catheter removal" (catheter-first order)
    (re.compile(r'\bcath(eter)?\b.*\b(remove|removal|off)\b', re.I),
        'Off catheter (PERM/TCC/IJV)'),

    # Nail extraction (รวม partial / total / specific toe)
    (re.compile(r'nail\s*(extract(ion)?|removal|avulsion)', re.I),
        'Nail extraction'),

    # ESWL
    (re.compile(r'\beswl\b', re.I), 'ESWL'),

    # I&D — Incision & Drainage (รวมรูปแบบ "I and D", "I & D", "I+D")
    (re.compile(r'\bi\s*(?:and|&|\+)\s*d\b|\bincision\s*(?:and|&)\s*drainage\b', re.I),
        'I&D'),

    # Excision (รวม Excisional biopsy ทั่วไป)
    (re.compile(r'\bexcis(ion|e|ional)\b', re.I), 'Excision'),

    # EC
    (re.compile(r'^\s*ec\s*$|\bec\b\s*(case|biopsy)?', re.I), 'EC'),

    # Morpheus (laser)
    (re.compile(r'\bmorpheus\b', re.I), 'Morpheus'),
]


def _strip_modifiers(name: str) -> str:
    """ตัดคำขยายที่ไม่ส่งผลต่อชนิดหัตถการ เช่น Rt/Lt/Right/Left และเลขท้าย."""
    s = re.sub(r'\b(rt|lt|right|left|bilateral|bil|both)\b\.?', '', name, flags=re.I)
    s = re.sub(r'\bbig\s*toe\b|\b(1st|2nd|3rd|4th|5th)\s*toe\b', 'toe', s, flags=re.I)
    s = re.sub(r'\s+\d+\s*$', '', s)              # ลบเลขท้าย เช่น "extraction 2"
    s = re.sub(r'[\(\)\[\]\.]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalize_procedure_name(name) -> str:
    """แปลงชื่อหัตถการดิบ → canonical group ตาม rule + cleanup."""
    if name is None:
        return 'UNKNOWN'
    s = str(name).strip()
    if not s or s.lower() in ('nan', 'none', '-'):
        return 'UNKNOWN'
    # Rule-based ก่อน
    for pat, canonical in _PROC_RULES:
        if pat.search(s):
            return canonical
    # ตัด side / เลขท้าย แล้ว Title Case
    cleaned = _strip_modifiers(s)
    if not cleaned:
        return s
    # ถ้าเป็นตัวย่อสั้น ๆ ทั้งหมด (≤4 ตัว) เก็บ uppercase ไว้
    if len(cleaned) <= 4 and cleaned.isalpha():
        return cleaned.upper()
    return cleaned[0].upper() + cleaned[1:]


def _fuzzy_merge(df: pd.DataFrame, name_col: str = 'procedure_name',
                 threshold: float = 0.88) -> pd.DataFrame:
    """หลังผ่าน rule แล้ว ถ้ายังมีชื่อใกล้เคียงกันมาก (เช่น พิมพ์ผิดเล็กน้อย)
    ให้รวมเข้ากลุ่มที่มีจำนวนเคสมากกว่า"""
    if df.empty:
        return df
    df = df.sort_values('n', ascending=False).reset_index(drop=True)
    canonical_for = {}      # raw name → canonical
    canonical_list = []     # canonical names ที่เลือกแล้ว
    for raw in df[name_col].tolist():
        best_canon, best_ratio = None, 0.0
        rl = raw.lower()
        for canon in canonical_list:
            r = SequenceMatcher(None, rl, canon.lower()).ratio()
            if r > best_ratio:
                best_ratio, best_canon = r, canon
        if best_canon is not None and best_ratio >= threshold:
            canonical_for[raw] = best_canon
        else:
            canonical_for[raw] = raw
            canonical_list.append(raw)
    df['_canon'] = df[name_col].map(canonical_for)
    # weighted mean ของ avg_min
    df['_total_min'] = df['n'] * df['avg_min'].fillna(0)
    g = (df.groupby('_canon', as_index=False)
           .agg(n=('n', 'sum'), _total_min=('_total_min', 'sum')))
    g['avg_min'] = (g['_total_min'] / g['n']).round(0)
    g = g.rename(columns={'_canon': name_col}).drop(columns=['_total_min'])
    return g.sort_values('n', ascending=False).reset_index(drop=True)


def group_top_procedures(proc_df: pd.DataFrame, top_n: int = 10,
                         fuzzy_threshold: float = 0.88) -> pd.DataFrame:
    """รวมหัตถการที่คล้ายกันเข้าด้วยกัน แล้วคืน Top-N
    Returns DataFrame[procedure_name, n, avg_min]
    """
    if proc_df is None or proc_df.empty:
        return proc_df
    df = proc_df.copy()
    df['procedure_name'] = (df['procedure_name']
                            .fillna('UNKNOWN').astype(str)
                            .apply(_normalize_procedure_name))
    if 'avg_min' not in df.columns:
        df['avg_min'] = 0
    # rollup ครั้งแรกหลัง normalize (weighted mean)
    df['_total_min'] = df['n'] * df['avg_min'].fillna(0)
    df = (df.groupby('procedure_name', as_index=False)
            .agg(n=('n', 'sum'), _total_min=('_total_min', 'sum')))
    df['avg_min'] = (df['_total_min'] / df['n']).round(0)
    df = df.drop(columns=['_total_min'])
    # fuzzy merge รอบสองสำหรับชื่อที่หลุด rule
    df = _fuzzy_merge(df, 'procedure_name', threshold=fuzzy_threshold)
    return df.head(top_n).reset_index(drop=True)


# ============================================================================
# CSS
# ============================================================================

_ADMIN_CSS = """
<style>
.admin-header {
    background: linear-gradient(135deg, #1a237e, #283593);
    color: white; padding: 18px 24px; border-radius: 12px;
    margin-bottom: 20px;
}
.admin-header h1 { margin: 0; font-size: 24px; }
.admin-header p { margin: 4px 0 0; font-size: 13px; opacity: 0.85; }

.room-card {
    border-radius: 12px; padding: 16px; text-align: center;
    min-height: 140px; box-shadow: 0 2px 8px rgba(0,0,0,.08);
}
.room-free  { background: #f5f5f5; border-top: 4px solid #bdbdbd; }
.room-busy  { background: #e3f2fd; border-top: 4px solid #1976d2; }
.room-done  { background: #e8f5e9; border-top: 4px solid #388e3c; }

.kpi-card {
    background: white; border-radius: 12px; padding: 16px; text-align: center;
    box-shadow: 0 2px 6px rgba(0,0,0,.06); min-height: 100px;
}
.kpi-value { font-size: 32px; font-weight: 700; margin: 4px 0; }
.kpi-label { font-size: 13px; color: #757575; }

.alert-card {
    border-radius: 10px; padding: 12px 16px; margin: 6px 0;
    display: flex; align-items: center; gap: 10px;
}
.alert-high   { background: #ffebee; border-left: 4px solid #d32f2f; }
.alert-medium { background: #fff8e1; border-left: 4px solid #f9a825; }
.alert-info   { background: #f5f5f5; border-left: 4px solid #9e9e9e; }

.section-title {
    font-size: 16px; font-weight: 700; color: #37474f;
    margin: 20px 0 10px; padding-bottom: 6px;
    border-bottom: 2px solid #e0e0e0;
}
</style>
"""


# ============================================================================
# COMPONENTS
# ============================================================================

def _render_room_cards(rooms):
    """แสดงสถานะห้องผ่าตัดเป็น cards."""
    cols = st.columns(len(rooms))
    for i, rm in enumerate(rooms):
        with cols[i]:
            active = rm['active_case']
            if active:
                # กำลังผ่าตัดอยู่
                elapsed = ''
                if active.get('in_or_at'):
                    try:
                        start = datetime.strptime(active['in_or_at'], '%Y-%m-%d %H:%M:%S')
                        mins = int((_now_bkk() - start).total_seconds() / 60)
                        elapsed = f'<div style="font-size:24px;font-weight:700;color:#1565c0;">{mins} นาที</div>'
                    except:
                        pass
                pred_txt = f"ทำนาย {active['ai_predicted_min']} นาที" if active.get('ai_predicted_min') else ""
                st.markdown(f"""
                <div class="room-card room-busy">
                    <div style="font-size:14px;font-weight:700;color:#1565c0;">ห้อง {rm['room_no']}</div>
                    <div style="font-size:11px;color:#1976d2;margin:2px 0;">🔵 กำลังผ่าตัด</div>
                    {elapsed}
                    <div style="font-size:12px;margin-top:4px;"><b>{active['procedure_name'] or '-'}</b></div>
                    <div style="font-size:11px;color:#666;">{active['name'] or '-'}</div>
                    <div style="font-size:11px;color:#999;">{pred_txt}</div>
                </div>
                """, unsafe_allow_html=True)
            elif rm['done'] > 0 and rm['waiting'] == 0:
                # เสร็จหมดแล้ว
                st.markdown(f"""
                <div class="room-card room-done">
                    <div style="font-size:14px;font-weight:700;color:#2e7d32;">ห้อง {rm['room_no']}</div>
                    <div style="font-size:11px;color:#388e3c;margin:2px 0;">✅ เสร็จแล้ว</div>
                    <div style="font-size:28px;font-weight:700;color:#2e7d32;">{rm['done']}</div>
                    <div style="font-size:12px;color:#666;">เคสเสร็จ</div>
                </div>
                """, unsafe_allow_html=True)
            elif rm['total'] > 0:
                # มีเคสแต่ยังไม่เข้าห้อง
                st.markdown(f"""
                <div class="room-card room-free">
                    <div style="font-size:14px;font-weight:700;color:#616161;">ห้อง {rm['room_no']}</div>
                    <div style="font-size:11px;color:#f57f17;margin:2px 0;">⏳ รอเข้าห้อง</div>
                    <div style="font-size:28px;font-weight:700;color:#f57f17;">{rm['waiting']}</div>
                    <div style="font-size:12px;color:#666;">เคสรอ / {rm['total']} ทั้งหมด</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                # ไม่มีเคส
                st.markdown(f"""
                <div class="room-card room-free">
                    <div style="font-size:14px;font-weight:700;color:#9e9e9e;">ห้อง {rm['room_no']}</div>
                    <div style="font-size:11px;color:#bdbdbd;margin:2px 0;">—</div>
                    <div style="font-size:28px;font-weight:700;color:#bdbdbd;">ว่าง</div>
                    <div style="font-size:12px;color:#ccc;">ไม่มีเคส</div>
                </div>
                """, unsafe_allow_html=True)


def _render_kpi(kpi):
    """แสดง KPI cards."""
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">เคสทั้งหมด</div>
            <div class="kpi-value" style="color:#1565c0;">{kpi['total']}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">ผ่าเสร็จแล้ว</div>
            <div class="kpi-value" style="color:#2e7d32;">{kpi['done']}</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">กำลังผ่าตัด</div>
            <div class="kpi-value" style="color:#1976d2;">{kpi['in_or']}</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        color = '#2e7d32' if kpi['utilization'] <= 80 else ('#f57f17' if kpi['utilization'] <= 100 else '#d32f2f')
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Utilization</div>
            <div class="kpi-value" style="color:{color};">{kpi['utilization']}%</div>
        </div>""", unsafe_allow_html=True)
    with c5:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Turnover เฉลี่ย</div>
            <div class="kpi-value" style="color:#6a1b9a;">{kpi['avg_turnover']:.0f}<span style="font-size:14px;"> นาที</span></div>
        </div>""", unsafe_allow_html=True)


def _render_alerts(alerts):
    """แสดง Delay / Alert cards."""
    if not alerts:
        st.markdown("""
        <div style="background:#e8f5e9;padding:16px;border-radius:10px;text-align:center;">
            <span style="font-size:20px;">✅</span>
            <span style="font-size:14px;color:#2e7d32;font-weight:600;"> ไม่มีปัญหา — ทุกอย่างปกติ</span>
        </div>
        """, unsafe_allow_html=True)
        return

    for a in alerts:
        icon = '🔴' if a['severity'] == 'high' else ('🟡' if a['severity'] == 'medium' else '⚪')
        css_class = f"alert-{a['severity']}"
        st.markdown(f"""
        <div class="alert-card {css_class}">
            <span style="font-size:18px;">{icon}</span>
            <div>
                <div style="font-size:13px;font-weight:600;">ห้อง {a['room_no']} — {a['procedure'] or '-'}</div>
                <div style="font-size:12px;color:#666;">{a['name'] or '-'} | {a['message']}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)


def _render_workload(wl):
    """แสดงภาระงาน — แพทย์ + สาขา + ประเภท."""
    col_left, col_right = st.columns(2)

    with col_left:
        # Top แพทย์
        st.markdown('<div class="section-title">👨‍⚕️ แพทย์วันนี้</div>', unsafe_allow_html=True)
        if len(wl['top_surgeons']) > 0:
            for _, row in wl['top_surgeons'].iterrows():
                n_total = int(row['n'])
                n_done = int(row['done'])
                pct = int(n_done / n_total * 100) if n_total > 0 else 0
                bar_color = '#4caf50' if pct == 100 else '#42a5f5'
                st.markdown(f"""
                <div style="margin:6px 0;">
                    <div style="display:flex;justify-content:space-between;font-size:13px;">
                        <span><b>{row['surgeon_name']}</b></span>
                        <span style="color:#666;">{n_done}/{n_total} เคส</span>
                    </div>
                    <div style="background:#e0e0e0;border-radius:4px;height:8px;margin-top:3px;">
                        <div style="background:{bar_color};height:100%;width:{pct}%;border-radius:4px;"></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("ยังไม่มีข้อมูลแพทย์")

    with col_right:
        # สาขา (pie)
        st.markdown('<div class="section-title">🏥 สาขาที่ทำวันนี้</div>', unsafe_allow_html=True)
        if len(wl['div_stats']) > 0:
            div_df = wl['div_stats'].copy()
            div_df['division_name'] = div_df['division_code'].apply(div_name)
            fig = px.pie(div_df, values='n', names='division_name',
                         color_discrete_sequence=px.colors.qualitative.Set3)
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10), height=220,
                showlegend=True, legend=dict(font=dict(size=11)),
            )
            fig.update_traces(textposition='inside', textinfo='value+label',
                              textfont_size=11)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("ยังไม่มีข้อมูลสาขา")

    # ประเภทเคส — แถว badges
    st.markdown('<div class="section-title">📊 ประเภทเคส</div>', unsafe_allow_html=True)
    badges_html = f"""
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:6px;">
        <div style="background:#e0f2f1;padding:8px 16px;border-radius:20px;font-size:13px;">
            📋 SET <b>{wl['n_set']}</b></div>
        <div style="background:#e3f2fd;padding:8px 16px;border-radius:20px;font-size:13px;">
            🚶 Walk-in <b>{wl['n_walkin']}</b></div>
        <div style="background:#e0f7fa;padding:8px 16px;border-radius:20px;font-size:13px;">
            🏥 OPD <b>{wl['n_opd']}</b></div>
        <div style="background:#fff3e0;padding:8px 16px;border-radius:20px;font-size:13px;">
            🛏️ IPD <b>{wl['n_ipd']}</b></div>
        <div style="background:#fce4ec;padding:8px 16px;border-radius:20px;font-size:13px;">
            🌙 นอกเวลา <b>{wl['n_after']}</b></div>
    </div>
    """
    st.markdown(badges_html, unsafe_allow_html=True)


def _render_ai_accuracy(op_date: str = None):
    """AI Prediction Accuracy — ส่วนเล็กๆ สำหรับวิจัย."""
    summary = get_summary(date_from=op_date, date_to=op_date)
    ai_df = summary.get('ai_df')
    if ai_df is None or len(ai_df) == 0:
        st.caption("ยังไม่มีข้อมูล AI prediction วันนี้")
        return

    # Calculate MAE, MAPE
    ai_df = ai_df.copy()
    ai_df['error'] = (ai_df['ai_predicted_min'] - ai_df['actual_duration_min']).abs()
    ai_df['pct_error'] = ai_df['error'] / ai_df['actual_duration_min'] * 100
    mae = ai_df['error'].mean()
    mape = ai_df['pct_error'].mean()
    n = len(ai_df)
    within_15 = (ai_df['pct_error'] <= 15).sum()
    accuracy_pct = round(within_15 / n * 100, 1) if n > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("MAE", f"{mae:.1f} นาที")
    c2.metric("MAPE", f"{mape:.1f}%")
    c3.metric(f"ทำนายแม่น (±15%)", f"{within_15}/{n} ({accuracy_pct}%)")

    # Mini scatter
    fig = px.scatter(ai_df, x='actual_duration_min', y='ai_predicted_min',
                     hover_data=['procedure_name', 'surgeon_name'],
                     labels={'actual_duration_min': 'จริง (นาที)', 'ai_predicted_min': 'AI ทำนาย (นาที)'},
                     color_discrete_sequence=['#5c6bc0'])
    # Perfect prediction line
    max_val = max(ai_df['actual_duration_min'].max(), ai_df['ai_predicted_min'].max()) * 1.1
    fig.add_trace(go.Scatter(x=[0, max_val], y=[0, max_val],
                             mode='lines', line=dict(dash='dash', color='#bdbdbd'),
                             showlegend=False))
    fig.update_layout(margin=dict(t=10, b=30, l=40, r=10), height=200,
                      xaxis_title='จริง (นาที)', yaxis_title='AI (นาที)')
    st.plotly_chart(fig, use_container_width=True)


_NURSE_PIN = 'muke'


def _render_nurse_progress(op_date: str):
    """Progress รายบุคคล — ล็อคด้วย PIN."""

    # ---- PIN Lock ----
    if not st.session_state.get('nurse_unlocked'):
        st.markdown(
            '<div style="background:#f5f5f5;border-radius:10px;padding:16px;'
            'text-align:center;margin:8px 0;">'
            '<span style="font-size:24px;">🔒</span><br>'
            '<span style="font-size:14px;color:#616161;font-weight:600;">'
            'Nurse Progress — ต้องใส่รหัสเพื่อดู</span></div>',
            unsafe_allow_html=True,
        )
        pc1, pc2 = st.columns([3, 1])
        with pc1:
            pin_input = st.text_input("รหัส PIN", type="password",
                                       key="nurse_pin_input",
                                       placeholder="กรอก PIN")
        with pc2:
            st.markdown('<div style="height:28px;"></div>', unsafe_allow_html=True)
            if st.button("🔓 ปลดล็อค", key="nurse_unlock_btn",
                         use_container_width=True):
                if pin_input == _NURSE_PIN:
                    st.session_state['nurse_unlocked'] = True
                    st.rerun()
                else:
                    st.error("❌ PIN ไม่ถูกต้อง")
        return

    # ---- Unlocked: show Progress รายบุคคล ----
    # เลือกช่วงเวลา
    period = st.radio("ช่วงเวลา", ["วันนี้", "7 วัน", "30 วัน", "ทั้งหมด"],
                      horizontal=True, key="nurse_period", label_visibility='collapsed')
    from datetime import timedelta
    if period == "วันนี้":
        d_from, d_to = op_date, op_date
    elif period == "7 วัน":
        d_from = (datetime.strptime(op_date, '%Y-%m-%d') - timedelta(days=6)).strftime('%Y-%m-%d')
        d_to = op_date
    elif period == "30 วัน":
        d_from = (datetime.strptime(op_date, '%Y-%m-%d') - timedelta(days=29)).strftime('%Y-%m-%d')
        d_to = op_date
    else:
        d_from, d_to = None, None

    ns = get_nurse_stats(date_from=d_from, date_to=d_to)
    summary = ns['nurse_summary']
    cases_df = ns['nurse_cases']

    if summary.empty:
        st.info("ยังไม่มีข้อมูลพยาบาล")
        return

    # ---- Progress รายบุคคล ----
    nurse_names = sorted(summary['nurse_name'].tolist())
    sel_nurse = st.selectbox("เลือกพยาบาล", nurse_names, key="sel_nurse_detail")
    individual = cases_df[cases_df['nurse_name'] == sel_nurse].copy()
    if not individual.empty:
        total = len(individual)
        n_scrub = len(individual[individual['role'] == 'Scrub'])
        n_circ = len(individual[individual['role'] == 'Circ'])
        n_procs = individual['procedure_name'].nunique()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("เคสทั้งหมด", total)
        m2.metric("🧤 Scrub", n_scrub)
        m3.metric("📋 Circ", n_circ)
        m4.metric("หัตถการ", f"{n_procs} ชนิด")

        # Procedure breakdown
        proc_counts = individual.groupby(['procedure_name', 'role']).size().reset_index(name='n')
        st.markdown(f"**{sel_nurse}** — หัตถการที่เคยทำ:")
        for _, p in proc_counts.iterrows():
            role_icon = '🧤' if p['role'] == 'Scrub' else '📋'
            st.markdown(f"- {role_icon} **{p['procedure_name']}** × {p['n']} ครั้ง ({p['role']})")

        # Timeline chart
        daily = individual.groupby('op_date').size().reset_index(name='n')
        if len(daily) > 1:
            fig = px.bar(daily, x='op_date', y='n',
                         labels={'op_date': 'วันที่', 'n': 'จำนวนเคส'},
                         color_discrete_sequence=['#5c6bc0'])
            fig.update_layout(margin=dict(t=10, b=30, l=40, r=10), height=200)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("ยังไม่มีข้อมูลสำหรับพยาบาลที่เลือก")

    # Lock button
    if st.button("🔒 ล็อคอีกครั้ง", key="nurse_lock_btn"):
        st.session_state['nurse_unlocked'] = False
        st.rerun()


# ============================================================================
# MAIN PAGE
# ============================================================================

def _render_historical_analytics(date_from: str, date_to: str):
    """Tab stats yonlang - 4 metric cards + 4 charts + export."""

    data = get_historical_analytics(date_from, date_to)

    if data['total_cases'] == 0:
        st.info("ยังไม่มีข้อมูลเคสที่เสร็จแล้วในช่วงนี้ — เริ่มใช้งานแล้วสถิติจะสะสมอัตโนมัติ")
        return

    # -- Metric cards --
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">เคสรวม</div>
            <div class="kpi-value" style="color:#1565c0;">{data['total_cases']}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        peak_label = '—'
        if data['peak_date']:
            try:
                dt = datetime.strptime(data['peak_date'], '%Y-%m-%d')
                _THAI_DAY = ['จ.','อ.','พ.','พฤ.','ศ.','ส.','อา.']
                peak_label = f"{_THAI_DAY[dt.weekday()]} {dt.strftime('%d/%m')}"
            except (ValueError, TypeError):
                peak_label = str(data['peak_date'])
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">วันที่เคสเยอะสุด</div>
            <div class="kpi-value" style="color:#1565c0;font-size:22px;">{peak_label}</div>
            <div style="font-size:12px;color:#999;">{data['peak_count']} เคส</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        # Peak-hour KPI now reports OR-occupancy minutes (Level-3 utilization)
        _phc = data['peak_hour_count']
        if _phc >= 60:
            _phc_label = f"{_phc//60} ชม. {_phc%60} นาที"
        else:
            _phc_label = f"{_phc} นาที"
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">ช่วงยุ่งสุด</div>
            <div class="kpi-value" style="color:#e65100;font-size:22px;">{data['peak_hour']:02d}:00</div>
            <div style="font-size:12px;color:#999;">ห้องถูกใช้ {_phc_label}</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">สาขาเยอะสุด</div>
            <div class="kpi-value" style="color:#6a1b9a;font-size:16px;">{data['top_div_name']}</div>
            <div style="font-size:12px;color:#999;">{data['top_div_count']} เคส ({data['top_div_pct']}%)</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # -- Chart 1: cases per day --
    st.markdown('<div class="section-title">📊 จำนวนเคสรายวัน</div>', unsafe_allow_html=True)
    daily = data['daily_total']
    if not daily.empty:
        daily = daily.copy()
        daily['date_label'] = pd.to_datetime(daily['op_date']).dt.strftime('%d/%m')
        fig = px.bar(daily, x='date_label', y='n_cases',
                     labels={'date_label': 'วันที่', 'n_cases': 'จำนวนเคส'},
                     color_discrete_sequence=['#42a5f5'])
        fig.update_layout(
            margin=dict(t=10, b=40, l=40, r=10), height=260,
            xaxis_title='วันที่', yaxis_title='จำนวนเคส',
            xaxis=dict(tickangle=-45),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("ยังไม่มีข้อมูลรายวัน")

    # -- Chart 2 & 3: Heatmap + Division --
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown('<div class="section-title">🔥 ช่วงเวลาที่ยุ่ง (นาทีที่ห้องถูกใช้)</div>',
                    unsafe_allow_html=True)
        hm = data['heatmap_df']
        if not hm.empty:
            _THAI_DAYS = ['จันทร์','อังคาร','พุธ','พฤหัสฯ','ศุกร์','เสาร์','อาทิตย์']
            pivot = hm.pivot_table(index='dow', columns='hour', values='n',
                                   fill_value=0, aggfunc='sum')
            for d in range(5):
                if d not in pivot.index:
                    pivot.loc[d] = 0
            for h in range(8, 17):
                if h not in pivot.columns:
                    pivot[h] = 0
            pivot = pivot.reindex(index=range(5), columns=range(8, 17), fill_value=0)

            fig = go.Figure(data=go.Heatmap(
                z=pivot.values,
                x=[f'{h}:00' for h in range(8, 17)],
                y=[_THAI_DAYS[i] for i in range(5)],
                colorscale='OrRd',
                colorbar=dict(title='นาที'),
                hovertemplate='%{y} %{x}<br>ห้องถูกใช้: %{z} นาที<extra></extra>',
            ))
            fig.update_layout(
                margin=dict(t=10, b=10, l=80, r=10), height=240,
                xaxis_title='ชั่วโมง', yaxis=dict(autorange='reversed'),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "💡 นับ **นาทีที่ห้องถูกใช้** (room-in → room-out) "
                "กระจายลงในแต่ละ hour bucket ตามเวลาจริง — เคสยาวจะถ่วงน้ำหนักมากกว่าเคสสั้น"
            )
        else:
            st.caption("ยังไม่มีข้อมูลเวลา (ต้องมีทั้ง in_or_at และ op_end_at)")

    with col_right:
        st.markdown('<div class="section-title">🏥 สาขาที่ผ่าตัดเยอะ</div>', unsafe_allow_html=True)
        div_df = data['div_df']
        if not div_df.empty:
            fig = px.bar(div_df.head(8), x='n', y='division_name', orientation='h',
                         labels={'n': 'จำนวนเคส', 'division_name': 'สาขา'},
                         color_discrete_sequence=['#7e57c2'])
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10), height=240,
                yaxis=dict(autorange='reversed'),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("ยังไม่มีข้อมูลสาขา")

    # -- Chart 4: Top procedures (with fuzzy grouping) --
    st.markdown('<div class="section-title">🔬 Top หัตถการที่ทำบ่อย</div>', unsafe_allow_html=True)
    proc_df = data['proc_df']
    if not proc_df.empty:
        # รวมหัตถการที่คล้ายกัน เช่น Off PERM/Off TCC, nail extraction, excision/Excision
        proc_show = group_top_procedures(proc_df, top_n=10).copy()
        proc_show['label'] = proc_show['procedure_name'].str[:40]
        proc_show['avg_min'] = proc_show['avg_min'].fillna(0).round(0).astype(int)
        fig = px.bar(proc_show, x='n', y='label', orientation='h',
                     text='n',
                     labels={'n': 'จำนวนเคส', 'label': 'หัตถการ'},
                     color_discrete_sequence=['#26a69a'],
                     hover_data={'avg_min': True})
        fig.update_layout(
            margin=dict(t=10, b=10, l=10, r=10), height=max(240, len(proc_show) * 32),
            yaxis=dict(autorange='reversed'),
        )
        fig.update_traces(textposition='outside')
        st.plotly_chart(fig, use_container_width=True)
        st.caption("💡 ระบบรวมหัตถการที่คล้ายกันโดยอัตโนมัติ (เช่น Off PERM/TCC, nail extraction)")
    else:
        st.caption("ยังไม่มีข้อมูลหัตถการ")

    # -- Wait time + Handover trends --
    col_wt, col_ho = st.columns(2)

    with col_wt:
        st.markdown('<div class="section-title">⏱️ เวลารอผู้ป่วย</div>', unsafe_allow_html=True)
        wt = get_wait_stats(date_from, date_to)
        # KPI row
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("เฉลี่ยรอ", f"{wt['avg_all']} นาที")
        with m2:
            st.metric("นานสุด", f"{int(wt['max_all'])} นาที")
        with m3:
            st.metric("รอ >60 นาที", f"{wt['over_60']} เคส")
        # Chart: avg wait per day
        dw = wt['daily_wait']
        if not dw.empty:
            dw = dw.copy()
            dw['date_label'] = pd.to_datetime(dw['op_date']).dt.strftime('%d/%m')
            fig = px.line(dw, x='date_label', y='avg_wait',
                          labels={'date_label': 'วันที่', 'avg_wait': 'เฉลี่ย (นาที)'},
                          markers=True, color_discrete_sequence=['#e53935'])
            fig.add_hline(y=60, line_dash='dash', line_color='#c62828',
                          annotation_text='60 นาที')
            fig.update_layout(margin=dict(t=10, b=40, l=40, r=10), height=240,
                              xaxis=dict(tickangle=-45))
            st.plotly_chart(fig, use_container_width=True)

    with col_ho:
        st.markdown('<div class="section-title">🔄 สถิติรับเวร (หลัง 15:30)</div>',
                    unsafe_allow_html=True)
        ho = get_handover_stats(date_from, date_to)
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("เคสรับเวร", f"{ho['n_handover']} เคส")
        with m2:
            st.metric("จากทั้งหมด", f"{ho['total']} เคส")
        with m3:
            st.metric("สัดส่วน", f"{ho['pct']}%")
        # Chart: handover per day
        dh = ho['daily_handover']
        if not dh.empty:
            dh = dh.copy()
            dh['date_label'] = pd.to_datetime(dh['op_date']).dt.strftime('%d/%m')
            fig = px.bar(dh, x='date_label', y='n_handover',
                         labels={'date_label': 'วันที่', 'n_handover': 'เคสรับเวร'},
                         color_discrete_sequence=['#ef6c00'])
            fig.update_layout(margin=dict(t=10, b=40, l=40, r=10), height=240,
                              xaxis=dict(tickangle=-45))
            st.plotly_chart(fig, use_container_width=True)

        # Table: handover cases
        hc = ho['handover_cases']
        if not hc.empty:
            with st.expander(f"📋 รายชื่อเคสรับเวร ({len(hc)} เคส)"):
                show_cols = ['op_date', 'name', 'procedure_name', 'status', 'discharged_at']
                col_rename = {'op_date': 'วันที่', 'name': 'ชื่อ',
                              'procedure_name': 'หัตถการ', 'status': 'สถานะ',
                              'discharged_at': 'เวลา discharge'}
                if 'division_name' in hc.columns:
                    show_cols.insert(3, 'division_name')
                    col_rename['division_name'] = 'สาขา'
                st.dataframe(hc[show_cols].rename(columns=col_rename),
                             use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # -- Summary KPI (สะสม) --
    st.markdown('<div class="section-title">📋 สรุปยอดสะสม</div>', unsafe_allow_html=True)
    s_all = get_summary(date_from=date_from, date_to=date_to)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("เคสทั้งหมด", s_all['total'])
    k2.metric("ผ่าเสร็จ", s_all['completed'])
    k3.metric("ยกเลิก", s_all['cancelled'])
    cancel_r = s_all['cancelled'] / s_all['total'] * 100 if s_all['total'] > 0 else 0
    k4.metric("อัตรายกเลิก", "%.0f%%" % cancel_r)

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("OPD", s_all['n_opd'])
    k6.metric("IPD", s_all['n_ipd'])
    k7.metric("เคสนัดหมาย", s_all['n_set'])
    k8.metric("Walk-in", s_all['n_walkin'])

    k9, k10, k11, k12 = st.columns(4)
    k9.metric("💰 ค่าหัตถการ", f"{s_all['total_treatment']:,} ฿")
    k10.metric("💵 รายได้รวม", f"{s_all['total_revenue']:,} ฿")
    k11.metric("🧬 ส่งชิ้นเนื้อ", f"{s_all['n_patho_sent']} ราย")
    k12.metric("🔬 ค่าชิ้นเนื้อ", f"{s_all['total_patho']:,} ฿")

    # -- นอกเวลา สะสม --
    st.markdown('<div class="section-title">🌙 เคสนอกเวลา (สะสม)</div>', unsafe_allow_html=True)
    df_range = get_cases()
    df_range = df_range[
        (df_range['op_date'] >= date_from) &
        (df_range['op_date'] <= date_to)
    ]
    aft_range = df_range[df_range['patient_type'] == 'นอกเวลา'].copy()
    if aft_range.empty:
        st.info("ไม่มีเคสนอกเวลาในช่วงนี้")
    else:
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("เคสนอกเวลา", len(aft_range))
        a2.metric("ยืนยันแล้ว", len(aft_range[aft_range['status'] == 'discharged']))
        a3.metric("ยกเลิก", len(aft_range[aft_range['status'] == 'cancelled']))
        a4.metric("💰 รายได้", f"{int(aft_range['treatment_cost'].fillna(0).sum()):,} ฿")

    st.markdown("<br>", unsafe_allow_html=True)

    # -- Export --
    st.markdown('<div class="section-title">💾 Export ข้อมูล</div>', unsafe_allow_html=True)
    st.caption("ดาวน์โหลดข้อมูลสำหรับผู้บริหารหรือวิทยานิพนธ์")
    col_e1, col_e2 = st.columns([1, 3])
    with col_e1:
        export_scope = st.radio("ช่วง export", ["ตามที่เลือก", "ทั้งหมด"],
                                horizontal=True, key="export_scope",
                                label_visibility='collapsed')
    with col_e2:
        if export_scope == "ทั้งหมด":
            exp_from, exp_to = None, None
        else:
            exp_from, exp_to = date_from, date_to

        df_export = export_cases_csv(exp_from, exp_to)
        if not df_export.empty:
            dl_a, dl_b = st.columns(2)
            with dl_a:
                xlsx_data = export_summary_excel(exp_from, exp_to)
                fname_xlsx = f"minor_or_summary_{_now_bkk().strftime('%Y%m%d_%H%M')}.xlsx"
                st.download_button(
                    label=f"📊 สรุปสถิติ (Excel+กราฟ)",
                    data=xlsx_data,
                    file_name=fname_xlsx,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
            with dl_b:
                csv_bytes = df_export.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
                fname_csv = f"minor_or_data_{_now_bkk().strftime('%Y%m%d_%H%M')}.csv"
                st.download_button(
                    label=f"📥 ข้อมูลดิบ (CSV — {len(df_export)} เคส)",
                    data=csv_bytes,
                    file_name=fname_csv,
                    mime='text/csv',
                )
        else:
            st.caption("ไม่มีข้อมูลให้ export")


def _render_after_hours_admin(op_date: str):
    """แสดงสรุปเคสนอกเวลาในหน้า Admin."""
    df = get_cases(op_date=op_date)
    if df.empty:
        st.info("ไม่มีเคสนอกเวลา")
        return

    aft = df[df['patient_type'] == 'นอกเวลา'].copy()
    if aft.empty:
        st.info("ไม่มีเคสนอกเวลา")
        return

    n_total = len(aft)
    n_done = len(aft[aft['status'] == 'discharged'])
    n_cancel = len(aft[aft['status'] == 'cancelled'])
    n_pending = n_total - n_done - n_cancel
    revenue = int(aft['treatment_cost'].fillna(0).sum())

    # Metrics
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("เคสนอกเวลา", n_total)
    a2.metric("ยืนยันแล้ว", n_done)
    a3.metric("ยกเลิก", n_cancel)
    a4.metric("💰 รายได้", f"{revenue:,} ฿")

    if n_pending > 0:
        st.caption(f"⏳ รอดำเนินการ {n_pending} เคส")

    # Top procedures
    done_aft = aft[aft['status'] == 'discharged']
    if not done_aft.empty:
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("**หัตถการนอกเวลา**")
            proc_counts = done_aft['procedure_name'].str.upper().value_counts().head(5)
            for proc_name, n in proc_counts.items():
                st.markdown(f"- {proc_name} — {n} ราย")
        with col_r:
            st.markdown("**แพทย์นอกเวลา**")
            surg_counts = done_aft['surgeon_name'].value_counts().head(5)
            for surg, n in surg_counts.items():
                st.markdown(f"- {surg} — {n} ราย")


def page_admin():
    """หน้าบริหารจัดการ — สำหรับหัวหน้าพยาบาล / ผู้บริหาร."""
    st.markdown(_ADMIN_CSS, unsafe_allow_html=True)

    today = _now_bkk().strftime('%Y-%m-%d')

    # Header
    st.markdown(f"""
    <div class="admin-header">
        <h1>📊 บริหารจัดการห้องผ่าตัดเล็ก</h1>
        <p>ข้อมูล ณ วันที่ {_now_bkk().strftime('%d/%m/%Y เวลา %H:%M น.')}</p>
    </div>
    """, unsafe_allow_html=True)

    # ===== TABS =====
    tab_today, tab_history = st.tabs(["📋 ภาพรวมวันนี้", "📈 สถิติย้อนหลัง"])

    # -- TAB 1: Today overview --
    with tab_today:
        op_date = _now_bkk().strftime('%Y-%m-%d')

        # =========================================================
        # Section: เคสในเวลา
        # =========================================================
        st.markdown(
            '<div style="background:linear-gradient(135deg,#e8f5e9,#c8e6c9);'
            'border-radius:10px;padding:10px 16px;margin:12px 0 8px;">'
            '<span style="font-size:16px;font-weight:700;color:#2e7d32;">'
            '🏥 เคสในเวลา</span>'
            '<span style="font-size:12px;color:#388e3c;margin-left:8px;">'
            'Full OR Flow + AI Prediction</span></div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="section-title">🏥 สถานะห้องผ่าตัด</div>', unsafe_allow_html=True)
        rooms = get_room_status(op_date)
        _render_room_cards(rooms)


        _thai_months = ['','ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.']
        _today_dt = _now_bkk()
        _thai_date = f"{_today_dt.day} {_thai_months[_today_dt.month]} {_today_dt.year + 543}"
        st.markdown(f'<div class="section-title">📈 ตัวเลขสำคัญ — {_thai_date}</div>', unsafe_allow_html=True)
        kpi = get_kpi(op_date)
        _render_kpi(kpi)

        if kpi['total'] > 0:
            progress = kpi['done'] / kpi['total']
            st.markdown(f"""
            <div style="margin:12px 0 4px;">
                <div style="display:flex;justify-content:space-between;font-size:13px;color:#333;font-weight:700;">
                    <span>ความคืบหน้าวันนี้</span>
                    <span>{kpi['done']}/{kpi['total']} เคส ({progress:.0%})</span>
                </div>
                <div style="background:#e0e0e0;border-radius:6px;height:12px;margin-top:4px;">
                    <div style="background:linear-gradient(90deg,#43a047,#66bb6a);
                                height:100%;width:{progress*100:.0f}%;border-radius:6px;
                                transition:width 0.5s;"></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown('<div class="section-title">⚠️ แจ้งเตือน</div>', unsafe_allow_html=True)
        alerts = get_delay_alerts(op_date)
        _render_alerts(alerts)

        st.markdown('<div class="section-title">👥 ภาระงาน</div>', unsafe_allow_html=True)
        wl = get_workload(op_date)
        _render_workload(wl)

        st.markdown('<div class="section-title">🔍 Progress รายบุคคล</div>', unsafe_allow_html=True)
        _render_nurse_progress(op_date)

        with st.expander("🤖 AI Prediction Accuracy (สำหรับวิจัย)", expanded=False):
            _render_ai_accuracy(op_date)

        # =========================================================
        # Section: สถิติรับเวร (วันนี้)
        # =========================================================
        st.markdown('<div class="section-title">🔄 สถิติรับเวร (หลัง 15:30 น.)</div>',
                    unsafe_allow_html=True)
        ho_today = get_handover_stats(op_date, op_date)
        if ho_today['n_handover'] > 0:
            st.markdown(f"""
            <div style="background:#fff3e0;border-left:4px solid #ef6c00;
                        padding:10px 14px;border-radius:6px;margin-bottom:8px;">
                <span style="font-weight:700;color:#e65100;">
                    {ho_today['n_handover']} เคส</span>
                <span style="color:#666;font-size:13px;">
                    จากทั้งหมด {ho_today['total']} เคส
                    ({ho_today['pct']}%) — ยังไม่ discharge ก่อน 15:30 น.</span>
            </div>""", unsafe_allow_html=True)
            ho_df = ho_today['handover_cases']
            for _, r in ho_df.iterrows():
                dc_time = ''
                if r.get('discharged_at'):
                    dc_time = r['discharged_at'][11:16]
                    lbl = f"discharge {dc_time}"
                else:
                    lbl = f"สถานะ: {r['status']}"
                st.markdown(f"""
                <div style="background:var(--bg-secondary-color,#f5f5f5);
                            border-radius:6px;padding:8px 12px;margin:4px 0;
                            font-size:13px;border:1px solid var(--border-color,#e0e0e0);">
                    <b>{r.get('name','')}</b> — {r.get('procedure_name','')}
                    <span style="float:right;color:#ef6c00;font-weight:600;">{lbl}</span>
                </div>""", unsafe_allow_html=True)
        else:
            st.success("ไม่มีเคสรับเวรวันนี้ — ทุกเคส discharge ก่อน 15:30 น.")

        # =========================================================
        # Section: ผู้ป่วยรอนาน (วันนี้)
        # =========================================================
        st.markdown('<div class="section-title">⏱️ ผู้ป่วยรอนานเกิน 60 นาที</div>',
                    unsafe_allow_html=True)
        wt_today = get_wait_stats(op_date, op_date)
        if wt_today['over_60'] > 0:
            st.markdown(f"""
            <div style="background:#fce4ec;border-left:4px solid #c62828;
                        padding:10px 14px;border-radius:6px;margin-bottom:8px;">
                <span style="font-weight:700;color:#c62828;">
                    {wt_today['over_60']} เคส</span>
                <span style="color:#666;font-size:13px;">
                    รอเกิน 60 นาที — เฉลี่ยรอ {wt_today['avg_all']} นาที,
                    นานสุด {wt_today['max_all']} นาที</span>
            </div>""", unsafe_allow_html=True)
        else:
            if wt_today['total'] > 0:
                st.success(f"ไม่มีเคสรอเกิน 60 นาที — เฉลี่ยรอ {wt_today['avg_all']} นาที")
            else:
                st.info("ยังไม่มีข้อมูลเวลารอ")

        # =========================================================
        # Section: เคสนอกเวลา
        # =========================================================
        st.markdown("")
        st.markdown("")
        st.markdown(
            '<div style="background:linear-gradient(135deg,#fce4ec,#f8bbd0);'
            'border-radius:10px;padding:10px 16px;margin:12px 0 8px;">'
            '<span style="font-size:16px;font-weight:700;color:#c62828;">'
            '🌙 เคสนอกเวลา</span>'
            '<span style="font-size:12px;color:#d32f2f;margin-left:8px;">'
            'ยืนยัน / ยกเลิก เท่านั้น</span></div>',
            unsafe_allow_html=True,
        )
        _render_after_hours_admin(op_date)

    # -- TAB 2: Historical analytics --
    with tab_history:
        today_dt = _now_bkk().date()
        today_dt = _now_bkk().date()

        # --- Quick preset buttons ---
        preset = st.radio(
            "ช่วงเวลา", ["7 วัน", "30 วัน", "90 วัน", "กำหนดเอง"],
            horizontal=True, key="hist_period", label_visibility='collapsed',
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
                                     max_value=today_dt, key="hist_from")

        with col_to:
            sel_to = st.date_input("📅 วันที่สิ้นสุด", value=default_to,
                                   max_value=today_dt, key="hist_to")

        if sel_from > sel_to:
            st.warning("⚠️ วันที่เริ่มต้นต้องไม่เกินวันที่สิ้นสุด")
            return

        d_from = sel_from.strftime('%Y-%m-%d')
        d_to = sel_to.strftime('%Y-%m-%d')

        if st.button("📊 แสดงสถิติ", type="primary", use_container_width=True, key="btn_show_hist"):
            st.session_state['hist_show'] = True
            st.session_state['hist_range'] = (d_from, d_to)

        if st.session_state.get('hist_show') and st.session_state.get('hist_range'):
            _render_historical_analytics(*st.session_state['hist_range'])

        # =========================================================
        # Section: เครื่องมือจัดการข้อมูล (Maintenance tools)
        # =========================================================
        st.markdown("---")
        st.markdown(
            '<div style="background:linear-gradient(135deg,#fff3e0,#ffe0b2);'
            'border-radius:10px;padding:12px 16px;margin:16px 0 8px;'
            'border-left:4px solid #ef6c00;">'
            '<span style="font-size:18px;font-weight:700;color:#e65100;">'
            '🛠️ เครื่องมือจัดการข้อมูล (Maintenance)</span>'
            '<div style="font-size:12px;color:#bf360c;margin-top:4px;">'
            'สำหรับ admin/หัวหน้าพยาบาล: แก้ไขข้อมูลที่ import มาผิด '
            'โดยไม่ต้องลบ DB — ต้องอัพโหลด CSV ต้นทาง'
            '</div></div>',
            unsafe_allow_html=True,
        )
        with st.expander("🔧 ① Reclassify case_category (เคสนัดหมาย ↔ Walk-in)",
                         expanded=False):
            st.caption(
                "ใช้สำหรับแก้ข้อมูลเก่าที่ import มาแล้ว category ผิด "
                "(เช่น เคสที่ควรเป็น Walk-in แต่ถูกตั้งเป็น 'เคสนัดหมาย') "
                "โดยอ่านวันนัด (reqdate) จาก scheduling CSV เทียบกับวันผ่าตัด (opedate)"
            )
            csv_file = st.file_uploader(
                "📄 อัพโหลด scheduling CSV (ต้องมี columns: hn, reqdate, opedate)",
                type=['csv'], key="reclassify_csv"
            )
            if csv_file is not None:
                colA, colB = st.columns(2)
                with colA:
                    btn_preview = st.button(
                        "🔍 Preview (Dry-run)", use_container_width=True,
                        key="btn_reclassify_preview"
                    )
                with colB:
                    btn_apply = st.button(
                        "✅ Apply (อัพเดต DB)", type="primary",
                        use_container_width=True, key="btn_reclassify_apply"
                    )

                if btn_preview or btn_apply:
                    import io
                    csv_file.seek(0)
                    csv_bytes = csv_file.read()
                    try:
                        # Detect encoding (utf-16 from MSSQL export, else utf-8)
                        try:
                            df_csv = pd.read_csv(io.BytesIO(csv_bytes), encoding='utf-16')
                        except (UnicodeError, UnicodeDecodeError):
                            df_csv = pd.read_csv(io.BytesIO(csv_bytes), encoding='utf-8')

                        required = {'hn', 'reqdate', 'opedate'}
                        missing = required - set(df_csv.columns)
                        if missing:
                            st.error(f"❌ CSV ขาด columns: {missing}")
                        else:
                            # Run reclassify (import only when used)
                            from import_historical import reclassify_existing
                            # Save uploaded CSV to a temp file (reclassify_existing reads from path)
                            import tempfile, os as _os
                            with tempfile.NamedTemporaryFile(
                                delete=False, suffix='.csv', mode='wb') as tmp:
                                tmp.write(csv_bytes)
                                tmp_path = tmp.name
                            try:
                                info = reclassify_existing(tmp_path, dry_run=not btn_apply)
                            finally:
                                _os.unlink(tmp_path)

                            mode_label = "✅ Applied" if btn_apply else "🔍 Preview"
                            st.success(
                                f"{mode_label} — เปลี่ยนเป็น Walk-in: {info['set_to_walkin']}, "
                                f"เป็นเคสนัดหมาย: {info['set_to_scheduled']}, "
                                f"ไม่เปลี่ยน: {info['unchanged']}, "
                                f"ไม่เจอใน DB: {info['not_found']}"
                            )

                            # Show sample of changes
                            sample_rows = []
                            for hn, od, rd, old, new in info['samples'][:50]:
                                if old != new:
                                    sample_rows.append({
                                        'HN': hn, 'op_date': od, 'reqdate': rd or '-',
                                        'เดิม': old or '-', 'ใหม่': new,
                                    })
                            if sample_rows:
                                st.markdown("**ตัวอย่างเคสที่จะเปลี่ยน:**")
                                st.dataframe(pd.DataFrame(sample_rows),
                                             use_container_width=True, hide_index=True)

                            if btn_apply:
                                st.info("🔄 กดปุ่ม Rerun (R) เพื่อโหลดข้อมูลใหม่")
                    except Exception as e:
                        st.error(f"❌ Error: {e}")

        # =========================================================
        # Section: Re-import room timestamps (จาก intraop CSV)
        # =========================================================
        with st.expander("⏱️ ② Re-import room timestamps (in_or_at / op_end_at)",
                         expanded=False):
            st.caption(
                "รีเฟรชค่า in_or_at / op_end_at ของเคสที่มีอยู่ใน DB จาก intraop CSV "
                "(`รอลบ.csv`) — ใช้ **roomtimein** / **roomtimeout** เป็นเวลาห้อง "
                "(เดิมใช้ opesttime/opendtime ที่เป็น incision/closure ทำให้ heatmap "
                "'ช่วงเวลาที่ยุ่ง' ไม่ตรงกับ OR utilization จริง)"
            )
            intra_file = st.file_uploader(
                "📄 อัพโหลด intraop CSV (ต้องมี columns: hn, opedate, "
                "roomtimein, roomtimeout, arrivtime, opusetime)",
                type=['csv'], key="reimport_intra_csv"
            )
            if intra_file is not None:
                colA2, colB2 = st.columns(2)
                with colA2:
                    btn_preview2 = st.button(
                        "🔍 Preview (Dry-run)", use_container_width=True,
                        key="btn_reimport_preview"
                    )
                with colB2:
                    btn_apply2 = st.button(
                        "✅ Apply (อัพเดต DB)", type="primary",
                        use_container_width=True, key="btn_reimport_apply"
                    )

                if btn_preview2 or btn_apply2:
                    import io
                    intra_file.seek(0)
                    intra_bytes = intra_file.read()
                    try:
                        try:
                            df_intra = pd.read_csv(io.BytesIO(intra_bytes), encoding='utf-16')
                        except (UnicodeError, UnicodeDecodeError):
                            df_intra = pd.read_csv(io.BytesIO(intra_bytes), encoding='utf-8')

                        required = {'hn', 'opedate', 'roomtimein', 'roomtimeout'}
                        missing = required - set(df_intra.columns)
                        if missing:
                            st.error(f"❌ CSV ขาด columns: {missing}")
                        else:
                            from import_historical import reimport_timestamps
                            import tempfile, os as _os
                            with tempfile.NamedTemporaryFile(
                                delete=False, suffix='.csv', mode='wb') as tmp:
                                tmp.write(intra_bytes)
                                tmp_path = tmp.name
                            try:
                                info = reimport_timestamps(
                                    tmp_path, dry_run=not btn_apply2)
                            finally:
                                _os.unlink(tmp_path)

                            mode_label = "✅ Applied" if btn_apply2 else "🔍 Preview"
                            st.success(
                                f"{mode_label} — เคสที่จะเปลี่ยน: {info['changed']}, "
                                f"ไม่เจอใน DB: {info['not_found']}"
                            )
                            sample_rows = []
                            for s in info['samples'][:50]:
                                if s['changed']:
                                    sample_rows.append({
                                        'HN': s['hn'], 'op_date': s['op_date'],
                                        'in_or_at เดิม': s['old_in_or'] or '-',
                                        'in_or_at ใหม่': s['new_in_or'] or '-',
                                        'op_end_at เดิม': s['old_op_end'] or '-',
                                        'op_end_at ใหม่': s['new_op_end'] or '-',
                                    })
                            if sample_rows:
                                st.markdown("**ตัวอย่างเคสที่จะเปลี่ยน:**")
                                st.dataframe(pd.DataFrame(sample_rows),
                                             use_container_width=True, hide_index=True)
                            if btn_apply2:
                                st.info("🔄 กดปุ่ม Rerun (R) เพื่อโหลดข้อมูลใหม่")
                    except Exception as e:
                        st.error(f"❌ Error: {e}")

    # Auto refresh hint
    st.markdown("""
    <div style="text-align:center;margin-top:24px;padding:8px;color:#9e9e9e;font-size:11px;">
        💡 กด <b>R</b> หรือ <b>F5</b> เพื่อรีเฟรชข้อมูล
    </div>
    """, unsafe_allow_html=True)
