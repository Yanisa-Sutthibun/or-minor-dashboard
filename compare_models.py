"""
Compare ML models v3 (old, single split) vs v4 (K-fold CV + held-out test)
═══════════════════════════════════════════════════════════════════
รัน: python compare_models.py
จะแสดง:
 - ตารางเปรียบเทียบ v3 vs v4
 - Performance gap (CV vs Test) เพื่อดู overfitting
 - คำแนะนำว่าควรเปลี่ยน model ไปใช้ v4 ไหม
"""
import pickle
import json
import os
import sys


def load_model(path):
    """Load .pkl model file. Returns None if not found."""
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return pickle.load(f)


def fmt_metric(value, decimals=3, sign=False):
    """Format float for display."""
    if value is None:
        return '—'
    if sign:
        return f"{value:+.{decimals}f}"
    return f"{value:.{decimals}f}"


def main():
    print("=" * 72)
    print(" Model Comparison: v3 (single split) vs v4 (K-fold CV)")
    print("=" * 72)

    v3 = load_model('minor_or_model.pkl')           # active (v3)
    v4 = load_model('minor_or_model_v4.pkl')        # new

    if v3 is None:
        print("❌ ไม่พบ minor_or_model.pkl (v3)")
        sys.exit(1)
    if v4 is None:
        print("❌ ไม่พบ minor_or_model_v4.pkl")
        print("   รัน: python train_minor_or_v4.py ก่อน")
        sys.exit(1)

    print(f"\n📦 V3 (active):  {v3.get('model_name', '?')} "
          f"(trained {v3.get('trained_at', '?')[:10]})")
    print(f"📦 V4 (new):     {v4.get('model_name', '?')} "
          f"(trained {v4.get('trained_at', '?')[:10]})")

    # ── V3 metrics ──
    v3_results = v3.get('results', {})
    v3_best_name = v3.get('model_name')
    v3_best = v3_results.get(v3_best_name, {})
    v3_r2 = v3_best.get('R2')
    v3_mae = v3_best.get('MAE')
    v3_w10 = v3_best.get('within_10min')

    # ── V4 metrics (CV + Test) ──
    v4_cv = v4.get('cv_results', {})
    v4_best_name = v4.get('model_name')
    v4_best_cv = v4_cv.get(v4_best_name, {})
    v4_cv_r2_mean = v4_best_cv.get('r2_mean')
    v4_cv_r2_std = v4_best_cv.get('r2_std')
    v4_cv_mae_mean = v4_best_cv.get('mae_mean')
    v4_cv_mae_std = v4_best_cv.get('mae_std')
    v4_cv_w10 = v4_best_cv.get('within_10min_mean')

    v4_test = v4.get('test_results', {})
    v4_test_r2 = v4_test.get('R2')
    v4_test_mae = v4_test.get('MAE')
    v4_test_w10 = v4_test.get('within_10min')

    # ── Table ──
    print("\n" + "─" * 72)
    print(f"{'Metric':<20s} | {'V3 (single test)':<22s} | "
          f"{'V4 (CV mean)':<22s} | {'V4 (held-out test)':<20s}")
    print("─" * 72)
    print(f"{'Model':<20s} | {v3_best_name:<22s} | "
          f"{v4_best_name + ' (CV)':<22s} | {v4_best_name + ' (test)':<20s}")
    print(f"{'R² Score':<20s} | "
          f"{fmt_metric(v3_r2, 4, True):<22s} | "
          f"{fmt_metric(v4_cv_r2_mean, 3, True) + ' ± ' + fmt_metric(v4_cv_r2_std, 3):<22s} | "
          f"{fmt_metric(v4_test_r2, 4, True):<20s}")
    print(f"{'MAE (นาที)':<20s} | "
          f"{fmt_metric(v3_mae, 2):<22s} | "
          f"{fmt_metric(v4_cv_mae_mean, 2) + ' ± ' + fmt_metric(v4_cv_mae_std, 2):<22s} | "
          f"{fmt_metric(v4_test_mae, 2):<20s}")
    print(f"{'Within ±10 min':<20s} | "
          f"{fmt_metric(v3_w10, 1) + '%':<22s} | "
          f"{fmt_metric(v4_cv_w10, 1) + '%':<22s} | "
          f"{fmt_metric(v4_test_w10, 1) + '%':<20s}")
    print("─" * 72)

    # ── Sample sizes ──
    print(f"\n📊 Sample sizes:")
    print(f"   V3: train+test = single split (no info)")
    print(f"   V4: train={v4.get('n_train','?')}, "
          f"val={v4.get('n_val','?')}, "
          f"test={v4.get('n_test','?')} (held out for final eval)")
    print(f"       Total samples: {v4.get('n_samples', '?')}")

    # ── Overfit analysis ──
    if v4_cv_r2_mean is not None and v4_test_r2 is not None:
        gap = abs(v4_cv_r2_mean - v4_test_r2)
        print(f"\n🔍 Overfit Analysis (V4):")
        print(f"   CV R² ({v4_cv_r2_mean:.3f}) vs Test R² ({v4_test_r2:.3f})")
        print(f"   Gap = {gap:.3f}")
        if gap < 0.1:
            print(f"   ✅ Good fit — model generalizes well")
        elif gap < 0.2:
            print(f"   ⚠️ Some overfitting — เก็บข้อมูลเพิ่มจะช่วยได้")
        else:
            print(f"   ❌ Significant overfit — ลด complexity หรือเพิ่ม data")

    # ── Recommendation ──
    print(f"\n💡 Recommendation:")
    if v4_test_r2 is not None and v3_r2 is not None:
        if v4_test_r2 > v3_r2 * 0.5:  # rough threshold
            print(f"   🟢 ใช้ V4 ดีกว่า — performance ที่รายงานสมจริง")
        else:
            print(f"   🟡 V4 รายงาน R² ต่ำกว่า V3 แต่ใกล้ความจริงมากกว่า")
            print(f"      (V3 R²={v3_r2:.3f} อาจเป็นค่าฟลุคจาก single test)")

    print(f"\n📝 To switch to V4 (backup V3 first):")
    print(f"   copy minor_or_model.pkl minor_or_model_v3_backup.pkl")
    print(f"   copy minor_or_model_v4.pkl minor_or_model.pkl")
    print(f"   streamlit run minor_or_app.py")

    print(f"\n📚 For thesis (paste into Methods/Results):")
    if v4_cv_r2_mean is not None:
        n_test_disp = v4.get('n_test', '?')
        print(f'   "Five-fold cross validation was used for model selection."')
        print(f'   "Best model: {v4_best_name}"')
        print(f'   "CV R² = {v4_cv_r2_mean:.3f} ± {v4_cv_r2_std:.3f}"')
        print(f'   "Held-out test R² = {v4_test_r2:.3f} (n={n_test_disp})"')
        print(f'   "MAE = {v4_test_mae:.2f} minutes, '
              f'{v4_test_w10:.1f}% within ±10 minutes"')


if __name__ == '__main__':
    main()
