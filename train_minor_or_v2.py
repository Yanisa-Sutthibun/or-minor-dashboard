"""
Minor OR ML Training v2 — Full Feature Set
โครงสร้างใหม่: ใช้ surgeon จาก intraop (dctnm) เพราะ schedule มีชื่อผิด
Features ครบ: procedure, surgeon, anesthesia, nurses, day/month, interaction features
"""
import pandas as pd
import numpy as np
import pickle
import json
import warnings
from datetime import datetime
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, median_absolute_error

warnings.filterwarnings('ignore')

try:
    from xgboost import XGBRegressor
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

print("=" * 70)
print("Minor OR ML Training v2 — Full Feature Set")
print("=" * 70)

# ============================================================================
# 1. LOAD DATA
# ============================================================================
print("\n[1/7] Loading data...")
intraop = pd.read_csv('สถิติintraopห้องผ่าตัดเล็ก.csv', encoding='utf-16', sep=',')
sched = pd.read_csv('สถิติschedห้องผ่าตัดเล็ก.csv', encoding='utf-16', sep=',')
print(f"  intraop: {intraop.shape} | sched: {sched.shape}")

# ============================================================================
# 2. PARSE TARGET (opusetime → minutes)
# ============================================================================
print("\n[2/7] Parsing target (opusetime)...")
def parse_duration(val):
    try:
        s = str(val).strip()
        if ':' in s:
            parts = s.split(':')
            return int(parts[0]) * 60 + int(parts[1])
        return None
    except:
        return None

intraop['duration_min'] = intraop['opusetime'].apply(parse_duration)
valid = intraop[intraop['duration_min'].notna() & (intraop['duration_min'] >= 5) & (intraop['duration_min'] <= 600)].copy()
print(f"  Valid cases: {len(valid)} | mean={valid['duration_min'].mean():.1f} min | median={valid['duration_min'].median():.1f}")

# ============================================================================
# 3. TIME FEATURES FROM INTRAOP
# ============================================================================
print("\n[3/7] Extracting time features...")

def parse_date(val):
    try:
        return pd.to_datetime(str(val), dayfirst=True, errors='coerce')
    except:
        return None

