# T4C — Captura nativa point-in-time

## Estado

Implementação preparada no worktree; commit/deploy bloqueados por sobreposição com alterações preexistentes não commitadas do usuário.

Decisão técnica: `NATIVE_CAPTURE_IMPLEMENTED` no código local; `MODEL_APPROVAL_NOT_YET_POSSIBLE`.

## Arquitetura anterior

`features_captured_at` era copiado de `decision.created_at`. O hash aceitava serialização permissiva e faltavam versões explícitas do extractor e contrato de captura.

## Arquitetura corrigida

- `capture_native_snapshot()` gera `utcnow()` no momento da materialização.
- Hash: JSON canônico com chaves ordenadas, separadores compactos, UTF-8, `allow_nan=False`.
- Versões: `feature-engine-v2`, `entry_features_v2`, `point-in-time-v1`.
- Snapshot, timestamp, hash, versões e lineage entram no mesmo INSERT.
- Strategy Lab deixou de usar `promotion_at` como timestamp de captura.
- Dataset oficial exige `capture_contract_version='point-in-time-v1'`, extractor/schema/hash presentes.
- Migration 133 adiciona versões e trigger de imutabilidade do contrato completo; não foi aplicada.

## Atomicidade e imutabilidade

A captura é construída como objeto imutável antes do INSERT. Falha de hash (NaN/Infinity), lineage ou persistência impede elegibilidade; a transação existente faz rollback. `ON CONFLICT DO NOTHING` preserva idempotência existente.

## Testes

- Suite direcionada: `24` aprovados [test].
- Três testes antigos de L1 falharam antes de alcançar a captura, pois mockam `CeleryAsyncSessionLocal` ausente no módulo [test]. Não foram alterados por serem problema preexistente e fora do escopo.
- Alembic: uma head, `133_native_feature_capture`; histórico íntegro [test].

## Primeiro timestamp confiável

`NÃO DISPONÍVEL`: somente existirá após migration e deploy verificados. Nenhum timestamp local foi apresentado como produção.

## Volume mínimo e previsão

- Taxa real de coleta nativa: `NÃO DISPONÍVEL` antes do deploy.
- `minimum_train_rows`, validation, test e dias: `NÃO DISPONÍVEL`; calcular agora fabricaria números sem prevalência nativa e taxa observada.
- Gate operacional existente de `2.800` linhas não substitui o cálculo estatístico multidimensional T9.

## Bloqueio de Git/deploy

`shadow_trade_service.py`, `shadow_trade.py`, migration 131 e `feature_contract_v2.py` já continham mudanças não commitadas anteriores à T4C. Criar o commit exclusivo incorporaria trabalho do usuário. Nenhum push ou deploy foi feito.

