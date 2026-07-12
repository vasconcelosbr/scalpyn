# Metodologia de aprovação de modelos ML

Estados: `DATA_COLLECTION_NOT_STARTED` → `MODEL_APPROVAL_DATA_COLLECTION_STARTED` → `NATIVE_CAPTURE_COLLECTION_IN_PROGRESS` → `CANDIDATE_INSUFFICIENT_PROVEN_DATA` → `CANDIDATE_READY_FOR_VALIDATION` → aprovação humana ou rejeição. Nunca há transição automática para `APPROVED`.

Os mínimos devem ser calculados após observar prevalência, taxa de conclusão de labels, distribuição por lane/profile/source e estabilidade temporal. Train/validation/test devem ser temporalmente separados. Gates futuros devem registrar intervalos de confiança para precision, recall, FPR, ROC-AUC, PR-AUC, EV e erro de calibração, além de estabilidade por tempo e profile. Nenhum valor final é presumido antes dos dados comprovados.

Rejeitar se houver temporalidade não comprovada/inválida, legado, lineage incompleta, hash divergente, uma única classe, cobertura insuficiente ou amostra comprovada abaixo do mínimo calculado.
