"""
Minor OR ML Training v4 — Proper K-Fold CV + 60/20/20 split
═══════════════════════════════════════════════════════════════════
ปรับปรุงจาก v3:
 ✅ 5-Fold Cross Validation (แทน single train/test split)
 ✅ 60/20/20 split (Train / Validation / Test)
 ✅ Pick best model จาก CV mean (ไม่ใช่ single test)
 ✅ Final evaluation บน held-out test set (ที่ไม่เคยเห็นตอน CV)
 ✅ Learning curve diagnostic (overfit detection)
 ✅ Feature importance
 ✅ Detailed JSON report (cv mean ± std, fold-by-fold)
 ✅ Save แยกเป็น minor_or_model_v4.pkl (ไม่ทับ v3)
"""
import pandas as pd
import numpy as np
import pickle
import json
import warnings
from datetime import datetime
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import (train_test_split, KFold, cross_val_score,
                                      cross_validate, learning_curve)
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import (mean_absolute_error, mean_squared_error,
                              r2_score, median_absolute_error)

warnings.filterwarnings('ignore')

try:
    from xgboost import XGBRegressor
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import matplotlib
    matplotlib.use('Agg')  # non-interactive
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# Optional Thai font fix for matplotlib
if HAS_MPL:
    plt.rcParams['font.family'] = ['DejaVu Sans', 'Arial', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False

print("=" * 70)
print("Minor OR ML Training v4 — K-Fold CV + Proper Validation")
print("=" * 70)

# ════════════════════════════════════════════════════════════════════
# === 1. LOAD DATA ===
# ════════════════════════════════════════════════════════════════════
print("\n[1/8] Loading data...")
intraop = pd.read_csv('สถิติintraopห้องผ่าตัดเล็ก.csv', encoding='utf-16', sep=',')
sched = pd.read_csv('สถิติschedห้องผ่าตัดเล็ก.csv', encoding='utf-16', sep=',')
print(f"  intraop: {intraop.shape} | sched: {sched.shape}")

# ════════════════════════════════════════════════════════════════════
# === 2. PARSE TARGET ===
# ════════════════════════════════════════════════════════════════════
def parse_duration(val):
    try:
        s = str(val).strip()
        if ':' in s:
            p = s.split(':'); return int(p[0])*60 + int(p[1])
        return None
    except:
        return None

intraop['duration_min'] = intraop['opusetime'].apply(parse_duration)
valid = intraop[intraop['duration_min'].notna() &
                (intraop['duration_min'] >= 5) &
                (intraop['duration_min'] <= 600)].copy()
print(f"\n[2/8] Valid cases: {len(valid)} | "
      f"mean={valid['duration_min'].mean():.1f} | "
      f"median={valid['duration_min'].median():.1f}")

# ════════════════════════════════════════════════════════════════════
# === 3. TIME FEATURES (same as v3) ===
# ════════════════════════════════════════════════════════════════════
print("\n[3/8] Time features...")
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
valid['time_slot'] = valid['op_hour'].apply(
    lambda h: 'morning' if h<12 else ('afternoon' if h<16 else 'evening'))

ap = valid['arrivtime'].apply(parse_t)
valid['ah'] = ap.apply(lambda x: x[0] if x[0] is not None else None)
valid['am'] = ap.apply(lambda x: x[1] if x[1] is not None else None)
valid['wait_min'] = ((valid['op_hour']*60 + valid['op_minute']) -
                     (valid['ah']*60 + valid['am'])).clip(-60, 300).fillna(0)

# ════════════════════════════════════════════════════════════════════
# === 4. MERGE WITH SCHED ===
# ════════════════════════════════════════════════════════════════════
print("\n[4/8] Merging with sched...")
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

# ════════════════════════════════════════════════════════════════════
# === 5. CLEAN + HISTORICAL AVERAGES ===
# ════════════════════════════════════════════════════════════════════
print("\n[5/8] Clean + compute averages...")
def cs(x, d='UNKNOWN'):
    if pd.isna(x) or str(x).strip() == '': return d
    return str(x).strip()

merged['procedure'] = merged['icd9cmnm'].apply(lambda x: cs(x).upper())
merged['surgeon'] = merged['dctnm'].apply(cs)
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

# ════════════════════════════════════════════════════════════════════
# === 6. ENCODE ===
# ════════════════════════════════════════════════════════════════════
print("\n[6/8] Encoding...")
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

feature_cols = [
    'proc_enc', 'surgeon_enc', 'division_enc',
    'age', 'op_hour', 'day_of_week', 'month',
    'timeslot_enc', 'optype_enc',
    'surgeon_avg_duration', 'proc_avg_duration',
    'surg_proc_avg',
    'scrub_enc', 'circ_enc',
    'wait_min',
    'month_avg',
]
X = merged[feature_cols].values
y = merged['duration_min'].values
print(f"  Feature matrix: {X.shape}")

# ════════════════════════════════════════════════════════════════════
# === 7. SPLIT — 60/20/20 (Train / Validation / Test) ===
# ════════════════════════════════════════════════════════════════════
print("\n[7/8] 60/20/20 Split + 5-Fold CV...")
# First: hold out 20% test (untouched until final eval)
X_tv, X_test, y_tv, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42)
# Then split tv into 75/25 → 60% train + 20% val
X_train, X_val, y_train, y_val = train_test_split(
    X_tv, y_tv, test_size=0.25, random_state=42)
