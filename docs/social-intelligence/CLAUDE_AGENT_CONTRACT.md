# Contrato do Agente de Inteligência Social

O relatório Markdown continua sendo produzido para leitura humana. Ao final de
cada execução, o agente deve enviar o mesmo resultado em JSON para:

`POST /api/social-intelligence/runs`

Autenticação: `Authorization: Bearer <SOCIAL_INTELLIGENCE_INGEST_TOKEN>`.

## Regras do agente

- Use UTC com timezone em `window_start`, `window_end` e `collected_at`.
- Separe atenção de direção: `attention_score` mede intensidade e
  `sentiment_score` usa `0=bearish`, `50=neutro`, `100=bullish`.
- Não derive direção apenas de volume de menções.
- Inclua ao menos uma URL verificável por ativo.
- Reutilize o mesmo `external_run_id` ao repetir o mesmo payload. Gere outro id
  quando qualquer conteúdo mudar.
- Não envie somente texto Markdown e não inclua tokens no JSON ou no relatório.

## Exemplo mínimo

```json
{
  "contract_version": "social-intelligence-v1",
  "external_run_id": "claude-social-2026-08-03T12:00:00Z",
  "source": "claude_social_agent",
  "model": "claude-haiku-4.5",
  "prompt_version": "social-research-v1",
  "window_start": "2026-08-02T12:00:00Z",
  "window_end": "2026-08-03T12:00:00Z",
  "collected_at": "2026-08-03T12:05:00Z",
  "assets": [
    {
      "symbol": "NEAR",
      "attention_score": 64,
      "sentiment_score": 71,
      "confidence": 0.78,
      "sentiment_label": "bullish",
      "recommendation": "observe_positive",
      "summary": "Atividade moderada e narrativa positiva de AI staking.",
      "narratives": ["AI staking"],
      "anomalies": [],
      "metrics": {"mentions": 120},
      "sources": [
        {
          "platform": "X",
          "url": "https://example.com/source",
          "title": "Fonte auditável",
          "published_at": "2026-08-03T10:00:00Z"
        }
      ]
    }
  ]
}
```

Uma resposta `PARTIAL` contém `rejected_items`; corrija apenas esses ativos e
envie uma nova execução. Uma resposta `DUPLICATE` confirma retry idempotente.