def parse_hhmmss(val):
    """Parse 153000.0 → (15, 30, 0)"""
    try:
        v = int(float(val))
        return (v // 10000, (v % 10000) // 100, v % 100)
    except:
        return (None, None, None)

valid['opedate_parsed'] = valid['roomdatein'].apply(parse_date)
valid['day_of_week'] = valid['opedate_parsed'].dt.dayofweek  # Mon=0, Sun=6
valid['month'] = valid['opedate_parsed'].dt.month
valid['is_weekend'] = (valid['day_of_week'] >= 5).astype(int)
valid['quarter'] = valid['opedate_parsed'].dt.quarter

time_parts = valid['roomtimein'].apply(parse_hhmmss)
valid['op_hour'] = time_parts.apply(lambda x: x[0] if x[0] is not None else 9)
valid['op_minute'] = time_parts.apply(lambda x: x[1] if x[1] is not None else 0)

def time_slot(h):
    if h < 12: return 'morning'
    elif h < 16: return 'afternoon'
    else: return 'evening'
valid['time_slot'] = valid['op_hour'].apply(time_slot)

# Optional: arrival-to-room wait time
arr_parts = valid['arrivtime'].apply(parse_hhmmss)
valid['arrival_hour'] = arr_parts.apply(lambda x: x[0] if x[0] is not None else None)
valid['arrival_minute'] = arr_parts.apply(lambda x: x[1] if x[1] is not None else None)
valid['wait_min'] = (valid['op_hour'] * 60 + valid['op_minute']) - (valid['arrival_hour'] * 60 + valid['arrival_minute'])
valid['wait_min'] = valid['wait_min'].clip(-60, 300).fillna(0)

print(f"  Day distribution: {valid['day_of_week'].value_counts().sort_index().to_dict()}")
print(f"  Month distribution: {valid['month'].value_counts().sort_index().to_dict()}")
print(f"  Weekend cases: {valid['is_weekend'].sum()} ({valid['is_weekend'].mean()*100:.1f}%)")

# ============================================================================
# 4. MERGE WITH SCHED FOR AGE + ANESTHESIA + OPTYPE
# ============================================================================
print("\n[4/7] Merging with sched for age, anesthesia, optype...")

# Prepare sched
sched['opedate_parsed'] = sched['opedate'].apply(parse_date)
sched['opedate_str'] = sched['opedate_parsed'].dt.strftime('%Y-%m-%d')
sched['hn_str'] = sched['hn'].astype(str).str.strip()

# Key columns from sched
sched_keep = sched[['hn_str', 'opedate_str', 'age', 'name.1', 'optype_var', 'optypenm']].copy()
sched_keep = sched_keep.rename(columns={'name.1': 'anesthesia_sched'})
# Dedupe by (hn, opedate) keeping first
sched_keep = sched_keep.drop_duplicates(subset=['hn_str', 'opedate_str'], keep='first')

valid['hn_str'] = valid['hn'].astype(str).str.strip()
valid['opedate_str'] = valid['opedate_parsed'].dt.strftime('%Y-%m-%d')

merged = valid.merge(sched_keep, on=['hn_str', 'opedate_str'], how='left')
print(f"  Merged: {len(merged)} | age available: {merged['age'].notna().sum()} ({merged['age'].notna().mean()*100:.1f}%)")
print(f"  Anesthesia available: {merged['anesthesia_sched'].notna().sum()}")
print(f"  Optype available: {merged['optype_var'].notna().sum()}")

# Fill missing
merged['age'] = merged['age'].fillna(merged['age'].median()).astype(int)
merged['anesthesia_sched'] = merged['anesthesia_sched'].fillna('UNKNOWN').astype(str)
merged['optype_var'] = merged['optype_var'].fillna('elective').astype(str).str.lower()

# ============================================================================
# 5. CLEAN CATEGORICAL + BUILD INTERACTION FEATURES
# ============================================================================
print("\n[5/7] Cleaning categoricals + interaction features...")

def clean_str(v, default='UNKNOWN'):
    if pd.isna(v) or str(v).strip() == '':
        return default
    return str(v).strip()

# ⭐ SURGEON from INTRAOP (dctnm) — the ACTUAL operating surgeon
merged['procedure'] = merged['icd9cmnm'].apply(lambda x: clean_str(x).upper())
merged['surgeon'] = merged['dctnm'].apply(clean_str)
merged['division_str'] = merged['division'].apply(lambda x: clean_str(x, '75'))
merged['scrub_nurse'] = merged['nursurgnm'].apply(clean_str)
merged['circ_nurse'] = merged['nurcircunm'].apply(clean_str)
merged['has_assistant'] = merged['assisdct1nm'].notna().astype(int)
merged['anesthesia'] = merged['anesthesia_sched'].apply(lambda x: clean_str(x).upper())

print(f"  Unique procedures: {merged['procedure'].nunique()}")
print(f"  Unique surgeons (from intraop): {merged['surgeon'].nunique()}")
print(f"  Unique scrub nurses: {merged['scrub_nurse'].nunique()}")
print(f"  Unique circ nurses: {merged['circ_nurse'].nunique()}")
print(f"  Unique anesthesia: {merged['anesthesia'].nunique()}")
print(f"  Has assistant: {merged['has_assistant'].sum()} ({merged['has_assistant'].mean()*100:.1f}%)")

# ⭐⭐ HISTORICAL AVG FEATURES (strongest signal)
# Global
global_avg = merged['duration_min'].mean()

# Per surgeon
surgeon_avg = merged.groupby('surgeon')['duration_min'].mean().to_dict()
surgeon_count = merged.groupby('surgeon')['duration_min'].count().to_dict()
merged['surgeon_avg_duration'] = merged['surgeon'].map(surgeon_avg).fillna(global_avg)
merged['surgeon_case_count'] = merged['surgeon'].map(surgeon_count).fillna(0)

# Per procedure
proc_avg = merged.groupby('procedure')['duration_min'].mean().to_dict()
proc_count = merged.groupby('procedure')['duration_min'].count().to_dict()
merged['proc_avg_duration'] = merged['procedure'].map(proc_avg).fillna(global_avg)
merged['proc_case_count'] = merged['procedure'].map(proc_count).fillna(0)

# ⭐ INTERACTION: surgeon × procedure (strongest feature\!)
merged['surg_proc_key'] = merged['surgeon'] + '||' + merged['procedure']
surg_proc_avg = merged.groupby('surg_proc_key')['duration_min'].mean().to_dict()
surg_proc_count = merged.groupby('surg_proc_key')['duration_min'].count().to_dict()
merged['surg_proc_avg'] = merged['surg_proc_key'].map(surg_proc_avg).fillna(global_avg)
merged['surg_proc_count'] = merged['surg_proc_key'].map(surg_proc_count).fillna(0)

# Per day of week
dow_avg = merged.groupby('day_of_week')['duration_min'].mean().to_dict()
merged['dow_avg'] = merged['day_of_week'].map(dow_avg).fillna(global_avg)

# Per month
month_avg = merged.groupby('month')['duration_min'].mean().to_dict()
merged['month_avg'] = merged['month'].map(month_avg).fillna(global_avg)

# Per anesthesia
anes_avg = merged.groupby('anesthesia')['duration_min'].mean().to_dict()
merged['anes_avg'] = merged['anesthesia'].map(anes_avg).fillna(global_avg)

print(f"  Global avg: {global_avg:.1f} min")
print(f"  Top 5 surgeons by avg time:")
for s, v in sorted(surgeon_avg.items(), key=lambda x: -x[1])[:5]:
    n = surgeon_count.get(s, 0)
    if n >= 10:
        print(f"    {s[:40]:40s} — {v:.1f} min (n={n})")
print(f"  Day-of-week pattern:")
for d, v in sorted(dow_avg.items()):
    day_name = ['จันทร์','อังคาร','พุธ','พฤหัสบดี','ศุกร์','เสาร์','อาทิตย์'][d]
    print(f"    {day_name}: {v:.1f} min")

# ============================================================================
# 6. ENCODE + BUILD FEATURE MATRIX
# ============================================================================
print("\n[6/7] Encoding features + building matrix...")

le_proc = LabelEncoder().fit(merged['procedure'])
le_surgeon = LabelEncoder().fit(merged['surgeon'])
le_division = LabelEncoder().fit(merged['division_str'])
le_scrub = LabelEncoder().fit(merged['scrub_nurse'])
le_circ = LabelEncoder().fit(merged['circ_nurse'])
le_anes = LabelEncoder().fit(merged['anesthesia'])
le_optype = LabelEncoder().fit(merged['optype_var'])
le_timeslot = LabelEncoder().fit(merged['time_slot'])

merged['proc_enc'] = le_proc.transform(merged['procedure'])
merged['surgeon_enc'] = le_surgeon.transform(merged['surgeon'])
merged['division_enc'] = le_division.transform(merged['division_str'])
merged['scrub_enc'] = le_scrub.transform(merged['scrub_nurse'])
merged['circ_enc'] = le_circ.transform(merged['circ_nurse'])
merged['anes_enc'] = le_anes.transform(merged['anesthesia'])
merged['optype_enc'] = le_optype.transform(merged['optype_var'])
merged['timeslot_enc'] = le_timeslot.transform(merged['time_slot'])

# Feature columns
feature_cols = [
    # Categorical (encoded)
    'proc_enc', 'surgeon_enc', 'division_enc',
    'scrub_enc', 'circ_enc', 'anes_enc', 'optype_enc', 'timeslot_enc',
    # Numeric patient/time
    'age', 'op_hour', 'day_of_week', 'month', 'is_weekend', 'quarter',
    'has_assistant', 'wait_min',
    # Historical averages (strong signal)
    'surgeon_avg_duration', 'surgeon_case_count',
    'proc_avg_duration', 'proc_case_count',
    # ⭐ Interaction
    'surg_proc_avg', 'surg_proc_count',
    # Group averages
    'dow_avg', 'month_avg', 'anes_avg',
]

X = merged[feature_cols].values
y = merged['duration_min'].values
print(f"  Feature matrix: {X.shape} | Target: {y.shape}")
print(f"  Features used: {len(feature_cols)}")

# ============================================================================
# 7. TRAIN MODELS + EVALUATE
# ============================================================================
print("\n[7/7] Training models...")

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
print(f"  Train: {X_train.shape} | Test: {X_test.shape}")

models = {
    'Linear Regression': LinearRegression(),
    'Ridge (alpha=1)': Ridge(alpha=1.0),
    'Ridge (alpha=10)': Ridge(alpha=10.0),
    'Lasso (alpha=0.1)': Lasso(alpha=0.1),
    'Random Forest': RandomForestRegressor(n_estimators=200, max_depth=15, min_samples_leaf=3, random_state=42, n_jobs=-1),
    'Gradient Boosting': GradientBoostingRegressor(n_estimators=200, max_depth=5, learning_rate=0.05, random_state=42),
}
if HAS_XGB:
    models['XGBoost'] = XGBRegressor(n_estimators=300, max_depth=6, learning_rate=0.05, random_state=42, n_jobs=-1, verbosity=0)

results = {}
trained = {}
for name, model in models.items():
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    y_pred = np.clip(y_pred, 5, 600)

    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)
    medae = median_absolute_error(y_test, y_pred)
    within_10 = np.mean(np.abs(y_test - y_pred) <= 10) * 100
    within_15 = np.mean(np.abs(y_test - y_pred) <= 15) * 100
    mape = np.mean(np.abs((y_test - y_pred) / np.maximum(y_test, 1))) * 100

    results[name] = {
        'MAE': round(mae, 2), 'RMSE': round(rmse, 2), 'R2': round(r2, 4),
        'MedAE': round(medae, 2), 'MAPE': round(mape, 2),
        'within_10min': round(within_10, 1), 'within_15min': round(within_15, 1),
    }
    trained[name] = model
    print(f"  {name:22s}: MAE={mae:6.2f} | RMSE={rmse:6.2f} | R²={r2:.4f} | ±15min={within_15:.1f}%")

