"""
Minor OR — OR Board + Statistics Pages
"""
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import json


def page_or_board():
    st.markdown('<h1 style="color:#2c3e50;font-size:28px;font-weight:700;">🏥 OR Board — ห้องผ่าตัดเล็ก</h1>', unsafe_allow_html=True)

    cases = st.session_state.patient_cases
    if not cases:
        st.info("ยังไม่มีผู้ป่วย — อัพโหลด CSV ที่หน้า 📋 วางแผนตาราง แล้วกด 📤 ส่งเข้า OR Board")
        return

    n_not = sum(1 for c in cases if c['status'] == 'not_arrived')
    n_hold = sum(1 for c in cases if c['status'] == 'holding_pre')
    n_inor = sum(1 for c in cases if c['status'] == 'in_or')
    n_post = sum(1 for c in cases if c['status'] == 'holding_post')
    n_rec = sum(1 for c in cases if c['status'] == 'recovery')
    n_done = sum(1 for c in cases if c['status'] == 'discharged')

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("⬜ ยังไม่มา", n_not)
    m2.metric("🟡 รอผ่าตัด", n_hold)
    m3.metric("🔵 ในห้องผ่าตัด", n_inor)
    m4.metric("🟢 รอจำหน่าย", n_post + n_rec)
    m5.metric("✅ จำหน่าย", n_done)

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        f"⬜ ยังไม่มา ({n_not})", f"🟡 รอผ่าตัด ({n_hold})",
        f"🔵 ห้องผ่าตัด ({n_inor})", f"🟢 รอจำหน่าย ({n_post + n_rec})",
        f"✅ จำหน่าย ({n_done})"
    ])

    # === TAB 1: ยังไม่มา ===
    with tab1:
        na = [(i, c) for i, c in enumerate(cases) if c['status'] == 'not_arrived']
        if not na:
            st.caption("ไม่มีผู้ป่วยที่ยังไม่มา")
        else:
            st.markdown(f'<div style="background:linear-gradient(135deg,#95a5a6,#bdc3c7);color:white;padding:8px 14px;border-radius:8px;margin-top:12px;font-weight:700;">ห้องผ่าตัดเล็ก 1 ({len(na)} เคส)</div>', unsafe_allow_html=True)
            for idx, case in na:
                col_i, col_b = st.columns([5, 1])
                with col_i:
                    t = "TF" if case.get('is_tf') else f'{case.get("sched_hour",8):02d}:{case.get("sched_min",0):02d}'
                    st.markdown(f'<div style="border-left:4px solid #bdc3c7;background:#f8f9fa;padding:8px 12px;margin:2px 0;border-radius:0 4px 4px 0;"><b>#{case.get("ororder","-")}</b> {t} &nbsp; <b>{case["name"]}</b> <span style="color:#7f8c8d;font-size:12px;">HN: {case.get("hn","-")} | อายุ {case.get("age","-")} ปี</span><br><span style="font-size:12px;">Op: {case["procedure"]} | Surg: {case.get("surgeon","-")}</span></div>', unsafe_allow_html=True)
                with col_b:
                    if st.button("รับเข้า", key=f"arrive_{idx}", use_container_width=True):
                        cases[idx]['status'] = 'holding_pre'
                        cases[idx]['time_arrived_holding'] = datetime.now()
                        st.rerun()

    # === TAB 2: รอผ่าตัด — JS count-up timer ===
    with tab2:
        hp = [(i, c) for i, c in enumerate(cases) if c['status'] == 'holding_pre']
        if not hp:
            st.caption("ไม่มีผู้ป่วยรอผ่าตัด")
        else:
            st.markdown(f'<div style="background:linear-gradient(135deg,#f39c12,#f1c40f);color:white;padding:8px 14px;border-radius:8px;margin-top:12px;font-weight:700;">🟡 ห้องผ่าตัดเล็ก 1 ({len(hp)} เคส)</div>', unsafe_allow_html=True)
            for idx, case in hp:
                start_ts = case['time_arrived_holding'].timestamp() if case.get('time_arrived_holding') else datetime.now().timestamp()
                ai_min = case.get('effective_min', case.get('predicted_min', '?'))

                col_i, col_b = st.columns([5, 1])
                with col_i:
                    html = (
                        '<html><head><style>'
                        "*{margin:0;padding:0}body{font-family:'Sarabun',sans-serif;background:transparent}"
                        '.card{border-left:4px solid #f1c40f;background:#fffde7;padding:8px 12px;border-radius:0 4px 4px 0}'
                        '.nm{font-weight:700;font-size:14px}.hn{color:#7f8c8d;font-size:12px}'
                        '.badge{color:white;padding:2px 8px;border-radius:10px;font-size:12px;display:inline-block;min-width:140px;text-align:center;font-weight:600}'
                        '.det{font-size:12px;color:#2c3e50;margin-top:2px}'
                        '</style></head><body>'
                        f'<div class="card"><span class="nm">{case["name"]}</span> '
                        f'<span class="hn">HN: {case.get("hn","-")}</span> '
                        f'<span class="badge" id="t"></span><br>'
                        f'<span class="det">Op: {case["procedure"]} | Surg: {case.get("surgeon","-")} | AI: {ai_min} นาที</span></div>'
                        f'<script>var s={start_ts},e=document.getElementById("t");'
                        'function u(){var n=Date.now()/1000,d=Math.floor((n-s)/60),sec=Math.floor((n-s)%60),'
                        'c=d<30?"#27ae60":d<60?"#f39c12":"#e74c3c";'
                        'e.style.background=c;'
                        'e.textContent="\u23F1 \u0E23\u0E2D\u0E41\u0E25\u0E49\u0E27 "+d+" \u0E19\u0E32\u0E17\u0E35 "+String(sec).padStart(2,"0")+" \u0E27\u0E34\u0E19\u0E32\u0E17\u0E35"}'
                        'u();setInterval(u,1000)</script></body></html>'
                    )
                    components.html(html, height=62)
                with col_b:
                    if st.button("เข้าห้อง", key=f"to_or_{idx}", use_container_width=True):
                        cases[idx]['status'] = 'in_or'
                        cases[idx]['or_room_assigned'] = 1
                        cases[idx]['time_entered_or'] = datetime.now()
                        st.session_state.or_rooms[1]['status'] = 'กำลังผ่าตัด'
                        st.session_state.or_rooms[1]['current_case'] = cases[idx]
                        st.session_state.or_rooms[1]['start_time'] = datetime.now()
                        st.session_state.or_rooms[1]['predicted_time'] = cases[idx].get('effective_min', 30)
                        st.session_state.statistics['total_cases'] += 1
                        st.rerun()

    # === TAB 3: ในห้องผ่าตัด — JS countdown + progress bar ===
    with tab3:
        ior = [(i, c) for i, c in enumerate(cases) if c['status'] == 'in_or']
        if not ior:
            st.caption("ไม่มีผู้ป่วยในห้องผ่าตัด")
        else:
            st.markdown(f'<div style="background:linear-gradient(135deg,#2c3e50,#3498db);color:white;padding:8px 14px;border-radius:8px;margin-top:12px;font-weight:700;">🔵 ห้องผ่าตัดเล็ก 1 ({len(ior)} เคส)</div>', unsafe_allow_html=True)
            for idx, case in ior:
                eff = case.get('effective_min') or case.get('ai_predicted_min') or case.get('predicted_min') or 30
                entered = case.get('time_entered_or')
                sts = entered.timestamp() if entered else datetime.now().timestamp()
                ai_txt = case.get('ai_predicted_min', case.get('predicted_min', '?'))
                ov_txt = f' | User: {case["user_override_min"]} min' if case.get('user_override_min') else ''

                html = (
                    '<html><head><style>'
                    "*{margin:0;padding:0}body{font-family:'Sarabun',sans-serif;background:transparent}"
                    '.card{border-left:4px solid #3498db;background:#e3f2fd;padding:10px 12px;border-radius:0 4px 4px 0}'
                    '.nm{font-weight:700;font-size:14px}.hn{color:#7f8c8d;font-size:12px}'
                    '.badge{color:white;padding:2px 10px;border-radius:10px;font-size:13px;font-weight:700;display:inline-block;min-width:150px;text-align:center}'
                    '.det{font-size:12px;margin-top:2px}'
                    '.bar-bg{background:#ddd;border-radius:6px;height:10px;margin-top:6px;overflow:hidden}'
                    '.bar-fill{height:100%;border-radius:6px;transition:width 0.5s}'
                    '.stats{font-size:11px;color:#7f8c8d;margin-top:3px}'
                    '</style></head><body>'
                    f'<div class="card"><span class="nm">{case["name"]}</span> '
                    f'<span class="hn">HN: {case.get("hn","-")}</span> '
                    f'<span class="badge" id="t"></span><br>'
                    f'<span class="det">Op: {case["procedure"]} | Surg: {case.get("surgeon","-")}</span>'
                    f'<div class="bar-bg"><div class="bar-fill" id="b" style="width:0%"></div></div>'
                    f'<div class="stats" id="s"></div></div>'
                    f'<script>var s={sts},eff={eff},'
                    'te=document.getElementById("t"),ba=document.getElementById("b"),st=document.getElementById("s");'
                    'function u(){var n=Date.now()/1000,el=Math.floor((n-s)/60),sec=Math.floor((n-s)%60),'
                    'ts=eff*60,rs=Math.max(0,ts-(n-s)),rm=Math.floor(rs/60),rse=Math.floor(rs%60),'
                    'os=n-s-ts,om=Math.floor(os/60),ose=Math.floor(os%60),'
                    'pct=Math.min(100,Math.max(0,Math.round(el/eff*100))),'
                    'c=rs>600?"#27ae60":rs>0?"#f39c12":"#e74c3c";'
                    'if(rs>0)te.textContent="\u23F3 \u0E40\u0E2B\u0E25\u0E37\u0E2D "+rm+" \u0E19. "+String(rse).padStart(2,"0")+" \u0E27.";'
                    'else te.textContent="\u26A0\uFE0F \u0E40\u0E01\u0E34\u0E19 "+om+" \u0E19. "+String(ose).padStart(2,"0")+" \u0E27.";'
                    'te.style.background=c;ba.style.background=c;ba.style.width=pct+"%";'
                    f'st.textContent="\u0E1C\u0E48\u0E32\u0E41\u0E25\u0E49\u0E27 "+el+"/"+eff+" \u0E19\u0E32\u0E17\u0E35 ("+pct+"%) | AI: {ai_txt} \u0E19\u0E32\u0E17\u0E35{ov_txt}"'
                    '}u();setInterval(u,1000)</script></body></html>'
                )
                components.html(html, height=90)

                col_ov, col_dest, col_done = st.columns([2, 2, 1])
                with col_ov:
                    new_t = st.number_input("แก้เวลา", min_value=5, max_value=600, value=int(eff), key=f"ov_{idx}", label_visibility="collapsed")
                    if new_t != int(eff):
                        if st.button("💾", key=f"sv_{idx}"):
                            cases[idx]['user_override_min'] = new_t
                            cases[idx]['effective_min'] = new_t
                            st.rerun()
                with col_dest:
                    dest = st.selectbox("ส่งไป", ["รับ-ส่ง (หลังผ่าตัด)", "ห้องพักฟื้น"], key=f"dest_{idx}", label_visibility="collapsed")
                with col_done:
                    if st.button("ผ่าเสร็จ", key=f"done_{idx}", type="primary", use_container_width=True):
                        now = datetime.now()
                        cases[idx]['time_exited_or'] = now
                        if cases[idx].get('time_entered_or'):
                            cases[idx]['actual_duration_min'] = int((now - cases[idx]['time_entered_or']).total_seconds() / 60)
                        cases[idx]['status'] = 'recovery' if dest == "ห้องพักฟื้น" else 'holding_post'
                        st.session_state.or_rooms[1].update({'status': 'ว่าง', 'current_case': None, 'start_time': None})
                        st.session_state.statistics['completed_cases'] += 1
                        record = {
                            'timestamp': now.isoformat(),
                            'case_id': case.get('id'),
                            'procedure': case.get('procedure'),
                            'surgeon': case.get('surgeon'),
                            'division': case.get('division', '75'),
                            'age': case.get('age'),
                            'op_hour': case.get('op_hour'),
                            'scrub': case.get('scrub_nurse', ''),
                            'circ': case.get('circ_nurse', ''),
                            'ai_predicted_min': case.get('ai_predicted_min', case.get('predicted_min')),
                            'user_override_min': case.get('user_override_min'),
                            'actual_duration_min': cases[idx].get('actual_duration_min'),
                            'wait_min': case.get('wait_min', 0),
                            'room': 1,
                        }
                        st.session_state.statistics['case_history'].append(record)
                        # Persistent save for Top N stats across sessions
                        try:
                            from minor_or_core import append_case_history
                            append_case_history(record)
                        except Exception as ex:
                            st.warning(f"บันทึก history ไม่สำเร็จ: {ex}")
                        st.rerun()

    # === TAB 4: รอจำหน่าย ===
    with tab4:
        post = [(i, c) for i, c in enumerate(cases) if c['status'] in ('holding_post', 'recovery')]
        if not post:
            st.caption("ไม่มีผู้ป่วยรอจำหน่าย")
        for idx, case in post:
            col_i, col_b = st.columns([5, 1])
            with col_i:
                at = f' | ผ่าจริง {case["actual_duration_min"]} นาที' if case.get("actual_duration_min") else ''
                lbl = "✅ ผ่าเสร็จ" if case['status'] == 'holding_post' else "🟣 พักฟื้น"
                bg = "#e8f5e9" if case['status'] == 'holding_post' else "#f3e5f5"
                bc = "#27ae60" if case['status'] == 'holding_post' else "#9b59b6"
                st.markdown(f'<div style="border-left:4px solid {bc};background:{bg};padding:8px 12px;margin:2px 0;border-radius:0 4px 4px 0;"><span style="background:{bc};color:white;padding:2px 8px;border-radius:10px;font-size:11px;">{lbl}</span> <b>{case["name"]}</b> <span style="color:#7f8c8d;font-size:12px;">HN: {case.get("hn","-")}{at}</span><br><span style="font-size:12px;">Op: {case["procedure"]}</span></div>', unsafe_allow_html=True)
            with col_b:
                if st.button("จำหน่าย", key=f"disch_{idx}", use_container_width=True):
                    cases[idx]['status'] = 'discharged'
                    cases[idx]['time_discharged'] = datetime.now()
                    st.rerun()

    # === TAB 5: จำหน่ายแล้ว ===
    with tab5:
        done = [c for c in cases if c['status'] == 'discharged']
        if not done:
            st.caption("ไม่มีผู้ป่วยจำหน่าย")
        else:
            st.success(f"จำหน่ายแล้ว {len(done)} เคส")
            for c in done:
                at = f' | ผ่าจริง {c["actual_duration_min"]} นาที | AI {c.get("ai_predicted_min","?")} นาที' if c.get("actual_duration_min") else ''
                st.markdown(f'<div style="border-left:4px solid #95a5a6;background:#f8f9fa;padding:6px 12px;margin:2px 0;border-radius:0 4px 4px 0;">✅ <b>{c["name"]}</b> <span style="color:#7f8c8d;font-size:12px;">HN: {c.get("hn","-")}{at}</span></div>', unsafe_allow_html=True)


