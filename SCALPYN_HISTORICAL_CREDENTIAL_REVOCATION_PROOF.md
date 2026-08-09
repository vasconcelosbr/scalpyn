# Scalpyn historical credential revocation proof

Verdict: `HISTORICAL_CREDENTIALS_3_OF_3_REVOKED_OR_INVALIDATED`.

Nenhum valor de credencial foi impresso ou preservado neste artefato.

## Inventário e prova

| credential_reference_id | origem histórica | escopo provável | prova autoritativa atual | replacement reference | status |
|---|---|---|---|---|---|
| `hist-jwt-205a64d23` | `.replit` no commit `205a64d23` | assinatura JWT de desenvolvimento | valor Railway atual difere em production API/worker e staging API/worker; valor Vercel difere em production/preview; token assinado com o valor histórico recebeu HTTP `401` em production e staging `[negative HTTP]` | `JWT_SECRET` gerenciado por ambiente, valor não exibido | `REVOKED_OR_INVALIDATED` |
| `hist-encryption-205a64d23` | `.replit` no commit `205a64d23` | Fernet de credenciais de exchange | valor histórico não está no keyring Railway atual; valores Vercel production/preview diferem; key-id histórico ausente no health de production e staging `[Railway + Vercel + HTTP]` | `ENCRYPTION_KEY` atual gerenciado por ambiente, valor não exibido | `REVOKED_OR_INVALIDATED` |
| `hist-debug-205a64d23` | `.replit` no commit `205a64d23` | endpoint write-capable `/debug/run-collect` | variável ausente em production API/worker, staging API/worker e Vercel production/preview; removida do source no commit seguinte `24cd1622` `[Railway + Vercel + git]` | sem replacement; endpoint permanece sem token configurado | `REVOKED_OR_INVALIDATED` |

## Testes negativos e saúde

- JWT histórico: HTTP `401` em production e staging `[negative HTTP]`;
- encryption production: `scanned=1`, `decryptable=1`, `indecryptable=0`, `legacy_rows=0`, `rotation_complete=true` `[HTTP /api/health/encryption]`;
- encryption staging: `scanned=0`, `decryptable=0`, `indecryptable=0`, `legacy_rows=0`; `rotation_complete=false` é consequência literal de não haver linhas para rewrap `[HTTP + source contract]`;
- DEBUG token: teste HTTP deliberadamente não executado, pois sucesso inesperado dispararia um coletor com escrita. A ausência autoritativa em todos os ambientes substitui esse teste `[safety decision]`;
- arquivos temporários gerados pelo `vercel env pull` para comparação em memória foram removidos após cada escopo `[filesystem check: true]`.

## Gate

Todos os três itens terminam `REVOKED_OR_INVALIDATED`. A FASE 1 está fechada e não bloqueia a preparação do provider em staging.

## Ledger de evidências numéricas

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| credenciais fechadas `3/3` | `[Railway/Vercel/HTTP/git]` | três linhas com `REVOKED_OR_INVALIDATED` |
| JWT negativo `401/401` | `[HTTP]` | production `401`; staging `401` |
| production encryption `1/1/0/0` | `[HTTP]` | scanned `1`; decryptable `1`; indecryptable `0`; legacy `0` |
| staging encryption `0/0/0/0` | `[HTTP]` | scanned `0`; decryptable `0`; indecryptable `0`; legacy `0` |
