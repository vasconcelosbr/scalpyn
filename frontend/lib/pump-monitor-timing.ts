export type PumpMonitorTiming = {
  monitor_entered_at?: string | null;
  last_strong_long_at?: string | null;
  tracking_started_at?: string | null;
  timing_initial_baseline?: boolean | null;
  timing_status?: "available" | "unavailable" | null;
};

const formatter = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit", year: "numeric",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

export function formatPumpTime(value?: string | null): string | null {
  // A timestamp without an offset would depend on the viewer's computer timezone.
  if (!value || !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return formatter.format(date).replace(",", "");
}

export function pumpTimingDisplay(row: PumpMonitorTiming) {
  const available = row.timing_status === "available";
  const baseline = available && row.timing_initial_baseline === true;
  const entered = available ? formatPumpTime(row.monitor_entered_at) : null;
  const started = available ? formatPumpTime(row.tracking_started_at) : null;
  const strong = available ? formatPumpTime(row.last_strong_long_at) : null;
  return {
    entryLabel: baseline ? "Registro iniciado em" : "No monitor desde",
    entry: (baseline ? started : entered) ?? "Indisponível",
    strong: strong ?? "Indisponível",
    entryTitle: baseline
      ? "Horário de Brasília. Ativo já presente no início do registro; entrada anterior desconhecida."
      : "Horário de Brasília. Primeira detecção desta permanência no monitor, independente dos filtros.",
    strongTitle: strong
      ? "Horário de Brasília. Última entrada observada na seta verde forte; preservada ao perder força."
      : "Ainda não há uma entrada comprovada em força alta neste registro. Horários anteriores não são estimados.",
  };
}
