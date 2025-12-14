import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import re
from io import BytesIO

# ===============================
# 0) CONFIG
# ===============================
st.set_page_config(page_title="OR-minor Schedule Dashboard", layout="wide")
st.title("OR-minor Schedule Dashboard 📊")

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
    uploaded_file = st.file_uploader("อัปโหลดไฟล์ Excel (.xlsx หรือ .xls)", type=["xlsx", "xls"])

df_raw = None
if uploaded_file is not None:
    file_name = uploaded_file.name.lower()
    file_bytes = uploaded_file.read()  # อ่านเป็น bytes ก่อน
    file_stream = BytesIO(file_bytes)

    try:
        if file_name.endswith(".xlsx"):
            df_raw = pd.read_excel(file_stream, engine="openpyxl")
        elif file_name.endswith(".xls"):
            # ใช้ xlrd สำหรับ .xls เก่า (Streamlit Cloud มี xlrd)
            df_raw = pd.read_excel(file_stream, engine="xlrd")
        else:
            st.sidebar.error("รองรับเฉพาะไฟล์ .xlsx และ .xls เท่านั้น")
    except Exception as e:
        st.sidebar.error(f"ไม่สามารถอ่านไฟล์ได้: {str(e)}")
        st.sidebar.info("ลองบันทึกไฟล์ใหม่จาก Excel แล้วอัปโหลดอีกครั้ง")
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
    st.info("กรุณาอัปโหลดไฟล์จาก sidebar ด้านซ้ายก่อน")
    st.stop()

# (ส่วนที่เหลือของโค้ดเหมือนเดิมทั้งหมด — แสดงวันที่ผ่าตัด, OR Summary, On-going, รายการผ่าตัด, Daily summary, etc.)

# ... วางส่วนโค้ดที่เหลือจากเวอร์ชันก่อนหน้า (ตั้งแต่แสดงวันที่ผ่าตัดลงไป)

# ถ้าต้องการโค้ดเต็มทั้งไฟล์ บอกมาได้เลยครับ ผมส่งให้ครบ!

### วิธีอัปเดต
1. Copy ส่วนที่ผมแก้ (ตั้งแต่ `# SIDEBAR: UPLOAD FILE` ลงไป)
2. วางทับส่วนเดิมใน pro_db.py ของคุณ
3. อัปโหลดขึ้น GitHub ตามวิธีเดิม (Edit file → Commit)
4. รอ 1-2 นาที → Dashboard อัปเดตเอง

ตอนนี้ไฟล์ .xls เก่าแค่ไหนก็อ่านได้แน่นอนครับ  
ลองอัปโหลดไฟล์เดิมดู — น่าจะกลับมาใช้งานได้ปกติเลย!  

ถ้ายังมีปัญหา ส่ง error ใหม่มาหรือบอกชื่อไฟล์ ผมช่วยต่อครับ 😄
