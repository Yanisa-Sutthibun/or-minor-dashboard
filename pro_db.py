           border-bottom: {thickness_px}px solid {color};
       "></div>
       """,
unsafe_allow_html=True
)

# ===============================
# PASSWORD PROTECTION
# ===============================
try:
PASSWORD = st.secrets["APP_PASSWORD"]
except Exception:
PASSWORD = "pghnurse30"

if "authenticated" not in st.session_state:
st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
st.markdown("### 🔐 เข้าสู่ระบบ OR Dashboard")
col1, col2 = st.columns([1, 2])
with col2:
password_input = st.text_input("กรุณาใส่รหัสผ่าน", type="password", key="pw_input")
if st.button("เข้าสู่ระบบ", key="login_btn"):
if password_input == PASSWORD:
st.session_state["authenticated"] = True
st.success("เข้าสู่ระบบสำเร็จ!")
st.rerun()
else:
st.error("รหัสผ่านไม่ถูกต้อง")
st.stop()

# ===============================
# TOP BAR: Manual Refresh only
# ===============================
top_c1, top_c2, top_c3 = st.columns([1.2, 6, 1.2])
with top_c1:
if st.button("🔄 Refresh", key="btn_refresh"):
st.rerun()
with top_c2:
st.caption("ℹ️ กด Refresh เพื่ออัปเดต")
with top_c3:
if st.button("ออกจากระบบ", key="btn_logout"):
st.session_state["authenticated"] = False
st.rerun()

# แทน st.divider()
small_divider(width_pct=70, thickness_px=2, color="#e6e6e6", margin_px=10)

# ===============================
@@ -307,15 +306,19 @@
# ===============================
with st.sidebar:
st.header("Upload file")
    uploaded_file = st.file_uploader("อัปโหลดไฟล์ Excel (.xlsx หรือ .xls)", type=["xlsx", "xls"], key="uploader_main")
    uploaded_file = st.file_uploader(
        "อัปโหลดไฟล์ Excel (.xlsx หรือ .xls)",
        type=["xlsx", "xls"],
        key="uploader_main"
    )

df_raw = None
active_file_name = None
active_file_bytes = None

if uploaded_file is not None:
active_file_name = uploaded_file.name
    active_file_bytes = uploaded_file.getvalue()  # ✅ สำคัญ
    active_file_bytes = uploaded_file.getvalue()
st.session_state["uploaded_name"] = active_file_name
st.session_state["uploaded_bytes"] = active_file_bytes
elif "uploaded_bytes" in st.session_state:
@@ -361,7 +364,7 @@
# ===============================
# MAIN CONTENT
# ===============================
# แสดงวันที่ผ่าตัดจากไฟล์ (opedate)
# แสดงวันที่ผ่าตัดจากไฟล์ (opedate) + ไม่มีเส้นใต้ฟ้า
if "opedate" in df_raw.columns:
opedate_raw = pd.to_datetime(df_raw["opedate"].dropna().iloc[0], errors="coerce")
if pd.notna(opedate_raw):
@@ -380,6 +383,7 @@
               font-weight: 600;
               color: #1f77b4;
               margin: 10px 0 4px 0;
                text-decoration: none;
           ">
               📅 ตารางผ่าตัดวันที่ {op_date_str}
           </div>
@@ -391,17 +395,17 @@
"<div style='text-align:center; font-size:22px; font-weight:600; margin:10px 0;'>📅 ตารางผ่าตัด</div>",
unsafe_allow_html=True
)
        small_divider(width_pct=25, thickness_px=2, color="#eeeeee", margin_px=8)
else:
st.markdown(
"<div style='text-align:center; font-size:22px; font-weight:600; margin:10px 0;'>📅 ตารางผ่าตัด</div>",
unsafe_allow_html=True
)
    small_divider(width_pct=25, thickness_px=2, color="#eeeeee", margin_px=8)

# แทน st.markdown("---")
small_divider(width_pct=70, thickness_px=2, color="#eeeeee", margin_px=12)

# ===============================
# OR SUMMARY
# ===============================
st.subheader("📊 OR-Minor Summary")

summary_df_temp, meta_temp, _ = build_daily_summary(df_raw, use_fuzzy=False, fuzzy_threshold=85)
@@ -417,30 +421,33 @@
st.markdown(f"<h2 style='text-align: center; color: black; margin-top: -10px;'>{total_cases}</h2>", unsafe_allow_html=True)

for i, cat in enumerate(display_cats):
    count = category_counts[cat]
    count = int(category_counts.get(cat, 0))
with cols[i+1]:
st.markdown(f"<h4 style='text-align: center; color: black;'>{cat}</h4>", unsafe_allow_html=True)
st.markdown(f"<h2 style='text-align: center; color: black; margin-top: -10px;'>{count}</h2>", unsafe_allow_html=True)

small_divider(width_pct=70, thickness_px=2, color="#eeeeee", margin_px=12)

# Operation On-going Card
# ===============================
# OPERATION ON-GOING
# ===============================
st.subheader("⏳ Operation On-going")

proc_col = pick_text_col(df_raw, ["icd9cm_name", "operation", "opname", "procedure", "proc", "หัตถการ", "ผ่าตัด"])
if proc_col:
    df_raw["__proc_category__"] = df_raw[proc_col].apply(classify_proc_category_rules)
    df_tmp = df_raw.copy()
    df_tmp["__proc_category__"] = df_tmp[proc_col].apply(classify_proc_category_rules)

completed_by_category = {}
for idx in st.session_state.get("completed_cases", set()):
        if idx < len(df_raw):
            cat = df_raw.iloc[idx]["__proc_category__"]
        if idx < len(df_tmp):
            cat = df_tmp.iloc[idx]["__proc_category__"]
completed_by_category[cat] = completed_by_category.get(cat, 0) + 1

ongoing_counts = {}
for cat, total in category_counts.items():
completed = completed_by_category.get(cat, 0)
        remaining = total - completed
        remaining = int(total) - int(completed)
if remaining > 0:
ongoing_counts[cat] = remaining

@@ -466,6 +473,7 @@
year_short_cur = year_th_cur % 100
current_time_str = f"{current_time.day:02d}/{current_time.month:02d}/{year_short_cur:02d} {current_time.strftime('%H:%M:%S')}"

# remaining ตาม completed_cases (ใช้รวมทั้งระบบ)
remaining_cases = total_cases - len(st.session_state.get("completed_cases", set()))

status_cols = st.columns(3)
@@ -479,38 +487,74 @@
small_divider(width_pct=70, thickness_px=2, color="#eeeeee", margin_px=12)

# ===============================
# ✅ รายการผ่าตัดวันนี้ (ซ่อนข้อมูลระบุตัวบุคคล)
# ✅ รายการผ่าตัดวันนี้ (ไม่แสดงชื่อผู้ป่วย/ชื่อแพทย์) + ปุ่มเสร็จแล้ว
# ===============================
st.subheader("✅ รายการผ่าตัดวันนี้ (ไม่แสดงชื่อผู้ป่วย/ชื่อแพทย์)")

# แสดงเฉพาะข้อมูลที่ไม่ระบุตัวบุคคล: Operation + Proc note (ถ้ามี)
# ใช้คอลัมน์ที่ไม่ระบุตัวบุคคลเท่านั้น
safe_cols = []
if "icd9cm_name" in df_raw.columns:
safe_cols.append("icd9cm_name")
if "procnote" in df_raw.columns:
safe_cols.append("procnote")

if safe_cols:
if not safe_cols:
    st.info("ไม่พบคอลัมน์ Operation/Proc note สำหรับแสดงรายการแบบไม่ระบุตัวบุคคล")
else:
df_safe = df_raw.copy()
    # เรียงตามเวลา ถ้ามี

    # เรียงตามเวลา ถ้ามี (ช่วยให้ลำดับเสถียรขึ้น)
if "estmtime" in df_safe.columns:
df_safe = df_safe.sort_values("estmtime")

    # สร้าง index สำหรับ “กดเสร็จแล้ว”
df_safe = df_safe[safe_cols].copy().reset_index(drop=True)

    rename_map = {
        "icd9cm_name": "Operation",
        "procnote": "Proc note",
    }
    rename_map = {"icd9cm_name": "Operation", "procnote": "Proc note"}
df_safe.rename(columns=rename_map, inplace=True)

    df_show(df_safe, stretch=True)
else:
    st.info("ไม่พบคอลัมน์ Operation/Proc note สำหรับแสดงรายการแบบไม่ระบุตัวบุคคล")
    if "completed_cases" not in st.session_state:
        st.session_state["completed_cases"] = set()

    completed = st.session_state["completed_cases"]

    st.caption("กดปุ่ม **เสร็จแล้ว** เพื่อทำเครื่องหมายว่าเคสนั้นเสร็จ (ไม่ใช้ชื่อผู้ป่วย/ชื่อแพทย์)")

    header = st.columns([0.6, 3.5, 4.5, 1.4])
    header[0].markdown("**#**")
    header[1].markdown("**Operation**")
    header[2].markdown("**Proc note**")
    header[3].markdown("**สถานะ**")

    for i, row in df_safe.iterrows():
        c0, c1, c2, c3 = st.columns([0.6, 3.5, 4.5, 1.4])

        c0.write(i)
        c1.write(row.get("Operation", ""))

        proc_note = row.get("Proc note", "")
        c2.write("" if pd.isna(proc_note) else proc_note)

        if i in completed:
            c3.success("✓ เสร็จแล้ว")
        else:
            if c3.button("เสร็จแล้ว", key=f"done_safe_{i}"):
                completed.add(i)
                st.session_state["completed_cases"] = completed
                st.rerun()

    # ปุ่มรีเซ็ตสถานะ (เผื่อกดผิด)
    col_reset1, col_reset2 = st.columns([6, 1.5])
    with col_reset2:
        if st.button("รีเซ็ตสถานะ", key="reset_completed_safe"):
            st.session_state["completed_cases"] = set()
            st.rerun()

small_divider(width_pct=70, thickness_px=2, color="#eeeeee", margin_px=12)

# ===============================
# Daily case summary
# ===============================
st.subheader("📈 Daily case summary (เช้า/บ่าย/TF)")
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
@@ -538,7 +582,9 @@

small_divider(width_pct=70, thickness_px=2, color="#eeeeee", margin_px=12)

# Other review (ไม่ระบุตัวบุคคล)
# ===============================
# Other review (ไม่โชว์ข้อมูลดิบ/ชื่อคน)
# ===============================
st.subheader("🔍 Operation นอกเหนือที่ตั้งค่าไว้ (Other review)")
proc_col_used = meta.get("proc_col_used")
if not proc_col_used:
@@ -551,8 +597,4 @@
st.caption("ใช้รายการนี้เพิ่ม ALIASES หรือ pattern ได้")
df_show(unk_df, stretch=True)

# ===============================
# 🚫 ลบส่วนดูข้อมูลดิบออก (ป้องกันข้อมูลหลุด)
# ===============================
# with st.expander("ดูข้อมูลดิบ (preview 50 แถวแรก)"):
#     df_show(df_raw.head(50), stretch=True)
# ✅ ตัดส่วน “ดูข้อมูลดิบ (preview 50 แถวแรก)” ออกเพื่อป้องกันข้อมูลหลุด
