# RELATORIO_XGB_DUAL_LANE_LABELS_2026-06-26

- generated_at: 2026-06-26T15:33:57.452506+00:00
- commit_hash: ed780a715ef918467dbe1692fbdb7c2041bb4c4f
- verdict: XGB_DUAL_LANE_CHALLENGERS_VALIDATED
- persisted: True

## Pre-flight
```json
{
  "profiles_flags": {
    "live_enabled": 0,
    "autopilot_enabled": 0,
    "total_profiles": 109
  },
  "possible_live_orders": {
    "possible_live_orders": 0
  }
}
```

## Dataset Contracts
```json
{
  "l1": {
    "dataset_contract_id": "XGB_L1_SPECTRUM_V1",
    "model_lane": "XGB_L1_SPECTRUM",
    "train_sources": [
      "L1_SPECTRUM"
    ],
    "source_breakdown": {
      "L1_SPECTRUM": 1978
    },
    "sample_count": 1978,
    "positive_rate": 0.22598584428715873,
    "feature_count": 33,
    "label_name": "l1_mfe_30m_gte_1pct"
  },
  "l3": {
    "dataset_contract_id": "XGB_L3_PROFILE_V1",
    "model_lane": "XGB_L3_PROFILE",
    "train_sources": [
      "L3",
      "L3_LAB"
    ],
    "source_breakdown": {
      "L3": 6381,
      "L3_LAB": 2845
    },
    "profile_breakdown_count": 45,
    "sample_count": 9226,
    "positive_rate": 0.3841318014307392,
    "feature_count": 24,
    "label_name": "l3_profile_ev_positive",
    "excluded_count": 0,
    "exclusion_reasons": {}
  }
}
```

## Leakage Audit
```json
{
  "l1": [
    {
      "feature": "taker_ratio",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9974721941354904
    },
    {
      "feature": "volume_delta",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9974721941354904
    },
    {
      "feature": "rsi",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "macd_histogram_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "macd_histogram_slope",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "adx",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "adx_acceleration",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "spread_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9979777553083923
    },
    {
      "feature": "volume_spike",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9984833164812943
    },
    {
      "feature": "bb_width",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "atr_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "ema9_gt_ema21",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9984833164812943
    },
    {
      "feature": "ema50_gt_ema200",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "volume_24h_usdt",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.564206268958544
    },
    {
      "feature": "orderbook_depth_usdt",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9974721941354904
    },
    {
      "feature": "vwap_distance_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9984833164812943
    },
    {
      "feature": "flow_strength",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9974721941354904
    },
    {
      "feature": "trend_alignment",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9984833164812943
    },
    {
      "feature": "momentum_strength",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "delta_normalized",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.5631951466127402
    },
    {
      "feature": "ema_distance_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9984833164812943
    },
    {
      "feature": "ema50_distance_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "ema200_distance_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9989888776541962
    },
    {
      "feature": "rsi_slope_3",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "rsi_slope_5",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "macd_hist_slope_3",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "macd_hist_slope_5",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "ema21_ema50_distance_pct",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "di_plus_minus_diff",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "adx_slope_3",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "vwap_reclaim_bool",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "higher_highs_5",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "higher_lows_5",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9994944388270981
    },
    {
      "feature": "sp500_change_1h",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "nasdaq_change_1h",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "russell2000_change_1h",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "vix_value",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "vix_change_1h",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "dxy_value",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "dxy_change_1h",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "us10y_yield",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "us10y_change_1h",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "btc_dominance",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "btc_dominance_change",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "crypto_market_cap_change",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "crypto_volume_change",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "fear_greed_index",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "macro_context_available",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    }
  ],
  "l3": [
    {
      "feature": "taker_ratio",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9655321916323434
    },
    {
      "feature": "volume_delta",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9655321916323434
    },
    {
      "feature": "rsi",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "macd_histogram_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "macd_histogram_slope",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "adx",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "adx_acceleration",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "spread_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "volume_spike",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "bb_width",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "atr_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "ema9_gt_ema21",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "ema50_gt_ema200",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "volume_24h_usdt",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "orderbook_depth_usdt",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "vwap_distance_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "flow_strength",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9655321916323434
    },
    {
      "feature": "trend_alignment",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "momentum_strength",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "delta_normalized",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "ema_distance_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "ema50_distance_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "ema200_distance_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "rsi_slope_3",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "rsi_slope_5",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "macd_hist_slope_3",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "macd_hist_slope_5",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "ema21_ema50_distance_pct",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "di_plus_minus_diff",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "adx_slope_3",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "vwap_reclaim_bool",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "higher_highs_5",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "higher_lows_5",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "sp500_change_1h",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "nasdaq_change_1h",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "russell2000_change_1h",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "vix_value",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "vix_change_1h",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "dxy_value",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "dxy_change_1h",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "us10y_yield",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "us10y_change_1h",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6915239540429222
    },
    {
      "feature": "btc_dominance",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.1449165402124431
    },
    {
      "feature": "btc_dominance_change",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "crypto_market_cap_change",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "crypto_volume_change",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "fear_greed_index",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.6880554953392586
    },
    {
      "feature": "macro_context_available",
      "status": "excluded",
      "reason": "low_coverage",
      "coverage": 0.0
    },
    {
      "feature": "profile_id_encoded",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "source_encoded",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "stable_profile_bucket",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "profile_trade_count_prior",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "profile_positive_count_prior",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 1.0
    },
    {
      "feature": "profile_win_rate_prior",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9951224799479731
    },
    {
      "feature": "profile_precision_rolling",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9858009971818773
    },
    {
      "feature": "profile_ev_rolling",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9858009971818773
    },
    {
      "feature": "profile_fpr_rolling",
      "status": "included",
      "reason": "point_in_time",
      "coverage": 0.9858009971818773
    }
  ]
}
```

