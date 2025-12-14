
import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import re
from io import BytesIO

import gspread
from google.oauth2.service_account import Credentials

# ===============================
# 0) CONFIG
# ===============================
st.set_page_config(page_title="OR-minor Schedule Dashboard", layout="wide")
st.markdown(
    "<h1 style='font-size:34px; margin-bottom: 0.2rem;'>OR-minor Schedule Dashboard 📊</h1>",
    unsafe_allow_html=True
)

# -------------------------------
# Small divider
# -------------------------------
def small_divider(width_pct: int = 70, thickness_px: int = 2, color: str = "#eeeeee", margin_px: int = 12):
    st.markdown(
        f"""
        <div style="
            width: {width_pct}%;
            margin: {margin_px}px auto;
            border-bottom: {thickness_px}px solid {color};
        "></div>
        """,
        unsafe_allow_html=True
    )

def df_show(df, stretch: bool = True):
    try:
        return st.dataframe(df, width=("stretch" if stretch else "content"))
    except TypeError:
        return st.dataframe(df, use_container_width=stretch)

# ===============================
# PASSWORD PROTECTION (ผู้ใช้ทุกคนต้อง login ก่อนดู)
# ===============================
try:
    PASSWORD = st.secrets["APP_PASSWORD"]
except Exception:
    PASSWORD = "pghnurse30"  # fallback

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
# TOP BAR
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

small_divider(width_pct=70, thickness_px=2, color="#e6e6e6", margin_px=10)

# ===============================
# Shift labels
# ===============================
SHIFT_ORDER = ["AM", "PM", "Unknown"]
SHIFT_LABEL_MAP = {"AM": "เช้า", "PM": "บ่าย", "Unknown": "TF"}

# ===============================
# COLUMN PICKER
# ===============================
def pick_text_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    for c in candidates:
        if c.lower() in cols:
            return cols[c.lower()]
    return None

# ===============================
# PROCEDURE CATEGORIES & ALIASES
# ===============================
PROC_CATEGORIES = [
    "I+D", "Excision", "Nail extraction", "Off perm/catheter", "Lymphnode biopsy",
    "Debridement", "EC", "Frenectomy", "Morpheus", "Cooltech", "Laser",
    "Eyelid correction", "Facelift", "Other",
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
# TIME PARSING
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
# BUILD SUMMARY
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
# GOOGLE SHEET CONFIG
# ===============================
SHEET_ID = st.secrets.get("SHEET_ID", "")
SHEET_NAME = st.secrets.get("SHEET_NAME", "Sheet1")

def _require_sheet_config():
    if not SHEET_ID:
        st.error("ยังไม่ได้ตั้งค่า SHEET_ID ใน secrets")
        st.stop()
    if "gcp_service_account" not in st.secrets:
        st.error("ยังไม่ได้ตั้งค่า gcp_service_account ใน secrets")
        st.stop()

@st.cache_resource(ttl=300)
def get_worksheet():
    _require_sheet_config()
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    return ws
# ===============================
# TEST GOOGLE SHEET CONNECTION (ชั่วคราว)
# ===============================
st.subheader("🔧 Google Sheet Connection Debug")

try:
    ws = get_worksheet()
    st.success("✅ Auth ผ่าน และเปิด Spreadsheet ได้")
    st.write("Worksheet title:", ws.title)

    # ลองอ่าน 3 แถวแรก
    vals = ws.get_all_values()[:3]
    st.write("Preview (first 3 rows):")
    st.json(vals)

    # ลองเขียน/อ่านกลับ (ทดสอบสิทธิ์แก้ไข)
    ws.update("A1", [["PING"]])
    st.success("✅ Write test ผ่าน (มีสิทธิ์แก้ไข)")

except Exception as e:
    st.error("❌ ต่อ Google Sheet ไม่ได้")
    st.code(str(e))
    st.stop()

def sanitize_for_public_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    """
    ป้องกันข้อมูลหลุด: ตัดคอลัมน์ระบุตัวบุคคล ก่อนเขียนลง Sheet
    """
    drop_exact = [
        "dspname", "surgstfnm", "surgeon", "anesthetist",
        "hn", "an", "patient", "name"
    ]
    safe = df.drop(columns=[c for c in drop_exact if c in df.columns], errors="ignore").copy()

    # ถ้ากลัวไฟล์มีคอลัมน์ชื่อคนแปลกๆ ให้ตัดตาม pattern เพิ่ม:
    # (ถ้าคอลัมน์มีคำว่า name/ชื่อ/แพทย์/doctor ฯลฯ จะโดนตัด)
    pattern = re.compile(r"(name|ชื่อ|แพทย์|doctor|physician|surge|anesth|staff)", re.IGNORECASE)
    extra_drop = [c for c in safe.columns if pattern.search(str(c))]
    safe = safe.drop(columns=extra_drop, errors="ignore")

    safe["__upload_ts__"] = dt.datetime.now().isoformat(timespec="seconds")
    return safe

def write_df_to_sheet(ws, df: pd.DataFrame):
    df2 = df.copy().replace({np.nan: ""})
    values = [df2.columns.tolist()] + df2.astype(str).values.tolist()
    ws.clear()
    ws.update(values)

@st.cache_data(ttl=60)
def read_df_from_sheet() -> pd.DataFrame:
    ws = get_worksheet()
    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()
    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header)
    df = df.replace({"": np.nan}).dropna(how="all")
    return df

