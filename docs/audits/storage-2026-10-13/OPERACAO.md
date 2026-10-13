# Expurgo operacional recuperável

Implementação autorizada em 2026-10-13. A política inicia desativada por padrão; somente operadores provisionados explicitamente podem alterá-la. Nenhum papel de usuário é promovido para administrador.

## Ordem de ativação

- Integrar em main, validar paridade e publicar API, worker-compute e beat.
- Aplicar migration `244_storage_retention` (apenas tabelas novas de controle/arquivo).
- Executar `PYTHONPATH=. python scripts/storage_retention_ops.py indexes` com DATABASE_URL do ambiente correto. Índices parciais são construídos com CONCURRENTLY fora do startup; construção interrompida é retomável. O serviço bloqueia enquanto não estiverem válidos.
- Provisionar o UUID do operador com `provision --owner UUID --authorization 'autorização explícita do usuário'`.
- Gravar pelo serviço de configuração uma política habilitada em dry_run, com `configure --owner UUID --config arquivo.json`. Conferir leitura fresca/invalidar o cache.
- Executar `preview --owner UUID`, conferir planos/contagens, depois salvar mode=execute. O executor exige uma simulação concluída com o mesmo hash de configuração e consultas.
- Executar `run --owner UUID` para o canário e verificar o ledger. Beat consulta a cada minuto; o intervalo efetivo é `cadence_seconds` da configuração no banco.

## Escopo e restauração

Lista fixa: `ohlcv_state_ingestion_observations`, `ohlcv_settlement_latency_samples`, `pump_opportunity_label_queue`. A origem e o arquivo comprimido são gravados atomicamente na mesma transação. Arquivos completos ficam em `storage_retention_batches.archive_gzip`, fora do expurgo. Não existe expurgo genérico por nome recebido do cliente. Nenhum dado de pesquisa entra nesta fase.

O arquivo gzip contém um array JSON exportado pelo PostgreSQL, preservando os literais numéricos. SHA-256 é calculado sobre o JSON descomprimido. A validação de restauração usa `jsonb_populate_recordset` com o tipo real da tabela e compara todas as linhas antes da exclusão. A restauração completa foi também exercitada no PostgreSQL isolado, incluindo alta precisão decimal e rollback de falha no arquivo.

Para recuperar um lote: pausar a política; selecionar o lote pelo UUID; descomprimir e conferir SHA-256; restaurar primeiro em um banco de ensaio com schema compatível usando `INSERT INTO tabela SELECT * FROM jsonb_populate_recordset(NULL::tabela, :json::jsonb)`; conferir contagem/conteúdo e só então restaurar a origem em transação. Não sobrescrever registros que já existam. Não usar CASCADE em produção.

Holds operacionais são intervalos em `storage_retention_holds` por tabela e opcionalmente símbolo. Na fila, um hold protege todo o intervalo, inclusive quando há símbolo preenchido, por segurança. Inserções de holds e exclusões são serializadas por lock. Erros, rejeições, últimas evidências sem chave recente, pendências, bloqueios e filas sem label correspondente são preservados.

## Limites e acompanhamento

O máximo de lotes é global por execução, distribuído entre as tabelas. Tempos de consulta, espera de locks, duração e volume de WAL são limitados. Falhas interrompem a rodada; lotes já confirmados ficam documentados. WAL observado inclui outras sessões, portanto o limite é conservador. Tarefas expiradas não se acumulam; o próximo polling reavalia se a política está vencida.

Arquivos permanecem no mesmo banco nesta fase: a recuperação é local, não um backup externo. A compressão reduz a duplicação, mas DELETE não garante redução de arquivo físico. Não há VACUUM FULL nem ampliação de volume/orçamento. Estatísticas diárias por lote estão no ledger; amostras completas preservam a possibilidade de recalcular saúde e percentis.

Painel autenticado: `/settings/storage`. Mostra a política, estado das execuções, quantidades removidas e bytes dos arquivos, e permite pausar, simular e editar limites. Falhas são visíveis no painel e nos logs `[storage-retention]`; não foi acrescentado envio externo de mensagens nem automação de chat.

As fases de particionamento, arquivamento de datasets de pesquisa e indicadores históricos dependem de validação própria e não são ativadas pelo executor operacional. A retomada do Pump depende de capacidade física efetivamente comprovada.
