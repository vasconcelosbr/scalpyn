# Target e configuração das etapas MTF

Implementação local na branch `codex/mtf-target-settings`. Publicação em produção não realizada.

## Comportamento

A watchlist L3 Consolidado exibe a curva **Target1%**, calculada sobre o fechamento de cada candle, incluindo o candle em formação. A etiqueta usa a precisão do par. O percentual fica em `mtf_analysis.presentation.target_pct`; a visibilidade pessoal fica em `mtf_preferences.show_target`. O cálculo é visual e não altera os alvos estruturais, taxas, score ou ordens.

As configurações do gráfico oferecem ARMADO, GATILHO, Compartilhados e Avançado. Os formulários mostram unidades e distinguem condições obrigatórias de contribuições para o score. JSON avançado e formulários editam o mesmo rascunho. Cancelar não grava; falhas de gravação preservam a configuração anterior.

O ATR pode ser personalizado no MTF por `atr_period`, sem escrever nos indicadores globais. Configurações antigas continuam herdando o ATR. A primeira gravação com mudanças nas regras copia o período herdado para o MTF; salvar sem mudanças ou alterar somente apresentação preserva a herança e a identidade do motor. Editar o ATR explicitamente cria o ajuste local. Dados globais ausentes não são substituídos por um período inventado.

`force_patterns` e `rejection_patterns` passam a ser editáveis. Os valores iniciais reproduzem o comportamento anterior. A combinação das regras e os timeframes existentes permanecem. O resolvedor de indicadores efetivos é compartilhado pelo cálculo do gráfico/observador e pela validação do Consolidado. Alterar regras invalida leituras antigas até uma nova observação; históricos e contratos congelados de simulações permanecem preservados.

## Contratos e migração

- `GET /api/mtf/bootstrap`: acrescenta `config_schema` e `inherited_atr`, normalizando configurações antigas apenas na resposta, sem gravação.
- `PUT /api/mtf/config`: reutiliza validação e auditoria existentes para os novos campos MTF.
- `PUT /api/mtf/preferences`: aceita a preferência `show_target`.
- Migração `250_mtf_target_presentation`: acrescenta exclusivamente o percentual visual às apresentações que ainda não o possuem; preserva valores personalizados e configurações incompletas.

Em uma futura publicação, entregar backend e migração antes do frontend. A tela bloqueia gravação quando o servidor ainda não fornece o contrato de validação. Nenhuma migração ou alteração foi aplicada à produção nesta tarefa.

## Verificação

Backend, em PostgreSQL local isolado, incluindo motor, UI, API, novas configurações, Consolidado, histórico e admissão Shadow:

```text
python -m pytest tests/test_mtf_chart_engine.py tests/test_mtf_ui_v2.py tests/test_mtf_chart_api.py tests/test_mtf_settings.py tests/test_l3_armed_observation.py tests/test_mtf_history.py tests/test_shadow_consolidated_armed.py tests/test_mtf_chart_postgres.py -q --disable-warnings --tb=short
114 passed in 16.22s
```

Frontend: testes de curva, atualização incremental, rascunho, isolamento do ATR, validação e controles existentes:

```text
node --import tsx --test lib/mtfSettings.test.ts lib/mtfUi.test.ts
tests 6
pass 6
fail 0
```

ESLint dos arquivos alterados e `npm run build` concluíram sem erros. A checagem TypeScript do build passou. `git diff --check` passou.

Migração aplicada com Alembic a uma base local preparada na revisão anterior. O teste PostgreSQL também verificou reaplicação idempotente, preservação de percentual personalizado, identidade do motor e downgrade:

```text
Running upgrade 249_shadow_consolidated_armed -> 250_mtf_target_presentation, Seed the visual target without changing engine or user preferences.
250_mtf_target_presentation (head)
```

`graphify update .` concluiu a atualização AST do grafo local. A visualização HTML foi omitida automaticamente pelo limite de tamanho do Graphify.

O navegador foi validado contra a versão compilada, com dados sintéticos e rotas de API interceptadas. Nenhuma gravação desses testes acessou a produção. Cenários e capturas estão em `docs/evidence/mtf-target-settings/`.

Para repetir o teste visual, gerar JSON com `preset`, `presentation_preset`, `config_schema` e `inherited_atr` a partir do schema Python; indicar o arquivo em `MTF_SCHEMA_PATH`, o módulo Playwright em `PLAYWRIGHT_MODULE` e, se necessário, Chromium em `MTF_BROWSER_PATH`. Com o frontend local ativo, executar `node frontend/scripts/verify-mtf-target-settings.cjs`. O script aceita `MTF_BASE_URL` e `MTF_SCREENSHOT_DIR`.

## Ledger de evidências

| Número reportado | Origem | Valor literal da fonte |
| --- | --- | --- |
| Percentual visual inicial | [config: presentation_preset] | `{"target_pct": 1}` |
| Resultado backend | [query: pytest local] | `114 passed in 16.22s` |
| Resultado frontend | [query: node --test] | `tests 6`, `pass 6`, `fail 0` |
| Revisão local da migração | [query: alembic current] | `250_mtf_target_presentation (head)` |

Os preços e parâmetros nas capturas são dados sintéticos de teste, não evidência do mercado nem da configuração de produção.
