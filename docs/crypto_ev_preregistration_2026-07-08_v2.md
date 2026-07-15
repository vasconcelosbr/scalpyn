# Crypto EV Score - Pre-registro da validacao G (v2)

Data do pre-registro: 2026-07-08
Substitui: `crypto_ev_preregistration_2026-07-08.md` (v1, nao commitado / invalidado por brechas de trigger aberto, tabela ambigua e regra de replay incorreta)

## Clausula de imutabilidade

Este documento NAO sera editado apos o commit. Qualquer correcao exige novo arquivo datado (`crypto_ev_preregistration_YYYY-MM-DD_vN.md`) com justificativa explicita da mudanca e referencia ao hash do commit deste arquivo. A avaliacao descrita aqui sera executada UMA UNICA VEZ no trigger; qualquer reexecucao exige novo pre-registro.

## Escopo congelado

O Crypto EV Score e um score operacional pos-sinal por simbolo, calculado sobre `shadow_trades` com `source='L1_SPECTRUM'` e outcome do simulador (ground truth). Ele nao entra em `FEATURE_COLUMNS`, `features_snapshot`, datasets de treino, datasets de inferencia ou qualquer modelo ML. Esta proibicao e permanente e nenhum resultado desta validacao a altera.

## Visao avaliada

- **Teste principal (unico que alimenta o veredito):** view `executable` (subset `would_pass_l3 = true`), pois e a view consumida por block rules em caso de liberacao.
- **Teste secundario (diagnostico, sem efeito no veredito):** view `spectrum`.

## Hipotese

Se o score captura edge operacional real por cripto, o score em t prediz o retorno liquido realizado dos trades do simbolo na janela forward, com associacao positiva de ranking (quintis superiores de score apresentam EV liquido forward superior aos inferiores).

## Trigger de avaliacao (concreto e congelado)

A avaliacao sera executada quando AMBAS as condicoes forem verdadeiras:

1. Dataset pos-`ml_dataset_valid_from` atingir **2.000 trades fechados** com `source='L1_SPECTRUM'`, PnL liquido valido e flag de replay resolvido (true ou false; UNREPLAYABLE nao conta para o trigger).
2. Data igual ou posterior a **2026-08-15**.

Ou seja: o que ocorrer POR ULTIMO entre as duas condicoes. Nenhuma inspecao dos resultados de correlacao antes do trigger (proibicao de peeking). Metricas operacionais do score (N, w, exclusoes) podem ser monitoradas normalmente; a correlacao score vs forward nao.

## Parametros congelados do metodo

- Janela forward: **72 horas** apos o `computed_at` de cada snapshot.
- Associacao: cada trade fechado e associado ao snapshot de score mais recente do seu simbolo ANTERIOR a abertura do trade (nunca posterior; trade sem snapshot anterior e excluido e contado).
- Sem reuso: trades usados no calculo de um snapshot nao entram na janela forward avaliada desse mesmo snapshot.
- Retorno avaliado: PnL liquido por trade (apos fee roundtrip da config vigente no trade).
- Quintis Q1-Q5 definidos sobre a distribuicao de scores dos snapshots incluidos na avaliacao.
- Correlacao: **Spearman** entre score do snapshot e media do PnL liquido forward associado.
- Spread Q5 - Q1: diferenca de media de PnL liquido forward entre quintil superior e inferior.
- Intervalo de confianca do spread: **bootstrap percentil, 10.000 reamostragens, nivel 95%**, reamostrando por trade.
- Registro obrigatorio no relatorio: n de trades, n de simbolos, n de snapshots, periodo coberto, `config_version` e `l3_config_version` presentes, contagens de exclusao (`sem snapshot anterior`, `n_excluded_no_pnl`, `n_excluded_unreplayable`).

## Analise secundaria pre-registrada (diagnostico obrigatorio, sem efeito no veredito)

