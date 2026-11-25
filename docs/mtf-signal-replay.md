# Replay independente dos gatilhos MTF

O registro é observacional, sem ordens e sem admissão de Shadow. Cada contexto
(usuário, watchlist, ativo, perfil) mantém um latch de estado em candles fechados.
Uma transição para TRIGGERED abre um replay; avaliações repetidas, atrasadas ou
consecutivas em TRIGGERED não abrem outro. ARMED após TRIGGERED inicia novo contexto
de preparação, permitindo reativação enquanto o replay anterior continua aberto.

## Evidência e resultado

Entrada analítica = fechamento do candle que confirmou o sinal. Alvo = preço de
referência × (1 + replay_target_pct / 100). Stop = stop válido do plano original.
O alvo é configurado em mtf_history, separado do engine_hash; alterações só afetam
novos registros. Não há taxas, slippage, execução, prazo máximo ou encerramento
automático quando o ativo deixa a watchlist. Stop ausente é uma pendência explícita.

As tabelas mtf_signal_replays, mtf_replay_candles e mtf_replay_snapshots têm triggers
de imutabilidade. mtf_replay_progress mantém o cursor transacional recuperável.
Os dados futuros nunca substituem a captura original. Campos de origem, captura,
configuração, uso e qualidade dos indicadores permanecem nos snapshots.

A trajetória utiliza candles Gate fechados de um minuto, recuperados a partir do
primeiro minuto posterior à confirmação. Um gap bloqueia o cursor e a conclusão
até recuperação. Toque duplo dentro do minuto é INDETERMINATE; não inferimos ordem
OHLC. A abertura pode resolver a ordem quando já está além de uma barreira. Horário
do toque é um intervalo de candle, não um timestamp de negócio inventado.

Antes da primeira captura de cada contexto, registros antigos com inputs congelados
são percorridos em ordem temporal e identificados como reconstruções. Ausência de
histórico não comprova o instante inicial da ativação: first_observation é explícito.
Não se recalculam indicadores históricos com configurações atuais.

## Captura

persist_evaluation captura antes de deduplicação/admissão de Shadow. A fila dedicada
mtf_history cobre o universo completo sem o limite visual do observador de Shadow,
inclusive se o histórico genérico estiver desabilitado. Dispatch verifica cada
minuto, deduplicando na menor periodicidade configurada. A manutenção retoma
replays abertos com locks por registro e commits por replay, continuando mesmo
após remoção da watchlist. Capturas atuais de indicadores usam o engine congelado;
o profile_config e as identidades efetivamente observados ficam no snapshot, sem
alegar que valores ausentes ou alterações de perfil são os inputs originais.

Fechamentos perdidos durante indisponibilidade não têm indicadores fabricados:
o gráfico indica o horário da última captura disponível. Captura e execução de
workers têm latência; não há promessa de timestamp exato de primeiro negócio.

## Interface e API

/grafico-mtf/replays: filtros, reprodução/pausa, velocidade, avanço por candle,
marcadores ARMED/TRIGGERED/TARGET/STOP e comparação dos indicadores com o início.
O gráfico usa contexto anterior do timeframe do gatilho e trajetória posterior
em um minuto. Valores futuros não são revelados no quadro de indicadores.

- GET /api/mtf/replays: lista paginada por usuário, filtros de data/ativo/watchlist/resultado.
- GET /api/mtf/replays/{id}: evidência imutável e progresso.
- GET /api/mtf/replays/{id}/trajectory: candles e snapshots paginados por cursor.
- PUT /api/mtf/observations/settings: replay_target_pct, preservando campos omitidos.

Leituras dependem do proprietário, não da permanência do ativo na watchlist.

## Validação e implantação

Executar tests/test_mtf_signal_replay.py em PostgreSQL isolado, testes históricos,
de Shadow, engine e preview; alembic heads/history; tsc e build frontend. A revisão
252 é aditiva e preserva evidência no downgrade. Publicar API, worker de histórico,
worker de observação e scheduler com o mesmo commit canônico; confirmar esquema,
captura real, progresso e a UI autenticada antes do registro no ledger de produção.
