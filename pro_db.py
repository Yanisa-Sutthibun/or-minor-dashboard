import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import re
from io import BytesIO
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ===============================
# 0) CONFIG
# ===============================
st.set_page_config(page_title="OR-minor Schedule Dashboard", layout="wide")
st.title("OR-minor Schedule Dashboard 📊")

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
                st.success("เข้าสู่ระบบสำเร็จ! 🎉")
                st.rerun()
            else:
                st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()

# ===============================
# GOOGLE SHEET CONNECTION (ใช้ gspread มาตรฐาน)
# ===============================
SHEET_ID = "1xseEQo0ZqGrVA00yn9Y4LZtCw3kEb2zTF6ao4IbjfyA"
SHEET_NAME = "Sheet1"

@st.cache_resource(ttl=60)
def get_sheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
    return sheet

# ===============================
# SIDEBAR: UPLOAD FILE (Admin only)
# ===============================
with st.sidebar:
    st.header("Upload file (Admin only)")
    uploaded_file = st.file_uploader("อัปโหลดไฟล์ Excel (.xlsx หรือ .xls)", type=["xlsx", "xls"])

df_raw = None
if uploaded_file is not None:
    file_name = uploaded_file.name.lower()
    file_bytes = uploaded_file.read()
    file_stream = BytesIO(file_bytes)

    try:
        if file_name.endswith(".xlsx"):
            df_raw = pd.read_excel(file_stream, engine="openpyxl")
        elif file_name.endswith(".xls"):
            df_raw = pd.read_excel(file_stream, engine="xlrd")
        st.sidebar.success("อัปโหลดและบันทึกข้อมูลสำเร็จ!")
        
        # บันทึกข้อมูลลง Google Sheet
        sheet = get_sheet()
        sheet.clear()
        sheet.append_row(df_raw.columns.tolist())
        sheet.append_rows(df_raw.values.tolist())
    except Exception as e:
        st.sidebar.error(f"ไม่สามารถอ่านหรือบันทึกไฟล์ได้: {str(e)}")
        st.sidebar.info("ลองบันทึกไฟล์ใหม่จาก Excel แล้วอัปโหลดอีกครั้ง")

# ===============================
# LOAD DATA FROM SHEET (ทุกเครื่องดึงจากที่นี่)
# ===============================
try:
    sheet = get_sheet()
    data = sheet.get_all_values()
    if len(data) <= 1:  # มีแค่ header หรือว่าง
        st.info("ยังไม่มีข้อมูลใน Sheet — รอ Admin อัปโหลดไฟล์")
        st.stop()
    df_raw = pd.DataFrame(data[1:], columns=data[0])
    df_raw = df_raw.replace("", np.nan)
    df_raw = df_raw.dropna(how="all")
    if df_raw.empty:
        st.info("ยังไม่มีข้อมูลใน Sheet — รอ Admin อัปโหลดไฟล์")
        st.stop()
except Exception as e:
    st.error(f"ไม่สามารถดึงข้อมูลจาก Google Sheet ได้: {str(e)}")
    st.info("ตรวจสอบ Secrets และการแชร์ Sheet ให้ Service Account")
    st.stop()

# ===============================
# MAIN CONTENT (ส่วนที่เหลือเหมือนเดิม)
# ===============================
st.divider()

# แสดงวันที่ผ่าตัดจากไฟล์ (opedate)
if "opedate" in df_raw.columns:
    opedate_raw = pd.to_datetime(df_raw["opedate"].dropna().iloc[0], errors="coerce")
    if pd.notna(opedate_raw):
        day_op = opedate_raw.day
        month_op = opedate_raw.month
        year_th_op = opedate_raw.year + 543
        month_names = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                       "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
        op_date_str = f"{day_op} {month_names[month_op]} {year_th_op}"
        st.markdown(f"<h2 style='text-align: center; color: #1f77b4;'>📅 ตารางผ่าตัดวันที่ {op_date_str}</h2>", unsafe_allow_html=True)
    else:
        st.markdown("<h2 style='text-align: center;'>📅 ตารางผ่าตัด</h2>", unsafe_allow_html=True)
else:
    st.markdown("<h2 style='text-align: center;'>📅 ตารางผ่าตัด</h2>", unsafe_allow_html=True)

st.markdown("---")

st.subheader("📊 OR-Minor Summary")

