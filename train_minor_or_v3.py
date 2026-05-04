"""
Minor OR ML Training v3 — Optimized Feature Set (16 features)
เก็บ features ที่ pro09.py (ห้องใหญ่) ใช้ทั้งหมด + features แรงๆ จาก v2 + ตัดตัวไม่มีผล
"""
import pandas as pd
import numpy as np
import pickle
import json
import warnings
from datetime import datetime
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
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
print("Minor OR ML Training v3 — Optimized (16 features)")
print("=" * 70)

# === 1. LOAD DATA ===
print("\n[1/7] Loading data...")
intraop = pd.read_csv('สถิติintraopห้องผ่าตัดเล็ก.csv', encoding='utf-16', sep=',')
sched = pd.read_csv('สถิติschedห้องผ่าตัดเล็ก.csv', encoding='utf-16', sep=',')
print(f"  intraop: {intraop.shape} | sched: {sched.shape}")

# === 2. PARSE TARGET ===
def parse_duration(val):
    try:
        s = str(val).strip()
        if ':' in s:
            p = s.split(':'); return int(p[0])*60 + int(p[1])
        return None
    except: return None

intraop['duration_min'] = intraop['opusetime'].apply(parse_duration)
valid = intraop[intraop['duration_min'].notna() & (intraop['duration_min'] >= 5) & (intraop['duration_min'] <= 600)].copy()
print(f"\n[2/7] Valid cases: {len(valid)} | mean={valid['duration_min'].mean():.1f} | median={valid['duration_min'].median():.1f}")

# === 3. TIME FEATURES ===
print("\n[3/7] Time features...")
def parse_date(v):
    try: return pd.to_datetime(str(v), dayfirst=True, errors='coerce')
    except: return None
def parse_t(v):
    try:
        x = int(float(v)); return (x // 10000, (x % 10000) // 100)
    except: return (None, None)

valid['dt'] = valid['roomdatein'].apply(parse_date)
valid['day_of_week'] = valid['dt'].dt.dayofweek
valid['month'] = valid['dt'].dt.month
tp = valid['roomtimein'].apply(parse_t)
valid['op_hour'] = tp.apply(lambda x: x[0] if x[0] is not None else 9)
valid['op_minute'] = tp.apply(lambda x: x[1] if x[1] is not None else 0)
valid['time_slot'] = valid['op_hour'].apply(lambda h: 'morning' if h<12 else ('afternoon' if h<16 else 'evening'))

# wait_min from arrival
ap = valid['arrivtime'].apply(parse_t)
valid['ah'] = ap.apply(lambda x: x[0] if x[0] is not None else None)
valid['am'] = ap.apply(lambda x: x[1] if x[1] is not None else None)
valid['wait_min'] = ((valid['op_hour']*60 + valid['op_minute']) - (valid['ah']*60 + valid['am'])).clip(-60, 300).fillna(0)

# === 4. MERGE WITH SCHED ===
print("\n[4/7] Merging with sched...")
sched['dt2'] = sched['opedate'].apply(parse_date)
sched['ods'] = sched['dt2'].dt.strftime('%Y-%m-%d')
sched['hs'] = sched['hn'].astype(str).str.strip()
sk = sched[['hs','ods','age','optype_var']].drop_duplicates(['hs','ods'])
valid['hs'] = valid['hn'].astype(str).str.strip()
valid['ods'] = valid['dt'].dt.strftime('%Y-%m-%d')
merged = valid.merge(sk, on=['hs','ods'], how='left')
merged['age'] = merged['age'].fillna(merged['age'].median()).astype(int)
merged['optype_var'] = merged['optype_var'].fillna('elective').astype(str).str.lower()
print(f"  Merged: {len(merged)} | age available: {merged['age'].notna().sum()}")

# === 5. CLEAN + HISTORICAL AVERAGES ===
print("\n[5/7] Clean + compute averages...")
def cs(x, d='UNKNOWN'):
    if pd.isna(x) or str(x).strip() == '': return d
    return str(x).strip()

merged['procedure'] = merged['icd9cmnm'].apply(lambda x: cs(x).upper())
merged['surgeon'] = merged['dctnm'].apply(cs)  # ⭐ from intraop (actual surgeon)
merged['division_str'] = merged['division'].apply(lambda x: cs(x, '75'))
merged['scrub_nurse'] = merged['nursurgnm'].apply(cs)
merged['circ_nurse'] = merged['nurcircunm'].apply(cs)

global_avg = merged['duration_min'].mean()
surgeon_avg = merged.groupby('surgeon')['duration_min'].mean().to_dict()
proc_avg = merged.groupby('procedure')['duration_min'].mean().to_dict()
merged['surg_proc_key'] = merged['surgeon'] + '||' + merged['procedure']
surg_proc_avg = merged.groupby('surg_proc_key')['duration_min'].mean().to_dict()
surg_proc_count = merged.groupby('surg_proc_key')['duration_min'].count().to_dict()
month_avg = merged.groupby('month')['duration_min'].mean().to_dict()

merged['surgeon_avg_duration'] = merged['surgeon'].map(surgeon_avg).fillna(global_avg)
merged['proc_avg_duration'] = merged['procedure'].map(proc_avg).fillna(global_avg)
merged['surg_proc_avg'] = merged['surg_proc_key'].map(surg_proc_avg).fillna(global_avg)
merged['month_avg'] = merged['month'].map(month_avg).fillna(global_avg)

# === 6. ENCODE ===
print("\n[6/7] Encoding...")
le_proc = LabelEncoder().fit(merged['procedure'])
le_surgeon = LabelEncoder().fit(merged['surgeon'])
le_division = LabelEncoder().fit(merged['division_str'])
le_scrub = LabelEncoder().fit(merged['scrub_nurse'])
le_circ = LabelEncoder().fit(merged['circ_nurse'])
le_optype = LabelEncoder().fit(merged['optype_var'])
le_timeslot = LabelEncoder().fit(merged['time_slot'])

merged['proc_enc'] = le_proc.transform(merged['procedure'])
merged['surgeon_enc'] = le_surgeon.transform(merged['surgeon'])
merged['division_enc'] = le_division.transform(merged['division_str'])
merged['scrub_enc'] = le_scrub.transform(merged['scrub_nurse'])
merged['circ_enc'] = le_circ.transform(merged['circ_nurse'])
merged['optype_enc'] = le_optype.transform(merged['optype_var'])
merged['timeslot_enc'] = le_timeslot.transform(merged['time_slot'])

# ⭐ Selected 16 features
feature_cols = [
    # Core 11 from pro09.py (categorical + basic)
    'proc_enc', 'surgeon_enc', 'division_enc',
    'age', 'op_hour', 'day_of_week', 'month',
    'timeslot_enc', 'optype_enc',
    'surgeon_avg_duration', 'proc_avg_duration',
    # +5 strong additions from v2
    'surg_proc_avg',     # ⭐ dominant
    'scrub_enc', 'circ_enc',
    'wait_min',
    'month_avg',
]
X = merged[feature_cols].values
y = merged['duration_min'].values
print(f"  Feature matrix: {X.shape}")

# === 7. TRAIN & EVALUATE ===
print("\n[7/7] Training models...")
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)

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
    model.fit(Xtr, ytr)
    yp = np.clip(model.predict(Xte), 5, 600)
    mae = mean_absolute_error(yte, yp)
    rmse = np.sqrt(mean_squared_error(yte, yp))
    r2 = r2_score(yte, yp)
    medae = median_absolute_error(yte, yp)
    w10 = np.mean(np.abs(yte - yp) <= 10) * 100
    w15 = np.mean(np.abs(yte - yp) <= 15) * 100
    mape = np.mean(np.abs((yte - yp) / np.maximum(yte, 1))) * 100
    results[name] = {
        'MAE': round(mae, 2), 'RMSE': round(rmse, 2), 'R2': round(r2, 4),
        'MedAE': round(medae, 2), 'MAPE': round(mape, 2),
        'within_10min': round(w10, 1), 'within_15min': round(w15, 1),
    }
    trained[name] = model
    print(f"  {name:22s}: MAE={mae:6.2f} | R²={r2:.4f} | ±15min={w15:5.1f}%")

