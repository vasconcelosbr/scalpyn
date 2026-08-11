# Analysis Chat Data Contracts

- `ai_analysis_conversations`: tenant, immutable parent run/result, canonical conversation thread, summary lineage, totals and optimistic lock.
- `ai_analysis_messages`: ordered user/assistant audit records, canonical request/result/run links, provider/model, evidence/tool references, usage/cost and terminal state.
- `ai_analysis_message_evidence`: normalized evidence relation without copying raw evidence.
- `ai_requests`: nullable backward-compatible chat lineage fields.

Retention is `RESTRICT`; conversations are archived/cancelled rather than cascade-deleted. Original datasets, bundles, results and evidence are referenced, never edited by chat. Child execution may not reuse the parent's snapshot or bundle as a new child contract.
