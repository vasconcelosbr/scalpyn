# Inativação segura de profiles pela UI

## Escopo

Esta entrega cria uma alteração operacional de status separada da edição de regras. Inativar ou reativar um profile não altera seu `config`, `profile_version`, hash, papel, vínculo com watchlist ou histórico.

Nenhum profile de produção é inativado automaticamente por esta publicação.

## Contrato da operação

- Prévia: `POST /api/profiles/{profile_id}/status-preview`
- Aplicação: `PATCH /api/profiles/{profile_id}/status`
- Campos obrigatórios: `is_active`, `reason` e `expected_updated_at`
- Concorrência: a aplicação bloqueia o registro e rejeita uma versão temporal desatualizada.
- Idempotência: repetir o estado já aplicado não cria uma segunda alteração.
- Auditoria: `profile_audit_log` registra estado anterior, estado novo e justificativa, sem criar versão de regras.
- Edição comum: qualquer tentativa de enviar `is_active` pelo salvamento de regras retorna `PROFILE_STATUS_ENDPOINT_REQUIRED`.

## Proteções do funil

- Uma associação nova não aceita profile inativo.
- Uma watchlist só produz ativos quando ela está habilitada, tem profile associado e esse profile está ativo.
- POOL, L1 e L2 associados não podem ser inativados diretamente; a resposta informa as watchlists que exigem substituição ou desligamento governado.
- Ao inativar uma L3, somente os snapshots atuais de oportunidades e rejeições são removidos. A watchlist e todo o histórico permanecem.
- Antes de uma decisão aprovada, um novo Shadow ou uma ordem, o profile L3 é revalidado sob trava de banco.
- Shadows e posições já abertos continuam no monitor existente, usando os snapshots imutáveis capturados na entrada.

## Interface

A tela de edição exibe o selo `ATIVO` ou `INATIVO` e uma ação separada de inativação/reativação. A confirmação exige justificativa e mostra os efeitos e as watchlists associadas. Em profiles upstream protegidos, a confirmação fica bloqueada.

## Rollback

### Rollback do deployment

Restaurar os deployments anteriores de backend/workers e frontend. A migração é aditiva e as colunas novas são anuláveis; mantê-las durante o rollback de aplicação é compatível com o código anterior.

### Rollback de uma alteração operacional

Usar a mesma operação exclusiva de status, informando o estado inverso, uma nova justificativa e o `updated_at` atual. Não editar `is_active` por SQL nem pelo salvamento comum do profile.

## Evidência de aceite

Os identificadores do commit e dos deployments, o estado terminal dos provedores, a confirmação da migração, os testes e a validação autenticada devem ser registrados no ledger de produção após a publicação.
