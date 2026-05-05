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
    get_historical_analytics, export_cases_csv, get_cases,
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
        st.markdown('<div class="section-title">🔥 ช่วงเวลาที่ยุ่ง</div>', unsafe_allow_html=True)
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
                hovertemplate='%{y} %{x}<br>เคส: %{z}<extra></extra>',
            ))
            fig.update_layout(
                margin=dict(t=10, b=10, l=80, r=10), height=240,
                xaxis_title='ชั่วโมง', yaxis=dict(autorange='reversed'),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("ยังไม่มีข้อมูลเวลา")

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

    # -- Chart 4: Top procedures --
    st.markdown('<div class="section-title">🔬 Top หัตถการที่ทำบ่อย</div>', unsafe_allow_html=True)
    proc_df = data['proc_df']
    if not proc_df.empty:
        proc_show = proc_df.head(10).copy()
        proc_show['label'] = proc_show['procedure_name'].str[:40]
        proc_show['avg_min'] = proc_show['avg_min'].round(0).astype(int)
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
    else:
        st.caption("ยังไม่มีข้อมูลหัตถการ")

    # -- Export CSV --
    st.markdown('<div class="section-title">💾 Export ข้อมูล</div>', unsafe_allow_html=True)
    st.caption("ดาวน์โหลดข้อมูลเคสเป็น CSV เพื่อ backup หรือใช้ในวิทยานิพนธ์")
    col_e1, col_e2 = st.columns([1, 3])
    with col_e1:
        export_scope = st.radio("ช่วง export", ["ตามที่เลือก", "ทั้งหมด"],
                                horizontal=True, key="export_scope",
                                label_visibility='collapsed')
    with col_e2:
        if export_scope == "ทั้งหมด":
            df_export = export_cases_csv()
        else:
            df_export = export_cases_csv(date_from, date_to)
        if not df_export.empty:
            csv_bytes = df_export.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
            fname = f"minor_or_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            st.download_button(
                label=f"📥 Download CSV ({len(df_export)} เคส)",
                data=csv_bytes,
                file_name=fname,
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

    today = datetime.now().strftime('%Y-%m-%d')

    # Header
    st.markdown(f"""
    <div class="admin-header">
        <h1>📊 บริหารจัดการห้องผ่าตัดเล็ก</h1>
        <p>ข้อมูล ณ วันที่ {datetime.now().strftime('%d/%m/%Y เวลา %H:%M น.')}</p>
    </div>
    """, unsafe_allow_html=True)

    # ===== TABS =====
    tab_today, tab_history = st.tabs(["📋 ภาพรวมวันนี้", "📈 สถิติย้อนหลัง"])

    # -- TAB 1: Today overview --
    with tab_today:
        # Date picker
        with st.expander("📅 เลือกวันที่", expanded=False):
            sel_date = st.date_input("วันที่", value=datetime.now().date(), key="admin_date")
            op_date = sel_date.strftime('%Y-%m-%d')

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

        st.markdown('<div class="section-title">📈 ตัวเลขสำคัญ</div>', unsafe_allow_html=True)
        kpi = get_kpi(op_date)
        _render_kpi(kpi)

        if kpi['total'] > 0:
            progress = kpi['done'] / kpi['total']
            st.markdown(f"""
            <div style="margin:12px 0 4px;">
                <div style="display:flex;justify-content:space-between;font-size:12px;color:#666;">
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

        st.markdown('<div class="section-title">👩‍⚕️ สถิติพยาบาล — Nurse Progress</div>', unsafe_allow_html=True)
        _render_nurse_progress(op_date)

        with st.expander("🤖 AI Prediction Accuracy (สำหรับวิจัย)", expanded=False):
            _render_ai_accuracy(op_date)

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
        period = st.radio(
            "เลือกช่วงเวลา", ["7 วัน", "30 วัน", "90 วัน", "ทั้งหมด"],
            horizontal=True, key="hist_period", label_visibility='collapsed',
        )
        today_dt = datetime.now().date()
        if period == "7 วัน":
            d_from = (today_dt - timedelta(days=6)).strftime('%Y-%m-%d')
            d_to = today_dt.strftime('%Y-%m-%d')
        elif period == "30 วัน":
            d_from = (today_dt - timedelta(days=29)).strftime('%Y-%m-%d')
            d_to = today_dt.strftime('%Y-%m-%d')
        elif period == "90 วัน":
            d_from = (today_dt - timedelta(days=89)).strftime('%Y-%m-%d')
            d_to = today_dt.strftime('%Y-%m-%d')
        else:
            d_from, d_to = None, None

        _render_historical_analytics(d_from, d_to)

    # Auto refresh hint
    st.markdown("""
    <div style="text-align:center;margin-top:24px;padding:8px;color:#9e9e9e;font-size:11px;">
        💡 กด <b>R</b> หรือ <b>F5</b> เพื่อรีเฟรชข้อมูล
    </div>
    """, unsafe_allow_html=True)