# ============================================================================
# STATISTICS PAGE
# ============================================================================

def page_statistics():
    st.markdown('<h1 style="color:#2c3e50;font-size:28px;font-weight:700;">📊 สถิติและรายงาน</h1>', unsafe_allow_html=True)

    st.markdown('<h3 style="color:#34495e;font-size:18px;font-weight:600;">📈 สรุปรายวัน</h3>', unsafe_allow_html=True)
    tc = st.session_state.statistics['total_cases']
    cc = st.session_state.statistics['completed_cases']
    xc = st.session_state.statistics['cancelled_cases']

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f'<div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;"><div style="color:#7f8c8d;font-size:14px;font-weight:600;">เคสทั้งหมด</div><div style="color:#2c3e50;font-size:32px;font-weight:bold;">{tc}</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;"><div style="color:#7f8c8d;font-size:14px;font-weight:600;">เสร็จแล้ว</div><div style="color:#27ae60;font-size:32px;font-weight:bold;">{cc}</div></div>', unsafe_allow_html=True)
    with c3:
        rate = round((cc / tc * 100) if tc > 0 else 0)
        st.markdown(f'<div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;"><div style="color:#7f8c8d;font-size:14px;font-weight:600;">อัตราสำเร็จ</div><div style="color:#2c3e50;font-size:32px;font-weight:bold;">{rate}%</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;"><div style="color:#7f8c8d;font-size:14px;font-weight:600;">ยกเลิก</div><div style="color:#e74c3c;font-size:32px;font-weight:bold;">{xc}</div></div>', unsafe_allow_html=True)

    # AI vs Actual chart
    st.markdown('<h3 style="color:#34495e;font-size:18px;font-weight:600;margin-top:20px;">🤖 AI ทำนายเวลาใช้ห้อง vs เวลาจริง</h3>', unsafe_allow_html=True)
    history = st.session_state.statistics.get('case_history', [])
    hist = [h for h in history if h.get('actual_duration_min') and h.get('ai_predicted_min')]

    if hist:
        df_h = pd.DataFrame(hist)
        df_h['proc_short'] = df_h['procedure'].str[:40]
        df_h['error'] = df_h['actual_duration_min'] - df_h['ai_predicted_min']

        fig = go.Figure(data=[
            go.Bar(name='AI ทำนายเวลาใช้ห้อง', x=df_h['proc_short'], y=df_h['ai_predicted_min'], marker_color='#3498db'),
            go.Bar(name='เวลาจริง (room duration)', x=df_h['proc_short'], y=df_h['actual_duration_min'], marker_color='#2ecc71'),
        ])
        fig.update_layout(barmode='group', title='AI ทำนายเวลาใช้ห้อง vs เวลาจริง', font=dict(family="Sarabun"), height=400, xaxis_title='หัตถการ', yaxis_title='นาที (Room Duration)')
        st.plotly_chart(fig, use_container_width=True)

        mae = df_h['error'].abs().mean()
        w10 = (df_h['error'].abs() <= 10).mean() * 100
        w15 = (df_h['error'].abs() <= 15).mean() * 100
        ec1, ec2, ec3 = st.columns(3)
        ec1.metric("MAE", f"{mae:.1f} นาที")
        ec2.metric("±10 นาที", f"{w10:.0f}%")
        ec3.metric("±15 นาที", f"{w15:.0f}%")
    else:
        st.info("ยังไม่มีข้อมูล AI vs เวลาจริง — ใช้ OR Board แล้วจะเก็บสถิติอัตโนมัติ")

    # Pie chart
    st.markdown('<h3 style="color:#34495e;font-size:18px;font-weight:600;margin-top:20px;">📉 สถานะเคส</h3>', unsafe_allow_html=True)
    fig_pie = px.pie(values=[cc, tc - cc, xc], names=['เสร็จแล้ว', 'รอดำเนินการ', 'ยกเลิก'],
                     color_discrete_map={'เสร็จแล้ว': '#27ae60', 'รอดำเนินการ': '#f39c12', 'ยกเลิก': '#e74c3c'})
    fig_pie.update_layout(font=dict(family="Sarabun"), height=350)
    st.plotly_chart(fig_pie, use_container_width=True)

    # ========================================================================
    # TOP N OPERATION STATISTICS (persistent across sessions)
    # ========================================================================
    st.markdown('<h3 style="color:#34495e;font-size:18px;font-weight:600;margin-top:28px;">🏆 Top Statistics (ข้อมูลสะสม)</h3>', unsafe_allow_html=True)
    from minor_or_core import (load_case_history, top_n_procedures,
                               top_n_surgeons, top_n_surg_proc, top_n_nurses)
    df_hist = load_case_history()

    if df_hist.empty:
        st.info("ยังไม่มีข้อมูลสะสม — กด 'ผ่าเสร็จ' ใน OR Board จะเก็บเข้า case_history.csv อัตโนมัติ")
    else:
        cc1, cc2, cc3 = st.columns([1, 1, 2])
        with cc1:
            top_n = st.selectbox("แสดง Top", [5, 10, 20], index=1, key="topn_sel")
        with cc2:
            scope = st.selectbox("ขอบเขต", ["ทั้งหมด", "30 วันล่าสุด", "7 วันล่าสุด"], key="topn_scope")
        with cc3:
            st.caption(f"📦 ข้อมูลสะสมทั้งหมด: **{len(df_hist)}** เคส")

        df_v = df_hist.copy()
        df_v['timestamp'] = pd.to_datetime(df_v['timestamp'], errors='coerce')
        if scope == "30 วันล่าสุด":
            df_v = df_v[df_v['timestamp'] >= (datetime.now() - pd.Timedelta(days=30))]
        elif scope == "7 วันล่าสุด":
            df_v = df_v[df_v['timestamp'] >= (datetime.now() - pd.Timedelta(days=7))]

        t1, t2, t3, t4, t5 = st.tabs([
            "🔝 หัตถการยอดนิยม", "⏱️ หัตถการใช้เวลานาน",
            "👨‍⚕️ ศัลยแพทย์", "🤝 Surgeon × Procedure", "👩‍⚕️ พยาบาล"
        ])

        with t1:
            st.markdown(f"**Top {top_n} หัตถการ (ตามจำนวนเคส)**")
            df_top = top_n_procedures(df_v, by='volume', n=top_n)
            if not df_top.empty:
                st.dataframe(df_top, use_container_width=True, hide_index=True)
                fig = px.bar(df_top, x='procedure', y='n_cases',
                             title=f'Top {top_n} หัตถการที่ทำบ่อยที่สุด',
                             color='avg_duration', color_continuous_scale='Blues',
                             labels={'n_cases': 'จำนวนเคส', 'procedure': 'หัตถการ'})
                fig.update_layout(height=380, font=dict(family="Sarabun"))
                st.plotly_chart(fig, use_container_width=True)

        with t2:
            st.markdown(f"**Top {top_n} หัตถการ (ใช้เวลาเฉลี่ยนานที่สุด)**")
            df_top = top_n_procedures(df_v, by='avg_duration', n=top_n)
            if not df_top.empty:
                st.dataframe(df_top, use_container_width=True, hide_index=True)
                st.markdown(f"**Top {top_n} หัตถการ MAE สูง (ทำนายยาก — ต้องการข้อมูลเพิ่ม)**")
                df_mae = top_n_procedures(df_v, by='volume', n=50).sort_values('mae', ascending=False).head(top_n)
                st.dataframe(df_mae, use_container_width=True, hide_index=True)

        with t3:
            colS1, colS2 = st.columns(2)
            with colS1:
                st.markdown(f"**Top {top_n} ศัลยแพทย์ (ตามจำนวนเคส)**")
                df_s = top_n_surgeons(df_v, by='volume', n=top_n)
                if not df_s.empty:
                    st.dataframe(df_s, use_container_width=True, hide_index=True)
            with colS2:
                st.markdown(f"**Top {top_n} ศัลยแพทย์ (เวลาเฉลี่ยนาน)**")
                df_s = top_n_surgeons(df_v, by='avg_duration', n=top_n)
                if not df_s.empty:
                    st.dataframe(df_s, use_container_width=True, hide_index=True)

        with t4:
            st.markdown(f"**Top {top_n} คู่ Surgeon × Procedure (ทำบ่อย = ทำนายแม่น)**")
            df_sp = top_n_surg_proc(df_v, n=top_n)
            if not df_sp.empty:
                st.dataframe(df_sp, use_container_width=True, hide_index=True)
                st.caption("💡 คู่ที่มี n_cases ≥ 3 จะให้ AI ทำนายด้วย confidence **สูงมาก**")

        with t5:
            colN1, colN2 = st.columns(2)
            with colN1:
                st.markdown(f"**Top {top_n} Scrub Nurse**")
                df_n = top_n_nurses(df_v, role='scrub', n=top_n)
                if not df_n.empty:
                    st.dataframe(df_n, use_container_width=True, hide_index=True)
            with colN2:
                st.markdown(f"**Top {top_n} Circulating Nurse**")
                df_n = top_n_nurses(df_v, role='circ', n=top_n)
                if not df_n.empty:
                    st.dataframe(df_n, use_container_width=True, hide_index=True)

    # Export
    st.markdown('<h3 style="color:#34495e;font-size:18px;font-weight:600;margin-top:20px;">💾 ส่งออกข้อมูล</h3>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("📥 Export JSON (session)"):
            j = json.dumps({'timestamp': datetime.now().isoformat(), 'statistics': st.session_state.statistics}, indent=2, ensure_ascii=False, default=str)
            st.download_button("⬇️ JSON", j, "minor_or_export.json", "application/json")
    with c2:
        if hist and st.button("📊 Export CSV (session)"):
            csv = pd.DataFrame(hist).to_csv(index=False).encode('utf-8-sig')
            st.download_button("⬇️ CSV", csv, "minor_or_history.csv", "text/csv")
    with c3:
        if not df_hist.empty and st.button("🗄️ Export Full History"):
            csv_all = df_hist.to_csv(index=False).encode('utf-8-sig')
            st.download_button("⬇️ Full CSV", csv_all, "case_history_full.csv", "text/csv")
