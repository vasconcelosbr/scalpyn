# ============================================================
# PATCH: celery_app.py — wiring collect_structural_30m + compute_30m
#
# 4 pontos de toque. Aplicar na ordem listada.
# ============================================================


# ──────────────────────────────────────────────────────────────
# PATCH 1 — include[]
# LOCALIZAÇÃO: celery_app.py linha ~66-86 (lista include do Celery)
# AÇÃO: adicionar a nova task ao autodiscovery
#
# ANTES:
#     include=[
#         "app.tasks.collect_market_data",
#         ...
#         "app.tasks.orphan_tx_watchdog",
#     ],
#
# DEPOIS: adicionar estas duas linhas na lista include (ordem não importa):
#         "app.tasks.collect_structural_30m",
#         # compute_30m vive em compute_indicators — já incluído via
#         # "app.tasks.compute_indicators" existente. Não adicionar novamente.
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# PATCH 2 — TASK_ROUTES
# LOCALIZAÇÃO: celery_app.py linha ~93-137 (dict TASK_ROUTES)
# AÇÃO: adicionar rotas das novas tasks
#
# Inserir após o bloco de Structural existente:

NEW_ROUTES = {
    # Structural 30m collector (substitui OHLCV 1h do collect_all)
    "app.tasks.collect_structural_30m.run":          {"queue": "structural"},

    # Structural 30m compute (substitui compute 1h — Opção A)
    "app.tasks.compute_indicators.compute_30m":      {"queue": "structural"},
}

# ATENÇÃO: a task "app.tasks.compute_indicators.compute" (1h) permanece
# no TASK_ROUTES por enquanto como stub deprecated — o lint test
# test_every_registered_task_is_routed exige que toda task registrada
# via @celery_app.task tenha entrada aqui. Remover apenas quando o stub
# for removido do compute_indicators.py.
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# PATCH 3 — TASK_ANNOTATIONS (cost guards)
# LOCALIZAÇÃO: celery_app.py linha ~178-261 (dict TASK_ANNOTATIONS)
# AÇÃO: adicionar guards para as novas tasks
#
# Inserir após o bloco de Structural existente (antes de Execution):

NEW_ANNOTATIONS = {
    # collect_structural_30m: perfil igual ao structural padrão.
    # time_limit 600s é suficiente para 95 símbolos × fetch_ohlcv(30m).
    # Se o pool crescer além de 150, reavaliar.
    "app.tasks.collect_structural_30m.run": {
        "time_limit":      600,
        "soft_time_limit": 540,
        "rate_limit":      "2/h",   # dispara 2× por hora (0h e 30min)
        "max_retries":     3,
    },

    # compute_30m: mesmo perfil do compute 1h original.
    "app.tasks.compute_indicators.compute_30m": {
        "time_limit":      600,
        "soft_time_limit": 540,
        "rate_limit":      "2/h",
        "max_retries":     3,
    },
}
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# PATCH 4 — beat_schedule
# LOCALIZAÇÃO: celery_app.py linha ~313-400 (beat_schedule dict)
# AÇÃO: dois sub-patches dentro do beat_schedule
#
# SUB-PATCH 4a — REMOVER chain do collect_all (OHLCV saiu de lá):
#   O collect_all continua no beat @ 60s, mas sem o chain para compute.
#   O chain era disparado no wrapper Python de collect_all (collect_market_data.py).
#   A remoção está no PATCH do collect_market_data.py — NÃO há entrada
#   beat_schedule para o chain. Este sub-patch serve de lembrete apenas.
#
# SUB-PATCH 4b — ADICIONAR entradas para as novas tasks:
#
# Inserir no dict beat_schedule:

NEW_BEAT_ENTRIES = {
    # Structural 30m collector — dispara exatamente no fechamento da candle 30m
    # (UTC 00:00, 00:30, 01:00, 01:30 … 23:30). Sem drift de sleep().
    "collect_structural_30m_candle_close": {
        "task": "app.tasks.collect_structural_30m.run",
        "schedule": "crontab(minute='0,30')",  # remover aspas — é objeto Python
        # schedule: crontab(minute="0,30"),
    },
    # NOTA: compute_30m NÃO tem entrada no beat — é sempre disparado via
    # chain pelo collect_structural_30m. Beat só agenda collectors.
    # Manter esse invariante: beat → collectors → chain → compute → chain → score → chain → evaluate
}

# AVISO FINAL:
# A entrada existente "collect_market_data_every_minute" (collect_all @ 60s)
# PERMANECE no beat_schedule sem modificação de schedule.
# A única mudança em collect_all é no arquivo Python:
# remover o loop OHLCV e o task_dispatch.enqueue("compute_indicators.compute").
# O beat continua disparando collect_all @ 60s para ticker + metadata.
# ──────────────────────────────────────────────────────────────
