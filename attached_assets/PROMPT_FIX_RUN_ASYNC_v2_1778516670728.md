# Prompt — Fix _run_async: RuntimeError Event Loop is Closed

## Contexto preciso do problema

O worker `scalpyn-worker-structural` foi a óbito com esta sequência nos logs de produção:

```
RuntimeError: Event loop is closed
  File "asyncpg/protocol/protocol.pyx", line 650
    self._cancellations.add(self._loop.create_task(self._cancel(waiter)))
  File "asyncio/base_events.py", line 455, in create_task
    self._check_closed()
RuntimeError: Event loop is closed

→ sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid
  transaction is rolled back.
  File "app/tasks/collect_market_data.py", line 323, in _collect_all_async
    return await run_db_task(_inner, celery=True)
  File "app/database.py", line 370, in run_db_task
    async with session.begin():
```

**Causa raiz confirmada:** todas as tasks Celery usam este pattern em
`collect_market_data.py` (e nos demais arquivos de tasks):

```python
def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()  # ← BUG: fecha o loop antes do asyncpg terminar cleanup
```

**O que acontece:**

1. `run_db_task(celery=True)` usa `CeleryAsyncSessionLocal` com `NullPool`
   (correto — evita "Future attached to a different loop")
2. Quando a coroutine termina (ou é interrompida por `soft_time_limit`),
   o `async with session.begin().__aexit__` tenta fazer commit ou rollback
3. O asyncpg precisa de `loop.create_task()` para cancelar operações pendentes
   durante o fechamento da conexão NullPool
4. Mas `loop.close()` já foi chamado no `finally` do `_run_async`
5. → `RuntimeError: Event loop is closed`
6. → A sessão fica num estado `PendingRollbackError` que o `_safe_rollback`
   em `database.py:382` não consegue limpar porque o loop já está fechado
7. → Task falha, worker acumula erros, fila >500

**Importante:** `database.py` já tem defesas corretas (`NullPool`,
`_safe_rollback`, `idle_in_transaction_session_timeout`). O único fix
necessário é no `_run_async` dos arquivos de tasks.

---

## O que fazer

### Passo 1 — Localizar todos os arquivos com `_run_async`

```bash
grep -rn "def _run_async" backend/app/tasks/
```

### Passo 2 — Substituir `_run_async` em cada arquivo encontrado

Substituir a implementação atual:

```python
# ANTES (problemático em todos os arquivos):
def _run_async(coro):
    """Run async code in sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
```

Por esta versão corrigida:

```python
# DEPOIS (correto):
def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Creates a dedicated event loop per task invocation (required because
    Celery workers are synchronous and asyncpg connections are bound to
    the event loop that created them).

    IMPORTANT: drains all pending asyncpg tasks before closing the loop.
    Without this drain step, asyncpg._terminate_graceful_close() calls
    loop.create_task() on an already-closed loop, raising:
        RuntimeError: Event loop is closed
    which leaves the NullPool session in PendingRollbackError state and
    causes the next task invocation to fail at session.begin().__aexit__.

    See: github.com/MagicStack/asyncpg/issues/863
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        # Drain pending asyncpg callbacks before closing the loop.
        # asyncpg schedules cleanup tasks (cancel, close, terminate) that
        # must complete before the loop is destroyed.
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        finally:
            loop.close()
```

### Passo 3 — Verificar que todos os arquivos foram atualizados

```bash
grep -A 20 "def _run_async" backend/app/tasks/collect_market_data.py
grep -A 20 "def _run_async" backend/app/tasks/compute_indicators.py
grep -A 20 "def _run_async" backend/app/tasks/compute_scores.py
grep -A 20 "def _run_async" backend/app/tasks/evaluate_signals.py
```

Cada arquivo deve mostrar o bloco `pending = asyncio.all_tasks(loop)`.

### Passo 4 — Confirmar que `database.py` NÃO foi modificado

```bash
git diff backend/app/database.py
```

Deve retornar vazio. `database.py` já está correto e não precisa de mudança.

---

## Regras estritas de aplicação

1. Modificar **apenas** a função `_run_async` em cada arquivo de task
2. **Não alterar** nenhuma outra linha de nenhum arquivo
3. **Não alterar** `database.py` — já está correto
4. A implementação do `_run_async` deve ser **idêntica** em todos os arquivos
5. Não mudar nomes, assinaturas, rotas Celery, lógica de negócio ou testes

---

## Validação pós-deploy

```bash
# Worker structural voltou online e está processando
celery -A app.tasks.celery_app inspect ping

# Nenhum RuntimeError: Event loop is closed nos últimos 10 minutos
# Cloud Logging filter:
# resource.labels.service_name="scalpyn-worker-structural"
# textPayload=~"Event loop is closed"

# Nenhum PendingRollbackError
# textPayload=~"PendingRollbackError"
```

---

## Contexto adicional

Este fix é uma proteção estrutural. A causa raiz arquitetural
(loop OHLCV pesado dentro do `collect_all` com alta probabilidade de
ser interrompido pelo `soft_time_limit`) está sendo eliminada
em paralelo pelo refactor `structural-30m` (task separada).

Após o refactor em produção, este fix vira um safety net para
edge cases, não uma remediação ativa de falha recorrente.
