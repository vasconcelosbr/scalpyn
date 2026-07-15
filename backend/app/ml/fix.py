import re

path = r'C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\ml\trainer.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# The current broken code looks like:
#    return float(np.sum((e_pct - a_pct) * np.log(e_pct / a_pct)))
#
#
#        _leaked_cols = ML_EXCLUDED_FIELDS.intersection(feature_cols)

# We need to insert the class and methods back in.

replacement = """    return float(np.sum((e_pct - a_pct) * np.log(e_pct / a_pct)))


class WinFastTrainer:
    \"\"\"
    XGBoost trainer with Optuna hyperparameter optimization.

    Zero Hardcode: all parameters found by Optuna.
    Threshold is set post-training via ml_models.decision_threshold in Cloud SQL.
    \"\"\"

    def __init__(self, n_trials: int = 50):
        self.n_trials = n_trials
        self.model: Optional[xgb.XGBClassifier] = None

    def train(
        self,
        df: pd.DataFrame,
        optuna_storage_url: Optional[str] = None,
        ml_target: str = "binary",
        win_fast_threshold_s: int = 14400,
    ) -> dict:
        \"\"\"
        Train XGBoost model with Optuna hyperparameter optimization.

        Args:
            df: Training DataFrame from build_training_dataframe()
            optuna_storage_url: PostgreSQL URL for Optuna study persistence
            win_fast_threshold_s: Used as target_window_seconds for embargo

        Returns:
            Dict with: best_params, metrics, run_id, train_from, train_to,
                       n_train, n_val, n_test
        \"\"\"
        feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]

        # ML_EXCLUDED_FIELDS — guardrail no entry-point do treino. Nenhum
        # desses campos pode entrar em X_train/X_val/X_test (leakage circular
        # ou metadado operacional sem valor preditivo).
        _leaked_cols"""

content = re.sub(r'    return float\(np\.sum\(\(e_pct - a_pct\) \* np\.log\(e_pct / a_pct\)\)\)\n\n\n        _leaked_cols', replacement, content)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed.")
