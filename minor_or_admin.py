"""
Minor OR Admin Dashboard — หน้าบริหารจัดการสำหรับหัวหน้า/ผู้บริหาร
ดูอย่างเดียว ไม่ต้องกดอะไร — เปิดมาเห็นภาพรวมทันที
"""
import time
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
    # Procedure-name fuzzy normalization (moved to db layer for sharing
    # with predict_from_local_history). Same rules apply across heatmap +
    # AI prediction so groupings stay consistent.
    _normalize_procedure_name, _PROC_RULES,
)
import numpy as np
from difflib import SequenceMatcher


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

# ============================================================================
# Demo Mode — จำลอง 1 วันของห้องผ่าตัด ภายใน 5 นาที (real time)
# ใช้ session_state — ไม่บันทึก DB
# ============================================================================

# Timeline (นาทีจาก 8:00 AM): arr, in_or, op_end, dc, room, name, hn, dx, proc, surgeon, ai_min, override
_DEMO_CASES = [
    (15, 30, 60, 75, 1, 'นาย สมชาย ทดสอบ', 'DEMO001',
        'Lipoma at neck', 'Excision', 'นพ.เอ ทดสอบ', 30, None),
    (30, 45, 105, 120, 3, 'น.ส. มาลี ทดลองใช้', 'DEMO002',
        'Rt. Renal stone', 'ESWL', 'นพ.บี ทดสอบ', 60, None),
    (90, 120, 145, 160, 1, 'นาย สมศักดิ์ ทดสอบ', 'DEMO003',
        'Abscess Lt. arm', 'I+D', 'นพ.ซี ทดสอบ', 25, None),
    (150, None, None, None, 4, 'นาง พรรณี ทดลองใช้', 'DEMO004',
        'Mass at chest', 'Excision', 'นพ.เอ ทดสอบ', 30, 'cancelled'),
    (180, 210, 230, 240, 4, 'นาย วิชัย ทดสอบ', 'DEMO005',
        'ESRD', 'Off PERM', 'นพ.บี ทดสอบ', 20, None),
    (300, 330, 365, 380, 3, 'น.ส. กัญญา ทดลองใช้', 'DEMO006',
        'Melasma', 'Q-Switch', 'นพ.ดี ทดสอบ', 35, None),
    (480, 510, 560, 580, 1, 'นาย ปรีชา ทดสอบ', 'DEMO007',
        'Aging Face', 'Morpheus', 'นพ.ดี ทดสอบ', 50, None),  # นอกเวลา
]
_DEMO_END_MIN = 600  # 8:00 + 10hr = 18:00


def _demo_to_real_ts(sim_min, current_sim_min):
    """Map demo sim minute → real timestamp string ที่ render card คำนวณ
    elapsed ได้ถูกต้อง (now - timestamp = elapsed sim minutes)."""
    if sim_min is None or current_sim_min < sim_min:
        return None
    delta = current_sim_min - sim_min
    real_dt = _now_bkk() - timedelta(minutes=delta)
    return real_dt.strftime('%Y-%m-%d %H:%M:%S')


def _get_demo_rooms(current_sim_min):
    """Return rooms list (เหมือน get_room_status) สำหรับ demo mode."""
    rooms_data = {1: [], 3: [], 4: [], 5: []}

    for c in _DEMO_CASES:
        (arr_m, ior_m, end_m, dc_m, room, name, hn, dx, proc,
         surg, ai_min, override) = c

        # Determine status at current sim_min
        if override == 'cancelled':
            status = 'cancelled' if current_sim_min >= arr_m else 'scheduled'
        elif current_sim_min < arr_m:
            status = 'scheduled'
        elif ior_m and current_sim_min < ior_m:
            status = 'arrived'
        elif end_m and current_sim_min < end_m:
            status = 'in_or'
        elif dc_m and current_sim_min < dc_m:
            status = 'post_op'
        elif dc_m and current_sim_min >= dc_m:
            status = 'discharged'
        else:
            status = 'arrived'

        case = {
            'case_id': hn,
            'name': name,
            'hn': hn,
            'diagnosis': dx,
            'procedure_name': proc,
            'surgeon_name': surg,
            'status': status,
            'arrived_at': _demo_to_real_ts(arr_m, current_sim_min),
            'in_or_at': _demo_to_real_ts(ior_m, current_sim_min),
            'op_end_at': _demo_to_real_ts(end_m, current_sim_min),
            'discharged_at': _demo_to_real_ts(dc_m, current_sim_min),
            'ai_predicted_min': ai_min,
            'actual_duration_min': (
                (end_m - ior_m) if (end_m and ior_m
                                    and current_sim_min >= end_m) else None),
            '_ai_n_cases': 5,                    # mock
            '_ai_confidence': 'สูง',              # mock
            '_ai_source': 'local_history',
        }
        rooms_data[room].append(case)

    result = []
    for rm in [1, 3, 4, 5]:
        cases_in_rm = rooms_data[rm]
        active = [c for c in cases_in_rm if c['status'] == 'in_or']
        done = [c for c in cases_in_rm
                if c['status'] in ('post_op', 'discharged')]
        waiting = [c for c in cases_in_rm
                   if c['status'] in ('scheduled', 'arrived')]
        result.append({
            'room_no': rm,
            'total': len([c for c in cases_in_rm
                          if c['status'] != 'cancelled']),
            'done': len(done),
            'waiting': len(waiting),
            'active_case': active[0] if active else None,
            'cases': cases_in_rm,
        })
    return result


def _get_demo_kpi(current_sim_min):
    """Build KPI dict for demo mode (matches get_kpi schema)."""
    total = done = cancelled = in_or = pending = 0
    total_op_min = 0  # for utilization calc
    for c in _DEMO_CASES:
        arr_m, ior_m, end_m, dc_m, room, *_rest, override = c
        if override == 'cancelled':
            if current_sim_min >= arr_m:
                cancelled += 1
                total += 1
            continue
        if current_sim_min >= arr_m:
            total += 1
            # in_or right now?
            if ior_m and current_sim_min >= ior_m:
                if end_m and current_sim_min >= end_m:
                    done += 1
                    total_op_min += (end_m - ior_m)
                else:
                    in_or += 1
                    total_op_min += (current_sim_min - ior_m)
            else:
                pending += 1
        # else: not yet arrived

    # Utilization: total op minutes / (4 rooms × elapsed sim time)
    elapsed = max(current_sim_min, 1)
    utilization = round(total_op_min / (4 * elapsed) * 100, 0)

    return {
        'total': total, 'done': done, 'cancelled': cancelled,
        'in_or': in_or, 'pending': pending,
        'utilization': int(utilization),
        'avg_turnover': 12,  # mock
    }