best = min(results, key=lambda k: results[k]['MAE'])
print(f"\n🏆 Best: {best} (MAE={results[best]['MAE']})")

# === SAVE ===
save = {
    'model': trained[best],
    'model_name': best,
    'feature_cols': feature_cols,
    'le_proc': le_proc, 'le_surgeon': le_surgeon, 'le_division': le_division,
    'le_scrub': le_scrub, 'le_circ': le_circ,
    'le_optype': le_optype, 'le_timeslot': le_timeslot,
    'surgeon_avg': surgeon_avg, 'proc_avg': proc_avg,
    'surg_proc_avg': surg_proc_avg, 'surg_proc_count': surg_proc_count,
    'month_avg': month_avg,
    'global_avg': float(global_avg),
    'results': results,
    'procedures': sorted(merged['procedure'].unique().tolist()),
    'surgeons': sorted(merged['surgeon'].unique().tolist()),
    'divisions': sorted(merged['division_str'].unique().tolist()),
    'scrub_nurses': sorted(merged['scrub_nurse'].unique().tolist()),
    'circ_nurses': sorted(merged['circ_nurse'].unique().tolist()),
    'version': 'v3',
    'trained_at': datetime.now().isoformat(),
    'n_samples': len(merged),
    'n_features': len(feature_cols),
}
with open('minor_or_model.pkl', 'wb') as f:
    pickle.dump(save, f)
with open('minor_or_evaluation.json', 'w', encoding='utf-8') as f:
    json.dump({'version': 'v3', 'n_samples': len(merged), 'n_features': len(feature_cols),
               'best_model': best, 'results': results, 'feature_cols': feature_cols},
              f, ensure_ascii=False, indent=2)
print(f"✅ Saved. Samples={len(merged)} | Features={len(feature_cols)}")