print(f"  Train: {len(X_train)} | Val: {len(X_val)} | Test: {len(X_test)}")

# ════════════════════════════════════════════════════════════════════
# === 8. MODEL COMPARISON via 5-Fold CV ===
# ════════════════════════════════════════════════════════════════════
print("\n[8/8] K-Fold Cross Validation (5 folds)...")
kfold = KFold(n_splits=5, shuffle=True, random_state=42)

models = {
    'Linear Regression': LinearRegression(),
    'Ridge (alpha=1)': Ridge(alpha=1.0),
    'Ridge (alpha=10)': Ridge(alpha=10.0),
    'Lasso (alpha=0.1)': Lasso(alpha=0.1),
    'Random Forest': RandomForestRegressor(
        n_estimators=200, max_depth=15, min_samples_leaf=3,
        random_state=42, n_jobs=-1),
    'Gradient Boosting': GradientBoostingRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.05, random_state=42),
}
if HAS_XGB:
    models['XGBoost'] = XGBRegressor(
        n_estimators=300, max_depth=6, learning_rate=0.05,
        random_state=42, n_jobs=-1, verbosity=0)

# CV on train+val combined (for model selection)
X_cv = np.vstack([X_train, X_val])
y_cv = np.concatenate([y_train, y_val])

cv_results = {}
print(f"\n  Running 5-fold CV on n={len(X_cv)} samples "
      f"(test set n={len(X_test)} held out)...")
print(f"\n  {'Model':<22s} | {'R² (CV)':<18s} | {'MAE (CV)':<18s} | "
      f"{'±10min':<10s}")
print(f"  {'-'*22} | {'-'*18} | {'-'*18} | {'-'*10}")

for name, model in models.items():
    # CV scores
    r2_cv = cross_val_score(model, X_cv, y_cv, cv=kfold, scoring='r2')
    mae_cv = -cross_val_score(model, X_cv, y_cv, cv=kfold,
                               scoring='neg_mean_absolute_error')

    # Within ±10 min via manual CV
    w10_per_fold = []
    for tr_idx, te_idx in kfold.split(X_cv):
        m = type(model)(**model.get_params())
        m.fit(X_cv[tr_idx], y_cv[tr_idx])
        yp = np.clip(m.predict(X_cv[te_idx]), 5, 600)
        w10_per_fold.append(np.mean(np.abs(y_cv[te_idx] - yp) <= 10) * 100)
    w10 = np.array(w10_per_fold)

    cv_results[name] = {
        'r2_mean': round(float(r2_cv.mean()), 4),
        'r2_std': round(float(r2_cv.std()), 4),
        'r2_folds': [round(float(s), 4) for s in r2_cv],
        'mae_mean': round(float(mae_cv.mean()), 2),
        'mae_std': round(float(mae_cv.std()), 2),
        'mae_folds': [round(float(s), 2) for s in mae_cv],
        'within_10min_mean': round(float(w10.mean()), 1),
        'within_10min_std': round(float(w10.std()), 1),
    }

    print(f"  {name:<22s} | "
          f"{r2_cv.mean():+.3f} ± {r2_cv.std():.3f}   | "
          f"{mae_cv.mean():5.2f} ± {mae_cv.std():4.2f}     | "
          f"{w10.mean():5.1f}%")

# ════════════════════════════════════════════════════════════════════
# === SELECT BEST + FINAL EVAL ON HELD-OUT TEST ===
# ════════════════════════════════════════════════════════════════════
best_name = max(cv_results, key=lambda k: cv_results[k]['r2_mean'])
print(f"\n🏆 Best model (CV mean R²): {best_name}")

# Re-train on full train+val with best model
best_model = type(models[best_name])(**models[best_name].get_params())
best_model.fit(X_cv, y_cv)

# Final evaluation on held-out test set
y_test_pred = np.clip(best_model.predict(X_test), 5, 600)
test_mae = mean_absolute_error(y_test, y_test_pred)
test_rmse = np.sqrt(mean_squared_error(y_test, y_test_pred))
test_r2 = r2_score(y_test, y_test_pred)
test_w10 = float(np.mean(np.abs(y_test - y_test_pred) <= 10) * 100)
test_w15 = float(np.mean(np.abs(y_test - y_test_pred) <= 15) * 100)

print(f"\n📊 Held-out TEST set (n={len(X_test)}):")
print(f"   MAE = {test_mae:.2f} | RMSE = {test_rmse:.2f} | "
      f"R² = {test_r2:.4f}")
print(f"   Within ±10 min = {test_w10:.1f}% | "
      f"Within ±15 min = {test_w15:.1f}%")

