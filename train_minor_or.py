"""
Train ML Model for Minor OR Surgical Duration Prediction
สคริปต์ฝึกโมเดลทำนายเวลาผ่าตัดสำหรับห้องผ่าตัดเล็ก

Usage: python train_minor_or.py
Output: minor_or_model.pkl, minor_or_evaluation.json
"""

import pandas as pd
import numpy as np
import pickle
import json
import re
import warnings
import os
warnings.filterwarnings('ignore')

from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, median_absolute_error

try:
    from xgboost import XGBRegressor
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False
    print("Warning: XGBoost not installed. Skipping XGBoost model.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. LOAD DATA
# ============================================================
print("=" * 60)
print("MINOR OR - MODEL TRAINING PIPELINE")
print("=" * 60)

intraop_path = os.path.join(BASE_DIR, 'สถิติintraopห้องผ่าตัดเล็ก.csv')
sched_path = os.path.join(BASE_DIR, 'สถิติschedห้องผ่าตัดเล็ก.csv')

df_intraop = pd.read_csv(intraop_path, encoding='utf-16', sep=',')
df_sched = pd.read_csv(sched_path, encoding='utf-16', sep=',')

print(f"\n[1] Data loaded")
print(f"    Intraop: {df_intraop.shape[0]:,} rows × {df_intraop.shape[1]} cols")
print(f"    Sched:   {df_sched.shape[0]:,} rows × {df_sched.shape[1]} cols")

# ============================================================
# 2. PARSE TARGET: opusetime → minutes
# ============================================================
def parse_opusetime(t):
    if pd.isna(t):
        return np.nan
    try:
        parts = str(t).split(':')
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        return h * 60 + m + s / 60.0
    except:
        return np.nan

df_intraop['duration_minutes'] = df_intraop['opusetime'].apply(parse_opusetime)
df_intraop = df_intraop.dropna(subset=['duration_minutes'])
df_intraop = df_intraop[df_intraop['duration_minutes'] > 0]

print(f"\n[2] Target parsed: {len(df_intraop):,} valid cases")
print(f"    Duration: mean={df_intraop['duration_minutes'].mean():.1f}, "
      f"median={df_intraop['duration_minutes'].median():.1f}, "
      f"std={df_intraop['duration_minutes'].std():.1f} min")

# ============================================================
# 3. FEATURE ENGINEERING
# ============================================================
def parse_time_code(tc):
    try:
        tc = int(float(tc))
        return tc // 10000
    except:
        return np.nan

def parse_date(d):
    try:
        return pd.to_datetime(d, format='%d/%m/%Y %H:%M:%S', dayfirst=True)
    except:
        try:
            return pd.to_datetime(d, dayfirst=True)
        except:
            return pd.NaT

def normalize_procedure(name):
    if pd.isna(name):
        return 'UNKNOWN'
    name = str(name).strip().upper()
    name = re.sub(r'\s+', ' ', name)
    return name

df_intraop['op_hour'] = df_intraop['opetime'].apply(parse_time_code)
df_intraop['opedate_parsed'] = df_intraop['opedate'].apply(parse_date)
df_intraop['day_of_week'] = df_intraop['opedate_parsed'].dt.dayofweek
df_intraop['month'] = df_intraop['opedate_parsed'].dt.month

def time_slot(hour):
    if pd.isna(hour): return 'unknown'
    if hour < 12: return 'morning'
    elif hour < 16: return 'afternoon'
    else: return 'evening'

df_intraop['time_slot'] = df_intraop['op_hour'].apply(time_slot)
df_intraop['procedure_clean'] = df_intraop['icd9cmnm'].apply(normalize_procedure)

print(f"\n[3] Features engineered")

# ============================================================
# 4. MERGE WITH SCHED
# ============================================================
df_intraop['hn_str'] = df_intraop['hn'].astype(str).str.strip()
df_intraop['opedate_str'] = df_intraop['opedate'].astype(str).str.strip()
df_sched['hn_str'] = df_sched['hn'].astype(str).str.strip()
df_sched['opedate_str'] = df_sched['opedate'].astype(str).str.strip()

sched_subset = df_sched[['hn_str', 'opedate_str', 'age', 'optype_var']].copy()
sched_subset = sched_subset.drop_duplicates(subset=['hn_str', 'opedate_str'], keep='first')

df = df_intraop.merge(sched_subset, on=['hn_str', 'opedate_str'], how='left')
df['age'] = df['age'].fillna(df['age'].median())
df['optype_var'] = df['optype_var'].fillna('elective')
df['surgeon_clean'] = df['dctnm'].fillna('UNKNOWN').astype(str).str.strip()
df['division_str'] = df['division'].astype(str).str.strip()

# Historical averages
surgeon_avg = df.groupby('surgeon_clean')['duration_minutes'].mean().to_dict()
proc_avg = df.groupby('procedure_clean')['duration_minutes'].mean().to_dict()
global_avg = df['duration_minutes'].mean()

df['surgeon_avg_duration'] = df['surgeon_clean'].map(surgeon_avg)
df['proc_avg_duration'] = df['procedure_clean'].map(proc_avg)

print(f"[4] Merged: {len(df):,} rows (age available: {df['age'].notna().mean()*100:.1f}%)")

# ============================================================
# 5. ENCODE & PREPARE
# ============================================================
le_proc = LabelEncoder()
le_surgeon = LabelEncoder()
le_division = LabelEncoder()
le_timeslot = LabelEncoder()
le_optype = LabelEncoder()

df['procedure_encoded'] = le_proc.fit_transform(df['procedure_clean'])
df['surgeon_encoded'] = le_surgeon.fit_transform(df['surgeon_clean'])
df['division_encoded'] = le_division.fit_transform(df['division_str'])
df['time_slot_encoded'] = le_timeslot.fit_transform(df['time_slot'])
df['optype_encoded'] = le_optype.fit_transform(df['optype_var'])

feature_cols = [
    'procedure_encoded', 'surgeon_encoded', 'division_encoded',
    'age', 'op_hour', 'day_of_week', 'month',
    'time_slot_encoded', 'optype_encoded',
    'surgeon_avg_duration', 'proc_avg_duration',
]

df_model = df.dropna(subset=feature_cols + ['duration_minutes'])
X = df_model[feature_cols].values
y = df_model['duration_minutes'].values

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

print(f"\n[5] Dataset ready: {len(X):,} samples ({len(feature_cols)} features)")
print(f"    Train: {len(X_train):,} | Test: {len(X_test):,}")

# ============================================================
# 6. TRAIN & EVALUATE
# ============================================================
models = {
    'Linear Regression': LinearRegression(),
    'Ridge Regression': Ridge(alpha=1.0),
    'Random Forest': RandomForestRegressor(
        n_estimators=200, max_depth=15, min_samples_leaf=5,
        random_state=42, n_jobs=-1
    ),
    'Gradient Boosting': GradientBoostingRegressor(
        n_estimators=200, max_depth=6, learning_rate=0.1,
        random_state=42
    ),
}

if HAS_XGBOOST:
    models['XGBoost'] = XGBRegressor(
        n_estimators=300, max_depth=6, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbosity=0
    )

results = {}
best_model = None
best_mae = float('inf')

print(f"\n{'='*60}")
print("MODEL EVALUATION")
print(f"{'='*60}")

for name, model in models.items():
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)
    medae = median_absolute_error(y_test, y_pred)
    mape = np.mean(np.abs((y_test - y_pred) / np.clip(y_test, 1, None))) * 100
    within_10 = np.mean(np.abs(y_test - y_pred) <= 10) * 100
    within_15 = np.mean(np.abs(y_test - y_pred) <= 15) * 100

    results[name] = {
        'MAE': round(mae, 2), 'RMSE': round(rmse, 2), 'R2': round(r2, 4),
        'MedAE': round(medae, 2), 'MAPE': round(mape, 1),
        'within_10min': round(within_10, 1), 'within_15min': round(within_15, 1)
    }

    print(f"\n  {name}")
    print(f"    MAE: {mae:.2f} | RMSE: {rmse:.2f} | R²: {r2:.4f}")
    print(f"    MedAE: {medae:.2f} | MAPE: {mape:.1f}%")
    print(f"    ±10min: {within_10:.1f}% | ±15min: {within_15:.1f}%")

    if mae < best_mae:
        best_mae = mae
        best_model = (name, model)

