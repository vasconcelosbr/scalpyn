# Stop MTF dimensionado pelo RR bruto

Em Configuração → Gatilho → Stop pelo RR bruto, `stop_sizing` controla o
dimensionamento do stop do plano observacional. O padrão desativado conserva
o cálculo anterior e o hash do engine, incluindo configurações históricas.

```json
{"stop_sizing":{"enabled":true,"target_pct":1,"gross_rr":2}}
```

Distância percentual = target_pct / gross_rr; stop = preço do fechamento que
confirmou o sinal × (1 − distância / 100). O exemplo solicitado calcula uma
distância de 0,5%. O parâmetro substitui a mínima do candle e a margem ATR
somente no dimensionamento do stop. Não ativa nem modifica o veto de RR líquido.
Os outros vetos, score, custos e alvos estruturais continuam sendo aplicados.
O tamanho da posição simulada utiliza o risco do novo stop.

`plan.stop_sizing` preserva preço de referência, alvo percentual, preço do alvo
de referência, RR bruto solicitado e distância percentual. `plan.rr_gross` e
`plan.rr_net` continuam descrevendo o primeiro alvo estrutural, que pode diferir
do alvo percentual. A entrada simulada também pode diferir do fechamento por
gap/slippage. Logo o RR do Shadow não é apresentado como igual ao RR de referência.

O replay mantém seu alvo percentual independente. Para a relação solicitada,
o alvo do replay e o alvo de referência do stop devem coincidir. O replay copia
o stop do sinal e o JSON completo inclui a nova evidência automaticamente.

Novos planos usam a configuração salva via API. Shadows existentes consomem
seu config_snapshot original e o stop congelado; replays e sinais históricos
não são reescritos. O modo não aciona operações reais.

Validação: engine e configurações, identidade com modo desativado, cálculo
independente do veto, redimensionamento de quantidade, preservação do stop
existente, formulário/JSON, snapshots e replays em PostgreSQL.
