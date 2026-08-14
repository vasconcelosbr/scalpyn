import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  activeChatTurnForReattach,
  cacheReconciliationStates,
  consumeChatEventStream,
  pollExactChatTurn,
} from "./analysis-chat-runtime";

const panel = readFileSync(new URL("../components/ai/AnalysisChatPanel.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/intelligence-runs/page.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("api.ts", import.meta.url), "utf8");

test("chat button is gated by completed run and persisted result", () => {
  assert.match(page, /selected\?\.status === "COMPLETED"/);
  assert.match(page, /runContext\?\.result\.status === "COMPLETED"/);
  assert.match(panel, /Conversar sobre esta análise/);
});

test("chat renders model, evidence, budget and provider-blocked state", () => {
  assert.match(panel, /Provider bloqueado/);
  assert.match(panel, /evidence_refs/);
  assert.match(panel, /budget_reservation/);
  assert.match(panel, /effective_provider/);
});

test("cache reconciliation state distinguishes persisted writes from effective runtime", () => {
  const states = cacheReconciliationStates({
    cache_invalidation_status: "PENDING_AFTER_COMMIT",
    cache_reconciliation_retry_state: "DISPATCHED",
    cache_reconciliation_attempts: 2,
    cache_reconciliation_max_attempts: 6,
    cache_reconciliation_next_retry_at: "2026-08-14T12:00:00+00:00",
    rollback: {
      cache_invalidation_status: "RECONCILIATION_REQUIRED",
      cache_reconciliation_retry_state: "EXHAUSTED",
      cache_reconciliation_attempts: 6,
      cache_reconciliation_max_attempts: 6,
      cache_reconciliation_next_retry_at: null,
    },
  });

  assert.deepEqual(states, [
    {
      kind: "EXECUTION",
      status: "PENDING_AFTER_COMMIT",
      retryState: "DISPATCHED",
      attempts: 2,
      maxAttempts: 6,
      nextRetryAt: "2026-08-14T12:00:00+00:00",
    },
    {
      kind: "ROLLBACK",
      status: "RECONCILIATION_REQUIRED",
      retryState: "EXHAUSTED",
      attempts: 6,
      maxAttempts: 6,
      nextRetryAt: null,
    },
  ]);
  assert.match(panel, /Alteração persistida/);
  assert.match(panel, /cache pendente/);
  assert.match(panel, /Runtime reconciliado/);
  assert.match(panel, /intervenção necessária/);
});

test("profile changes report cache as not applicable instead of pending", () => {
  assert.deepEqual(cacheReconciliationStates({
    cache_invalidation_status: "NOT_REQUIRED",
    cache_reconciliation_retry_state: "NOT_APPLICABLE",
    cache_reconciliation_attempts: 0,
    cache_reconciliation_max_attempts: 0,
    cache_reconciliation_next_retry_at: null,
  }), [{
    kind: "EXECUTION",
    status: "NOT_REQUIRED",
    retryState: "NOT_APPLICABLE",
    attempts: 0,
    maxAttempts: 0,
    nextRetryAt: null,
  }]);
  assert.match(panel, /Cache n.o aplic.vel a altera..es de perfil/);
});

test("chat uses authenticated SSE with reconnect and safe plain-text rendering", () => {
  assert.match(panel, /text\/event-stream/);
  assert.match(panel, /Last-Event-ID/);
  assert.match(panel, /Authorization/);
  assert.match(panel, /STREAM_RECONNECT_ATTEMPTS/);
  assert.match(panel, /for \(let attempt = 0; attempt < STREAM_RECONNECT_ATTEMPTS/);
  assert.match(panel, /pollTurnUntilTerminal/);
  assert.doesNotMatch(panel, /dangerouslySetInnerHTML/);
});

test("chat sends without browser alerts and keeps inline authority gates", () => {
  assert.doesNotMatch(panel, /window\.confirm/);
  assert.match(panel, /provider_max_cost_usd/);
  assert.match(panel, /pending_interrupt/);
  assert.match(panel, /CREATE_CHILD_ANALYSIS/);
  assert.match(panel, /DRAFT_PROPOSAL/);
  assert.match(panel, /\/cancel/);
});

test("chat exposes audit-only budget mode without presenting a cost ceiling", () => {
  assert.match(panel, /budget_enforcement_enabled/);
  assert.match(panel, /orçamento somente auditável/);
});

test("chat labels the default composer as automatic governed routing", () => {
  assert.match(panel, /Automático: análise ou ação governada/);
});

test("nested API detail codes are shown instead of a generic 409 error", () => {
  assert.match(api, /parsed\?\.detail\?\.code/);
});

test("human decisions are submitted once while the graph resumes", () => {
  assert.match(panel, /submittedInterrupts\.has\(interruptId\)/);
  assert.match(panel, /new Set\(current\)\.add\(interruptId\)/);
  assert.match(panel, /GRAPH_INTERRUPT_ALREADY_RESOLVED/);
});

test("ReadableStream parsing preserves chunks and stops on the exact terminal frame", async () => {
  const encoder = new TextEncoder();
  const payload = encoder.encode([
    "id: 41\nevent: token\ndata: {\"data\":{\"chunk\":\"resposta \"}}\n\n",
    "id: 42\nevent: token\ndata: {\"data\":{\"chunk\":\"final\"}}\n\n",
    "id: 43\nevent: completed\ndata: {\"data\":{}}\n\n",
  ].join(""));
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(payload.slice(0, 37));
      controller.enqueue(payload.slice(37));
      controller.close();
    },
  });
  const ids: number[] = [];
  const chunks: string[] = [];
  let terminals = 0;

  const outcome = await consumeChatEventStream({
    body,
    isCurrent: () => true,
    onEventId: (eventId) => ids.push(eventId),
    onToken: (chunk) => chunks.push(chunk),
    onTerminal: () => { terminals += 1; },
  });

  assert.equal(outcome, "terminal");
  assert.deepEqual(ids, [41, 42, 43]);
  assert.equal(chunks.join(""), "resposta final");
  assert.equal(terminals, 1);
});