# ===============================
# SIDEBAR: UPLOAD (หลัง login แล้วอัปโหลดได้เลย ไม่มี admin password)
# ===============================
with st.sidebar:
    st.header("Upload file")
    uploaded_file = st.file_uploader("อัปโหลดไฟล์ Excel (.xlsx หรือ .xls)", type=["xlsx", "xls"], key="uploader_admin")

    if uploaded_file is not None:
        try:
            file_name = uploaded_file.name.lower()
            file_bytes = uploaded_file.getvalue()
            file_stream = BytesIO(file_bytes)

            if file_name.endswith(".xlsx"):
                df_up = pd.read_excel(file_stream, engine="openpyxl")
            elif file_name.endswith(".xls"):
                df_up = pd.read_excel(file_stream, engine="xlrd")
            else:
                st.error("รองรับเฉพาะ .xlsx/.xls")
                df_up = None

            if df_up is None or df_up.empty:
                st.warning("ไฟล์ว่าง หรืออ่านไม่ได้")
            else:
                df_safe = sanitize_for_public_dashboard(df_up)
                ws = get_worksheet()
                write_df_to_sheet(ws, df_safe)
                st.success("อัปโหลดสำเร็จ และบันทึกลง Google Sheet แล้ว")
                st.cache_data.clear()
                st.rerun()

        except Exception as e:
            st.error("อัปโหลด/บันทึกไม่สำเร็จ")
            st.code(str(e))
            st.caption("เช็ก 2 อย่าง: 1) แชร์ Sheet ให้ service account 2) เปิด API Sheets/Drive ใน Google Cloud")

# ===============================
# LOAD DATA FROM SHEET (ทุกเครื่องดึงจากที่นี่)
# ===============================
try:
    df_raw = read_df_from_sheet()
except Exception as e:
    st.error("ไม่สามารถเชื่อมต่อ Google Sheet ได้")
    st.code(str(e))
    st.info("ให้เช็ก: secrets และแชร์ Sheet ให้ service account email")
    st.stop()

if df_raw is None or df_raw.empty:
    st.info("ยังไม่มีข้อมูลใน Sheet — รออัปโหลดไฟล์")
    st.stop()

# ===============================
# UPLOAD TIME (อ่านจาก __upload_ts__)
# ===============================
upload_time_str = "-"
if "__upload_ts__" in df_raw.columns:
    try:
        ts = pd.to_datetime(df_raw["__upload_ts__"].dropna().iloc[-1], errors="coerce")
        if pd.notna(ts):
            upload_time_str = ts.strftime("%d/%m/%y %H:%M")
    except Exception:
        pass

# ===============================
# Completed state (ต่อเครื่อง/ต่อ session)
# ===============================
if "completed_cases" not in st.session_state:
    st.session_state["completed_cases"] = set()

