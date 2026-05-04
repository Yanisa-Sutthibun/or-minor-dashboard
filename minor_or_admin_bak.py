"""
Minor OR Admin Dashboard — หน้าบริหารจัดการสำหรับหัวหน้า/ผู้บริหาร
ดูอย่างเดียว ไม่ต้องกดอะไร — เปิดมาเห็นภาพรวมทันที
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from datetime import timedelta
from minor_or_db import (
    get_room_status, get_kpi, get_delay_alerts, get_workload,
    get_summary, get_nurse_stats, div_name, DIV_CODE_MAP,
    get_historical_analytics, export_cases_csv,
)
import numpy as np


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
                        mins = int((datetime.now() - start).total_seconds() / 60)
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


def _render_nurse_progress(op_date: str):
    """แสดงสถิติพยาบาล — Novice Nurse Progress Tracking."""
    # เลือกช่วงเวลา: วันนี้ / 7 วัน / 30 วัน / ทั้งหมด
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
        st.info("ยังไม่มีข้อมูลพยาบาล — เลือกพยาบาล Scrub/Circ ตอนกดเข้าห้องผ่าตัดในหน้า Tracking")
        return

    # ---- Summary cards per nurse ----
    for _, nurse in summary.iterrows():
        name = nurse['nurse_name']
        total = int(nurse['total_cases'])
        n_scrub = int(nurse['n_scrub'])
        n_circ = int(nurse['n_circ'])
        n_procs = int(nurse['unique_procedures'])
        avg_dur = nurse['avg_duration']
        avg_txt = f"{avg_dur:.0f} นาที" if pd.notna(avg_dur) else "-"

        # Scrub vs Circ ratio bar
        scrub_pct = int(n_scrub / total * 100) if total > 0 else 0
        circ_pct = 100 - scrub_pct

        st.markdown(f"""
        <div style="background:white;border-radius:12px;padding:14px 18px;margin:8px 0;
                    box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid #5c6bc0;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <span style="font-size:15px;font-weight:700;color:#283593;">👩‍⚕️ {name}</span>
                    <span style="font-size:12px;color:#9e9e9e;margin-left:8px;">
                        {nurse['first_date'] if nurse['first_date'] != nurse['last_date'] else ''}{(' — ' + nurse['last_date']) if nurse['first_date'] != nurse['last_date'] else nurse['first_date']}</span>
                </div>
                <div style="font-size:22px;font-weight:700;color:#283593;">{total} <span style="font-size:12px;color:#999;">เคส</span></div>
            </div>
            <div style="display:flex;gap:16px;margin-top:8px;font-size:12px;">
                <span style="background:#e8eaf6;padding:3px 10px;border-radius:12px;color:#3949ab;">
                    🧤 Scrub {n_scrub}</span>
                <span style="background:#e0f2f1;padding:3px 10px;border-radius:12px;color:#00695c;">
                    📋 Circ {n_circ}</span>
                <span style="color:#666;">หัตถการ {n_procs} ชนิด</span>
                <span style="color:#666;">เฉลี่ย {avg_txt}</span>
            </div>
            <div style="display:flex;margin-top:6px;border-radius:4px;overflow:hidden;height:6px;">
                <div style="background:#5c6bc0;width:{scrub_pct}%;"></div>
                <div style="background:#26a69a;width:{circ_pct}%;"></div>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10px;color:#999;margin-top:2px;">
                <span>Scrub {scrub_pct}%</span><span>Circ {circ_pct}%</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ---- Detail table (expandable) ----
    with st.expander("📋 รายละเอียดเคสทั้งหมด", expanded=False):
        if not cases_df.empty:
            display_df = cases_df[['nurse_name', 'role', 'op_date', 'procedure_name',
                                    'surgeon_name', 'actual_duration_min', 'room_no']].copy()
            display_df.columns = ['พยาบาล', 'ตำแหน่ง', 'วันที่', 'หัตถการ',
                                  'แพทย์', 'เวลาจริง (นาที)', 'ห้อง']
            display_df['สาขา'] = cases_df['division_code'].apply(div_name)
            st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ---- Nurse filter for individual progress ----
    nurse_names = sorted(summary['nurse_name'].tolist())
    if len(nurse_names) > 1:
        with st.expander("🔍 ดู Progress รายบุคคล", expanded=False):
            sel_nurse = st.selectbox("เลือกพยาบาล", nurse_names, key="sel_nurse_detail")
            individual = cases_df[cases_df['nurse_name'] == sel_nurse].copy()
            if not individual.empty:
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


# ============================================================================
# MAIN PAGE
# ============================================================================

def _render_historical_analytics(date_from: str, date_to: str):
    """แท็บสถิติย้อนหลัง — 4 metric cards + 4 charts + export."""

    data = get_historical_analytics(date_from, date_to)

    if data['total_cases'] == 0:
        st.info("ยังไม่มีข้อมูลเคสที่เสร็จแล้วในช่วงนี้ — เริ่มใช้งานแล้วสถิติจะสะสมอัตโนมัติ")
        return

    # ── Metric cards ──
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
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">ช่วงยุ่งสุด</div>
            <div class="kpi-value" style="color:#e65100;font-size:22px;">{data['peak_hour']:02d}:00</div>
            <div style="font-size:12px;color:#999;">{data['peak_hour_count']} เคส</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">สาขาเยอะสุด</div>
            <div class="kpi-value" style="color:#6a1b9a;font-size:16px;">{data['top_div_name']}</div>
            <div style="font-size:12px;color:#999;">{data['top_div_count']} เคส ({data['top_div_pct']}%)</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Chart 1: เคสรายวัน ──
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
            xaxis_title='วันที