test("streaming resumes after the accepted turn boundary instead of replaying old terminal events", () => {
  assert.match(panel, /accepted\.stream_after_event_id/);
  assert.match(panel, /streamCursor\.current = Math\.max\(0, Number\(accepted\.stream_after_event_id\) \|\| 0\)/);
  assert.equal(panel.match(/accepted\.stream_after_event_id/g)?.length, 2);
  assert.match(panel, /graph_run_id=\$\{encodeURIComponent\(graphRunId\)\}/);
});

test("bounded fallback polling follows the exact run and ignores another terminal run", async () => {
  let loads = 0;
  const controller = new AbortController();
  const result = await pollExactChatTurn({
    graphRunId: "run-active",
    attempts: 3,
    signal: controller.signal,
    isCurrent: () => true,
    loadMessages: async () => {
      loads += 1;
      return [
        { role: "ASSISTANT", status: "COMPLETED", graph_run_id: "run-old" },
        {
          role: "ASSISTANT",
          status: loads === 1 ? "STREAMING" : "INTERRUPTED",
          graph_run_id: "run-active",
          pending_interrupt: loads === 1 ? null : { id: "gate-1" },
        },
      ];
    },
    delay: async () => undefined,
  });

  assert.equal(loads, 2);
  assert.equal(result?.graph_run_id, "run-active");
  assert.equal(result?.status, "INTERRUPTED");
  assert.match(panel, /TURN_POLL_ATTEMPTS/);
  assert.match(panel, /message\.pending_interrupt/);
});

test("opening or reselecting a conversation reattaches only its active persisted run", () => {
  const active = activeChatTurnForReattach([
    { role: "ASSISTANT", status: "COMPLETED", graph_run_id: "run-old" },
    { role: "USER", status: "COMPLETED", graph_run_id: null },
    { role: "ASSISTANT", status: "STREAMING", graph_run_id: "run-current" },
  ]);

  assert.equal(active?.graph_run_id, "run-current");
  assert.match(panel, /activeChatTurnForReattach\(rows\)/);
  assert.match(panel, /streamCursor\.current = 0/);
  assert.match(panel, /consumeStream\(conversationId, reattachedRunId\)/);
});

test("late stream and message responses cannot overwrite another selected conversation", () => {
  assert.match(panel, /activeConversationRef\.current === id/);
  assert.match(panel, /activeStreamRunRef\.current === graphRunId/);
  assert.match(panel, /selectConversation\(item\.conversation_id\)/);
});

test("a terminal governed turn without a proposal is shown as not generated", () => {
  assert.match(panel, /Prévia executável não gerada/);
  assert.match(panel, /Nenhum perfil foi alterado/);
});

test("bulk profile proposals show their profile count", () => {
  assert.match(panel, /target\.profile_ids\?\.length/);
  assert.match(panel, /perfis/);
});

test("governed proposals distinguish policy approval from fail-closed missing validation", () => {
  assert.doesNotMatch(panel, /NOT_APPLICABLE:/);
  assert.match(panel, /NOT_PERFORMED: "não validado \(execução bloqueada\)"/);
  assert.match(panel, /Risk policy:/);
  assert.match(panel, /Strategy policy:/);
});

test("the first message can create its conversation without a separate click", () => {
  assert.match(panel, /let activeConversationId = conversationId/);
  assert.match(panel, /if \(!activeConversationId\)/);
  assert.doesNotMatch(panel, /disabled=\{busy \|\| !conversationId \|\| !draft\.trim\(\)\}/);
});
