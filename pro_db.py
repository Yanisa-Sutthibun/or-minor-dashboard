import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import re

# ===============================
# 0) CONFIG
# ===============================
st.set_page_config(page_title="OR-minor Schedule Dashboard", layout="wide")
st.title("OR-minor Schedule Dashboard")

# ส่วนโค้ดหลักต่อจากนี้...
# ===============================
# Helper: dataframe width compat
# ===============================
def df_show(df, stretch: bool = True):
    try:
        return st.dataframe(df, width=("stretch" if stretch else "content"))
    except TypeError:
        return st.dataframe(df, use_container_width=stretch)

# ===============================
# Shift labels
# ===============================
SHIFT_ORDER = ["AM", "PM", "Unknown"]
SHIFT_LABEL_MAP = {"AM": "เช้า", "PM": "บ่าย", "Unknown": "TF"}

# ===============================
# 3) COLUMN PICKER
# ===============================
def pick_text_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    for c in candidates:
        if c.lower() in cols:
            return cols[c.lower()]
    return None

# ===============================
# 4) PROCEDURE CATEGORIES & ALIASES
# ===============================
PROC_CATEGORIES = [
    "I+D",
    "Excision",
    "Nail extraction",
    "Off perm/catheter",
    "Lymphnode biopsy",
    "Debridement",
    "EC",
    "Frenectomy",
    "Morpheus",
    "Cooltech",
    "Laser",
    "Eyelid correction",
    "Facelift",
    "Other",
]

ALIASES = {
    "i&d": "i+d", "i/d": "i+d", "i d": "i+d", "i and d": "i+d", "i n d": "i+d",
    "incision and drainage": "incision drainage", "incision & drainage": "incision drainage",
    "incision drainage": "incision drainage",
    "debridement": "debridement", "debride": "debridement", "debrided": "debridement",
    "db": "debridement", "d/b": "debridement", "d&b": "debridement",
    "excisional debridement": "debridement",
    "off permanent catheter": "off perm", "off perm cath": "off perm",
    "off perm catheter": "off perm", "off cath": "off perm", "off tcc": "off perm",
    "e.c.": "ec", "e. c.": "ec", "e c": "ec", "ec.": "ec", "ec,": "ec", "ec;": "ec",
    "blepharoptosis repair": "ptosis correction",
    "correction of blepharoptosis": "ptosis correction",
    "upper eyelid ptosis repair": "ptosis correction",
    "upper lid ptosis correction": "ptosis correction",
    "eyelid ptosis correction": "ptosis correction",
    "ptosis repair": "ptosis correction",
    "ptosis surgery": "ptosis correction",
    "levator advancement": "ptosis correction",
    "levator aponeurosis advancement": "ptosis correction",
    "levator resection": "ptosis correction",
    "levator plication": "ptosis correction",
    "frontalis sling": "ptosis correction",
    "frontalis suspension": "ptosis correction",
    "upper eyelid correction": "ptosis correction",
}