# Pick best (lowest MAE)
best_name = min(results, key=lambda k: results[k]['MAE'])
print(f"\n🏆 Best model: {best_name} (MAE={results[best_name]['MAE']})")

# Feature importance (if tree-based)
best_model = trained[best_name]
if hasattr(best_model, 'feature_importances_'):
    imp = best_model.feature_importances_
    imp_sorted = sorted(zip(feature_cols, imp), key=lambda x: -x[1])
    print(f"\n📊 Top 10 Feature Importances:")
    for name, val in imp_sorted[:10]:
        print(f"  {name:25s}: {val:.4f}")
elif hasattr(best_model, 'coef_'):
    coefs = best_model.coef_
    coef_sorted = sorted(zip(feature_cols, np.abs(coefs)), key=lambda x: -x[1])
    print(f"\n📊 Top 10 Feature Coefficients (absolute):")
    for name, val in coef_sorted[:10]:
        print(f"  {name:25s}: {val:.4f}")

# ============================================================================
# SAVE
# ============================================================================
print("\n💾 Saving model...")
save_data = {
    'model': best_model,
    'model_name': best_name,
    'feature_cols': feature_cols,
    'le_proc': le_proc,
    'le_surgeon': le_surgeon,
    'le_division': le_division,
    'le_scrub': le_scrub,
    'le_circ': le_circ,
    'le_anes': le_anes,
    'le_optype': le_optype,
    'le_timeslot': le_timeslot,
    'surgeon_avg': surgeon_avg,
    'surgeon_count': surgeon_count,
    'proc_avg': proc_avg,
    'proc_count': proc_count,
    'surg_proc_avg': surg_proc_avg,
    'surg_proc_count': surg_proc_count,
    'dow_avg': dow_avg,
    'month_avg': month_avg,
    'anes_avg': anes_avg,
    'global_avg': float(global_avg),
    'results': results,
    'procedures': sorted(merged['procedure'].unique().tolist()),
    'surgeons': sorted(merged['surgeon'].unique().tolist()),
    'divisions': sorted(merged['division_str'].unique().tolist()),
    'anesthesia_types': sorted(merged['anesthesia'].unique().tolist()),
    'version': 'v2',
    'trained_at': datetime.now().isoformat(),
    'n_samples': len(merged),
    'n_features': len(feature_cols),
}

with open('minor_or_model.pkl', 'wb') as f:
    pickle.dump(save_data, f)

with open('minor_or_evaluation.json', 'w', encoding='utf-8') as f:
    json.dump({
        'version': 'v2',
        'n_samples': len(merged),
        'n_features': len(feature_cols),
        'best_model': best_name,
        'results': results,
        'feature_cols': feature_cols,
    }, f, ensure_ascii=False, indent=2)

print(f"✅ Saved minor_or_model.pkl + minor_or_evaluation.json")
print(f"   Samples: {len(merged)} | Features: {len(feature_cols)} | Best: {best_name}")