# OR Summary Cards
summary_df_temp, meta_temp, _ = build_daily_summary(df_raw, use_fuzzy=False, fuzzy_threshold=85)
total_cases = meta_temp["cases_total"]
category_counts = meta_temp["category_counts"]

top_categories = category_counts.sort_values(ascending=False).head(4)
display_cats = top_categories.index.tolist()

cols = st.columns(5)

with cols[0]:
    st.markdown("<h4 style='text-align: center; color: black;'>Total</h4>", unsafe_allow_html=True)
    st.markdown(f"<h2 style='text-align: center; color: black; margin-top: -10px;'>{total_cases}</h2>", unsafe_allow_html=True)

for i, cat in enumerate(display_cats):
    count = category_counts[cat]
    with cols[i+1]:
        st.markdown(f"<h4 style='text-align: center; color: black;'>{cat}</h4>", unsafe_allow_html=True)
        st.markdown(f"<h2 style='text-align: center; color: black; margin-top: -10px;'>{count}</h2>", unsafe_allow_html=True)

for j in range(len(display_cats) + 1, 5):
    with cols[j]:
        st.write("")

st.markdown("---")

# Operation On-going Card
st.subheader("⏳ Operation On-going")

proc_col = pick_text_col(df_raw, ["icd9cm_name", "operation", "opname", "procedure", "proc", "หัตถการ", "ผ่าตัด"])
if proc_col:
    df_raw["__proc_category__"] = df_raw[proc_col].apply(classify_proc_category_rules)

    completed_by_category = {}
    for idx in st.session_state.get("completed_cases", set()):
        if idx < len(df_raw):
            cat = df_raw.iloc[idx]["__proc_category__"]
            completed_by_category[cat] = completed_by_category.get(cat, 0) + 1

    ongoing_counts = {}
    for cat, total in category_counts.items():
        completed = completed_by_category.get(cat, 0)
        remaining = total - completed
        if remaining > 0:
            ongoing_counts[cat] = remaining

    if ongoing_counts:
        ongoing_cats = sorted(ongoing_counts.items(), key=lambda x: x[1], reverse=True)
        ongoing_cols = st.columns(len(ongoing_cats) + 1)

        with ongoing_cols[0]:
            st.markdown("<h4 style='text-align: center; color: #2e86de;'>On-going</h4>", unsafe_allow_html=True)

        for i, (cat, count) in enumerate(ongoing_cats):
            with ongoing_cols[i+1]:
                st.markdown(f"<h4 style='text-align: center; color: black;'>{cat}</h4>", unsafe_allow_html=True)
                st.markdown(f"<h2 style='text-align: center; color: #e74c3c; margin-top: -10px;'>{count}</h2>", unsafe_allow_html=True)
    else:
        st.success("🎉 ไม่มีเคสที่เหลือทำแล้ว")
else:
    st.info("ไม่พบคอลัมน์หัตถการสำหรับคำนวณ On-going")

# เวลา + เหลือเคส
current_time = dt.datetime.now()
day_cur = current_time.day
month_cur = current_time.month
year_th_cur = current_time.year + 543
year_short_cur = year_th_cur % 100
current_time_str = f"{day_cur:02d}/{month_cur:02d}/{year_short_cur:02d} {current_time.strftime('%H:%M:%S')}"

remaining_cases = total_cases - len(st.session_state.get("completed_cases", set()))

status_cols = st.columns(3)
with status_cols[0]:
    st.markdown(f"<p style='text-align: left; color: black; margin-top: 20px;'><strong>⏰ เวลาปัจจุบัน:</strong> {current_time_str}</p>", unsafe_allow_html=True)
with status_cols[1]:
    st.markdown(f"<p style='text-align: center; color: #666666; margin-top: 20px;'><strong>📤 ข้อมูลล่าสุดจาก Google Sheet</strong></p>", unsafe_allow_html=True)
with status_cols[2]:
    st.markdown(f"<p style='text-align: right; color: #d73a3a; font-weight: bold; margin-top: 20px;'><strong>⏳ เหลือเคสที่ยังไม่เสร็จ:</strong> {remaining_cases} ราย</p>", unsafe_allow_html=True)

st.markdown("---")

# รายการผ่าตัดวันนี้
st.subheader("✅ รายการผ่าตัดวันนี้")

patient_cols = ["dspname", "icd9cm_name", "procnote", "surgstfnm"]
available_cols = [col for col in patient_cols if col in df_raw.columns]