def normalize_proc_text(x: str) -> str:
    if pd.isna(x):
        return ""
    s = str(x).lower().strip()
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\be\s*[\.\-\s]\s*c\b", "ec", s)
    for k, v in ALIASES.items():
        s = s.replace(k, v)
    s = re.sub(r"\bi\s*(?:\+|&|\band\b)\s*d\b", "i+d", s)
    s = re.sub(r"\bincision\s*(?:&|\band\b)?\s*drainage\b", "incision drainage", s)
    s = re.sub(r"[,\.;:\(\)\[\]\{\}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def classify_proc_category_rules(proc_text: str) -> str:
    s = normalize_proc_text(proc_text)
    if ("i+d" in s) or ("incision drainage" in s):
        return "I+D"
    if re.search(r"\bexcis", s):
        return "Excision"
    if re.search(r"\bnail\s*(extraction|extract|ext)\b", s):
        return "Nail extraction"
    if re.search(r"\boff\s*perm\b", s) or re.search(r"\boff\s*catheter\b", s):
        return "Off perm/catheter"
    if re.search(r"\blymph\s*node\s*biopsy\b", s) or re.search(r"\blymphnode\s*biopsy\b", s) or re.search(r"\bln\s*biopsy\b", s):
        return "Lymphnode biopsy"
    if re.search(r"\bdebrid", s):
        return "Debridement"
    if re.search(r"(?<![a-z0-9])ec(?![a-z0-9])", s):
        return "EC"
    if re.search(r"\bfrenectomy\b", s) or re.search(r"\bfrenulectomy\b", s):
        return "Frenectomy"
    if re.search(r"\bmorpheus\b", s):
        return "Morpheus"
    if re.search(r"\bcooltech\b", s) or re.search(r"\bcool\s*tech\b", s):
        return "Cooltech"
    if re.search(r"\blaser\b", s):
        return "Laser"
    if re.search(r"\bptosis\b", s) or re.search(r"\bblepharoptosis\b", s):
        return "Eyelid correction"
    if re.search(r"\bfacelift\b", s) or re.search(r"\bface\s*lift\b", s) or re.search(r"\brhytidectomy\b", s):
        return "Facelift"
    return "Other"

def classify_proc_category(proc_text: str, use_fuzzy: bool = False, threshold: int = 85) -> str:
    base = classify_proc_category_rules(proc_text)
    if (not use_fuzzy) or (base != "Other"):
        return base
    try:
        from rapidfuzz import process, fuzz
    except Exception:
        return base

    s = normalize_proc_text(proc_text)
    if not s:
        return "Other"

    CANON = {
        "I+D": ["i+d", "incision drainage"],
        "Excision": ["excision"],
        "Nail extraction": ["nail extraction"],
        "Off perm/catheter": ["off perm", "off catheter"],
        "Lymphnode biopsy": ["lymph node biopsy", "ln biopsy"],
        "Debridement": ["debridement"],
        "EC": ["ec"],
        "Frenectomy": ["frenectomy"],
        "Morpheus": ["morpheus"],
        "Cooltech": ["cooltech"],
        "Laser": ["laser"],
        "Eyelid correction": ["ptosis correction", "eyelid correction"],
        "Facelift": ["facelift"],
    }

    all_choices = [(cat, term) for cat, terms in CANON.items() for term in terms]
    choices = [term for _, term in all_choices]
    best = process.extractOne(s, choices, scorer=fuzz.token_set_ratio)
    if best and best[1] >= threshold:
        return all_choices[best[2]][0]
    return "Other"

# ===============================
# 5) TIME PARSING
# ===============================
def to_minutes_from_any(x):
    if pd.isna(x):
        return np.nan
    try:
        xi = int(float(x))
        hh, mm = xi // 100, xi % 100
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh * 60 + mm
    except Exception:
        pass
    try:
        s = str(x).strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh * 60 + mm
    except Exception:
        pass
    return np.nan

def classify_shift(mins: float) -> str:
    if pd.isna(mins):
        return "Unknown"
    return "AM" if mins < 12 * 60 else "PM"

# ===============================
# 6) BUILD SUMMARY
# ===============================
def build_daily_summary(df_raw_in: pd.DataFrame, use_fuzzy: bool, fuzzy_threshold: int):
    df = df_raw_in.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df_work = df.copy()

    proc_col = pick_text_col(df_work, ["icd9cm_name", "operation", "opname", "procedure", "proc", "หัตถการ", "ผ่าตัด"])
    time_col = pick_text_col(df_work, ["estmtime", "reqtime", "opetime", "time", "เวลา", "เวลาผ่า", "เวลาเริ่ม"])

    if proc_col is None:
        df_work["__proc_category__"] = "Other"
    else:
        df_work["__proc_category__"] = df_work[proc_col].apply(
            lambda v: classify_proc_category(v, use_fuzzy=use_fuzzy, threshold=fuzzy_threshold)
        )

    if time_col is None:
        df_work["__shift__"] = "Unknown"
    else:
        df_work["__mins__"] = df_work[time_col].apply(to_minutes_from_any)
        df_work["__shift__"] = df_work["__mins__"].apply(classify_shift)

    category_counts = df_work["__proc_category__"].value_counts()
    category_counts = category_counts[category_counts.index != "Other"]

    g = df_work.groupby(["__shift__", "__proc_category__"]).size().reset_index(name="n")
    pivot = g.pivot(index="__shift__", columns="__proc_category__", values="n").fillna(0).astype(int)

    for col in PROC_CATEGORIES:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot["Total"] = pivot.sum(axis=1)

    for sh in SHIFT_ORDER:
        if sh not in pivot.index:
            pivot.loc[sh] = 0

    pivot = pivot.loc[SHIFT_ORDER].reset_index().rename(columns={"__shift__": "Shift"})
    pivot["Shift"] = pivot["Shift"].map(SHIFT_LABEL_MAP)

    meta = {
        "proc_col_used": proc_col,
        "time_col_used": time_col,
        "cases_total": len(df_work),
        "category_counts": category_counts,
    }
    return pivot, meta, df_work

def top_unknowns(df_work: pd.DataFrame, proc_col: str, n=25) -> pd.DataFrame:
    tmp = df_work.copy()
    tmp["__norm__"] = tmp[proc_col].apply(normalize_proc_text)
    tmp["__cat__"] = tmp[proc_col].apply(classify_proc_category_rules)
    unk = tmp[tmp["__cat__"] == "Other"]
    if unk.empty:
        return pd.DataFrame(columns=["normalized_proc", "count"])
    vc = unk["__norm__"].value_counts().head(n).reset_index()
    vc.columns = ["normalized_proc", "count"]
    return vc

# ===============================
# SIDEBAR: UPLOAD FILE
# ===============================
with st.sidebar:
    st.header("Upload file")
    uploaded_file = st.file_uploader("📤 อัปโหลดไฟล์ Excel (.xlsx หรือ .xls)", type=["xlsx", "xls"])

df_raw = None
if uploaded_file is not None:
    file_name = uploaded_file.name.lower()
    converted_xlsx = None

    try:
        if file_name.endswith(".xlsx"):
            converted_xlsx = uploaded_file
        elif file_name.endswith(".xls"):
            import pyexcel as p
            from io import BytesIO
            file_bytes = uploaded_file.read()
            sheet = p.get_sheet(file_type="xls", file_content=file_bytes)
            xlsx_stream = sheet.save_to_memory("xlsx")
            converted_xlsx = BytesIO(xlsx_stream.getvalue())
            st.sidebar.info("ไฟล์ .xls ถูกแปลงเป็น .xlsx อัตโนมัติเรียบร้อยแล้ว")
        else:
            st.sidebar.error("รองรับเฉพาะไฟล์ .xlsx และ .xls เท่านั้น")
            converted_xlsx = None
    except Exception as e:
        st.sidebar.error(f"ไม่สามารถแปลงไฟล์ Excel ได้: {e}")
        converted_xlsx = None

    if converted_xlsx is not None:
        try:
            df_raw = pd.read_excel(converted_xlsx, engine="openpyxl")
        except Exception as e:
            st.sidebar.error(f"ไม่สามารถอ่านไฟล์ .xlsx ได้: {e}")
            df_raw = None

# ===============================
# 2) UPLOAD TIME + COMPLETED STATE
# ===============================
if uploaded_file is not None and (st.session_state.get("last_upload_name") != uploaded_file.name):
    st.session_state["last_upload_name"] = uploaded_file.name
    st.session_state["last_upload_ts"] = dt.datetime.now()
    st.session_state["completed_cases"] = set()

upload_ts = st.session_state.get("last_upload_ts")
if upload_ts:
    day = upload_ts.day
    month = upload_ts.month
    year_th = upload_ts.year + 543
    year_short = year_th % 100
    upload_time_str = f"{day:02d}/{month:02d}/{year_short:02d} {upload_ts.strftime('%H:%M')}"
else:
    upload_time_str = "-"

# ===============================
# MAIN CONTENT
# ===============================
st.divider()

if df_raw is None:
    st.info("กรุณาอัปโหลดไฟล์จาก sidebar ด้านซ้ายก่อน 📤")
    st.stop()

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
        st.markdown(f"<h2 style='text-align: center; color: #1f77b4;'>ตารางผ่าตัดวันที่ {op_date_str}</h2>", unsafe_allow_html=True)
    else:
        st.markdown("<h2 style='text-align: center;'>ตารางผ่าตัด</h2>", unsafe_allow_html=True)
else:
    st.markdown("<h2 style='text-align: center;'>ตารางผ่าตัด</h2>", unsafe_allow_html=True)

st.markdown("---")

st.subheader("OR-Minor Summary")

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
        st.success("ไม่มีเคสที่เหลือทำแล้ว 🎉")
else:
    st.info("ไม่พบคอลัมน์หัตถการสำหรับคำนวณ On-going")

# เวลา + เหลือเคส ใต้ On-going
current_time = dt.datetime.now()
day_cur = current_time.day
month_cur = current_time.month
year_th_cur = current_time.year + 543
year_short_cur = year_th_cur % 100
current_time_str = f"{day_cur:02d}/{month_cur:02d}/{year_short_cur:02d} {current_time.strftime('%H:%M:%S')}"

remaining_cases = total_cases - len(st.session_state.get("completed_cases", set()))

status_cols = st.columns(3)
with status_cols[0]:
    st.markdown(f"<p style='text-align: left; color: black; margin-top: 20px;'><strong>เวลาปัจจุบัน:</strong> {current_time_str}</p>", unsafe_allow_html=True)
with status_cols[1]:
    st.markdown(f"<p style='text-align: center; color: #666666; margin-top: 20px;'><strong>อัปโหลดเมื่อ:</strong> {upload_time_str}</p>", unsafe_allow_html=True)
with status_cols[2]:
    st.markdown(f"<p style='text-align: right; color: #d73a3a; font-weight: bold; margin-top: 20px;'><strong>เหลือเคสที่ยังไม่เสร็จ:</strong> {remaining_cases} ราย</p>", unsafe_allow_html=True)

st.markdown("---")

# รายการผ่าตัดวันนี้
st.subheader("รายการผ่าตัดวันนี้")

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

    st.write("กดปุ่ม **✅ เสร็จแล้ว** เมื่อทำเคสนั้นเสร็จ")

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
            if st.button("✅ เสร็จแล้ว", key=f"done_{idx}"):
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
st.subheader("Daily case summary (เช้า/บ่าย/TF)")
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

summary_df_display = summary_df[display_cols]
df_show(summary_df_display, stretch=True)

st.markdown("---")

# Operation
st.subheader("Operation")
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    use_fuzzy = st.checkbox("เปิดใช้ Fuzzy Matching เมื่อเป็น Other", value=False)
with c2:
    fuzzy_threshold = st.slider("Fuzzy threshold", min_value=60, max_value=95, value=85, step=1)
with c3:
    st.caption("ถ้าไม่มี rapidfuzz จะ fallback เป็น rule-based อัตโนมัติ")

if use_fuzzy:
    summary_df, meta, df_work = build_daily_summary(df_raw, use_fuzzy=True, fuzzy_threshold=fuzzy_threshold)
    summary_df_display = summary_df[display_cols]
    st.rerun()

# Other review
st.subheader("Operation นอกเหนือที่ตั้งค่าไว้ (Other review)")
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
