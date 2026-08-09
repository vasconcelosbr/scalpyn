
# Social Score Integration Report

Social Score is registered tenant-safe and read-only. Its snapshot contract preserves window, sources, mentions, sentiment, trend, confidence, coverage, freshness, missingness, anomaly flags, contract version and identity/hash. Untrusted text passes through the sanitizer. Missing data produces `NO_DATA`, never a fabricated zero. The staging systemic run persisted `social_score.get_snapshot` tool evidence [query: staging canary]. No ML dataset, score, trade or live setting was changed.
