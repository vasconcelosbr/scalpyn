/**
 * Display-timezone formatting: every consumer of this module renders in
 * America/Sao_Paulo (GMT-3, no DST since 2019) regardless of the viewer's
 * browser/OS timezone. Backend storage and API payloads stay UTC — only
 * this presentation layer converts.
 */

export const DISPLAY_TZ = "America/Sao_Paulo";
const LOCALE = "pt-BR";

export function formatDateTime(value: string | number | Date, opts: Intl.DateTimeFormatOptions = {}): string {
  return new Intl.DateTimeFormat(LOCALE, { timeZone: DISPLAY_TZ, ...opts }).format(new Date(value));
}

export function formatDate(value: string | number | Date, opts: Intl.DateTimeFormatOptions = {}): string {
  return formatDateTime(value, { year: "numeric", month: "2-digit", day: "2-digit", ...opts });
}

export function formatTime(value: string | number | Date, opts: Intl.DateTimeFormatOptions = {}): string {
  return formatDateTime(value, { hour: "2-digit", minute: "2-digit", second: "2-digit", ...opts });
}

/** lightweight-charts `localization.timeFormatter` — `time` is epoch seconds. */
export function chartTimeFormatter(time: number): string {
  return formatDateTime(time * 1000, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** lightweight-charts `timeScale.tickMarkFormatter` — `time` is epoch seconds. */
export function chartTickMarkFormatter(time: number): string {
  return formatDateTime(time * 1000, { hour: "2-digit", minute: "2-digit" });
}
