"""ML module — XGBoost model training, inference, and evaluation."""

# Audit Sprint 4: updated __all__ to reflect production classes.
# Legacy modules (predict_service, dataset_builder, model_loader, train_model)
# are deprecated — see deprecation warnings in each file.
__all__ = [
    "WinFastPredictor",
    "WinFastTrainer",
    "extract_features",
    "build_training_dataframe",
    "get_model",
]
