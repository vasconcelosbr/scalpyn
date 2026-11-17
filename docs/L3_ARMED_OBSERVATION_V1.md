# Consolidado: Watchlist, Aprovados e contrato externo de observação

## Endereços preservados

```http
GET https://api.scalpyn.com/api/watchlists/l3-consolidated/assets
Authorization: Bearer <token>
```

O mesmo caminho continua disponível diretamente em
`https://scalpyn-api-production.up.railway.app/api/watchlists/l3-consolidated/assets`.
Não é necessário parâmetro novo. Autenticação e isolamento por usuário permanecem.

## Mudança de significado no mesmo endereço

O endpoint retorna exclusivamente avaliações MTF **ARMED** atuais para o perfil
de referência dos ativos aprovados no Edit Profile. Não exige admissão Shadow,
não usa seu limite e não restringe a lista por score adicional. Ausência de
Armados retorna `items: []` e `total: 0`. GET não coleta nem altera dados.

O envelope preserva `id`, `name`, `level`, `virtual`, `read_only`, `items`, `total`
e `as_of`. A semântica passa a `MTF_ARMED_PROFILE_REFERENCE`, com
`observation_contract: L3_ARMED_OBSERVATION_V1`. Envelope e itens explicitam
`executable: false` e `execution_authority: false`.

Cada item mantém identidade do ativo e do perfil, contribuição, preço e score
do perfil; `profile_status` é `approved` e `status` é `ARMED`. O objeto `mtf`
identifica a observação, perfil, estado, horário observado, fechamento do candle,
`valid_until`, qualidade e motivos de indisponibilidade. Score/preço do perfil
não são apresentados como score/preço de execução da avaliação MTF.

**Incompatibilidade intencional:** o retorno não possui a declaração antiga de
autorização executável nem emite `authorization_id`, `authorization_status`,
`expires_at` ou `shadow_id`. Consumidores devem usar esse endpoint apenas para
observação. Não basta a presença de um ativo, nem `status: ARMED`, para ordenar.
`mtf.valid_until` é validade da leitura, nunca validade de autorização de compra.
Os bloqueios de ordens permanecem ativos. Código externo não é alterado por
esta publicação; consumidores externos devem reconhecer o novo contrato e
recusar execução. Não ativar integrações de ordens para testar a consulta.

## Fonte e atualidade

A projeção lê em lote a captura mais recente do histórico completo do
`l3-consolidated`, por usuário e ativo, antes de filtrar o estado. Ela verifica
perfil de referência, hashes atuais do perfil, configuração MTF e indicadores,
versão do motor, qualidade e horários. A validade reutiliza o multiplicador de
obsolescência configurado no MTF e a duração do candle. Não se busca um Armado
mais antigo quando a captura mais recente mudou de estado ou de perfil.

Avaliação ausente, vencida, com dados pendentes ou configuração incompatível
não entra em Aprovados nem no endpoint. O ativo continua na Watchlist.
Capturas antigas e resultados futuros do histórico não são reescritos.

## Interface e compatibilidade interna

- Watchlist: lista completa anteriormente exibida em Aprovados; aba inicial.
- Aprovados: subconjunto Armado, com o mesmo seletor do endpoint externo.
- Aguardando dados: conteúdo e classificação anteriores, preservados.

`/api/watchlists/l3-consolidated/profile-status` preserva `items`, `total`,
`awaiting_data_items` e `awaiting_data_count`; acrescenta `mtf` aos itens da
Watchlist e `armed_items`/`armed_count`. Comparações entre consultas sucessivas
devem considerar `as_of`, observação de origem e sua validade: o mercado muda.

O enriquecimento ocorre fora do carregador básico usado pela captura de histórico.
O observador MTF continua usando `load_l3_internal_candidates`, com o contrato
interno anterior, sem consumir a lista externa filtrada. Autorização e admissão
Shadow não são alteradas.

## Verificação

Testes de regressão em `backend/tests/test_l3_armed_observation.py` cobrem
atualidade, configuração, perfil de referência, universo sem limite Shadow,
isolamento, transições e igualdade entre as projeções da UI e da API.
Publicação exige validação autenticada dos endereços e das abas, bloqueios de
ordens preservados e registro no ledger de produção.
