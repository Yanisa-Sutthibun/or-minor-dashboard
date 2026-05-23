"""
ui_theme.py — Central Enterprise Medical Theme

Inject ครั้งเดียวต่อ page → consistent styling ทั้ง 3 หน้า
(Tracking · Admin · Settings)

Design: Enterprise medical (LeanTaaS/Caresyntax-inspired)
- Palette: navy ink + brand cyan + clean semantic colors
- Typography: Inter (Latin) + IBM Plex Sans Thai
- Components: refined buttons, metrics, cards, tabs, sidebar

NOTE: CSS minified onto continuous block (no blank lines!)
เหตุผล: Streamlit's markdown parser ตัด <style> block ถ้าเจอ blank line
→ CSS รั่วเป็น text บนหน้าจอ
"""

import streamlit as st

# CSS — must be single continuous block (no blank lines inside <style>!)
THEME_CSS = (
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Inter:wght@400;500;600;700;800&'
    'family=IBM+Plex+Sans+Thai:wght@400;500;600;700&display=swap" rel="stylesheet">'
    '<style>'
    ':root{'
    '--ink-900:#0a1628;--ink-800:#0f2540;--ink-700:#1e3a5f;--ink-600:#334e6d;'
    '--ink-500:#475c78;--ink-400:#6b7e96;--ink-300:#94a3b8;--ink-200:#cbd5e1;'
    '--ink-100:#e2e8f0;--ink-50:#f1f5f9;'
    '--canvas:#f8fafc;--surface:#ffffff;'
    '--brand-900:#083344;--brand-700:#0e7490;--brand-600:#0891b2;'
    '--brand-500:#06b6d4;--brand-400:#22d3ee;--brand-100:#cffafe;--brand-50:#ecfeff;'
    '--success-700:#15803d;--success-500:#10b981;--success-100:#dcfce7;'
    '--warning-700:#a16207;--warning-500:#f59e0b;--warning-100:#fef3c7;'
    '--danger-700:#b91c1c;--danger-500:#ef4444;--danger-100:#fee2e2;'
    '--info-700:#1d4ed8;--info-500:#3b82f6;--info-100:#dbeafe;'
    '--shadow-sm:0 1px 2px rgba(10,22,40,.04),0 1px 1px rgba(10,22,40,.06);'
    '--shadow-md:0 4px 12px -2px rgba(10,22,40,.06),0 2px 6px rgba(10,22,40,.04);'
    '--shadow-lg:0 12px 30px -8px rgba(10,22,40,.12),0 4px 12px -2px rgba(10,22,40,.06);'
    '}'
    # Global typography
    'html,body,[class*="css"],.stApp{'
    "font-family:'Inter','IBM Plex Sans Thai','Segoe UI',sans-serif !important;"
    '-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;'
    'color:var(--ink-700);}'
    '.stApp{background:var(--canvas)}'
    '.main .block-container{padding-top:1.5rem;padding-bottom:3rem;max-width:1280px}'
    'h1,h2,h3,h4,h5,h6,.stMarkdown h1,.stMarkdown h2,.stMarkdown h3{'
    'color:var(--ink-900) !important;font-weight:600 !important;letter-spacing:-0.3px;'
    "font-family:'Inter','IBM Plex Sans Thai',sans-serif !important;}"
    # Unified page header
    '.admin-header,.page-header{'
    'background:linear-gradient(135deg,#f0f9ff 0%,#e0f2fe 50%,#cffafe 100%);'
    'border:1px solid var(--brand-100);border-radius:14px;padding:22px 28px;'
    'margin-bottom:18px;box-shadow:var(--shadow-sm);position:relative;overflow:hidden;}'
    '.admin-header::before,.page-header::before{'
    'content:"";position:absolute;top:0;right:0;width:160px;height:100%;'
    'background:radial-gradient(circle at 80% 50%,rgba(6,182,212,0.08),transparent 70%);'
    'pointer-events:none;}'
    '.admin-header h1,.page-header h1,.page-header h2{'
    'margin:0 !important;font-size:24px !important;font-weight:700 !important;'
    'color:var(--ink-900) !important;letter-spacing:-0.4px;}'
    '.admin-header p,.page-header p{'
    'margin:6px 0 0 !important;font-size:14px !important;'
    'color:var(--ink-500) !important;font-weight:400;}'
    # Buttons
    '.stButton > button{'
    'border-radius:8px !important;border:1px solid var(--ink-200) !important;'
    'background:white !important;color:var(--ink-800) !important;'
    'font-weight:500 !important;font-size:14px !important;padding:8px 16px !important;'
    'transition:all .15s ease !important;box-shadow:var(--shadow-sm) !important;}'
    '.stButton > button:hover{'
    'border-color:var(--ink-400) !important;background:var(--ink-50) !important;'
    'transform:translateY(-1px);box-shadow:var(--shadow-md) !important;}'
    '.stButton > button:active{transform:translateY(0)}'
    '.stButton > button:focus{'
    'box-shadow:0 0 0 3px var(--brand-100),var(--shadow-sm) !important;outline:none !important;}'
    '.stButton > button:disabled{'
    'background:var(--ink-50) !important;color:var(--ink-300) !important;'
    'border-color:var(--ink-100) !important;cursor:not-allowed;opacity:0.75;}'
    '.stButton > button[kind="primary"],'
    '.stButton > button[data-baseweb="button"][kind="primary"]{'
    'background:var(--ink-900) !important;color:white !important;'
    'border-color:var(--ink-900) !important;}'
    '.stButton > button[kind="primary"]:hover{'
    'background:var(--ink-800) !important;'
    'box-shadow:0 8px 20px rgba(10,22,40,0.18) !important;}'
    '.stDownloadButton > button{'
    'background:var(--brand-500) !important;color:white !important;'
    'border-color:var(--brand-500) !important;}'
    '.stDownloadButton > button:hover{'
    'background:var(--brand-600) !important;border-color:var(--brand-600) !important;}'
    # Metrics
    '[data-testid="stMetric"]{'
    'background:white;border:1px solid var(--ink-100);border-radius:10px;'
    'padding:14px 16px !important;box-shadow:var(--shadow-sm);'
    'transition:border-color .15s,transform .15s;}'
    '[data-testid="stMetric"]:hover{border-color:var(--brand-400);transform:translateY(-1px);}'
    '[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] > div{'
    'font-size:12px !important;color:var(--ink-500) !important;'
    'font-weight:500 !important;text-transform:uppercase;letter-spacing:0.4px;}'
    '[data-testid="stMetricValue"]{'
    "font-family:'Inter',sans-serif !important;font-size:28px !important;"
    'font-weight:700 !important;color:var(--ink-900) !important;letter-spacing:-0.8px;}'
    '[data-testid="stMetricDelta"]{font-size:11px !important;font-weight:600 !important;}'
    # Tabs
    '.stTabs [data-baseweb="tab-list"]{'
    'gap:4px;background:transparent;border-bottom:1px solid var(--ink-100);padding:0 4px;}'
    '.stTabs [data-baseweb="tab"]{'
    'background:transparent !important;color:var(--ink-500) !important;'
    'font-weight:500 !important;font-size:14px !important;padding:10px 16px !important;'
    'border-radius:0 !important;border-bottom:2px solid transparent !important;transition:all .15s;}'
    '.stTabs [data-baseweb="tab"]:hover{'
    'color:var(--ink-800) !important;background:var(--ink-50) !important;}'
    '.stTabs [aria-selected="true"]{'
    'color:var(--brand-700) !important;border-bottom-color:var(--brand-500) !important;'
    'font-weight:600 !important;}'
    # Inputs
    '.stTextInput input,.stNumberInput input,.stDateInput input,'
    '.stTextArea textarea,.stSelectbox [data-baseweb="select"] > div{'
    'border:1px solid var(--ink-200) !important;border-radius:8px !important;'
    'background:white !important;font-size:14px !important;color:var(--ink-900) !important;'
    'transition:border-color .15s,box-shadow .15s;}'
    '.stTextInput input:focus,.stNumberInput input:focus,'
    '.stDateInput input:focus,.stTextArea textarea:focus{'
    'border-color:var(--brand-500) !important;'
    'box-shadow:0 0 0 3px var(--brand-100) !important;outline:none !important;}'
    # Expander
    '.streamlit-expanderHeader,[data-testid="stExpander"] summary{'
    'background:white !important;border:1px solid var(--ink-100) !important;'
    'border-radius:10px !important;font-weight:500 !important;color:var(--ink-700) !important;'
    'transition:all .15s;}'
    '.streamlit-expanderHeader:hover,[data-testid="stExpander"] summary:hover{'
    'border-color:var(--brand-400) !important;color:var(--ink-900) !important;}'
    '[data-testid="stExpander"]{border:none !important;box-shadow:none !important;}'
    # Sidebar
    '[data-testid="stSidebar"]{background:white !important;border-right:1px solid var(--ink-100);}'
    '[data-testid="stSidebar"] .block-container{padding-top:1.5rem}'
    # Alerts
    '.stAlert{border-radius:10px !important;border-width:1px !important;}'
    '[data-baseweb="notification"]{border-radius:10px !important}'
    # Tables
    '[data-testid="stDataFrame"]{border:1px solid var(--ink-100);border-radius:10px;overflow:hidden;}'
    # Checkbox/radio
    '.stCheckbox label,.stRadio label{font-size:14px !important;color:var(--ink-700) !important}'
    # Dividers
    'hr{border-color:var(--ink-100) !important;opacity:1 !important}'
    # Section/sub titles
    '.sub-title,.section-mega-title{'
    'display:flex;align-items:center;gap:10px;background:white;'
    'border:1px solid var(--ink-100);border-left:4px solid var(--brand-500);'
    'padding:14px 18px;border-radius:8px;margin:24px 0 14px;'
    'font-size:18px;font-weight:600;color:var(--ink-900);box-shadow:var(--shadow-sm);}'
    # OR room cards
    '.or-room-card{'
    'background:white !important;border-radius:12px !important;'
    'border:1px solid var(--ink-100) !important;box-shadow:var(--shadow-sm) !important;'
    'border-top:3px solid var(--ink-200) !important;transition:all .15s;}'
    '.or-room-card:hover{border-color:var(--ink-200) !important;box-shadow:var(--shadow-md) !important;}'
    '.or-room-empty{background:var(--ink-50) !important;border-top-color:var(--ink-300) !important;}'
    '.or-room-active{'
    'background:linear-gradient(180deg,#ecfeff 0%,white 80%) !important;'
    'border-top-color:var(--brand-500) !important;}'
    # Case cards
    '.case-card{'
    'background:white;border-radius:12px;border:1px solid var(--ink-100);'
    'border-left:4px solid var(--brand-500);padding:16px 20px;margin-bottom:10px;'
    'box-shadow:var(--shadow-sm);transition:all .15s;}'
    '.case-card:hover{box-shadow:var(--shadow-md);border-color:var(--ink-200)}'
    '.pt-name{font-weight:600;color:var(--ink-900);font-size:15px}'
    '.pt-hn{color:var(--ink-400);font-size:12px;margin-left:8px;font-weight:400}'
    '.pt-proc{font-size:14px;color:var(--ink-700);margin-top:4px}'
    '.pt-meta{font-size:12px;color:var(--ink-500);margin-top:2px}'
    '.pill{display:inline-block;font-size:11px;font-weight:600;'
    'padding:3px 10px;border-radius:12px;letter-spacing:0.2px;}'
    # KPI/metric boxes (legacy classes)
    '.metric-box,.kpi-card{'
    'background:white !important;border-radius:10px !important;'
    'border:1px solid var(--ink-100) !important;padding:14px 16px !important;'
    'box-shadow:var(--shadow-sm) !important;transition:all .15s;}'
    '.metric-box:hover,.kpi-card:hover{'
    'border-color:var(--brand-400) !important;transform:translateY(-1px);}'
    '.metric-num,.stat-value{'
    "font-family:'Inter',sans-serif !important;font-size:28px !important;"
    'font-weight:700 !important;color:var(--ink-900) !important;letter-spacing:-0.8px;}'
    '.metric-lbl,.stat-title{'
    'font-size:12px !important;color:var(--ink-500) !important;font-weight:500 !important;}'
    # Captions
    '.stCaption,[data-testid="stCaptionContainer"]{'
    'color:var(--ink-400) !important;font-size:12px !important;}'
    # Progress bars
    '.stProgress > div > div{background:var(--brand-500) !important}'
    '.stProgress > div{background:var(--ink-100) !important}'
    '</style>'
)


def inject_theme() -> None:
    """Inject central theme CSS.
    NOTE: ต้อง inject ทุก rerun (อย่าใส่ session_state guard) เพราะ Streamlit
    re-render entire DOM ทุก script run → CSS หายถ้าไม่ render ใหม่"""
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def render_page_header(emoji: str, title: str, subtitle: str = "") -> None:
    """Render unified page header — เรียกใช้ตอนต้น page

    Args:
        emoji: emoji icon (เช่น 🏥)
        title: page title
        subtitle: subtitle (optional)
    """
    sub_html = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f'<div class="page-header"><h2>{emoji} {title}</h2>{sub_html}</div>',
        unsafe_allow_html=True,
    )
