# Runbook T4F — captura nativa

## PRE-DEPLOY
Exigir T4K pronto, commits revisados, testes verdes, migration validada, backup e flags seguras.

## DEPLOY / MIGRATION
Confirmar commit e serviços. Aplicar 133 somente pelo procedimento autorizado; não fazer downgrade automático em produção.

## NATIVE_CAPTURE_START
Registrar UTC real após deploy e migration confirmados em `NATIVE_CAPTURE_START_AT`.

## CANARY
Executar `python -m scripts.audit_native_capture_canary --from <UTC> --limit 50 --dry-run`. Menos de 50 significa coleta em progresso, nunca aprovação.

## GO/NO-GO
GO operacional exige hash n/n, zero lineage incompleta, timestamps futuros, legado oficial e conflitos. Modelo permanece bloqueado.

## ROLLBACK
Reverter aplicação, bloquear dataset oficial e preservar linhas coletadas. Se o contrato falhar, invalidar `point-in-time-v1`, incrementar versão e nunca reescrever registros.
