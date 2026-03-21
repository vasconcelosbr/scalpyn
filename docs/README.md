# Scalpyn — Documentation Index

## Architecture

| Doc | Conteúdo | Referência Principal |
|-----|----------|---------------------|
| [01-score-driven-framework.md](architecture/01-score-driven-framework.md) | Dois perfis (Spot/Futures), score-driven sem grid, never sell at loss, anti-liquidação | Framework central do app |
| [02-futures-leveraged-framework.md](architecture/02-futures-leveraged-framework.md) | 5-Layer Institutional Scoring, 4 Gates, 6 estratégias, trailing ATR, macro gate | Futures engine design |
| [03-trading-desk-navigation.md](architecture/03-trading-desk-navigation.md) | Sidebar, rotas, wireframes ASCII, componentes, hooks, API endpoints | Frontend structure |
| [04-sell-flow-improvements.md](architecture/04-sell-flow-improvements.md) | 10 melhorias no fluxo de venda spot (volatility, structure, macro, ATR trailing) | Sell logic upgrades |

## API Integration

| Doc | Conteúdo |
|-----|----------|
| [gate-io-v4-mapping.md](api-integration/gate-io-v4-mapping.md) | Mapeamento completo Gate.io API v4 → Scalpyn. Endpoints, payloads, lifecycle, WebSocket, rate limits |

## Implementation

| Doc | Conteúdo |
|-----|----------|
| [ROADMAP.md](implementation/ROADMAP.md) | 5 fases, branch strategy, tasks por arquivo, commit conventions, timeline, prompts para Claude Code |

## Key Principles

1. **ZERO HARDCODE** — Todo threshold, %, margem → config no DB → editável na GUI
2. **Score Drives Everything** — Sem score ≥ threshold, sem trade
3. **Spot: Never Sell at Loss** — Posição underwater fica em holding indefinidamente
4. **Futures: Anti-Liquidation** — Stop SEMPRE executa antes da liquidação (3 camadas)
5. **Leverage is Calculated** — Consequência do risk sizing e stop distance, nunca arbitrária
6. **Each Position is Independent** — Entry price imutável, P&L individual, múltiplas na mesma moeda
