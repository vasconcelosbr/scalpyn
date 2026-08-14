export type ChatTurnState = {
  role: string;
  status: string;
  graph_run_id: string | null;
  pending_interrupt?: unknown | null;
};

export type CacheReconciliationState = {
  kind: "EXECUTION" | "ROLLBACK";
  status: string;
  retryState: string | null;
  attempts: number | null;
  maxAttempts: number | null;
  nextRetryAt: string | null;
};

type ChatStreamEnvelope = {
  data?: { chunk?: string };
};

const ACTIVE_TURN_STATUSES = new Set(["PENDING", "QUEUED", "STREAMING"]);
const TERMINAL_TURN_STATUSES = new Set(["COMPLETED", "BLOCKED", "FAILED", "CANCELLED"]);
const TERMINAL_STREAM_EVENTS = new Set(["completed", "blocked", "error", "cancelled"]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function cacheState(
  value: Record<string, unknown> | null,
  kind: CacheReconciliationState["kind"],
): CacheReconciliationState | null {
  const status = value?.cache_invalidation_status;
  if (typeof status !== "string" || !status) return null;
  const retryState = value?.cache_reconciliation_retry_state;
  const attempts = value?.cache_reconciliation_attempts;
  const maxAttempts = value?.cache_reconciliation_max_attempts;
  const nextRetryAt = value?.cache_reconciliation_next_retry_at;
  return {
    kind,
    status,
    retryState: typeof retryState === "string" ? retryState : null,
    attempts: typeof attempts === "number" && Number.isFinite(attempts) ? attempts : null,
    maxAttempts: typeof maxAttempts === "number" && Number.isFinite(maxAttempts)
      ? maxAttempts
      : null,
    nextRetryAt: typeof nextRetryAt === "string" ? nextRetryAt : null,
  };
}

export function cacheReconciliationStates(
  executionResult: Record<string, unknown> | null,
): CacheReconciliationState[] {
  const root = asRecord(executionResult);
  if (!root) return [];
  return [
    cacheState(root, "EXECUTION"),
    cacheState(asRecord(root.rollback), "ROLLBACK"),
  ].filter((item): item is CacheReconciliationState => item !== null);
}

export function assistantForGraphRun<T extends ChatTurnState>(
  rows: T[],
  graphRunId: string,
) {
  return [...rows].reverse().find(
    (item) => item.role === "ASSISTANT" && item.graph_run_id === graphRunId,
  );
}

export function isChatTurnTerminal(message: ChatTurnState | undefined) {
  return Boolean(
    message
    && (
      TERMINAL_TURN_STATUSES.has(message.status)
      || (message.status === "INTERRUPTED" && message.pending_interrupt)
    )
  );
}

export function activeChatTurnForReattach<T extends ChatTurnState>(rows: T[]) {
  return [...rows].reverse().find(
    (item) => (
      item.role === "ASSISTANT"
      && Boolean(item.graph_run_id)
      && ACTIVE_TURN_STATUSES.has(item.status)
    ),
  );
}

export async function pollExactChatTurn<T extends ChatTurnState>({
  graphRunId,
  attempts,
  signal,
  isCurrent,
  loadMessages,
  delay,
}: {
  graphRunId: string;
  attempts: number;
  signal: AbortSignal;
  isCurrent: () => boolean;
  loadMessages: () => Promise<T[]>;
  delay: (signal: AbortSignal) => Promise<void>;
}): Promise<T | null> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (!isCurrent()) return null;
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    try {
      const message = assistantForGraphRun(await loadMessages(), graphRunId);
      if (isChatTurnTerminal(message)) return message ?? null;
    } catch (caught) {
      if ((caught as Error).name === "AbortError") throw caught;
      // A later bounded attempt may recover from a transient refresh failure.
    }
    await delay(signal);
  }
  throw new Error("A resposta continua processando. Atualize a conversa para consultar o estado final.");
}

export async function consumeChatEventStream({
  body,
  isCurrent,
  onEventId,
  onToken,
  onTerminal,
}: {
  body: ReadableStream<Uint8Array>;
  isCurrent: () => boolean;
  onEventId: (eventId: number) => void;
  onToken: (chunk: string) => void;
  onTerminal: () => void | Promise<void>;
}): Promise<"terminal" | "ended" | "inactive"> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (isCurrent()) {
    const { value, done } = await reader.read();
    if (done) return "ended";
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const lines = frame.split("\n");
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const idLine = lines.find((line) => line.startsWith("id:"))?.slice(3).trim();
      if (idLine) {
        const eventId = Number(idLine);
        if (Number.isSafeInteger(eventId) && eventId > 0) onEventId(eventId);
      }
      const dataLine = lines.find((line) => line.startsWith("data:"))?.slice(5).trim();
      if (!dataLine) continue;
      const envelope = JSON.parse(dataLine) as ChatStreamEnvelope;
      if (event === "token" && envelope.data?.chunk) onToken(envelope.data.chunk);
      if (event && TERMINAL_STREAM_EVENTS.has(event)) {
        await onTerminal();
        await reader.cancel();
        return "terminal";
      }
    }
  }
  await reader.cancel();
  return "inactive";
}