def _render_cost_entry_tab():
    """Quick Cost Entry — เลือกวันแล้วกรอกราคาทีละหลายเคสในตารางเดียว."""
    import sqlite3
    from minor_or_db import DB_PATH

    st.markdown("### 💰 ใส่ราคาผ่าตัด + ราคา patho รายวัน")
    st.caption("เลือกวันแล้วแก้ราคาในตารางได้เลย — ระบบจะ suggest ราคาจากเคสคล้ายๆ ในอดีตให้")

    # ── Date picker — default = วันล่าสุดที่มีเคสยังไม่มี cost ──
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT op_date FROM cases
        WHERE (treatment_cost IS NULL OR treatment_cost = 0)
        ORDER BY op_date DESC LIMIT 1
    """)
    row = cur.fetchone()
    default_date = row[0] if row else _now_bkk().strftime('%Y-%m-%d')
    try:
        default_dt = datetime.strptime(default_date, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        default_dt = _now_bkk().date()

    col_d, col_btn = st.columns([2, 1])
    with col_d:
        target_date = st.date_input(
            "📅 เลือกวันที่",
            value=default_dt,
            format="YYYY/MM/DD",
            key='cost_entry_date',
        )
    target_iso = target_date.strftime('%Y-%m-%d')

    # ── Load cases ของวันนั้น ──
    cur.execute("""
        SELECT case_id, hn, name, procedure_name, surgeon_name, status,
               actual_duration_min, treatment_cost, patho_cost,
               in_or_at, case_category, patient_type
        FROM cases
        WHERE op_date = ?
        ORDER BY in_or_at
    """, (target_iso,))
    cases = cur.fetchall()

    if not cases:
        conn.close()
        st.info(f"ไม่พบเคสในวันที่ {target_iso}")
        return

    # ── Suggest costs จากอดีต (เคสคล้ายๆ) ──
    def _suggest_cost(proc_name):
        if not proc_name:
            return None, None, 0
        # Normalize keywords
        kw = proc_name.lower()
        for keyword in ['morpheus', 'excision bx', 'excision', 'i&d', 'i and d',
                        'eswl', 'tcc', 'stitch off', 'partial nail', 'correction',
                        'ssv ligation', 'midline', 'callus', 'biopsy']:
            if keyword.replace(' ', '').replace('&', '') in kw.replace(' ', '').replace('&', ''):
                r = cur.execute("""
                    SELECT AVG(treatment_cost), AVG(patho_cost), COUNT(*)
                    FROM cases
                    WHERE op_date < ? AND treatment_cost > 0
                      AND LOWER(procedure_name) LIKE ?
                """, (target_iso, f'%{keyword}%')).fetchone()
                if r and r[2]:
                    return (int(r[0] or 0), int(r[1] or 0), r[2])
        return None, None, 0

    # ── Build DataFrame for st.data_editor ──
    rows = []
    for c in cases:
        cid, hn, name, proc, surg, status, dur, treat, patho, tin, cat, ptype = c
        sug_treat, sug_patho, n_past = _suggest_cost(proc)
        tin_s = tin.split(' ')[1][:5] if tin else '-'
        rows.append({
            'case_id': cid,
            'HN': hn,
            'ชื่อ': (name or '-')[:25],
            'หัตถการ': (proc or '-')[:30],
            'แพทย์': (surg or '-')[:20],
            'เวลาเข้าห้อง': tin_s,
            'นาที': dur or 0,
            'สถานะ': 'ยกเลิก' if status == 'cancelled' else 'เสร็จ',
            'ราคาผ่า': int(treat or 0) if treat else (sug_treat or 0),
            'ราคา patho': int(patho or 0) if patho else (sug_patho or 0),
            'เคยคิด (อดีต)': f"~{sug_treat:,}/{sug_patho:,} ({n_past}เคส)" if sug_treat else "—",
        })
    conn.close()

    df = pd.DataFrame(rows)

    # ── Editor ──
    st.markdown(f"**📋 {len(df)} เคสในวันที่ {target_iso}** — กรอกตรงช่องราคาได้เลย")
    edited = st.data_editor(
        df,
        column_config={
            "case_id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "HN": st.column_config.TextColumn("HN", disabled=True, width="small"),
            "ชื่อ": st.column_config.TextColumn("ชื่อ", disabled=True),
            "หัตถการ": st.column_config.TextColumn("หัตถการ", disabled=True),
            "แพทย์": st.column_config.TextColumn("แพทย์", disabled=True),
            "เวลาเข้าห้อง": st.column_config.TextColumn("เข้าห้อง", disabled=True, width="small"),
            "นาที": st.column_config.NumberColumn("นาที", disabled=True, width="small"),
            "สถานะ": st.column_config.TextColumn("สถานะ", disabled=True, width="small"),
            "ราคาผ่า": st.column_config.NumberColumn(
                "💰 ราคาผ่า (บาท)",
                min_value=0, step=100, format="%d",
                help="กรอกราคาที่เก็บจริง (ค่าที่ suggest มาจากเคสคล้ายๆ อดีต)",
            ),
            "ราคา patho": st.column_config.NumberColumn(
                "🔬 patho (บาท)",
                min_value=0, step=50, format="%d",
            ),
            "เคยคิด (อดีต)": st.column_config.TextColumn("ราคาอ้างอิงอดีต", disabled=True),
        },
        hide_index=True,
        use_container_width=True,
        key=f'cost_editor_{target_iso}',
    )

    # ── Save button ──
    col_save, col_total = st.columns([1, 2])
    with col_save:
        save_clicked = st.button('💾 บันทึกทั้งหมด', type='primary', use_container_width=True)
    with col_total:
        total_treat = edited['ราคาผ่า'].sum()
        total_patho = edited['ราคา patho'].sum()
        st.metric("รวมรายได้วันนี้", f"{total_treat + total_patho:,} บาท",
                  delta=f"ผ่า {total_treat:,} + patho {total_patho:,}")

    if save_clicked:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        n_updated = 0
        for _, row in edited.iterrows():
            cur.execute(
                "UPDATE cases SET treatment_cost=?, patho_cost=? WHERE case_id=?",
                (int(row['ราคาผ่า']), int(row['ราคา patho']), int(row['case_id']))
            )
            n_updated += cur.rowcount
        conn.commit()
        conn.close()
        st.success(f"✅ บันทึกราคา {n_updated} เคสสำเร็จ (วันที่ {target_iso})")
        st.balloons()


def _render_demo_controls():
    """แสดง toggle + controls ของ Demo Mode. Return current sim_min หรือ None."""
    state = st.session_state.setdefault('demo', {
        'active': False, 'playing': True, 'speed': 1,
        'real_started': time.time(), 'paused_at_sim': 0.0,
    })

    col_t, col_info = st.columns([1, 3])
    with col_t:
        new_active = st.toggle(
            '🎬 Demo Mode', value=state['active'], key='demo_toggle',
            help='จำลองการทำงาน 1 วัน ภายใน 5 นาที — ไม่บันทึก DB จริง')
    if new_active != state['active']:
        state['active'] = new_active
        if new_active:
            state['real_started'] = time.time()
            state['paused_at_sim'] = 0.0
            state['playing'] = True
        st.rerun()

    if not state['active']:
        return None

    # Compute current sim_min
    if state['playing']:
        real_elapsed = time.time() - state['real_started']
        # 5 นาทีจริง = 600 นาทีจำลอง → 1 วินาทีจริง = 2 นาทีจำลอง
        sim_min = state['paused_at_sim'] + (real_elapsed * 2.0 * state['speed'])
    else:
        sim_min = state['paused_at_sim']

    # Cap at end of day
    sim_min = min(sim_min, _DEMO_END_MIN)
    if sim_min >= _DEMO_END_MIN and state['playing']:
        state['playing'] = False
        state['paused_at_sim'] = _DEMO_END_MIN

    # Display info
    sim_hour = 8 + sim_min / 60
    sim_time_str = f'{int(sim_hour):02d}:{int((sim_hour % 1) * 60):02d}'
    pct = sim_min / _DEMO_END_MIN * 100

    with col_info:
        st.markdown(f"""
        <div style="background:#fff3e0;border-radius:8px;padding:8px 12px;
                    border-left:4px solid #ef6c00;margin-top:6px;">
          <span style="font-size:13px;color:#e65100;font-weight:700;">
            🕐 เวลาจำลอง: <b>{sim_time_str}</b></span>
          <span style="font-size:11px;color:#bf360c;margin-left:12px;">
            ({sim_min:.0f}/{_DEMO_END_MIN} นาที · {pct:.0f}%)
            · 🔇 ไม่บันทึก DB จริง</span>
        </div>
        """, unsafe_allow_html=True)

    # Controls row
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        play_label = '⏸ หยุด' if state['playing'] else '▶ เล่น'
        if st.button(play_label, use_container_width=True, key='demo_play'):
            if state['playing']:
                # Pause: save current sim_min
                real_elapsed = time.time() - state['real_started']
                state['paused_at_sim'] += (real_elapsed * 2.0 * state['speed'])
                state['playing'] = False
            else:
                state['real_started'] = time.time()
                state['playing'] = True
            st.rerun()
    with c2:
        if st.button('⏹ รีเซ็ต', use_container_width=True, key='demo_reset'):
            state['real_started'] = time.time()
            state['paused_at_sim'] = 0.0
            state['playing'] = True
            st.rerun()
    with c3:
        new_speed = st.selectbox(
            'ความเร็ว', [1, 2, 5],
            index=[1, 2, 5].index(state['speed']),
            key='demo_speed_select', label_visibility='collapsed')
        if new_speed != state['speed']:
            real_elapsed = time.time() - state['real_started']
            state['paused_at_sim'] += (real_elapsed * 2.0 * state['speed'])
            state['real_started'] = time.time()
            state['speed'] = new_speed
            st.rerun()
    with c4:
        st.caption(
            "💡 เคสจะค่อย ๆ ผ่าน flow: รอ → เข้าห้อง → กำลังผ่า → เสร็จ "
            "(7 เคส รวม 1 cancel + 1 นอกเวลา)"
        )

    return sim_min


def _render_one_room_card(rm):
    """Render single room card (used in 2x2 grid)."""
    active = rm['active_case']

    if active:
        # กำลังผ่าตัด — แสดง diagnosis + AI bar + confidence
        elapsed_min = 0
        if active.get('in_or_at'):
            try:
                start = datetime.strptime(active['in_or_at'],
                                          '%Y-%m-%d %H:%M:%S')
                elapsed_min = int(
                    (_now_bkk() - start).total_seconds() / 60)
            except (ValueError, TypeError):
                pass

        ai_min = active.get('ai_predicted_min') or 0
        pct = int((elapsed_min / ai_min) * 100) if ai_min else 0
        # cap bar fill at 100% for display, but show actual % in label
        bar_width = min(pct, 100)

        # Bar color shifts subtly when over 100%
        bar_color = '#26a69a' if pct <= 100 else '#ef5350'

        n_cases = active.get('_ai_n_cases', 0)
        confidence = active.get('_ai_confidence', '-')
        source = active.get('_ai_source', '')

        # Confidence emoji
        conf_emoji = {'สูงมาก': '🟢', 'สูง': '🟢', 'ปานกลาง': '🟡',
                      'ต่ำ': '🔴'}.get(confidence, '⚪')
        if source == 'local_history' and n_cases:
            conf_text = (f"{conf_emoji} AI มั่นใจ <b>{confidence}</b> "
                         f"(จาก {n_cases} เคสคล้ายกัน)")
        elif source == 'ml_model':
            conf_text = (f"{conf_emoji} AI มั่นใจ <b>{confidence}</b> "
                         "(ML model — ไม่มีประวัติเคสคล้าย)")
        else:
            conf_text = ''

        diag = (active.get('diagnosis') or '').strip()
        diag_safe = diag.replace('"', '&quot;')
        diag_html = ''
        if diag and diag.lower() not in ('-', 'nan', 'none'):
            diag_html = (
                f'<div style="font-size:12px;color:#607d8b;'
                f'font-style:italic;margin:2px 0;overflow:hidden;'
                f'text-overflow:ellipsis;white-space:nowrap;" '
                f'title="{diag_safe}">🩺 {diag}</div>'
            )

        ai_pred_html = (f'🤖 AI ทำนายเวลาใช้ห้อง {ai_min} น. | '
                        f'ใช้ไป <b>{elapsed_min}</b> น.'
                        if ai_min else f'⏱ ใช้ไป {elapsed_min} นาที')

        # IMPORTANT: HTML must be on ONE LINE (no leading whitespace)
        # otherwise Streamlit's markdown parser treats it as code block
        bar_html = ''
        if ai_min:
            bar_html = (
                f'<div style="background:#e0e0e0;border-radius:8px;'
                f'height:18px;margin-top:6px;overflow:hidden;'
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

        # Build full HTML as single concatenated string — no leading whitespace
        # (Streamlit markdown treats indented lines as code blocks)
        nm_safe = (active.get('name') or '-').replace('"', '&quot;')
        proc_safe = (active.get('procedure_name') or '-').replace('"', '&quot;')
        card_html = (
            f'<div class="room-card room-busy" style="text-align:left;">'
            f'<div style="font-size:14px;font-weight:700;color:#1565c0;'
            f'margin-bottom:4px;">🏥 ห้อง {rm["room_no"]}'
            f'<span style="float:right;font-size:11px;color:#1976d2;">'
            f'🔵 กำลังผ่าตัด</span></div>'
            f'<div style="font-size:13px;color:#333;font-weight:600;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" '
            f'title="{nm_safe}">👤 {active.get("name") or "-"}</div>'
            f'{diag_html}'
            f'<div style="font-size:13px;color:#1565c0;margin:2px 0;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" '
            f'title="{proc_safe}">'
            f'⚕ <b>{active.get("procedure_name") or "-"}</b></div>'
            f'<div style="font-size:11px;color:#666;">'
            f'👨‍⚕️ {active.get("surgeon_name") or "-"}</div>'
            f'<div style="font-size:12px;color:#444;margin-top:6px;">'
            f'{ai_pred_html}</div>'
            f'{bar_html}'
            f'<div style="font-size:11px;color:#666;margin-top:4px;">'
            f'{conf_text}</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

    elif rm['done'] > 0 and rm['waiting'] == 0:
        st.markdown(f"""
        <div class="room-card room-done" style="text-align:center;">
            <div style="font-size:14px;font-weight:700;color:#2e7d32;">
              🏥 ห้อง {rm['room_no']}</div>
            <div style="font-size:11px;color:#388e3c;margin:4px 0;">
              ✅ เสร็จแล้ว</div>
            <div style="font-size:32px;font-weight:700;color:#2e7d32;">
              {rm['done']}</div>
            <div style="font-size:12px;color:#666;">เคสเสร็จวันนี้</div>
        </div>
        """, unsafe_allow_html=True)
    elif rm['total'] > 0:
        st.markdown(f"""
        <div class="room-card room-free" style="text-align:center;">
            <div style="font-size:14px;font-weight:700;color:#616161;">
              🏥 ห้อง {rm['room_no']}</div>
            <div style="font-size:11px;color:#f57f17;margin:4px 0;">
              ⏳ รอเข้าห้อง</div>
            <div style="font-size:32px;font-weight:700;color:#f57f17;">
              {rm['waiting']}</div>
            <div style="font-size:12px;color:#666;">
              เคสรอ / {rm['total']} ทั้งหมด</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="room-card room-free" style="text-align:center;">
            <div style="font-size:14px;font-weight:700;color:#9e9e9e;">
              🏥 ห้อง {rm['room_no']}</div>
            <div style="font-size:11px;color:#bdbdbd;margin:4px 0;">—</div>
            <div style="font-size:24px;font-weight:700;color:#bdbdbd;">
              🌙 ห้องว่าง</div>
            <div style="font-size:12px;color:#ccc;">พร้อมรับเคสถัดไป</div>
        </div>
        """, unsafe_allow_html=True)


def _render_room_cards(rooms):
    """2x2 grid layout (2 cards per row)"""
    n = len(rooms)
    per_row = 2
    for row_start in range(0, n, per_row):
        row_rooms = rooms[row_start:row_start + per_row]
        cols = st.columns(per_row)
        for i, rm in enumerate(row_rooms):
            with cols[i]:
                _render_one_room_card(rm)


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


def _render_ai_research_tab():
    """🤖 AI Prediction (งานวิจัย) — แสดงศักยภาพของ AI ทำนายเวลาผ่าตัด

    แสดง 4 ส่วน:
    1. Filter (หัตถการ — รวมกลุ่ม fuzzy ด้วย _normalize_procedure_name)
    2. KPI Cards (4): n, MAE, % within ±10 min, R²
    3. Scatter plot (predicted vs actual)
    4. Error distribution histogram
    """
    st.markdown('<div class="section-title">🤖 AI Prediction Performance</div>',
                unsafe_allow_html=True)

    # ── ดึงข้อมูล AI predictions ทั้งหมด (ทุกช่วงเวลา) ──
    summary = get_summary(date_from=None, date_to=None)
    ai_df = summary.get('ai_df')
    if ai_df is None or len(ai_df) == 0:
        st.info("ยังไม่มีข้อมูล AI prediction — ต้องมีเคสที่ทำเสร็จแล้ว "
                "และมีทั้ง ai_predicted_min และ actual_duration_min")
        return

    ai_df = ai_df.copy()
    ai_df['error'] = ai_df['ai_predicted_min'] - ai_df['actual_duration_min']
    ai_df['abs_error'] = ai_df['error'].abs()
    ai_df['pct_error'] = (ai_df['abs_error']
                          / ai_df['actual_duration_min'].replace(0, np.nan)
                          * 100)

    # ── Apply fuzzy normalization to procedure names (canonical groups) ──
    # ทำให้ filter dropdown แสดงชื่อแบบรวมแล้ว เช่น
    # "ESWL", "ESWL Right" → "ESWL"  /  "QS", "Q-Switch" → "Q-Switch ND:YAG"
    ai_df['proc_canonical'] = ai_df['procedure_name'].apply(_normalize_procedure_name)

    # ── Filter Control (เฉพาะหัตถการ — เอาแพทย์ออก) ──
    proc_options = sorted(
        [p for p in ai_df['proc_canonical'].dropna().unique() if p != 'UNKNOWN']
    )
    sel_procs = st.multiselect(
        "🔬 หัตถการ (รวมกลุ่ม fuzzy แล้ว)", proc_options, default=[],
        placeholder="ทั้งหมด — เลือกเพื่อกรอง",
        key="ai_filter_proc",
    )

    df = ai_df.copy()
    if sel_procs:
        df = df[df['proc_canonical'].isin(sel_procs)]

    n = len(df)
    if n == 0:
        st.warning("ไม่มีเคสที่ตรงกับ filter ที่เลือก")
        return

    # ── คำนวณ metrics ──
    mae = float(df['abs_error'].mean())
    rmse = float(np.sqrt((df['error'] ** 2).mean()))
    bias = float(df['error'].mean())
    within_10 = int((df['abs_error'] <= 10).sum())
    pct_within_10 = round(within_10 / n * 100, 1) if n > 0 else 0
    # R² computation
    actual = df['actual_duration_min'].astype(float)
    pred = df['ai_predicted_min'].astype(float)
    ss_res = float(((actual - pred) ** 2).sum())
    ss_tot = float(((actual - actual.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    # ── Judgment label & color ──
    def _judge_mae(v):
        if v <= 10: return ('🟢 ดีมาก', '#43a047')
        if v <= 20: return ('🟡 ใช้ได้', '#fb8c00')
        return ('🔴 ต้องปรับ', '#e53935')

    def _judge_pct(v):
        if v >= 80: return ('🟢 ดีมาก', '#43a047')
        if v >= 60: return ('🟡 ใช้ได้', '#fb8c00')
        return ('🔴 ต้องปรับ', '#e53935')

    def _judge_r2(v):
        if v >= 0.7: return ('🟢 ดีมาก', '#43a047')
        if v >= 0.5: return ('🟡 ใช้ได้', '#fb8c00')
        return ('🔴 ต้องปรับ', '#e53935')

    mae_label, mae_color = _judge_mae(mae)
    pct_label, pct_color = _judge_pct(pct_within_10)
    r2_label, r2_color = _judge_r2(r2)

    # ── KPI Cards (4 ตัว) ──
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">เคสที่ใช้ประเมิน</div>
            <div class="kpi-value" style="color:#1565c0;">{n}</div>
            <div style="font-size:11px;color:#999;">เคส</div>
        </div>""", unsafe_allow_html=True)
    k2.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">ผิดเฉลี่ย (MAE)</div>
            <div class="kpi-value" style="color:{mae_color};">±{mae:.1f}</div>
            <div style="font-size:11px;color:#999;">นาที • {mae_label}</div>
        </div>""", unsafe_allow_html=True)
    k3.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">ทำนายแม่น (±10 นาที)</div>
            <div class="kpi-value" style="color:{pct_color};">{pct_within_10:.0f}%</div>
            <div style="font-size:11px;color:#999;">{within_10}/{n} เคส • {pct_label}</div>
        </div>""", unsafe_allow_html=True)
    k4.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">R² Score</div>
            <div class="kpi-value" style="color:{r2_color};">{r2:.2f}</div>
            <div style="font-size:11px;color:#999;">model fit • {r2_label}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Scatter + Histogram (2 columns) ──
    col_s, col_h = st.columns(2)

    with col_s:
        st.markdown('<div class="section-title">📍 AI ทำนาย vs เวลาจริง</div>',
                    unsafe_allow_html=True)
        # สี categorize ตาม abs_error
        def _err_color(e):
            if e <= 10: return 'แม่น (≤10 นาที)'
            if e <= 20: return 'พอใช้ (11-20)'
            return 'ผิดมาก (>20)'
        df_plot = df.copy()
        df_plot['error_cat'] = df_plot['abs_error'].apply(_err_color)
        fig = px.scatter(
            df_plot, x='actual_duration_min', y='ai_predicted_min',
            color='error_cat',
            color_discrete_map={
                'แม่น (≤10 นาที)': '#43a047',
                'พอใช้ (11-20)':  '#fb8c00',
                'ผิดมาก (>20)':   '#e53935',
            },
            hover_data={'proc_canonical': True,
                        'error': ':.0f', 'error_cat': False,
                        'procedure_name': False, 'surgeon_name': False},
            labels={'actual_duration_min': 'เวลาจริง (นาที)',
                    'ai_predicted_min': 'AI ทำนายเวลาใช้ห้อง (นาที)'},
        )
        max_v = float(max(df['actual_duration_min'].max(),
                          df['ai_predicted_min'].max()) * 1.1)
        # Perfect prediction line (y = x)
        fig.add_trace(go.Scatter(
            x=[0, max_v], y=[0, max_v], mode='lines',
            line=dict(dash='dash', color='#9e9e9e', width=1.5),
            name='ทำนายแม่น (y=x)', hoverinfo='skip',
        ))
        fig.update_layout(
            margin=dict(t=10, b=40, l=50, r=10), height=320,
            legend=dict(orientation='h', y=-0.15),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_h:
        st.markdown('<div class="section-title">📊 การกระจายของ Error</div>',
                    unsafe_allow_html=True)
        fig = px.histogram(
            df, x='error', nbins=15,
            labels={'error': 'AI − จริง (นาที)', 'count': 'จำนวนเคส'},
            color_discrete_sequence=['#5c6bc0'],
        )
        fig.add_vline(x=0, line_dash='dash', line_color='#43a047',
                      annotation_text='ทำนายแม่น',
                      annotation_position='top right')
        fig.add_vline(x=bias, line_dash='dot', line_color='#e53935',
                      annotation_text=f'เฉลี่ย bias = {bias:+.1f}',
                      annotation_position='top left')
        fig.update_layout(
            margin=dict(t=10, b=40, l=40, r=10), height=320,
            xaxis_title='AI − จริง (นาที)  ←ต่ำกว่า | เกินจริง→',
            yaxis_title='จำนวนเคส',
        )
        st.plotly_chart(fig, use_container_width=True)

    # Footer note: data scope
    st.caption(
        f"📌 ใช้ข้อมูลทั้งหมด {len(ai_df)} เคสที่ทำเสร็จแล้ว "
        "(ตัดเคสนอกเวลาออก) — Filter ทำงานบน scatter / histogram"
    )


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
        # การ์ดใหม่: รวม "วันที่ยุ่ง (จ-ศ) + ช่วงเวลายุ่ง"
        _tdn = data.get('top_dow_name', '-')
        _tdh = data.get('top_dow_hour', 0)
        _tdc = data.get('top_dow_count', 0)
        if _tdn != '-':
            _peak_dh = f"{_tdn} {_tdh:02d}:00 น."
        else:
            _peak_dh = '—'
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">วัน+ช่วงเวลาเคสเยอะ</div>
            <div class="kpi-value" style="color:#1565c0;font-size:18px;">{_peak_dh}</div>
            <div style="font-size:12px;color:#999;">วัน{_tdn}รวม {_tdc} เคส</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        # Utilization Rate (Coverage 8:00-16:00, นับเฉพาะวันที่มีเคส)
        # = นาทีที่มีเคสในห้อง ÷ (วันมีเคส × 480 นาที)
        _ur = data.get('util_rate', 0)
        _uam = data.get('util_active_min', 0)
        _utm = data.get('util_total_min', 0)
        _und = data.get('util_n_days', 0)
        _uah = round(_uam / 60, 1)
        _uth = round(_utm / 60, 1)
        # สีไล่ตามเปอร์เซ็นต์ — เขียว/ส้ม/แดง
        if _ur >= 70:
            _ur_color = '#2e7d32'
        elif _ur >= 40:
            _ur_color = '#e65100'
        else:
            _ur_color = '#c62828'
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Utilization Rate</div>
            <div class="kpi-value" style="color:{_ur_color};font-size:22px;">{_ur}%</div>
            <div style="font-size:12px;color:#999;">{_uah}/{_uth} ชม. · {_und} วัน</div>
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
        st.markdown('<div class="section-title">🔥 ภาระงานห้องผ่าตัดเล็ก (เฉลี่ยเคสต่อครั้ง)</div>',
                    unsafe_allow_html=True)
        hm = data['heatmap_df']
        dow_counts = data.get('dow_counts', {})
        if not hm.empty and dow_counts:
            _THAI_DAYS = ['จันทร์','อังคาร','พุธ','พฤหัสฯ','ศุกร์','เสาร์','อาทิตย์']

            # raw count: เคส (overlapping) ในแต่ละ (dow, hour) รวมทั้งช่วง
            pivot_total = hm.pivot_table(index='dow', columns='hour', values='n',
                                         fill_value=0, aggfunc='sum')
            for d in range(5):
                if d not in pivot_total.index:
                    pivot_total.loc[d] = 0
            for h in range(8, 17):
                if h not in pivot_total.columns:
                    pivot_total[h] = 0
            pivot_total = pivot_total.reindex(index=range(5),
                                              columns=range(8, 17), fill_value=0)

            # avg per occurrence: หารด้วยจำนวน dow ในช่วง
            # ตัวอย่าง: ศุกร์ 13:00 มี 12 เคสรวมจาก 4 ศุกร์ → 12/4 = 3 เคส/ครั้ง
            pivot = pivot_total.copy().astype(float)
            for d in pivot.index:
                cnt = max(dow_counts.get(int(d), 1), 1)
                pivot.loc[d] = pivot_total.loc[d] / cnt
            pivot = pivot.round(1)

            # Format ค่าในช่อง: ว่างเปล่าถ้า 0, อื่น ๆ แสดงเลข (1 ทศนิยมถ้า <1)
            def _fmt_cell(v):
                if v == 0: return ''
                if v < 1:  return f'{v:.1f}'
                # >= 1 → แสดงทศนิยม 1 ตำแหน่งเสมอเพื่อความสม่ำเสมอ
                return f'{v:.1f}'

            text_overlay = [[_fmt_cell(pivot.loc[d, h]) for h in range(8, 17)]
                            for d in range(5)]

            customdata = []
            for d_idx in range(5):
                row_hover = []
                for h in range(8, 17):
                    avg = float(pivot.loc[d_idx, h])
                    total = int(pivot_total.loc[d_idx, h])
                    n_days = dow_counts.get(d_idx, 0)
                    if avg == 0:
                        line = 'ไม่มีเคสในช่วงนี้'
                    else:
                        line = (f'เฉลี่ย {avg:.1f} เคส/ครั้ง<br>'
                                f'(รวม {total} เคส จาก {n_days} '
                                f'{_THAI_DAYS[d_idx]})')
                    row_hover.append(line)
                customdata.append(row_hover)

            # Auto color scale (ยิ่งเคสเยอะ ยิ่งเข้ม) — ใช้ max ของข้อมูลจริง
            zmax_val = max(float(pivot.values.max()), 1.0)

            fig = go.Figure(data=go.Heatmap(
                z=pivot.values,
                x=[f'{h}:00' for h in range(8, 17)],
                y=[_THAI_DAYS[i] for i in range(5)],
                colorscale='OrRd',
                zmin=0, zmax=zmax_val,
                colorbar=dict(title='เคสเฉลี่ย'),
                text=text_overlay,
                texttemplate='%{text}',
                textfont=dict(size=11, color='black'),
                customdata=customdata,
                hovertemplate=('<b>%{y} เวลา %{x}</b><br>%{customdata}'
                               '<extra></extra>'),
            ))
            fig.update_layout(
                margin=dict(t=10, b=10, l=80, r=10), height=260,
                xaxis_title='ชั่วโมง', yaxis=dict(autorange='reversed'),
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── สรุปภาพรวม: เคสเยอะ/น้อยสุด → วัน + ช่วงเช้า/บ่าย ──
            def _period(h):
                """แปลงชั่วโมง → ช่วงเช้า / ช่วงบ่าย"""
                return 'ช่วงเช้า' if h < 12 else 'ช่วงบ่าย'

            flat = pivot.stack()
            peak_idx = flat.idxmax() if flat.max() > 0 else None
            quiet_nonzero = flat[flat > 0]
            quiet_idx = quiet_nonzero.idxmin() if not quiet_nonzero.empty else None

            insight_html = """
<div style="background:#f5f5f5;border-radius:8px;padding:12px 14px;
            margin-top:8px;font-size:14px;line-height:1.8;">
  <div style="font-weight:700;color:#333;margin-bottom:6px;">
    📊 สรุปภาพรวม
  </div>
"""
            if peak_idx is not None:
                p_dow, p_hour = _THAI_DAYS[peak_idx[0]], int(peak_idx[1])
                insight_html += (
                    f'  🔝 <b>เคสเยอะสุด</b>: วัน{p_dow} {_period(p_hour)} '
                    f'(เฉลี่ย {float(flat[peak_idx]):.1f} เคส/{p_dow})<br>\n'
                )
            if quiet_idx is not None:
                q_dow, q_hour = _THAI_DAYS[quiet_idx[0]], int(quiet_idx[1])
                insight_html += (
                    f'  😴 <b>เคสน้อยสุด</b>: วัน{q_dow} {_period(q_hour)} '
                    f'(เฉลี่ย {float(flat[quiet_idx]):.1f} เคส/{q_dow})\n'
                )
            insight_html += "</div>"
            st.markdown(insight_html, unsafe_allow_html=True)

            with st.expander("💡 วิธีอ่านกราฟนี้", expanded=False):
                st.markdown("""
**ตัวเลขในช่อง = เฉลี่ยจำนวนเคสที่อยู่ในช่วงเวลานั้น ๆ ของวันนั้น ๆ**

**วิธีคำนวณ:** นับเคสที่คร่อมชั่วโมงนั้น (เคสที่ทำคร่อม 13:18-14:50
จะถูกนับใน slot 13:00 และ 14:00) แล้วหารด้วยจำนวนวันนั้น ๆ ในช่วงที่เลือก

**ตัวอย่าง:**
> "ศุกร์ 13:00 = 3.0" หมายความว่า ในช่วงที่เลือก (เช่น 4 สัปดาห์)
> ทุกวันศุกร์ตอน 13:00 มีเคสอยู่ในห้องผ่าตัดเฉลี่ย **3 เคส**

**ตีความสี:** ยิ่งสีเข้ม = เคสยิ่งเยอะในช่วงนั้น
- ⬜ ขาว/อ่อนมาก → เคสน้อย หรือไม่มีเคส
- 🟧 ส้มอ่อน → เคสปานกลาง
- 🟥 ส้มเข้ม → เคสเยอะ
- 🟫 แดงเข้ม → เคสเยอะที่สุดในช่วงเวลาที่เลือก

**ใช้ประโยชน์:**
- 📅 ดูว่า**ภาระงานหนักช่วงไหน** ของสัปดาห์
- 🗓️ หา **ช่วงเคสน้อย** เพื่อจองเคสเพิ่ม / นัด standby case
- 📈 ดู pattern ของหน่วย — เปรียบเทียบวัน/ช่วงเวลา
                """)
        else:
            st.caption("ยังไม่มีข้อมูลเวลา (ต้องมีเคสที่กดปุ่ม 'เข้าห้อง' และ 'เสร็จ' แล้ว)")

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

    # KPI op_type — Elective / Urgent / Emergency
    # NULL หรือ missing → ถือเป็น "elective" (default ของ minor OR)
    df_op = get_cases()
    df_op = df_op[(df_op['op_date'] >= date_from) & (df_op['op_date'] <= date_to)]
    if 'op_type' in df_op.columns:
        # normalize: NULL/empty → 'elective'
        op_norm = (df_op['op_type'].fillna('elective')
                   .astype(str).str.lower().str.strip()
                   .replace('', 'elective'))
        n_elec = int((op_norm == 'elective').sum())
        n_urg = int((op_norm == 'urgent').sum())
        n_emer = int((op_norm == 'emergency').sum())
        n_other = len(df_op) - n_elec - n_urg - n_emer
        ko1, ko2, ko3 = st.columns(3)
        ko1.metric("📋 Elective", n_elec, help="รวมเคสที่ไม่ได้ระบุ op_type")
        ko2.metric("⚡ Urgent", n_urg)
        ko3.metric("🚨 Emergency", n_emer)
        if n_other > 0:
            st.caption(f"⚠️ มี {n_other} เคสที่ op_type เป็นค่าอื่น "
                       f"(re-upload schedule.xls เพื่ออัปเดต)")

    # NOTE (thesis mode): ซ่อน KPI cost/patho ชั่วคราว — เปิดกลับโดย uncomment
    # k9, k10, k11, k12 = st.columns(4)
    # k9.metric("💰 ค่าหัตถการ", f"{s_all['total_treatment']:,} ฿")
    # k10.metric("💵 รายได้รวม", f"{s_all['total_revenue']:,} ฿")
    # k11.metric("🧬 ส่งชิ้นเนื้อ", f"{s_all['n_patho_sent']} ราย")
    # k12.metric("🔬 ค่าชิ้นเนื้อ", f"{s_all['total_patho']:,} ฿")

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
        # NOTE (thesis mode): ซ่อน "💰 รายได้" — เปลี่ยนเป็น 3 columns
        a1, a2, a3 = st.columns(3)
        a1.metric("เคสนอกเวลา", len(aft_range))
        a2.metric("ยืนยันแล้ว", len(aft_range[aft_range['status'] == 'discharged']))
        a3.metric("ยกเลิก", len(aft_range[aft_range['status'] == 'cancelled']))
        # a4.metric("💰 รายได้", f"{int(aft_range['treatment_cost'].fillna(0).sum()):,} ฿")

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
    # NOTE (thesis mode): ซ่อนรายได้
    # revenue = int(aft['treatment_cost'].fillna(0).sum())

    # Metrics
    a1, a2, a3 = st.columns(3)
    a1.metric("เคสนอกเวลา", n_total)
    a2.metric("ยืนยันแล้ว", n_done)
    a3.metric("ยกเลิก", n_cancel)
    # a4.metric("💰 รายได้", f"{revenue:,} ฿")

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
    # NOTE (thesis mode): ซ่อนแท็บ "💰 ใส่ราคารายวัน" ชั่วคราว
    # เปิดกลับเมื่อต้องการ → uncomment 4 บรรทัดล่าง + กลับมา 4 tabs
    # tab_today, tab_cost, tab_history, tab_ai = st.tabs([
    #     "📋 ภาพรวมวันนี้",
    #     "💰 ใส่ราคารายวัน",
    #     "📈 สถิติย้อนหลัง",
    #     "🤖 AI Prediction (งานวิจัย)",
    # ])
    # with tab_cost:
    #     _render_cost_entry_tab()
    tab_today, tab_history, tab_ai = st.tabs([
        "📋 ภาพรวมวันนี้",
        "📈 สถิติย้อนหลัง",
        "🤖 AI Prediction (งานวิจัย)",
    ])

    # -- TAB 1: Today overview --
    with tab_today:
        op_date = _now_bkk().strftime('%Y-%m-%d')

        # ── Demo Mode toggle + controls (ด้านบนสุด) ──
        sim_min = _render_demo_controls()
        demo_active = sim_min is not None

        # ── Auto-refresh: 3 วิ ใน demo mode, 60 วิใน normal ──
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(
                interval=3_000 if demo_active else 60_000,
                key='today_refresh' if not demo_active else 'demo_refresh')
        except ImportError:
            st.markdown(
                f'<meta http-equiv="refresh" content="{3 if demo_active else 60}">',
                unsafe_allow_html=True,
            )

        # =========================================================
        # Section: เคสในเวลา
        # =========================================================
        st.markdown(
            '<div style="background:linear-gradient(135deg,#e8f5e9,#c8e6c9);'
            'border-radius:10px;padding:10px 16px;margin:12px 0 8px;">'
            '<span style="font-size:16px;font-weight:700;color:#2e7d32;">'
            '🏥 เคสในเวลา</span>'
            '<span style="font-size:12px;color:#388e3c;margin-left:8px;">'
            'Full OR Flow + AI Prediction · auto-refresh ทุก '
            f'{"3 วินาที (demo)" if demo_active else "1 นาที"}</span></div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="section-title">🏥 สถานะห้องผ่าตัด</div>',
                    unsafe_allow_html=True)
        if demo_active:
            rooms = _get_demo_rooms(sim_min)
            kpi = _get_demo_kpi(sim_min)
        else:
            rooms = get_room_status(op_date)
            kpi = get_kpi(op_date)
        _render_room_cards(rooms)

        _thai_months = ['', 'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.',
                        'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.']
        _today_dt = _now_bkk()
        _thai_date = (f"{_today_dt.day} {_thai_months[_today_dt.month]} "
                      f"{_today_dt.year + 543}")
        title_label = ('🎬 ตัวเลขสำคัญ (Demo)' if demo_active
                       else f'📈 ตัวเลขสำคัญ — {_thai_date}')
        st.markdown(f'<div class="section-title">{title_label}</div>',
                    unsafe_allow_html=True)
        _render_kpi(kpi)

        if kpi.get('total', 0) > 0:
            progress = kpi['done'] / kpi['total']
            st.markdown(f"""
            <div style="margin:12px 0 4px;">
                <div style="display:flex;justify-content:space-between;font-size:13px;color:#333;font-weight:700;">
                    <span>ความคืบหน้า{'จำลอง' if demo_active else 'วันนี้'}</span>
                    <span>{kpi['done']}/{kpi['total']} เคส ({progress:.0%})</span>
                </div>
                <div style="background:#e0e0e0;border-radius:6px;height:12px;margin-top:4px;">
                    <div style="background:linear-gradient(90deg,#43a047,#66bb6a);
                                height:100%;width:{progress*100:.0f}%;border-radius:6px;
                                transition:width 0.5s;"></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        # ── Demo mode: ซ่อน sections ที่อิง real DB (alerts, workload, ฯลฯ) ──
        if demo_active:
            st.info(
                "🎬 **โหมด Demo** — แสดงเฉพาะ Room cards + KPI\n\n"
                "🔇 sections อื่น (แจ้งเตือน, ภาระงาน, รับเวร, ผู้ป่วยรอ) "
                "ถูกซ่อนชั่วคราว เพื่อ focus ที่ flow หลัก\n\n"
                "💡 ปิด Demo Mode เพื่อกลับไปดูข้อมูลจริงครบทุก section"
            )
            return

        st.markdown('<div class="section-title">⚠️ แจ้งเตือน</div>',
                    unsafe_allow_html=True)
        alerts = get_delay_alerts(op_date)
        _render_alerts(alerts)

        st.markdown('<div class="section-title">👥 ภาระงาน</div>',
                    unsafe_allow_html=True)
        wl = get_workload(op_date)
        _render_workload(wl)

        st.markdown('<div class="section-title">🔍 Progress รายบุคคล</div>',
                    unsafe_allow_html=True)
        _render_nurse_progress(op_date)

        with st.expander("🤖 AI Prediction Accuracy (สำหรับวิจัย)",
                         expanded=False):
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

        # --- Date range picker (manual only — preset 7/30/90 ลบออกเพราะ
        # confusion เรื่อง default range. ผู้ใช้เลือกวันที่เองโดยตรง) ---
        # Default = ดึงจาก DB เพื่อให้ครอบคลุมทุก data ที่มี
        try:
            from minor_or_db import get_conn
            _conn = get_conn()
            _row = _conn.execute(
                "SELECT MIN(op_date), MAX(op_date) FROM cases"
            ).fetchone()
            _conn.close()
            default_from = (datetime.strptime(_row[0], '%Y-%m-%d').date()
                            if _row and _row[0] else today_dt - timedelta(days=29))
            default_to = (datetime.strptime(_row[1], '%Y-%m-%d').date()
                          if _row and _row[1] else today_dt)
        except Exception:
            default_from = today_dt - timedelta(days=29)
            default_to = today_dt

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
            'สำหรับ admin/หัวหน้าพยาบาล: import / แก้ไขข้อมูล / reset DB'
            '</div></div>',
            unsafe_allow_html=True,
        )

        # =========================================================
        # ① Cost-Driven Bulk Import (เครื่องมือเดียวรวมทุกอย่าง)
        # =========================================================
        with st.expander(
                "📦 ① Cost-Driven Bulk Import "
                "(Cost Excel = master + enrich ด้วย sched + intraop)",
                expanded=False):
            st.caption(
                "🎯 **เครื่องมือเดียวจบ** — Cost Excel เป็น master "
                "(\"เคสที่ผ่าตัดจริง\") รวมข้อมูลจาก schedule + intraop "
                "อัตโนมัติในการ import ครั้งเดียว:\n\n"
                "✅ ทุก row ใน Cost Excel → 1 เคสใน DB\n"
                "✅ case_category จาก reqdate ใน schedule (ถ้ามี)\n"
                "✅ Room timestamps จาก intraop (ถ้ามี) "
                "fallback ไป Op.start/end ใน Cost\n"
                "✅ actual_duration จาก intraop opusetime > Cost Duration\n"
                "✅ scrub/circ จาก intraop > Cost\n"
                "✅ ค่าผ่าตัด, ค่าชิ้นเนื้อ จาก Cost\n"
                "✅ status='discharged' (ทุกเคสในสมุดสถิติ = ผ่าตัดเสร็จ)\n\n"
                "⏭️ เคสใน sched/intraop **ที่ไม่อยู่ใน Cost** จะถูก skip "
                "(ถือว่า cancel หรือไม่ได้ผ่าตัดจริง)"
            )
            cd_cost = st.file_uploader(
                "💰 Cost Excel (required) — สมุดสถิติ — มี HN, Date, "
                "Operation, ราคาผ่าตัด, ราคาชิ้นเนื้อ, แพทย์, times",
                type=['xlsx', 'xls'], key="cd_cost_xlsx",
            )
            cd_sched = st.file_uploader(
                "📅 Schedule CSV (optional) — เพิ่ม case_category, "
                "diagnosis, division, age — มี hn, reqdate, opedate",
                type=['csv'], key="cd_sched_csv",
            )
            cd_intra = st.file_uploader(
                "🏥 Intraop CSV (optional) — เพิ่ม precise timestamps + "
                "actual nurses — มี hn, opedate, roomtimein, roomtimeout",
                type=['csv'], key="cd_intra_csv",
            )

            if cd_cost is not None:
                cdb1, cdb2 = st.columns(2)
                with cdb1:
                    btn_cd_preview = st.button(
                        "🔍 Preview (Dry-run)", use_container_width=True,
                        key="btn_cd_preview")
                with cdb2:
                    btn_cd_apply = st.button(
                        "✅ Import (อัพเดต DB)", type="primary",
                        use_container_width=True, key="btn_cd_apply")

                if btn_cd_preview or btn_cd_apply:
                    import tempfile, os as _os
                    tmp_paths = {}
                    try:
                        # Save uploaded files
                        for upl, key, suffix in [
                            (cd_cost, 'cost', '.xlsx'),
                            (cd_sched, 'sched', '.csv'),
                            (cd_intra, 'intra', '.csv'),
                        ]:
                            if upl is None:
                                continue
                            upl.seek(0)
                            with tempfile.NamedTemporaryFile(
                                    delete=False, suffix=suffix,
                                    mode='wb') as tmp:
                                tmp.write(upl.read())
                                tmp_paths[key] = tmp.name

                        from import_historical import import_cost_driven
                        info = import_cost_driven(
                            cost_path=tmp_paths['cost'],
                            sched_path=tmp_paths.get('sched'),
                            intra_path=tmp_paths.get('intra'),
                            dry_run=not btn_cd_apply)

                        if 'error' in info:
                            st.error(f"❌ {info['error']}")
                        else:
                            mode = ("✅ Imported" if btn_cd_apply
                                    else "🔍 Preview")
                            st.success(
                                f"{mode} — เพิ่ม **{info['inserted']} เคส**, "
                                f"ข้าม {info['skipped_duplicate']} ซ้ำ"
                            )
                            st.markdown(
                                f"**📊 ที่มาของข้อมูล (enrichment):**\n\n"
                                f"- 📅 จาก Schedule: "
                                f"{info['enriched_sched']} เคส "
                                f"(ได้ reqdate + diag + division)\n"
                                f"- 🏥 จาก Intraop: "
                                f"{info['enriched_intra']} เคส "
                                f"(ได้ precise timestamps)\n"
                                f"- 💰 Cost-only (walk-in): "
                                f"{info['inserted'] - info['enriched_intra']} เคส"
                            )

                            if (info['skipped_no_hn']
                                    or info['skipped_no_date']):
                                st.warning(
                                    f"⚠️ Cost rows ที่ skip (ขาด HN/Date): "
                                    f"HN missing {info['skipped_no_hn']}, "
                                    f"Date missing {info['skipped_no_date']}"
                                )

                            # Cases ใน sched แต่ไม่ใน cost
                            if info.get('sched_only_not_in_cost'):
                                st.markdown(
                                    f"**⏭️ เคสใน Schedule แต่ไม่ใน Cost "
                                    f"({info['sched_only_not_in_cost']} เคส) "
                                    f"— อาจ cancel:**"
                                )
                                if info['sched_only_samples']:
                                    df_so = pd.DataFrame(
                                        info['sched_only_samples'])
                                    st.dataframe(df_so,
                                                 use_container_width=True,
                                                 hide_index=True)

                            if info.get('samples'):
                                st.markdown(
                                    f"**ตัวอย่างเคสที่ import "
                                    f"({len(info['samples'])} เคส):**"
                                )
                                df_s = pd.DataFrame(info['samples'])
                                st.dataframe(df_s,
                                             use_container_width=True,
                                             hide_index=True)
                            if btn_cd_apply:
                                st.info(
                                    "🔄 กดปุ่ม R เพื่อ refresh — "
                                    "หรือเปิด tab \"📈 สถิติย้อนหลัง\""
                                )
                    except Exception as e:
                        import traceback
                        st.error(f"❌ Error: {e}")
                        st.code(traceback.format_exc())
                    finally:
                        for p in tmp_paths.values():
                            try: _os.unlink(p)
                            except OSError: pass

        # ── ② Reclassify / ③ Re-import timestamps / ⑤ Walk-in import
        # — รวมเข้าใน ① Cost-Driven Bulk Import แล้ว (1 click จบงาน) ──
        if False:  # dead code below — kept temporarily for reference
            bulk_sched = st.file_uploader(
                "📅 Schedule CSV (required) — มี hn, reqdate, opedate, "
                "icd9cm_name, surgstfnm, division",
                type=['csv'], key="bulk_sched_csv",
            )
            bulk_intra = st.file_uploader(
                "🏥 Intraop CSV (required) — มี hn, opedate, **roomtimein**, "
                "**roomtimeout**, arrivtime, opusetime",
                type=['csv'], key="bulk_intra_csv",
            )
            bulk_cost = st.file_uploader(
                "💰 Cost Excel (optional) — มี HN, Date, ราคาผ่าตัด, ราคาชิ้นเนื้อ",
                type=['xlsx', 'xls'], key="bulk_cost_xlsx",
            )

            if bulk_sched and bulk_intra:
                cb1, cb2 = st.columns(2)
                with cb1:
                    btn_bulk_preview = st.button(
                        "🔍 Preview (Dry-run)", use_container_width=True,
                        key="btn_bulk_preview")
                with cb2:
                    btn_bulk_import = st.button(
                        "✅ Import (อัพเดต DB)", type="primary",
                        use_container_width=True, key="btn_bulk_import")

                if btn_bulk_preview or btn_bulk_import:
                    import tempfile, os as _os
                    tmp_paths = []
                    try:
                        # Save uploaded files to temp paths
                        for upl, suffix in [(bulk_sched, '.csv'),
                                            (bulk_intra, '.csv')]:
                            upl.seek(0)
                            with tempfile.NamedTemporaryFile(
                                    delete=False, suffix=suffix,
                                    mode='wb') as tmp:
                                tmp.write(upl.read())
                                tmp_paths.append(tmp.name)
                        cost_path = None
                        if bulk_cost:
                            bulk_cost.seek(0)
                            with tempfile.NamedTemporaryFile(
                                    delete=False, suffix='.xlsx',
                                    mode='wb') as tmp:
                                tmp.write(bulk_cost.read())
                                cost_path = tmp.name

                        from import_historical import (
                            import_historical_with_costs)
                        info = import_historical_with_costs(
                            tmp_paths[0], tmp_paths[1], cost_path,
                            dry_run=not btn_bulk_import)

                        mode = "✅ Imported" if btn_bulk_import else "🔍 Preview"
                        st.success(
                            f"{mode} — เคส: **{info['inserted']} เพิ่ม**, "
                            f"{info['skipped']} ซ้ำ (skip)"
                        )

                        # ── Skip stats: rows ที่ DB ไม่รับเพราะข้อมูลไม่ครบ ──
                        skip_no_date = info.get('skipped_no_date', 0)
                        skip_no_hn = info.get('skipped_no_hn', 0)
                        if skip_no_date or skip_no_hn:
                            st.warning(
                                f"⚠️ มี **{skip_no_date + skip_no_hn} row** "
                                f"ที่ skip เพราะข้อมูลไม่ครบ "
                                f"(missing op_date: {skip_no_date}, "
                                f"missing HN: {skip_no_hn})"
                            )

                        if cost_path:
                            if 'cost_error' in info:
                                st.warning(
                                    f"⚠️ Cost Excel: {info['cost_error']}")
                            else:
                                cm = info['cost_matched']
                                cnf = info['cost_not_found']
                                st.info(
                                    f"💰 Cost matched: **{cm} เคส**, "
                                    f"ไม่เจอใน DB: {cnf}"
                                )
                                # ⭐ แยกแสดง NOT_FOUND ทั้งหมด (สำคัญ — บอกว่า
                                # เคสไหนหาย จาก schedule.csv ไม่ครบ
                                # หรือถูก skip เพราะข้อมูลไม่ครบ)
                                not_found_rows = [
                                    s for s in info.get('cost_samples', [])
                                    if s.get('status') == 'NOT FOUND'
                                ]
                                if not_found_rows:
                                    st.markdown(
                                        f"**🚨 เคสที่อยู่ใน Cost Excel "
                                        f"แต่ไม่มีใน DB ({len(not_found_rows)} เคส):**"
                                    )
                                    st.caption(
                                        "เคสเหล่านี้น่าจะอยู่ใน schedule.csv "
                                        "แต่ถูก skip — ตรวจดูว่ามีข้อมูลครบ "
                                        "(เช่น opedate, hn) หรือไม่"
                                    )
                                    df_nf = pd.DataFrame(not_found_rows)
                                    st.dataframe(df_nf,
                                                 use_container_width=True,
                                                 hide_index=True)

                        # Sample รายชื่อเคสที่ import
                        if info.get('sample_results'):
                            st.markdown("**ตัวอย่างเคสที่ import (10 แรก):**")
                            df_s = pd.DataFrame(info['sample_results'])
                            st.dataframe(df_s, use_container_width=True,
                                         hide_index=True)

                        if btn_bulk_import:
                            st.info(
                                "🔄 กดปุ่ม Rerun (R) บน browser — "
                                "หรือเปิด tab \"📈 สถิติย้อนหลัง\" ดูข้อมูลใหม่")
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
                    finally:
                        for p in tmp_paths:
                            try: _os.unlink(p)
                            except OSError: pass
                        if cost_path:
                            try: _os.unlink(cost_path)
                            except OSError: pass

        # =========================================================
        # ② Import Pre-Merged CSV (เตรียมไฟล์ภายนอกแล้ว upload)
        # =========================================================
        with st.expander(
                "📥 ② Import Pre-Merged CSV/Excel (1 ไฟล์ครบ)",
                expanded=False):
            st.caption(
                "ใช้กรณีเตรียมไฟล์ merged มาเองภายนอก "
                "(เช่น script รวม sched + intraop + cost ก่อนแล้ว)\n\n"
                "ไฟล์ต้องมี columns: **op_date** (YYYY-MM-DD), **hn** "
                "และ optional ทุก column ใน DB เช่น procedure_name, "
                "surgeon_name, case_category, in_or_at, op_end_at, "
                "actual_duration_min, treatment_cost, patho_cost, ...\n\n"
                "Skip rows ที่ HN+Date ซ้ำกับ DB อยู่แล้ว"
            )
            merged_file = st.file_uploader(
                "📄 Merged CSV หรือ Excel",
                type=['csv', 'xlsx', 'xls'], key="merged_csv_file",
            )
            if merged_file is not None:
                mb1, mb2 = st.columns(2)
                with mb1:
                    btn_m_preview = st.button(
                        "🔍 Preview", use_container_width=True,
                        key="btn_merged_preview")
                with mb2:
                    btn_m_apply = st.button(
                        "✅ Import", type="primary",
                        use_container_width=True, key="btn_merged_apply")

                if btn_m_preview or btn_m_apply:
                    import tempfile, os as _os
                    tmp_p = None
                    try:
                        merged_file.seek(0)
                        suffix = ('.xlsx'
                                  if merged_file.name.endswith(('.xlsx', '.xls'))
                                  else '.csv')
                        with tempfile.NamedTemporaryFile(
                                delete=False, suffix=suffix,
                                mode='wb') as tmp:
                            tmp.write(merged_file.read())
                            tmp_p = tmp.name

                        from import_historical import import_merged_csv
                        info = import_merged_csv(
                            tmp_p, dry_run=not btn_m_apply)

                        if 'error' in info:
                            st.error(f"❌ {info['error']}")
                        else:
                            mode = "✅ Imported" if btn_m_apply else "🔍 Preview"
                            st.success(
                                f"{mode} — เพิ่ม **{info['inserted']} เคส**, "
                                f"ข้าม {info['skipped_duplicate']} ซ้ำ, "
                                f"{info['skipped_invalid']} invalid"
                            )
                            if info.get('columns_ignored'):
                                st.caption(
                                    f"ℹ️ Columns ที่ไม่ตรงกับ DB schema "
                                    f"(ถูก ignore): "
                                    f"{', '.join(info['columns_ignored'][:10])}"
                                )
                            if info.get('samples'):
                                st.markdown("**ตัวอย่างเคสที่ import:**")
                                df_m = pd.DataFrame(info['samples'])
                                st.dataframe(df_m, use_container_width=True,
                                             hide_index=True)
                            if btn_m_apply:
                                st.info("🔄 กดปุ่ม R เพื่อ refresh")
                    except Exception as e:
                        import traceback
                        st.error(f"❌ Error: {e}")
                        st.code(traceback.format_exc())
                    finally:
                        if tmp_p:
                            try: _os.unlink(tmp_p)
                            except OSError: pass

        with st.expander("🚨 ③ ล้าง DB สะอาดหมดจด (Clean Wipe)", expanded=False):
            from minor_or_db import get_db_table_counts, clear_all_data
            counts = get_db_table_counts()
            total_rows = sum(counts.values())

            st.error(
                f"⚠️ **เตือน: การลบนี้ไม่สามารถย้อนกลับได้!**\n\n"
                f"จะลบข้อมูล **ทั้งหมด {total_rows} แถว** จากทุก table:\n"
                f"- 🏥 **cases**: {counts.get('cases', 0)} เคส "
                "(รวม walk-in ที่เพิ่มผ่าน UI)\n"
                f"- 📝 **audit_log**: {counts.get('audit_log', 0)} รายการ "
                "(history การแก้ไข)\n"
                f"- ⚙️ **room_settings**: {counts.get('room_settings', 0)} แถว "
                "(nurse + ห้อง)\n\n"
                "🛡️ **ระบบจะตั้ง flag ป้องกัน auto-import** อัตโนมัติ — "
                "DB จะอยู่ในสถานะว่างจริงหลัง reboot จนกว่ามุ้กกจะ upload "
                "ไฟล์ผ่าน UI (flag จะถูกล้างเอง)"
            )

            confirm_wipe = st.checkbox(
                f"ฉันยืนยันว่าต้องการลบ DB ทั้งหมด ({total_rows} แถว)",
                key="clear_db_confirm",
            )
            btn_wipe = st.button(
                "🔴 ล้าง DB ทั้งหมด (Clean Wipe)",
                type="primary",
                disabled=not confirm_wipe or total_rows == 0,
                use_container_width=True,
                key="btn_clear_db",
            )
            if btn_wipe and confirm_wipe:
                try:
                    result = clear_all_data()
                    n_total = sum(result.values())
                    st.success(
                        f"✅ ลบเรียบร้อย — **{n_total} แถว** ถูกลบจาก DB\n\n"
                        f"- cases: {result.get('cases', 0)} เคส\n"
                        f"- audit_log: {result.get('audit_log', 0)} รายการ\n"
                        f"- room_settings: {result.get('room_settings', 0)} แถว"
                    )
                    st.info(
                        "🛡️ **ตั้ง flag กัน auto-import แล้ว** — reboot ครั้งหน้า "
                        "จะไม่โหลด historical_data/ ทับ\n\n"
                        "📌 **ขั้นต่อไป:**\n\n"
                        "1. ไปหน้า **ตารางผ่าตัด** (ทางเมนูซ้าย)\n"
                        "2. **Upload CSV** ของตารางผ่าตัดที่ต้องการ\n"
                        "3. flag จะถูกล้างอัตโนมัติเมื่อ upload สำเร็จ\n\n"
                        "💡 ถ้าอยากให้ auto-import จาก `historical_data/` "
                        "วิ่งใหม่: ลบ DB อีกครั้ง แล้วอย่า upload — กด reboot — "
                        "**แต่ flag ยังกันอยู่!** ต้องล้าง flag manual "
                        "ผ่าน Python console (รายละเอียดถามได้)"
                    )
                except Exception as e:
                    st.error(f"❌ Error: {e}")

    # -- TAB 3: AI Prediction (งานวิจัย) --
    with tab_ai:
        _render_ai_research_tab()

    # Auto refresh hint
    st.markdown("""
    <div style="text-align:center;margin-top:24px;padding:8px;color:#9e9e9e;font-size:11px;">
        💡 กด <b>R</b> หรือ <b>F5</b> เพื่อรีเฟรชข้อมูล
    </div>
    """, unsafe_allow_html=True)
