
# ML Read-Only Integration Report

The ML capability exposes registry, active models, metrics, feature/label contracts, training window, drift, authority, and experiment evidence. No `train`, `promote`, `activate`, feature-change or label-change tool exists. The staging run persisted `ml_models.get_authority_status` as read-only evidence [query: staging canary]. Social Score remains contextual and was not added to an ML dataset.