print(f"\n{'='*60}")
print(f"BEST: {best_model[0]} (MAE = {best_mae:.2f} min)")
print(f"{'='*60}")

# Feature importance
if hasattr(best_model[1], 'feature_importances_'):
    importances = best_model[1].feature_importances_
    print("\nFeature Importance:")
    for fname, imp in sorted(zip(feature_cols, importances), key=lambda x: -x[1]):
        print(f"  {fname:25s} {imp:.4f}")

# ============================================================
# 7. SAVE
# ============================================================
model_path = os.path.join(BASE_DIR, 'minor_or_model.pkl')
with open(model_path, 'wb') as f:
    pickle.dump({
        'model': best_model[1],
        'model_name': best_model[0],
        'feature_cols': feature_cols,
        'le_proc': le_proc,
        'le_surgeon': le_surgeon,
        'le_division': le_division,
        'le_timeslot': le_timeslot,
        'le_optype': le_optype,
        'surgeon_avg': surgeon_avg,
        'proc_avg': proc_avg,
        'global_avg': float(global_avg),
        'results': results,
        'procedures': list(le_proc.classes_),
        'surgeons': list(le_surgeon.classes_),
        'divisions': list(le_division.classes_),
    }, f)

eval_path = os.path.join(BASE_DIR, 'minor_or_evaluation.json')
with open(eval_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"\nFiles saved:")
print(f"  Model:      {model_path}")
print(f"  Evaluation: {eval_path}")
print(f"\nDone! Run the app with: streamlit run minor_or_app.py")