if available_cols:
    if "estmtime" in df_raw.columns:
        df_sorted = df_raw.sort_values("estmtime")
    else:
        df_sorted = df_raw

    df_patient = df_sorted[available_cols].copy()
    df_patient = df_patient.reset_index(drop=True)

    rename_map = {
        "dspname": "ชื่อผู้ป่วย",
        "icd9cm_name": "Operation",
        "procnote": "Proc note",
        "surgstfnm": "Staff"
    }
    df_patient.rename(columns=rename_map, inplace=True)

    completed = st.session_state.get("completed_cases", set())

    st.write("กดปุ่ม **เสร็จแล้ว** เมื่อทำเคสนั้นเสร็จ")

    has_completed = False
    for idx, row in df_patient.iterrows():
        if idx in completed:
            has_completed = True
            continue

        col1, col2, col3, col4, col5 = st.columns([3, 3, 3, 3, 1.5])
        with col1:
            st.write(row["ชื่อผู้ป่วย"])
        with col2:
            st.write(row["Operation"])
        with col3:
            st.write(row["Proc note"] if pd.notna(row["Proc note"]) else "")
        with col4:
            st.write(row["Staff"])
        with col5:
            if st.button("เสร็จแล้ว", key=f"done_{idx}"):
                st.session_state["completed_cases"].add(idx)
                st.rerun()

    if has_completed:
        st.markdown("---")
        st.caption("**✅ เคสที่เสร็จแล้ว**")
        for idx, row in df_patient.iterrows():
            if idx not in completed:
                continue
            col1, col2, col3, col4, col5 = st.columns([3, 3, 3, 3, 1.5])
            with col1:
                st.write(row["ชื่อผู้ป่วย"])
            with col2:
                st.write(row["Operation"])
            with col3:
                st.write(row["Proc note"] if pd.notna(row["Proc note"]) else "")
            with col4:
                st.write(row["Staff"])
            with col5:
                st.success("✓ เสร็จแล้ว")
else:
    st.info("ไม่พบคอลัมน์ที่ต้องการสำหรับแสดงรายการผู้ป่วย")

st.markdown("---")

# Daily case summary
st.subheader("📈 Daily case summary (เช้า/บ่าย/TF)")
summary_df, meta, df_work = build_daily_summary(df_raw, use_fuzzy=False, fuzzy_threshold=85)

st.caption(
    f"proc col: {meta.get('proc_col_used') or '-'} | "
    f"time col: {meta.get('time_col_used') or '-'} | "
    f"cases: {meta.get('cases_total')}"
)

base_cols = ["Shift", "Total"]
active_categories = [
    col for col in PROC_CATEGORIES
    if col in summary_df.columns and (summary_df[col] > 0).any()
]
display_cols = base_cols[:1] + active_categories + base_cols[1:]

if not active_categories and "Other" in summary_df.columns:
    display_cols = ["Shift", "Other", "Total"]

df_show(summary_df[display_cols], stretch=True)

st.markdown("---")

# Operation
st.subheader("⚙️ Operation")
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    use_fuzzy = st.checkbox("เปิดใช้ Fuzzy Matching เมื่อเป็น Other", value=False)
with c2:
    fuzzy_threshold = st.slider("Fuzzy threshold", min_value=60, max_value=95, value=85, step=1)
with c3:
    st.caption("ถ้าไม่มี rapidfuzz จะ fallback เป็น rule-based อัตโนมัติ")

if use_fuzzy:
    summary_df, meta, df_work = build_daily_summary(df_raw, use_fuzzy=True, fuzzy_threshold=fuzzy_threshold)
    df_show(summary_df[display_cols], stretch=True)

# Other review
st.subheader("🔍 Operation นอกเหนือที่ตั้งค่าไว้ (Other review)")
proc_col_used = meta.get("proc_col_used")
if not proc_col_used:
    st.info("ไม่พบคอลัมน์หัตถการในไฟล์ จึงไม่สามารถทำ Other review ได้")
else:
    unk_df = top_unknowns(df_work, proc_col_used, n=25)
    if unk_df.empty:
        st.success("ไม่มีรายการที่ตกเป็น Other")
    else:
        st.caption("ใช้รายการนี้เพิ่ม ALIASES หรือ pattern ได้")
        df_show(unk_df, stretch=True)

with st.expander("ดูข้อมูลดิบ (preview 50 แถวแรก)"):
    df_show(df_raw.head(50), stretch=True)
