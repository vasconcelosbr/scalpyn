# Direct Provider Scan Classification

- `backend/app/ai_orchestration/provider_adapters/*`: allowed central provider adapters.
- `backend/app/api/ai_keys.py` and `backend/app/services/ai_keys_service.py`: allowed provider catalog/key validation.
- `backend/app/services/preset_ia_service.py`: provider calls are routed through `SystemicLangGraphBridge` and the central Anthropic adapter.

No domain service calls a provider outside `SystemicLangGraphBridge` and the central adapters.
