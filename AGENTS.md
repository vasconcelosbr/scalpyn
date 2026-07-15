# Scalpyn — Institutional-Grade Crypto Trading Platform

## Core Principles
1. **ZERO HARDCODE** — Every threshold, %, margin → config in DB (JSONB in config_profiles) → GUI-editable
2. **Score Drives Everything** — No trade without score >= threshold
3. **Spot: NEVER sell at loss** — Underwater positions stay in HOLDING indefinitely
4. **Futures: Anti-Liquidation 3 layers** — Stop ALWAYS executes before liquidation
5. **Leverage is CALCULATED** — Consequence of risk sizing + stop distance, never arbitrary
6. **Each position is independent** — Immutable entry_price, individual P&L

## Architecture
- Two profiles: SPOT (score-driven, no grids, never sell at loss) and FUTURES (5-Layer Institutional Scoring, anti-liquidation)
- Score-driven opportunistic: scanner ranks ~100 coins, buys if score >= threshold AND USDT balance available
- No grids. Each buy is an independent position regardless of coin or existing positions.

## Stack
- Frontend: Next.js 14 (App Router) + TypeScript + TailwindCSS + shadcn/ui
- Backend: FastAPI (Python 3.11+, async) + SQLAlchemy 2.0 + Alembic
- DB: PostgreSQL 16 + TimescaleDB (time series) + Redis (cache, pub/sub)
- Tasks: Celery + Redis
- Exchange: Gate.io API v4 (settle: usdt)
- Design: Dark luxury fintech, Plus Jakarta Sans + JetBrains Mono

## Key Areas
- Trading Desk: /trading-desk/spot and /trading-desk/futures (separate sub-sections)
- Spot: scanner → buy → 5 sell layers (never at loss) → holding underwater → optional DCA
- Futures: 4 gates → 5-layer scoring → position sizing → leverage calc → anti-liq → TP1/TP2/TP3 → trailing ATR

## Docs
Read docs/ folder for complete architecture specs, Gate.io API mapping, and implementation roadmap.

## Disciplina de Evidência — Anti-Fabricação de Números

**Regra-mãe: EVIDÊNCIA-OU-SILÊNCIO.** Todo número num relatório/análise deve ser:
1. Um valor **literal copiado** de um output de query/comando colado; ou
2. Uma **conta cujos insumos são todos valores literais colados**, com a fórmula visível.

Qualquer número fora de (1) ou (2) → escreva `NÃO DISPONÍVEL`. Nunca um número plausível no lugar.

**Regras obrigatórias:**
- **Cole a fonte, não o resumo.** Para qualquer config/threshold citado, cole o JSON real.
- **Etiqueta de proveniência em todo número:** `[query]`, `[config: tipo]`, `[calc: fórmula]`, `[ABERTO]`. Número sem etiqueta é inválido.
- **Cross-check antes de reportar derivados.** Break-even/EV/taxa → confrontar com ao menos uma quantidade independente. Se contradisser, reporte a contradição, não escolha um lado.
- **Proibido preencher campo ausente por suposição.** Campo em representação diferente da esperada → reporte como está (ex.: SL é `stop_loss_atr_multiplier: 1.5`, não "SL=5%").
- **Separe LEITURA de INFERÊNCIA.** Leitura: cola o valor. Inferência: marca `[inferência]` e mostra o raciocínio.
- **Toda taxa carrega N e significância.** Caudas de n≲50 não ancoram conclusão de manchete.
- **Auditoria é read-only.** Só `SELECT`, `\d`, `EXPLAIN`, `grep`. Achado que exige correção é descrito, não executado.

**Ledger de Evidências obrigatório** ao fim de todo relatório numérico:
```
NÚMERO REPORTADO  | ORIGEM              | VALOR LITERAL DA FONTE
break-even=61,4%  | [calc] WR=TP/(TP+SL)| avg_TP=0,953; SL=-1,0; fee=0,20 [query]
SL live=1,5×ATR   | [config: risk]      | "stop_loss_atr_multiplier": 1.5
break-even=80%    | ❌ SEM FONTE         | — PROIBIDO, remover
```

**Self-check por número antes de emitir:** (1) está num output colado? (2) se config, colei o JSON? (3) se derivado, bate com quantidade independente? (4) tem N e significância? (5) está no Ledger? Se algum "não" → o número não sai.

> Motivo: numa auditoria real, `SL=5%` foi fabricado (config real: `stop_loss_atr_multiplier: 1.5`), gerando break-even de 80% e o veredito "estratégia quase morta". O valor correto era ~49–61%. Uma fabricação inverteu o diagnóstico estratégico.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