1. Spearman score vs PnL liquido forward DENTRO de cada bucket de ATR (LOW / MID / HIGH). Objetivo: verificar se a associacao global nao e apenas o artefato volatilidade-mecanico (TP fixo 1.5% + stop ATR-dinamico) ja confirmado no projeto. Resultado registrado no relatorio final.
2. Mesmo teste principal repetido na view `spectrum`, apenas para comparacao executable vs spectrum.

## Tabela de veredito (exaustiva, mutuamente exclusiva, avaliada em ordem)

Avaliada sobre o teste principal (view executable). Toda combinacao possivel de resultados cai em exatamente uma linha; a primeira linha satisfeita determina o veredito.

| Ordem | Criterio | Veredito | Acao permitida |
| --- | --- | --- | --- |
| 1 | Spearman >= 0.15 E spread Q5-Q1 > 0 com IC 95% excluindo 0 | SINAL VALIDO | Liberar uso do score exclusivamente em block rules / politica de execucao L3. Nada upstream da captura. Nada em ML. |
| 2 | (nao satisfez 1) E Spearman >= 0.05 | SINAL FRACO | Score permanece observacional (GUI/auditoria). Ajustes de k/janela permitidos, mas nova avaliacao exige novo pre-registro datado. |
| 3 | (nao satisfez 1 nem 2) | SEM SINAL | Score permanece apenas informativo em GUI/auditoria. Proibido em qualquer decisao automatica. Reformulacao do desenho exige novo pre-registro. |

Nao existe rota de saida fora desta tabela.

## Regras do flag would_pass_l3 (correcao da v1)

O flag e uma variavel de MEDICAO, nao uma decisao de execucao. Tres estados:

- `true`: replay deterministico do L3 vigente sobre o snapshot de entrada aprovou.
- `false`: replay executou e reprovou.
- `UNREPLAYABLE`: contexto insuficiente para replay (chaves ausentes no snapshot, config L3 da epoca indisponivel, etc.).

Regras:

1. E PROIBIDO mapear UNREPLAYABLE para false. Classificar dado faltante como rejeicao corrompe a populacao do EV_executable e o diagnostico executable vs spectrum.
2. Trades UNREPLAYABLE ficam FORA da media do EV_executable e sao contados em `n_excluded_unreplayable` no snapshot (fail-closed por exclusao contada, nao por classificacao inventada).
3. Se `n_excluded_unreplayable / (n_trades + n_excluded_unreplayable)` exceder o limiar em config (`crypto_ev.max_unreplayable_ratio`, sugerido 0.20), o snapshot da view executable degrada para estado `INSUFFICIENT_DATA`.
4. Todo replay grava `l3_config_version` utilizado. Replay sem versao registrada e invalido (conta como UNREPLAYABLE).
5. O flag JAMAIS condiciona a captura (pureza da captura: nenhum condicional entre promocao L1 e criacao do shadow trade).

## Regras de seguranca (permanentes)

- Nenhum resultado desta validacao autoriza inserir Crypto EV ou derivados em ML (features, labels, filtros de dataset, inferencia).
- O componente ML futuro do Crypto EV permanece desabilitado por padrao e fail-closed: checkbox na GUI expressa intencao; o health gate do backend (status promoted, OOS AUC minimo, canario concluido, dias limpos) decide a cada calculo.
- Guard executavel no trainer (rejeicao de colunas `crypto_ev*` / `post_model_operational*`) deve estar ativo ANTES da primeira gravacao de snapshot.

## Nota sobre valores congelados

Os valores 2.000 trades, 2026-08-15, 72h, quintis, Spearman 0.15/0.05, bootstrap 10.000/95% e max_unreplayable_ratio 0.20 sao escolhas de desenho fixadas NESTE pre-registro para eliminar graus de liberdade pos-hoc. Eles nao possuem derivacao empirica previa e nao pretendem ter; a funcao deles e serem imutaveis, nao otimos. Ajusta-los exige novo pre-registro antes de qualquer inspecao de resultado.