## Metrics
```json
{
  "l1": {
    "status": "trained",
    "metrics": {
      "validation": {
        "samples": 396,
        "positive_rate": 0.17424242424242425,
        "threshold": 0.95,
        "precision": 0.0,
        "recall": 0.0,
        "fpr": 0.0030581039755351682,
        "roc_auc": 0.5702255905686301,
        "pr_auc": 0.22603566534676245
      },
      "test": {
        "samples": 396,
        "positive_rate": 0.20707070707070707,
        "threshold": 0.95,
        "precision": 0.0,
        "recall": 0.0,
        "fpr": 0.0031847133757961785,
        "roc_auc": 0.6548469784060897,
        "pr_auc": 0.3187124946325255
      },
      "split": {
        "train": {
          "samples": 1186,
          "positive_rate": 0.24957841483979765,
          "min_created_at": "2026-06-11 14:10:14.444697+00:00",
          "max_created_at": "2026-06-15 14:10:01.297657+00:00"
        },
        "validation": {
          "samples": 396,
          "positive_rate": 0.17424242424242425,
          "min_created_at": "2026-06-15 14:10:02.858575+00:00",
          "max_created_at": "2026-06-23 15:56:34.113188+00:00"
        },
        "test": {
          "samples": 396,
          "positive_rate": 0.20707070707070707,
          "min_created_at": "2026-06-23 15:56:34.493992+00:00",
          "max_created_at": "2026-06-25 19:11:15.836040+00:00"
        }
      },
      "threshold_sweep_validation": [
        {
          "threshold": 0.05,
          "approved_count": 163,
          "precision": 0.20245398773006135,
          "recall": 0.4782608695652174,
          "fpr": 0.39755351681957185,
          "tp": 33,
          "fp": 130,
          "tn": 197,
          "fn": 36,
          "ev": -0.6185733724589316,
          "avg_pnl": -0.6185733724589316,
          "lift_vs_baseline": 1.1619098426246999
        },
        {
          "threshold": 0.1,
          "approved_count": 120,
          "precision": 0.21666666666666667,
          "recall": 0.37681159420289856,
          "fpr": 0.2874617737003058,
          "tp": 26,
          "fp": 94,
          "tn": 233,
          "fn": 43,
          "ev": -0.676414950335009,
          "avg_pnl": -0.676414950335009,
          "lift_vs_baseline": 1.2434782608695651
        },
        {
          "threshold": 0.15,
          "approved_count": 90,
          "precision": 0.24444444444444444,
          "recall": 0.3188405797101449,
          "fpr": 0.20795107033639143,
          "tp": 22,
          "fp": 68,
          "tn": 259,
          "fn": 47,
          "ev": -0.6953610357083859,
          "avg_pnl": -0.6953610357083859,
          "lift_vs_baseline": 1.4028985507246376
        },
        {
          "threshold": 0.2,
          "approved_count": 75,
          "precision": 0.25333333333333335,
          "recall": 0.2753623188405797,
          "fpr": 0.1712538226299694,
          "tp": 19,
          "fp": 56,
          "tn": 271,
          "fn": 50,
          "ev": -0.6917020000000047,
          "avg_pnl": -0.6917020000000047,
          "lift_vs_baseline": 1.453913043478261
        },
        {
          "threshold": 0.25,
          "approved_count": 62,
          "precision": 0.25806451612903225,
          "recall": 0.2318840579710145,
          "fpr": 0.14067278287461774,
          "tp": 16,
          "fp": 46,
          "tn": 281,
          "fn": 53,
          "ev": -0.7576443548387147,
          "avg_pnl": -0.7576443548387147,
          "lift_vs_baseline": 1.4810659186535764
        },
        {
          "threshold": 0.3,
          "approved_count": 47,
          "precision": 0.2765957446808511,
          "recall": 0.18840579710144928,
          "fpr": 0.10397553516819572,
          "tp": 13,
          "fp": 34,
          "tn": 293,
          "fn": 56,
          "ev": -0.6977521276595801,
          "avg_pnl": -0.6977521276595801,
          "lift_vs_baseline": 1.587419056429232
        },
        {
          "threshold": 0.35,
          "approved_count": 40,
          "precision": 0.275,
          "recall": 0.15942028985507245,
          "fpr": 0.08868501529051988,
          "tp": 11,
          "fp": 29,
          "tn": 298,
          "fn": 58,
          "ev": -0.816097500000005,
          "avg_pnl": -0.816097500000005,
          "lift_vs_baseline": 1.5782608695652174
        },
        {
          "threshold": 0.4,
          "approved_count": 33,
          "precision": 0.3333333333333333,
          "recall": 0.15942028985507245,
          "fpr": 0.0672782874617737,
          "tp": 11,
          "fp": 22,
          "tn": 305,
          "fn": 58,
          "ev": -0.5968454545454605,
          "avg_pnl": -0.5968454545454605,
          "lift_vs_baseline": 1.9130434782608694
        },
        {
          "threshold": 0.45,
          "approved_count": 32,
          "precision": 0.34375,
          "recall": 0.15942028985507245,
          "fpr": 0.06422018348623854,
          "tp": 11,
          "fp": 21,
          "tn": 306,
          "fn": 58,
          "ev": -0.5154968750000058,
          "avg_pnl": -0.5154968750000058,
          "lift_vs_baseline": 1.9728260869565215
        },
        {
          "threshold": 0.5,
          "approved_count": 27,
          "precision": 0.4074074074074074,
          "recall": 0.15942028985507245,
          "fpr": 0.04892966360856269,
          "tp": 11,
          "fp": 16,
          "tn": 311,
          "fn": 58,
          "ev": -0.4122000000000062,
          "avg_pnl": -0.4122000000000062,
          "lift_vs_baseline": 2.338164251207729
        },
        {
          "threshold": 0.55,
          "approved_count": 24,
          "precision": 0.375,
          "recall": 0.13043478260869565,
          "fpr": 0.045871559633027525,
          "tp": 9,
          "fp": 15,
          "tn": 312,
          "fn": 60,
          "ev": -0.3246375000000071,
          "avg_pnl": -0.3246375000000071,
          "lift_vs_baseline": 2.152173913043478
        },
        {
          "threshold": 0.6,
          "approved_count": 23,
          "precision": 0.391304347826087,
          "recall": 0.13043478260869565,
          "fpr": 0.04281345565749235,
          "tp": 9,
          "fp": 14,
          "tn": 313,
          "fn": 60,
          "ev": -0.3952739130434851,
          "avg_pnl": -0.3952739130434851,
          "lift_vs_baseline": 2.2457466918714557
        },
        {
          "threshold": 0.65,
          "approved_count": 19,
          "precision": 0.3684210526315789,
          "recall": 0.10144927536231885,
          "fpr": 0.03669724770642202,
          "tp": 7,
          "fp": 12,
          "tn": 315,
          "fn": 62,
          "ev": -0.6295447368421114,
          "avg_pnl": -0.6295447368421114,
          "lift_vs_baseline": 2.11441647597254
        },
        {
          "threshold": 0.7,
          "approved_count": 15,
          "precision": 0.4,
          "recall": 0.08695652173913043,
          "fpr": 0.027522935779816515,
          "tp": 6,
          "fp": 9,
          "tn": 318,
          "fn": 63,
          "ev": -0.5518733333333394,
          "avg_pnl": -0.5518733333333394,
          "lift_vs_baseline": 2.2956521739130435
        },
        {
          "threshold": 0.75,
          "approved_count": 9,
          "precision": 0.3333333333333333,
          "recall": 0.043478260869565216,
          "fpr": 0.01834862385321101,
          "tp": 3,
          "fp": 6,
          "tn": 321,
          "fn": 66,
          "ev": -0.48516111111111837,
          "avg_pnl": -0.48516111111111837,
          "lift_vs_baseline": 1.9130434782608694
        },
        {
          "threshold": 0.8,
          "approved_count": 6,
          "precision": 0.3333333333333333,
          "recall": 0.028985507246376812,
          "fpr": 0.012232415902140673,
          "tp": 2,
          "fp": 4,
          "tn": 323,
          "fn": 67,
          "ev": -0.6588000000000083,
          "avg_pnl": -0.6588000000000083,
          "lift_vs_baseline": 1.9130434782608694
        },
        {
          "threshold": 0.85,
          "approved_count": 2,
          "precision": 0.0,
          "recall": 0.0,
          "fpr": 0.0061162079510703364,
          "tp": 0,
          "fp": 2,
          "tn": 325,
          "fn": 69,
          "ev": -0.9500000000000095,
          "avg_pnl": -0.9500000000000095,
          "lift_vs_baseline": 0.0
        },
        {
          "threshold": 0.9,
          "approved_count": 2,
          "precision": 0.0,
          "recall": 0.0,
          "fpr": 0.0061162079510703364,
          "tp": 0,
          "fp": 2,
          "tn": 325,
          "fn": 69,
          "ev": -0.9500000000000095,
          "avg_pnl": -0.9500000000000095,
          "lift_vs_baseline": 0.0
        },
        {
          "threshold": 0.95,
          "approved_count": 1,
          "precision": 0.0,
          "recall": 0.0,
          "fpr": 0.0030581039755351682,
          "tp": 0,
          "fp": 1,
          "tn": 326,
          "fn": 69,
          "ev": 1.2999999999999883,
          "avg_pnl": 1.2999999999999883,
          "lift_vs_baseline": 0.0
        }
      ],
      "threshold_sweep_test": [
        {
          "threshold": 0.05,
          "approved_count": 184,
          "precision": 0.29891304347826086,
          "recall": 0.6707317073170732,
          "fpr": 0.410828025477707,
          "tp": 55,
          "fp": 129,
          "tn": 185,
          "fn": 27,
          "ev": -0.31800081521739415,
          "avg_pnl": -0.31800081521739415,
          "lift_vs_baseline": 1.4435312831389182
        },
        {
          "threshold": 0.1,
          "approved_count": 117,
          "precision": 0.3504273504273504,
          "recall": 0.5,
          "fpr": 0.24203821656050956,
          "tp": 41,
          "fp": 76,
          "tn": 238,
          "fn": 41,
          "ev": -0.2912333333333365,
          "avg_pnl": -0.2912333333333365,
          "lift_vs_baseline": 1.6923076923076923
        },
        {
          "threshold": 0.15,
          "approved_count": 88,
          "precision": 0.375,
          "recall": 0.4024390243902439,
          "fpr": 0.1751592356687898,
          "tp": 33,
          "fp": 55,
          "tn": 259,
          "fn": 49,
          "ev": -0.3838471590909126,
          "avg_pnl": -0.3838471590909126,
          "lift_vs_baseline": 1.8109756097560976
        },
        {
          "threshold": 0.2,
          "approved_count": 77,
          "precision": 0.38961038961038963,
          "recall": 0.36585365853658536,
          "fpr": 0.14968152866242038,
          "tp": 30,
          "fp": 47,
          "tn": 267,
          "fn": 52,
          "ev": -0.43512792207792583,
          "avg_pnl": -0.43512792207792583,
          "lift_vs_baseline": 1.8815331010452963
        },
        {
          "threshold": 0.25,
          "approved_count": 68,
          "precision": 0.39705882352941174,
          "recall": 0.32926829268292684,
          "fpr": 0.1305732484076433,
          "tp": 27,
          "fp": 41,
          "tn": 273,
          "fn": 55,
          "ev": -0.361591176470592,
          "avg_pnl": -0.361591176470592,
          "lift_vs_baseline": 1.9175035868005739
        },
        {
          "threshold": 0.3,
          "approved_count": 55,
          "precision": 0.4,
          "recall": 0.2682926829268293,
          "fpr": 0.10509554140127389,
          "tp": 22,
          "fp": 33,
          "tn": 281,
          "fn": 60,
          "ev": -0.35797818181818564,
          "avg_pnl": -0.35797818181818564,
          "lift_vs_baseline": 1.931707317073171
        },
        {
          "threshold": 0.35,
          "approved_count": 49,
          "precision": 0.3469387755102041,
          "recall": 0.2073170731707317,
          "fpr": 0.10191082802547771,
          "tp": 17,
          "fp": 32,
          "tn": 282,
          "fn": 65,
          "ev": -0.4386632653061259,
          "avg_pnl": -0.4386632653061259,
          "lift_vs_baseline": 1.6754604280736685
        },
        {
          "threshold": 0.4,
          "approved_count": 45,
          "precision": 0.35555555555555557,
          "recall": 0.1951219512195122,
          "fpr": 0.09235668789808917,
          "tp": 16,
          "fp": 29,
          "tn": 285,
          "fn": 66,
          "ev": -0.471931111111114,
          "avg_pnl": -0.471931111111114,
          "lift_vs_baseline": 1.7170731707317073
        },
        {
          "threshold": 0.45,
          "approved_count": 37,
          "precision": 0.3783783783783784,
          "recall": 0.17073170731707318,
          "fpr": 0.0732484076433121,
          "tp": 14,
          "fp": 23,
          "tn": 291,
          "fn": 68,
          "ev": -0.49723243243243526,
          "avg_pnl": -0.49723243243243526,
          "lift_vs_baseline": 1.827290705339486
        },
        {
          "threshold": 0.5,
          "approved_count": 27,
          "precision": 0.2962962962962963,
          "recall": 0.0975609756097561,
          "fpr": 0.06050955414012739,
          "tp": 8,
          "fp": 19,
          "tn": 295,
          "fn": 74,
          "ev": -0.5465000000000022,
          "avg_pnl": -0.5465000000000022,
          "lift_vs_baseline": 1.4308943089430894
        },
        {
          "threshold": 0.55,
          "approved_count": 21,
          "precision": 0.3333333333333333,
          "recall": 0.08536585365853659,
          "fpr": 0.044585987261146494,
          "tp": 7,
          "fp": 14,
          "tn": 300,
          "fn": 75,
          "ev": -0.2693642857142888,
          "avg_pnl": -0.2693642857142888,
          "lift_vs_baseline": 1.6097560975609755
        },
        {
          "threshold": 0.6,
          "approved_count": 17,
          "precision": 0.29411764705882354,
          "recall": 0.06097560975609756,
          "fpr": 0.03821656050955414,
          "tp": 5,
          "fp": 12,
          "tn": 302,
          "fn": 77,
          "ev": -0.24050882352941483,
          "avg_pnl": -0.24050882352941483,
          "lift_vs_baseline": 1.4203730272596844
        },
        {
          "threshold": 0.65,
          "approved_count": 11,
          "precision": 0.45454545454545453,
          "recall": 0.06097560975609756,
          "fpr": 0.01910828025477707,
          "tp": 5,
          "fp": 6,
          "tn": 308,
          "fn": 77,
          "ev": -0.007672727272731192,
          "avg_pnl": -0.007672727272731192,
          "lift_vs_baseline": 2.195121951219512
        },
        {
          "threshold": 0.7,
          "approved_count": 9,
          "precision": 0.3333333333333333,
          "recall": 0.036585365853658534,
          "fpr": 0.01910828025477707,
          "tp": 3,
          "fp": 6,
          "tn": 308,
          "fn": 79,
          "ev": -0.2982666666666705,
          "avg_pnl": -0.2982666666666705,
          "lift_vs_baseline": 1.6097560975609755
        },
        {
          "threshold": 0.75,
          "approved_count": 8,
          "precision": 0.375,
          "recall": 0.036585365853658534,
          "fpr": 0.01592356687898089,
          "tp": 3,
          "fp": 5,
          "tn": 309,
          "fn": 79,
          "ev": -0.23401250000000495,
          "avg_pnl": -0.23401250000000495,
          "lift_vs_baseline": 1.8109756097560976
        },
        {
          "threshold": 0.8,
          "approved_count": 6,
          "precision": 0.5,
          "recall": 0.036585365853658534,
          "fpr": 0.009554140127388535,
          "tp": 3,
          "fp": 3,
          "tn": 311,
          "fn": 79,
          "ev": 0.017149999999993597,
          "avg_pnl": 0.017149999999993597,
          "lift_vs_baseline": 2.4146341463414633
        },
        {
          "threshold": 0.85,
          "approved_count": 6,
          "precision": 0.5,
          "recall": 0.036585365853658534,
          "fpr": 0.009554140127388535,
          "tp": 3,
          "fp": 3,
          "tn": 311,
          "fn": 79,
          "ev": 0.017149999999993597,
          "avg_pnl": 0.017149999999993597,
          "lift_vs_baseline": 2.4146341463414633
        },
        {
          "threshold": 0.9,
          "approved_count": 3,
          "precision": 0.6666666666666666,
          "recall": 0.024390243902439025,
          "fpr": 0.0031847133757961785,
          "tp": 2,
          "fp": 1,
          "tn": 313,
          "fn": 80,
          "ev": 0.40609999999999596,
          "avg_pnl": 0.40609999999999596,
          "lift_vs_baseline": 3.219512195121951
        },
        {
          "threshold": 0.95,
          "approved_count": 1,
          "precision": 0.0,
          "recall": 0.0,
          "fpr": 0.0031847133757961785,
          "tp": 0,
          "fp": 1,
          "tn": 313,
          "fn": 82,
          "ev": -1.381699999999993,
          "avg_pnl": -1.381699999999993,
          "lift_vs_baseline": 0.0
        }
      ],
      "top_buckets_test": {
        "top_1pct": {
          "sample_count": 4,
          "positive_count": 3,
          "precision": 0.75,
          "lift": 3.6219512195121952,
          "ev": 0.6295749999999924,
          "avg_pnl": 0.6295749999999924,
          "symbols_distinct": 4,
          "profiles_distinct": 0
        },
        "top_5pct": {
          "sample_count": 20,
          "positive_count": 7,
          "precision": 0.35,
          "lift": 1.6902439024390243,
          "ev": -0.23443250000000343,
          "avg_pnl": -0.23443250000000343,
          "symbols_distinct": 13,
          "profiles_distinct": 0
        },
        "top_10pct": {
          "sample_count": 40,
          "positive_count": 15,
          "precision": 0.375,
          "lift": 1.8109756097560976,
          "ev": -0.5011600000000025,
          "avg_pnl": -0.5011600000000025,
          "symbols_distinct": 21,
          "profiles_distinct": 0
        },
        "top_20pct": {
          "sample_count": 80,
          "positive_count": 31,
          "precision": 0.3875,
          "lift": 1.8713414634146341,
          "ev": -0.46313375000000373,
          "avg_pnl": -0.46313375000000373,
          "symbols_distinct": 27,
          "profiles_distinct": 0
        }
      },
      "profile_thresholds": [
        {
          "profile_id": "nan",
          "trade_count": 396,
          "positive_count": 82,
          "base_win_rate": 0.20707070707070707,
          "precision_test": 0.6666666666666666,
          "fpr_test": 0.0031847133757961785,
          "ev_test": 0.40609999999999596,
          "threshold_optimal": 0.9,
          "status": "approved_candidate"
        }
      ],
      "hard_negative_patterns_json": [
        {
          "pattern": {
            "_profile_id": "nan",
            "_source": "L1_SPECTRUM",
            "_symbol": "LIT_USDT",
            "rsi_bucket": "(84.873, 84.907]",
            "adx_bucket": "(49.48, 49.5]",
            "atr_pct_bucket": "(0.7876, 0.788]"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "LIT_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        }
      ],
      "probability_distribution": {
        "count": 396,
        "min": 0.00046022646711207926,
        "p01": 0.0008195886039175093,
        "p05": 0.002374181291088462,
        "p50": 0.0410502627491951,
        "p95": 0.5600526332855225,
        "p99": 0.8803219199180603,
        "max": 0.9918404817581177,
        "mean": 0.1241888478398323,
        "std": 0.19095845520496368
      },
      "probability_valid": true,
      "threshold_global": 0.95,
      "promotion_gate_status": "PENDING_EVIDENCE"
    },
    "threshold_global": 0.95
  },
  "l3": {
    "status": "trained",
    "metrics": {
      "validation": {
        "samples": 1845,
        "positive_rate": 0.6818428184281843,
        "threshold": 0.95,
        "precision": 1.0,
        "recall": 0.0047694753577106515,
        "fpr": 0.0,
        "roc_auc": 0.519593849787256,
        "pr_auc": 0.7068698319269514
      },
      "test": {
        "samples": 1846,
        "positive_rate": 0.39653304442036835,
        "threshold": 0.95,
        "precision": 0.0,
        "recall": 0.0,
        "fpr": 0.008976660682226212,
        "roc_auc": 0.5691411346891525,
        "pr_auc": 0.4497512913121786
      },
      "split": {
        "train": {
          "samples": 5535,
          "positive_rate": 0.28075880758807586,
          "min_created_at": "2026-06-17 15:51:55.472462+00:00",
          "max_created_at": "2026-06-24 16:44:10.381206+00:00"
        },
        "validation": {
          "samples": 1845,
          "positive_rate": 0.6818428184281843,
          "min_created_at": "2026-06-24 16:44:10.381206+00:00",
          "max_created_at": "2026-06-25 02:51:35.194746+00:00"
        },
        "test": {
          "samples": 1846,
          "positive_rate": 0.39653304442036835,
          "min_created_at": "2026-06-25 02:51:36.608962+00:00",
          "max_created_at": "2026-06-25 19:31:47.287344+00:00"
        }
      },
      "threshold_sweep_validation": [
        {
          "threshold": 0.05,
          "approved_count": 1136,
          "precision": 0.6901408450704225,
          "recall": 0.6232114467408585,
          "fpr": 0.5996592844974447,
          "tp": 784,
          "fp": 352,
          "tn": 235,
          "fn": 474,
          "ev": 0.3370994439688909,
          "avg_pnl": 0.3370994439688909,
          "lift_vs_baseline": 1.012169999328243
        },
        {
          "threshold": 0.1,
          "approved_count": 821,
          "precision": 0.6967113276492083,
          "recall": 0.4546899841017488,
          "fpr": 0.424190800681431,
          "tp": 572,
          "fp": 249,
          "tn": 338,
          "fn": 686,
          "ev": 0.3473142123613413,
          "avg_pnl": 0.3473142123613413,
          "lift_vs_baseline": 1.021806358913187
        },
        {
          "threshold": 0.15,
          "approved_count": 642,
          "precision": 0.7087227414330218,
          "recall": 0.3616852146263911,
          "fpr": 0.3185689948892675,
          "tp": 455,
          "fp": 187,
          "tn": 400,
          "fn": 803,
          "ev": 0.3652275602460759,
          "avg_pnl": 0.3652275602460759,
          "lift_vs_baseline": 1.0394224625945352
        },
        {
          "threshold": 0.2,
          "approved_count": 527,
          "precision": 0.7077798861480076,
          "recall": 0.29650238473767887,
          "fpr": 0.262350936967632,
          "tp": 373,
          "fp": 154,
          "tn": 433,
          "fn": 885,
          "ev": 0.36124495954076113,
          "avg_pnl": 0.36124495954076113,
          "lift_vs_baseline": 1.038039658142348
        },
        {
          "threshold": 0.25,
          "approved_count": 443,
          "precision": 0.7065462753950339,
          "recall": 0.24880763116057233,
          "fpr": 0.22146507666098808,
          "tp": 313,
          "fp": 130,
          "tn": 457,
          "fn": 945,
          "ev": 0.35728237850560135,
          "avg_pnl": 0.35728237850560135,
          "lift_vs_baseline": 1.0362304277454988
        },
        {
          "threshold": 0.3,
          "approved_count": 382,
          "precision": 0.7146596858638743,
          "recall": 0.21701112877583467,
          "fpr": 0.18568994889267462,
          "tp": 273,
          "fp": 109,
          "tn": 478,
          "fn": 985,
          "ev": 0.36669134470675824,
          "avg_pnl": 0.36669134470675824,
          "lift_vs_baseline": 1.0481296664696726
        },
        {
          "threshold": 0.35,
          "approved_count": 318,
          "precision": 0.710691823899371,
          "recall": 0.17965023847376788,
          "fpr": 0.1567291311754685,
          "tp": 226,
          "fp": 92,
          "tn": 495,
          "fn": 1032,
          "ev": 0.3549562694276161,
          "avg_pnl": 0.3549562694276161,
          "lift_vs_baseline": 1.0423103458619551
        },
        {
          "threshold": 0.4,
          "approved_count": 279,
          "precision": 0.7204301075268817,
          "recall": 0.15977742448330684,
          "fpr": 0.13287904599659284,
          "tp": 201,
          "fp": 78,
          "tn": 509,
          "fn": 1057,
          "ev": 0.37482470852323335,
          "avg_pnl": 0.37482470852323335,
          "lift_vs_baseline": 1.0565926457767065
        },
        {
          "threshold": 0.45,
          "approved_count": 241,
          "precision": 0.7344398340248963,
          "recall": 0.14069952305246422,
          "fpr": 0.10902896081771721,
          "tp": 177,
          "fp": 64,
          "tn": 523,
          "fn": 1081,
          "ev": 0.3911871106970217,
          "avg_pnl": 0.3911871106970217,
          "lift_vs_baseline": 1.0771395022066246
        },
        {
          "threshold": 0.5,
          "approved_count": 198,
          "precision": 0.7424242424242424,
          "recall": 0.11685214626391097,
          "fpr": 0.0868824531516184,
          "tp": 147,
          "fp": 51,
          "tn": 536,
          "fn": 1111,
          "ev": 0.41604087716152727,
          "avg_pnl": 0.41604087716152727,
          "lift_vs_baseline": 1.0888495447318978
        },
        {
          "threshold": 0.55,
          "approved_count": 170,
          "precision": 0.7470588235294118,
          "recall": 0.10095389507154214,
          "fpr": 0.07325383304940375,
          "tp": 127,
          "fp": 43,
          "tn": 544,
          "fn": 1131,
          "ev": 0.41985937457636796,
          "avg_pnl": 0.41985937457636796,
          "lift_vs_baseline": 1.0956466847470308
        },
        {
          "threshold": 0.6,
          "approved_count": 140,
          "precision": 0.75,
          "recall": 0.0834658187599364,
          "fpr": 0.059625212947189095,
          "tp": 105,
          "fp": 35,
          "tn": 552,
          "fn": 1153,
          "ev": 0.4234006691284475,
          "avg_pnl": 0.4234006691284475,
          "lift_vs_baseline": 1.099960254372019
        },
        {
          "threshold": 0.65,
          "approved_count": 112,
          "precision": 0.7142857142857143,
          "recall": 0.06359300476947535,
          "fpr": 0.054514480408858604,
          "tp": 80,
          "fp": 32,
          "tn": 555,
          "fn": 1178,
          "ev": 0.34085797926770384,
          "avg_pnl": 0.34085797926770384,
          "lift_vs_baseline": 1.0475811946400182
        },
        {
          "threshold": 0.7,
          "approved_count": 91,
          "precision": 0.7582417582417582,
          "recall": 0.054848966613672494,
          "fpr": 0.03747870528109029,
          "tp": 69,
          "fp": 22,
          "tn": 565,
          "fn": 1189,
          "ev": 0.42281421624156995,
          "avg_pnl": 0.42281421624156995,
          "lift_vs_baseline": 1.1120477296947884
        },
        {
          "threshold": 0.75,
          "approved_count": 80,
          "precision": 0.7875,
          "recall": 0.050079491255961846,
          "fpr": 0.028960817717206135,
          "tp": 63,
          "fp": 17,
          "tn": 570,
          "fn": 1195,
          "ev": 0.4822011709747865,
          "avg_pnl": 0.4822011709747865,
          "lift_vs_baseline": 1.15495826709062
        },
        {
          "threshold": 0.8,
          "approved_count": 60,
          "precision": 0.8333333333333334,
          "recall": 0.0397456279809221,
          "fpr": 0.017035775127768313,
          "tp": 50,
          "fp": 10,
          "tn": 577,
          "fn": 1208,
          "ev": 0.5826308554378199,
          "avg_pnl": 0.5826308554378199,
          "lift_vs_baseline": 1.2221780604133545
        },
        {
          "threshold": 0.85,
          "approved_count": 38,
          "precision": 0.868421052631579,
          "recall": 0.026232114467408585,
          "fpr": 0.008517887563884156,
          "tp": 33,
          "fp": 5,
          "tn": 582,
          "fn": 1225,
          "ev": 0.6857329296386654,
          "avg_pnl": 0.6857329296386654,
          "lift_vs_baseline": 1.2736381892728643
        },
        {
          "threshold": 0.9,
          "approved_count": 28,
          "precision": 0.8571428571428571,
          "recall": 0.019077901430842606,
          "fpr": 0.0068143100511073255,
          "tp": 24,
          "fp": 4,
          "tn": 583,
          "fn": 1234,
          "ev": 0.6984946902239042,
          "avg_pnl": 0.6984946902239042,
          "lift_vs_baseline": 1.2570974335680218
        },
        {
          "threshold": 0.95,
          "approved_count": 6,
          "precision": 1.0,
          "recall": 0.0047694753577106515,
          "fpr": 0.0,
          "tp": 6,
          "fp": 0,
          "tn": 587,
          "fn": 1252,
          "ev": 0.7833333333333329,
          "avg_pnl": 0.7833333333333329,
          "lift_vs_baseline": 1.4666136724960255
        }
      ],
      "threshold_sweep_test": [
        {
          "threshold": 0.05,
          "approved_count": 959,
          "precision": 0.43274244004171014,
          "recall": 0.5669398907103825,
          "fpr": 0.4883303411131059,
          "tp": 415,
          "fp": 544,
          "tn": 570,
          "fn": 317,
          "ev": -0.20156412930135847,
          "avg_pnl": -0.20156412930135847,
          "lift_vs_baseline": 1.091314951252728
        },
        {
          "threshold": 0.1,
          "approved_count": 608,
          "precision": 0.4654605263157895,
          "recall": 0.3866120218579235,
          "fpr": 0.2917414721723519,
          "tp": 283,
          "fp": 325,
          "tn": 789,
          "fn": 449,
          "ev": -0.12203947368421361,
          "avg_pnl": -0.12203947368421361,
          "lift_vs_baseline": 1.1738253163646823
        },
        {
          "threshold": 0.15,
          "approved_count": 444,
          "precision": 0.4774774774774775,
          "recall": 0.2896174863387978,
          "fpr": 0.20825852782764812,
          "tp": 212,
          "fp": 232,
          "tn": 882,
          "fn": 520,
          "ev": -0.10180180180180473,
          "avg_pnl": -0.10180180180180473,
          "lift_vs_baseline": 1.2041303598680648
        },
        {
          "threshold": 0.2,
          "approved_count": 330,
          "precision": 0.5,
          "recall": 0.22540983606557377,
          "fpr": 0.1481149012567325,
          "tp": 165,
          "fp": 165,
          "tn": 949,
          "fn": 567,
          "ev": -0.045151515151518246,
          "avg_pnl": -0.045151515151518246,
          "lift_vs_baseline": 1.260928961748634
        },
        {
          "threshold": 0.25,
          "approved_count": 261,
          "precision": 0.5287356321839081,
          "recall": 0.1885245901639344,
          "fpr": 0.11041292639138241,
          "tp": 138,
          "fp": 123,
          "tn": 991,
          "fn": 594,
          "ev": 0.020689655172410792,
          "avg_pnl": 0.020689655172410792,
          "lift_vs_baseline": 1.3333961434583255
        },
        {
          "threshold": 0.3,
          "approved_count": 219,
          "precision": 0.5342465753424658,
          "recall": 0.1598360655737705,
          "fpr": 0.09156193895870736,
          "tp": 117,
          "fp": 102,
          "tn": 1012,
          "fn": 615,
          "ev": 0.033333333333330176,
          "avg_pnl": 0.033333333333330176,
          "lift_vs_baseline": 1.3472939591286774
        },
        {
          "threshold": 0.35,
          "approved_count": 175,
          "precision": 0.5371428571428571,
          "recall": 0.1284153005464481,
          "fpr": 0.07271095152603231,
          "tp": 94,
          "fp": 81,
          "tn": 1033,
          "fn": 638,
          "ev": 0.03371428571428278,
          "avg_pnl": 0.03371428571428278,
          "lift_vs_baseline": 1.3545979703356752
        },
        {
          "threshold": 0.4,
          "approved_count": 144,
          "precision": 0.5555555555555556,
          "recall": 0.1092896174863388,
          "fpr": 0.05745062836624776,
          "tp": 80,
          "fp": 64,
          "tn": 1050,
          "fn": 652,
          "ev": 0.07152777777777485,
          "avg_pnl": 0.07152777777777485,
          "lift_vs_baseline": 1.4010321797207044
        },
        {
          "threshold": 0.45,
          "approved_count": 115,
          "precision": 0.5391304347826087,
          "recall": 0.08469945355191257,
          "fpr": 0.04757630161579892,
          "tp": 62,
          "fp": 53,
          "tn": 1061,
          "fn": 670,
          "ev": 0.028695652173910697,
          "avg_pnl": 0.028695652173910697,
          "lift_vs_baseline": 1.3596103587550488
        },
        {
          "threshold": 0.5,
          "approved_count": 91,
          "precision": 0.5164835164835165,
          "recall": 0.06420765027322405,
          "fpr": 0.03949730700179533,
          "tp": 47,
          "fp": 44,
          "tn": 1070,
          "fn": 685,
          "ev": -0.025274725274727933,
          "avg_pnl": -0.025274725274727933,
          "lift_vs_baseline": 1.302498048399688
        },
        {
          "threshold": 0.55,
          "approved_count": 67,
          "precision": 0.47761194029850745,
          "recall": 0.04371584699453552,
          "fpr": 0.03141831238779174,
          "tp": 32,
          "fp": 35,
          "tn": 1079,
          "fn": 700,
          "ev": -0.11343283582089828,
          "avg_pnl": -0.11343283582089828,
          "lift_vs_baseline": 1.204469455998695
        },
        {
          "threshold": 0.6,
          "approved_count": 54,
          "precision": 0.46296296296296297,
          "recall": 0.03415300546448088,
          "fpr": 0.026032315978456014,
          "tp": 25,
          "fp": 29,
          "tn": 1085,
          "fn": 707,
          "ev": -0.14629629629629903,
          "avg_pnl": -0.14629629629629903,
          "lift_vs_baseline": 1.1675268164339203
        },
        {
          "threshold": 0.65,
          "approved_count": 44,
          "precision": 0.38636363636363635,
          "recall": 0.023224043715846996,
          "fpr": 0.02423698384201077,
          "tp": 17,
          "fp": 27,
          "tn": 1087,
          "fn": 715,
          "ev": -0.30000000000000254,
          "avg_pnl": -0.30000000000000254,
          "lift_vs_baseline": 0.9743541977148534
        },
        {
          "threshold": 0.7,
          "approved_count": 34,
          "precision": 0.2647058823529412,
          "recall": 0.012295081967213115,
          "fpr": 0.02244165170556553,
          "tp": 9,
          "fp": 25,
          "tn": 1089,
          "fn": 723,
          "ev": -0.5852941176470603,
          "avg_pnl": -0.5852941176470603,
          "lift_vs_baseline": 0.6675506268081003
        },
        {
          "threshold": 0.75,
          "approved_count": 31,
          "precision": 0.25806451612903225,
          "recall": 0.01092896174863388,
          "fpr": 0.02064631956912029,
          "tp": 8,
          "fp": 23,
          "tn": 1091,
          "fn": 724,
          "ev": -0.6193548387096787,
          "avg_pnl": -0.6193548387096787,
          "lift_vs_baseline": 0.6508020447734885
        },
        {
          "threshold": 0.8,
          "approved_count": 23,
          "precision": 0.21739130434782608,
          "recall": 0.006830601092896175,
          "fpr": 0.01615798922800718,
          "tp": 5,
          "fp": 18,
          "tn": 1096,
          "fn": 727,
          "ev": -0.7391304347826098,
          "avg_pnl": -0.7391304347826098,
          "lift_vs_baseline": 0.5482299833689712
        },
        {
          "threshold": 0.85,
          "approved_count": 20,
          "precision": 0.2,
          "recall": 0.00546448087431694,
          "fpr": 0.01436265709156194,
          "tp": 4,
          "fp": 16,
          "tn": 1098,
          "fn": 728,
          "ev": -0.7700000000000007,
          "avg_pnl": -0.7700000000000007,
          "lift_vs_baseline": 0.5043715846994536
        },
        {
          "threshold": 0.9,
          "approved_count": 15,
          "precision": 0.13333333333333333,
          "recall": 0.00273224043715847,
          "fpr": 0.011669658886894075,
          "tp": 2,
          "fp": 13,
          "tn": 1101,
          "fn": 730,
          "ev": -0.8933333333333332,
          "avg_pnl": -0.8933333333333332,
          "lift_vs_baseline": 0.336247723132969
        },
        {
          "threshold": 0.95,
          "approved_count": 10,
          "precision": 0.0,
          "recall": 0.0,
          "fpr": 0.008976660682226212,
          "tp": 0,
          "fp": 10,
          "tn": 1104,
          "fn": 732,
          "ev": -1.139999999999999,
          "avg_pnl": -1.139999999999999,
          "lift_vs_baseline": 0.0
        }
      ],
      "top_buckets_test": {
        "top_1pct": {
          "sample_count": 19,
          "positive_count": 3,
          "precision": 0.15789473684210525,
          "lift": 0.3981880931837791,
          "ev": -0.8526315789473687,
          "avg_pnl": -0.8526315789473687,
          "symbols_distinct": 8,
          "profiles_distinct": 11
        },
        "top_5pct": {
          "sample_count": 93,
          "positive_count": 49,
          "precision": 0.5268817204301075,
          "lift": 1.3287208414125389,
          "ev": -0.0021505376344112628,
          "avg_pnl": -0.0021505376344112628,
          "symbols_distinct": 26,
          "profiles_distinct": 25
        },
        "top_10pct": {
          "sample_count": 185,
          "positive_count": 101,
          "precision": 0.5459459459459459,
          "lift": 1.3767981095849948,
          "ev": 0.0497297297297267,
          "avg_pnl": 0.0497297297297267,
          "symbols_distinct": 28,
          "profiles_distinct": 27
        },
        "top_20pct": {
          "sample_count": 370,
          "positive_count": 182,
          "precision": 0.4918918918918919,
          "lift": 1.2404814650716292,
          "ev": -0.0643243243243274,
          "avg_pnl": -0.0643243243243274,
          "symbols_distinct": 29,
          "profiles_distinct": 29
        }
      },
      "profile_thresholds": [
        {
          "profile_id": "04b22254-e7e5-4c46-a5ab-36d0701b3a7a",
          "trade_count": 25,
          "positive_count": 6,
          "base_win_rate": 0.24,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 1.2999999999999858,
          "threshold_optimal": 0.2,
          "status": "approved_candidate"
        },
        {
          "profile_id": "0b05b6b8-98dc-4927-80d5-49ea5d64912c",
          "trade_count": 27,
          "positive_count": 14,
          "base_win_rate": 0.5185185185185185,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 1.299999999999997,
          "threshold_optimal": 0.25,
          "status": "approved_candidate"
        },
        {
          "profile_id": "0ceb5b72-df87-4bcb-8382-32055e29b74b",
          "trade_count": 14,
          "positive_count": 7,
          "base_win_rate": 0.5,
          "precision_test": 0.5454545454545454,
          "fpr_test": 0.7142857142857143,
          "ev_test": 0.16363636363635833,
          "threshold_optimal": 0.05,
          "status": "approved_candidate"
        },
        {
          "profile_id": "10d6d5ae-9fdf-41fc-a99f-d9be9a138bd7",
          "trade_count": 29,
          "positive_count": 15,
          "base_win_rate": 0.5172413793103449,
          "precision_test": 0.5555555555555556,
          "fpr_test": 0.5714285714285714,
          "ev_test": 0.14999999999999783,
          "threshold_optimal": 0.05,
          "status": "approved_candidate"
        },
        {
          "profile_id": "1fe39235-ddaa-41e9-b1ec-da83c1fae3f5",
          "trade_count": 18,
          "positive_count": 9,
          "base_win_rate": 0.5,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 1.2999999999999878,
          "threshold_optimal": 0.1,
          "status": "approved_candidate"
        },
        {
          "profile_id": "20756610-707a-4b88-b5b2-0f287274960f",
          "trade_count": 82,
          "positive_count": 29,
          "base_win_rate": 0.35365853658536583,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 1.299999999999993,
          "threshold_optimal": 0.65,
          "status": "approved_candidate"
        },
        {
          "profile_id": "223551a4-df08-4df4-a848-ba0abed156f0",
          "trade_count": 3,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.0,
          "ev_test": null,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "2271d844-69c7-4ee4-82e6-638129152571",
          "trade_count": 1,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.0,
          "ev_test": null,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "2b548651-dc19-4556-8510-d901a3924b55",
          "trade_count": 32,
          "positive_count": 7,
          "base_win_rate": 0.21875,
          "precision_test": 0.5,
          "fpr_test": 0.04,
          "ev_test": 0.0499999999999996,
          "threshold_optimal": 0.45,
          "status": "approved_candidate"
        },
        {
          "profile_id": "2b70dc42-1edd-4603-bc54-0403cd1e2f54",
          "trade_count": 327,
          "positive_count": 154,
          "base_win_rate": 0.4709480122324159,
          "precision_test": 0.5964912280701754,
          "fpr_test": 0.1329479768786127,
          "ev_test": 0.09298245614035,
          "threshold_optimal": 0.1,
          "status": "approved_candidate"
        },
        {
          "profile_id": "338d8207-7bdf-4c05-9b66-693205b7da71",
          "trade_count": 13,
          "positive_count": 6,
          "base_win_rate": 0.46153846153846156,
          "precision_test": 0.5,
          "fpr_test": 0.2857142857142857,
          "ev_test": 0.04999999999999227,
          "threshold_optimal": 0.15,
          "status": "approved_candidate"
        },
        {
          "profile_id": "33ed9391-ada9-4dc4-8bc7-f10b0dbcd05a",
          "trade_count": 64,
          "positive_count": 22,
          "base_win_rate": 0.34375,
          "precision_test": 0.8,
          "fpr_test": 0.023809523809523808,
          "ev_test": 0.7999999999999913,
          "threshold_optimal": 0.4,
          "status": "approved_candidate"
        },
        {
          "profile_id": "40a6ba34-22de-4b00-9d71-d914f88d6367",
          "trade_count": 18,
          "positive_count": 9,
          "base_win_rate": 0.5,
          "precision_test": 0.7142857142857143,
          "fpr_test": 0.2222222222222222,
          "ev_test": 0.5857142857142765,
          "threshold_optimal": 0.1,
          "status": "approved_candidate"
        },
        {
          "profile_id": "44d2a3bf-5a5f-49fb-99e4-7df945b8f333",
          "trade_count": 1,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 1.0,
          "ev_test": -1.2000000000000068,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "4e2bf257-f065-43a5-a806-ffd413560061",
          "trade_count": 25,
          "positive_count": 6,
          "base_win_rate": 0.24,
          "precision_test": 0.6,
          "fpr_test": 0.10526315789473684,
          "ev_test": 0.15999999999999287,
          "threshold_optimal": 0.25,
          "status": "approved_candidate"
        },
        {
          "profile_id": "561db244-b0eb-4cac-b1f7-fe29213a0e75",
          "trade_count": 59,
          "positive_count": 20,
          "base_win_rate": 0.3389830508474576,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 0.9499999999999982,
          "threshold_optimal": 0.55,
          "status": "approved_candidate"
        },
        {
          "profile_id": "5bdbefc4-4500-4eaa-8f1a-b9be1973b7e7",
          "trade_count": 185,
          "positive_count": 65,
          "base_win_rate": 0.35135135135135137,
          "precision_test": 0.6,
          "fpr_test": 0.016666666666666666,
          "ev_test": 0.13999999999999868,
          "threshold_optimal": 0.4,
          "status": "approved_candidate"
        },
        {
          "profile_id": "5da37177-7f0f-4f0b-b3e0-ff651025be37",
          "trade_count": 84,
          "positive_count": 34,
          "base_win_rate": 0.40476190476190477,
          "precision_test": 0.7142857142857143,
          "fpr_test": 0.08,
          "ev_test": 0.48571428571428055,
          "threshold_optimal": 0.3,
          "status": "approved_candidate"
        },
        {
          "profile_id": "67fb437f-ace5-444d-9be3-7ceaef3095bf",
          "trade_count": 15,
          "positive_count": 8,
          "base_win_rate": 0.5333333333333333,
          "precision_test": 0.5,
          "fpr_test": 0.42857142857142855,
          "ev_test": 0.04999999999999061,
          "threshold_optimal": 0.15,
          "status": "approved_candidate"
        },
        {
          "profile_id": "70d49616-a429-4daa-815d-3d1b42a36609",
          "trade_count": 12,
          "positive_count": 6,
          "base_win_rate": 0.5,
          "precision_test": 0.6,
          "fpr_test": 0.3333333333333333,
          "ev_test": 0.15999999999999295,
          "threshold_optimal": 0.05,
          "status": "approved_candidate"
        },
        {
          "profile_id": "7b560f2a-3aa6-492b-80ee-04ad8b60c39b",
          "trade_count": 65,
          "positive_count": 25,
          "base_win_rate": 0.38461538461538464,
          "precision_test": 0.8,
          "fpr_test": 0.025,
          "ev_test": 0.7999999999999893,
          "threshold_optimal": 0.25,
          "status": "approved_candidate"
        },
        {
          "profile_id": "7bdf45d3-5891-496e-96c5-cee9ea54935a",
          "trade_count": 21,
          "positive_count": 11,
          "base_win_rate": 0.5238095238095238,
          "precision_test": 0.6363636363636364,
          "fpr_test": 0.4,
          "ev_test": 0.3909090909090835,
          "threshold_optimal": 0.1,
          "status": "approved_candidate"
        },
        {
          "profile_id": "7e2a14d7-20ec-4a64-b7e6-ebaf39ac6578",
          "trade_count": 75,
          "positive_count": 21,
          "base_win_rate": 0.28,
          "precision_test": 0.5,
          "fpr_test": 0.037037037037037035,
          "ev_test": -0.09999999999999967,
          "threshold_optimal": 0.5,
          "status": "approved_candidate"
        },
        {
          "profile_id": "7fc17129-dc8f-4341-8b44-8a2e08dddd3c",
          "trade_count": 5,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.4,
          "ev_test": -1.2000000000000042,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "88b33f9b-050d-44a3-9c3d-b14172210c9f",
          "trade_count": 11,
          "positive_count": 4,
          "base_win_rate": 0.36363636363636365,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 0.8000000000000043,
          "threshold_optimal": 0.4,
          "status": "approved_candidate"
        },
        {
          "profile_id": "9549039c-9619-44f8-96d0-0a655c77d930",
          "trade_count": 4,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.25,
          "ev_test": -1.2000000000000008,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "985366f5-a962-4c42-b0e7-241221cba1cd",
          "trade_count": 15,
          "positive_count": 2,
          "base_win_rate": 0.13333333333333333,
          "precision_test": 0.5,
          "fpr_test": 0.15384615384615385,
          "ev_test": 0.04999999999999738,
          "threshold_optimal": 0.15,
          "status": "shadow_only"
        },
        {
          "profile_id": "9bf292a1-ec81-4c94-b501-499644caefea",
          "trade_count": 30,
          "positive_count": 4,
          "base_win_rate": 0.13333333333333333,
          "precision_test": 0.5,
          "fpr_test": 0.038461538461538464,
          "ev_test": -0.1000000000000007,
          "threshold_optimal": 0.55,
          "status": "approved_candidate"
        },
        {
          "profile_id": "a40cdbfe-b361-4953-91f1-2d4cc93ab424",
          "trade_count": 44,
          "positive_count": 12,
          "base_win_rate": 0.2727272727272727,
          "precision_test": 0.6666666666666666,
          "fpr_test": 0.0625,
          "ev_test": 0.3499999999999946,
          "threshold_optimal": 0.4,
          "status": "approved_candidate"
        },
        {
          "profile_id": "a565150d-74da-4308-914e-d586a37cdf99",
          "trade_count": 317,
          "positive_count": 146,
          "base_win_rate": 0.4605678233438486,
          "precision_test": 0.7142857142857143,
          "fpr_test": 0.023391812865497075,
          "ev_test": 0.24285714285714313,
          "threshold_optimal": 0.35,
          "status": "approved_candidate"
        },
        {
          "profile_id": "ae7f01b6-72ed-4db6-b424-4a408ea26102",
          "trade_count": 1,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.0,
          "ev_test": null,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "c0edd5af-6607-4956-ba0c-29d74f482023",
          "trade_count": 4,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.0,
          "ev_test": null,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "c9f65850-5117-4e48-a4c3-9d86da8bc752",
          "trade_count": 18,
          "positive_count": 8,
          "base_win_rate": 0.4444444444444444,
          "precision_test": 1.0,
          "fpr_test": 0.0,
          "ev_test": 1.2999999999999912,
          "threshold_optimal": 0.3,
          "status": "approved_candidate"
        },
        {
          "profile_id": "d0590123-5433-4b57-b29e-49b1be2113bc",
          "trade_count": 2,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.5,
          "ev_test": -1.1999999999999975,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "d1d73052-699e-44bd-8b1a-676d4d6db327",
          "trade_count": 35,
          "positive_count": 20,
          "base_win_rate": 0.5714285714285714,
          "precision_test": 0.6,
          "fpr_test": 0.13333333333333333,
          "ev_test": 0.29999999999999033,
          "threshold_optimal": 0.5,
          "status": "approved_candidate"
        },
        {
          "profile_id": "e44f3ad2-c536-48f1-85aa-62c63686ee27",
          "trade_count": 136,
          "positive_count": 61,
          "base_win_rate": 0.4485294117647059,
          "precision_test": 0.6428571428571429,
          "fpr_test": 0.06666666666666667,
          "ev_test": 0.19999999999999835,
          "threshold_optimal": 0.6,
          "status": "approved_candidate"
        },
        {
          "profile_id": "eb4958e6-e338-4652-9894-b153913ee206",
          "trade_count": 10,
          "positive_count": 1,
          "base_win_rate": 0.1,
          "precision_test": 0.25,
          "fpr_test": 0.3333333333333333,
          "ev_test": -0.550000000000006,
          "threshold_optimal": 0.1,
          "status": "shadow_only"
        },
        {
          "profile_id": "eeb7504d-0ad2-4403-9611-486d56b1e6de",
          "trade_count": 8,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.25,
          "ev_test": -1.1999999999999988,
          "threshold_optimal": 0.05,
          "status": "cold_start"
        },
        {
          "profile_id": "f86f47ae-f3a7-43ac-a6c8-6d9a89a67f64",
          "trade_count": 11,
          "positive_count": 0,
          "base_win_rate": 0.0,
          "precision_test": 0.0,
          "fpr_test": 0.2727272727272727,
          "ev_test": -1.2000000000000028,
          "threshold_optimal": 0.05,
          "status": "shadow_only"
        }
      ],
      "hard_negative_patterns_json": [
        {
          "pattern": {
            "_profile_id": "0ceb5b72-df87-4bcb-8382-32055e29b74b",
            "_source": "L3",
            "_symbol": "M_USDT",
            "rsi_bucket": "(21.707, 24.268]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "M_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "2b548651-dc19-4556-8510-d901a3924b55",
            "_source": "L3",
            "_symbol": "XLM_USDT",
            "rsi_bucket": "(29.364, 31.912]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "XLM_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "2b70dc42-1edd-4603-bc54-0403cd1e2f54",
            "_source": "L3_LAB",
            "_symbol": "M_USDT",
            "rsi_bucket": "(21.707, 24.268]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "M_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "5da37177-7f0f-4f0b-b3e0-ff651025be37",
            "_source": "L3",
            "_symbol": "BNB_USDT",
            "rsi_bucket": "(31.912, 34.46]",
            "adx_bucket": "(61.267, 61.898]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "BNB_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "7bdf45d3-5891-496e-96c5-cee9ea54935a",
            "_source": "L3",
            "_symbol": "PEPE_USDT",
            "rsi_bucket": "(21.707, 24.268]",
            "adx_bucket": "(63.782, 64.41]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "PEPE_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "985366f5-a962-4c42-b0e7-241221cba1cd",
            "_source": "L3",
            "_symbol": "XLM_USDT",
            "rsi_bucket": "(29.364, 31.912]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "XLM_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "a565150d-74da-4308-914e-d586a37cdf99",
            "_source": "L3_LAB",
            "_symbol": "M_USDT",
            "rsi_bucket": "(21.707, 24.268]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "M_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "e44f3ad2-c536-48f1-85aa-62c63686ee27",
            "_source": "L3",
            "_symbol": "M_USDT",
            "rsi_bucket": "(21.707, 24.268]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "M_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "e44f3ad2-c536-48f1-85aa-62c63686ee27",
            "_source": "L3",
            "_symbol": "PEPE_USDT",
            "rsi_bucket": "(29.364, 31.912]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "PEPE_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        },
        {
          "pattern": {
            "_profile_id": "e44f3ad2-c536-48f1-85aa-62c63686ee27",
            "_source": "L3_LAB",
            "_symbol": "M_USDT",
            "rsi_bucket": "(21.707, 24.268]",
            "adx_bucket": "(61.898, 62.526]",
            "atr_pct_bucket": "nan"
          },
          "fp_count": 1,
          "total_count": 1,
          "fp_rate": 1.0,
          "example_symbols": [
            "M_USDT"
          ],
          "suggested_feature_or_penalty": "candidate_only_validate_next_cycle",
          "do_not_apply_as_hard_veto_without_validation": true
        }
      ],
      "probability_distribution": {
        "count": 1846,
        "min": 0.0002457717200741172,
        "p01": 0.001731928321532905,
        "p05": 0.004617968574166298,
        "p50": 0.05355279892683029,
        "p95": 0.49736717343330383,
        "p99": 0.8639972805976868,
        "max": 0.9899869561195374,
        "mean": 0.12102487683296204,
        "std": 0.16922186315059662
      },
      "probability_valid": true,
      "threshold_global": 0.95,
      "promotion_gate_status": "PENDING_EVIDENCE"
    },
    "threshold_global": 0.95
  }
}
```

## Ledger
| Affirmacao | Origem | Valor literal |
|---|---|---|
| live_enabled | preflight SQL | 0 |
| autopilot_enabled | preflight SQL | 0 |
| possible_live_orders | preflight SQL | 0 |
| L1 contract | script constant | XGB_L1_SPECTRUM_V1 |
| L3 contract | script constant | XGB_L3_PROFILE_V1 |