# ===============================
# MAIN: Date title (ไม่มีเส้นใต้ฟ้า)
# ===============================
if "opedate" in df_raw.columns:
    opedate_raw = pd.to_datetime(df_raw["opedate"].dropna().iloc[0], errors="coerce")
    if pd.notna(opedate_raw):
        day_op = opedate_raw.day
        month_op = opedate_raw.month
        year_th_op = opedate_raw.year + 543
        month_names = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                       "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
        op_date_str = f"{day_op} {month_names[month_op]} {year_th_op}"

        st.markdown(
            f"""
            <div style="
                text-align:center;
                font-size:24px;
                font-weight:700;
                color:#1f77b4;
                margin:10px 0 6px 0;
                text-decoration:none;
            ">
                📅 ตารางผ่าตัดวันที่ {op_date_str}
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.markdown("<div style='text-align:center; font-size:22px; font-weight:700; margin:10px 0;'>📅 ตารางผ่าตัด</div>", unsafe_allow_html=True)
else:
    st.markdown("<div style='text-align:center; font-size:22px; font-weight:700; margin:10px 0;'>📅 ตารางผ่าตัด</div>", unsafe_allow_html=True)

small_divider()

# ===============================
# OR SUMMARY
# ===============================
st.subheader("📊 OR-Minor Summary")

summary_df_temp, meta_temp, _ = build_daily_summary(df_raw, use_fuzzy=False, fuzzy_threshold=85)
total_cases = meta_temp["cases_total"]
category_counts = meta_temp["category_counts"]

top_categories = category_counts.sort_values(ascending=False).head(4)
display_cats = top_categories.index.tolist()

cols = st.columns(5)
with cols[0]:
    st.markdown("<h4 style='text-align:center; color:black;'>Total</h4>", unsafe_allow_html=True)
    st.markdown(f"<h2 style='text-align:center; color:black; margin-top:-10px;'>{total_cases}</h2>", unsafe_allow_html=True)

for i, cat in enumerate(display_cats):
    count = int(category_counts.get(cat, 0))
    with cols[i + 1]:
        st.markdown(f"<h4 style='text-align:center; color:black;'>{cat}</h4>", unsafe_allow_html=True)
        st.markdown(f"<h2 style='text-align:center; color:black; margin-top:-10px;'>{count}</h2>", unsafe_allow_html=True)

small_divider()

# ===============================
# OPERATION ON-GOING
# ===============================
st.subheader("⏳ Operation On-going")

proc_col = pick_text_col(df_raw, ["icd9cm_name", "operation", "opname", "procedure", "proc", "หัตถการ", "ผ่าตัด"])
if proc_col:
    df_tmp = df_raw.copy()
    df_tmp["__proc_category__"] = df_tmp[proc_col].apply(classify_proc_category_rules)

    completed_by_category = {}
    for idx in st.session_state.get("completed_cases", set()):
        if idx < len(df_tmp):
            cat = df_tmp.iloc[idx]["__proc_category__"]
            completed_by_category[cat] = completed_by_category.get(cat, 0) + 1

    ongoing_counts = {}
    for cat, total in category_counts.items():
        completed = completed_by_category.get(cat, 0)
        remaining = int(total) - int(completed)
        if remaining > 0:
            ongoing_counts[cat] = remaining

    if ongoing_counts:
        ongoing_cats = sorted(ongoing_counts.items(), key=lambda x: x[1], reverse=True)
        ongoing_cols = st.columns(len(ongoing_cats) + 1)

        with ongoing_cols[0]:
            st.markdown("<h4 style='text-align:center; color:#2e86de;'>On-going</h4>", unsafe_allow_html=True)

        for i, (cat, count) in enumerate(ongoing_cats):
            with ongoing_cols[i + 1]:
                st.markdown(f"<h4 style='text-align:center; color:black;'>{cat}</h4>", unsafe_allow_html=True)
                st.markdown(f"<h2 style='text-align:center; color:#e74c3c; margin-top:-10px;'>{count}</h2>", unsafe_allow_html=True)
    else:
        st.success("🎉 ไม่มีเคสที่เหลือทำแล้ว")
else:
    st.info("ไม่พบคอลัมน์หัตถการสำหรับคำนวณ On-going")

# status row
current_time = dt.datetime.now()
current_time_str = current_time.strftime("%d/%m/%y %H:%M:%S")
remaining_cases = total_cases - len(st.session_state.get("completed_cases", set()))

status_cols = st.columns(3)
with status_cols[0]:
    st.markdown(f"<p style='text-align:left; color:black; margin-top:20px;'><strong>⏰ เวลาปัจจุบัน:</strong> {current_time_str}</p>", unsafe_allow_html=True)
with status_cols[1]:
    st.markdown(f"<p style='text-align:center; color:#666666; margin-top:20px;'><strong>📤 อัปเดตล่าสุด:</strong> {upload_time_str}</p>", unsafe_allow_html=True)
with status_cols[2]:
    st.markdown(f"<p style='text-align:right; color:#d73a3a; font-weight:bold; margin-top:20px;'><strong>⏳ เหลือเคสที่ยังไม่เสร็จ:</strong> {remaining_cases} ราย</p>", unsafe_allow_html=True)

small_divider()

# ===============================
# ✅ รายการผ่าตัดวันนี้ (ไม่แสดงชื่อผู้ป่วย/ชื่อแพทย์) + ปุ่มเสร็จแล้ว
# ===============================
st.subheader("✅ รายการผ่าตัดวันนี้ (ไม่แสดงชื่อผู้ป่วย/ชื่อแพทย์)")

safe_cols = []
if "icd9cm_name" in df_raw.columns:
    safe_cols.append("icd9cm_name")
if "procnote" in df_raw.columns:
    safe_cols.append("procnote")

if not safe_cols:
    st.info("ไม่พบคอลัมน์ Operation/Proc note สำหรับแสดงรายการแบบไม่ระบุตัวบุคคล")
else:
    df_list = df_raw.copy()

    if "estmtime" in df_list.columns:
        # sort แบบทน: ถ้าเป็น string ก็ยัง sort ได้
        df_list["__est_sort__"] = df_list["estmtime"].apply(to_minutes_from_any)
        df_list = df_list.sort_values(["__est_sort__"], na_position="last").drop(columns=["__est_sort__"], errors="ignore")

    df_list = df_list[safe_cols].copy().reset_index(drop=True)
    df_list.rename(columns={"icd9cm_name": "Operation", "procnote": "Proc note"}, inplace=True)

    completed = st.session_state["completed_cases"]

    header = st.columns([0.6, 3.5, 4.5, 1.6])
    header[0].markdown("**#**")
    header[1].markdown("**Operation**")
    header[2].markdown("**Proc note**")
    header[3].markdown("**สถานะ**")

    for i, row in df_list.iterrows():
        c0, c1, c2, c3 = st.columns([0.6, 3.5, 4.5, 1.6])
        c0.write(i)
        c1.write(row.get("Operation", ""))

        pn = row.get("Proc note", "")
        c2.write("" if pd.isna(pn) else pn)

        if i in completed:
            c3.success("✓ เสร็จแล้ว")
        else:
            if c3.button("เสร็จแล้ว", key=f"done_{i}"):
                completed.add(i)
                st.session_state["completed_cases"] = completed
                st.rerun()

    col_reset1, col_reset2 = st.columns([6, 2])
    with col_reset2:
        if st.button("รีเซ็ตสถานะ", key="reset_completed"):
            st.session_state["completed_cases"] = set()
            st.rerun()

small_divider()

# ===============================
# Daily case summary
# ===============================
st.subheader("📈 Daily case summary (เช้า/บ่าย/TF)")

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    use_fuzzy = st.checkbox("เปิดใช้ Fuzzy Matching เมื่อเป็น Other", value=False)
with c2:
    fuzzy_threshold = st.slider("Fuzzy threshold", min_value=60, max_value=95, value=85, step=1)
with c3:
    st.caption("ถ้าไม่มี rapidfuzz จะ fallback เป็น rule-based อัตโนมัติ")

summary_df, meta, df_work = build_daily_summary(df_raw, use_fuzzy=use_fuzzy, fuzzy_threshold=fuzzy_threshold)

st.caption(
    f"proc col: {meta.get('proc_col_used') or '-'} | "
    f"time col: {meta.get('time_col_used') or '-'} | "
    f"cases: {meta.get('cases_total')}"
)

base_cols = ["Shift", "Total"]
active_categories = [col for col in PROC_CATEGORIES if col in summary_df.columns and (summary_df[col] > 0).any()]
display_cols = base_cols[:1] + active_categories + base_cols[1:]
if not active_categories and "Other" in summary_df.columns:
    display_cols = ["Shift", "Other", "Total"]

df_show(summary_df[display_cols], stretch=True)

small_divider()

# ===============================
# Other review (ไม่โชว์ข้อมูลดิบ)
# ===============================
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

# ✅ ตัด preview ข้อมูลดิบออกเพื่อป้องกันข้อมูลหลุด





