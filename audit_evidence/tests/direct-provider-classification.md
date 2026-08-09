# Direct Provider Scan Classification

- `backend/app/ai_orchestration/provider_adapters/*`: allowed central provider adapters.
- `backend/app/api/ai_keys.py` and `backend/app/services/ai_keys_service.py`: allowed provider catalog/key validation.
- `backend/app/services/preset_ia_service.py`: unresolved legacy domain-service direct Anthropic calls. This violates the post-migration static criterion and blocks production readiness.

No new systemic bridge calls a provider outside `SystemicLangGraphBridge` and the central adapters.
