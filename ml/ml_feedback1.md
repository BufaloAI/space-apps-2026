Possible leakage from MODIS composites (check features.py). A MOD13Q1 composite is dated at the start of its 16-day window, so the “NDVI at week t” feature may already contain data from up to t+16 days. Linear gap-filling (max_gap_weeks=2) can also pull in the next observation. Test it: lag the ndvi and NDVI-lag features by one extra composite (about 2 weeks) and retrain. If the test RMSE gain over persistence mostly disappears, you had leakage.
The validation window has no growing season. Validation covers Jan–Jun 2024, but early stopping and the conformal Q are fitted there, then used on summer. Expect under-coverage in summer. Use a full year for validation (e.g. 2023) and test on 2024–25. Also, the “valid” coverage in the metrics is in-sample (about 0.80 by construction), so only the test coverage counts.
Area holdout is too optimistic. Random area folds on a 0.25° grid leave neighbors in training. Hold out whole grid blocks or regions instead.
Local observations replace the MODIS target. Field or drone NDVI isn’t on the same scale as a 250 m pixel, and giving it weight 3 mixes two sensors. Use it for bias calibration only, or add a sensor offset.
Water-stress risk uses the wrong reference. train.py stores per-area medians over all seasons, so 4-week rain is compared with an annual median. service.py already uses same-season medians, so make analyze.py do the same.
Smaller bugs:
climatology() always assumes a 2-week horizon.
Single-crop rotations get a diversity score of 1.0, which the repeated-crop penalty only partly offsets.

-----------

Two things to fix or say honestly

The 80% interval only covers about 76% on test (0.68 before the conformal correction). That’s the summer under-coverage I predicted, since the validation window had no growing season. For now, call it a “~75% interval” in the UI, or recompute the correction on a window that includes summer. Don’t tune it on the test set.
The area holdout is flattering. Fields from the same county are split across folds (for example ia_cedar_01 and ia_cedar_02, or il_champaign_01/02/03), so neighbors leak into training. Hold out whole counties if you want an honest spatial number.

Two checks for leakage (about 15 minutes each)

NDVI lags: the top feature is ndvi_lag5, which is odd. A 16-day composite cycle can cause that, but it can also mean the lags contain future information. Add 2 weeks to every NDVI lag and retrain. If the 30% advantage mostly vanishes, you have leakage.
crop_expected and crop_prev1: both rank high. Confirm they come only from previous years’ CDL, never the current year’s crop map. Knowing the current crop would make the NDVI forecast much easier than it will be for a real farm.