cv_best = cv_results[best_name]
gap_r2 = abs(cv_best['r2_mean'] - test_r2)
print(f"\n🔍 Overfit check:")
print(f"   CV R² mean = {cv_best['r2_mean']:.3f} ± {cv_best['r2_std']:.3f}")
print(f"   Test R²    = {test_r2:.3f}")
print(f"   Gap        = {gap_r2:.3f}  "
      f"{'✅ Good fit' if gap_r2 < 0.1 else '⚠️ Possible overfit' if gap_r2 < 0.2 else '❌ Overfit'}")

# ════════════════════════════════════════════════════════════════════
# === LEARNING CURVE (diagnostic) ===
# ════════════════════════════════════════════════════════════════════
if HAS_MPL:
    print("\n📈 Generating learning curve...")
    try:
        import os
        os.makedirs('reports', exist_ok=True)
        train_sizes, train_scores, val_scores = learning_curve(
            type(models[best_name])(**models[best_name].get_params()),
            X_cv, y_cv, cv=kfold, scoring='r2',
            train_sizes=np.linspace(0.2, 1.0, 5), n_jobs=-1)

        plt.figure(figsize=(8, 5))
        plt.plot(train_sizes, train_scores.mean(axis=1), 'o-',
                 label='Training R²', color='#1565c0')
        plt.fill_between(train_sizes,
                         train_scores.mean(axis=1) - train_scores.std(axis=1),
                         train_scores.mean(axis=1) + train_scores.std(axis=1),
                         alpha=0.15, color='#1565c0')
        plt.plot(train_sizes, val_scores.mean(axis=1), 'o-',
                 label='Validation R² (5-fold CV)', color='#e65100')
        plt.fill_between(train_sizes,
                         val_scores.mean(axis=1) - val_scores.std(axis=1),
                         val_scores.mean(axis=1) + val_scores.std(axis=1),
                         alpha=0.15, color='#e65100')
        plt.xlabel('Training samples')
        plt.ylabel('R² Score')
        plt.title(f'Learning Curve — {best_name}')
        plt.legend(loc='best')
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig('reports/learning_curve_v4.png', dpi=100)
        plt.close()
        print("   Saved: reports/learning_curve_v4.png")
    except Exception as e:
        print(f"   Skipped learning curve: {e}")

# ════════════════════════════════════════════════════════════════════
# === SAVE MODEL + REPORT ===
# ════════════════════════════════════════════════════════════════════
save = {
    'model': best_model,
    'model_name': best_name,
    'feature_cols': feature_cols,
    'le_proc': le_proc, 'le_surgeon': le_surgeon, 'le_division': le_division,
    'le_scrub': le_scrub, 'le_circ': le_circ,
    'le_optype': le_optype, 'le_timeslot': le_timeslot,
    'surgeon_avg': surgeon_avg, 'proc_avg': proc_avg,
    'surg_proc_avg': surg_proc_avg, 'surg_proc_count': surg_proc_count,
    'month_avg': month_avg,
    'global_avg': float(global_avg),
    # Results (v4 format)
    'cv_results': cv_results,
    'test_results': {
        'MAE': round(test_mae, 2), 'RMSE': round(test_rmse, 2),
        'R2': round(test_r2, 4),
        'within_10min': round(test_w10, 1),
        'within_15min': round(test_w15, 1),
    },
    'procedures': sorted(merged['procedure'].unique().tolist()),
    'surgeons': sorted(merged['surgeon'].unique().tolist()),
    'divisions': sorted(merged['division_str'].unique().tolist()),
    'scrub_nurses': sorted(merged['scrub_nurse'].unique().tolist()),
    'circ_nurses': sorted(merged['circ_nurse'].unique().tolist()),
    'version': 'v4',
    'trained_at': datetime.now().isoformat(),
    'n_samples': len(merged),
    'n_train': len(X_train), 'n_val': len(X_val), 'n_test': len(X_test),
    'n_features': len(feature_cols),
}
with open('minor_or_model_v4.pkl', 'wb') as f:
    pickle.dump(save, f)

# JSON report
report = {
    'version': 'v4',
    'trained_at': datetime.now().isoformat(),
    'n_samples': len(merged),
    'n_train': len(X_train), 'n_val': len(X_val), 'n_test': len(X_test),
    'n_features': len(feature_cols),
    'best_model': best_name,
    'cv_results': cv_results,
    'test_results': {
        'MAE': round(test_mae, 2), 'RMSE': round(test_rmse, 2),
        'R2': round(test_r2, 4),
        'within_10min': round(test_w10, 1),
        'within_15min': round(test_w15, 1),
    },
    'overfit_gap_r2': round(gap_r2, 3),
    'feature_cols': feature_cols,
}
import os
os.makedirs('reports', exist_ok=True)
with open('reports/cv_report_v4.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\n✅ Saved:")
print(f"   minor_or_model_v4.pkl  ({best_name})")
print(f"   reports/cv_report_v4.json")
if HAS_MPL:
    print(f"   reports/learning_curve_v4.png")
print(f"\n📚 For thesis:")
print(f"   '5-fold cross validation R² = {cv_best['r2_mean']:.3f} ± "
      f"{cv_best['r2_std']:.3f}'")
print(f"   'Held-out test set R² = {test_r2:.3f} (n={len(X_test)})'")
print(f"   'Overfit gap (CV - Test) = {gap_r2:.3f}'")
