/**
 * Display-timezone formatting: every consumer of this module renders in
 * America/Sao_Paulo (GMT-3, no DST since 2019) regardless of the viewer's
 * browser/OS timezone. Backend storage and API payloads stay UTC — only
 * this presentation layer converts.
 */

export const DISPLAY_TZ = "America/Sao_Paulo";
const LOCALE = "pt-BR";
const DISPLAY_TZ_UTC_OFFSET_HOURS = 3; // GMT-3, no DST since 2019 (see DISPLAY_TZ above).

export function formatDateTime(value: string | number | Date, opts: Intl.DateTimeFormatOptions = {}): string {
  return new Intl.DateTimeFormat(LOCALE, { timeZone: DISPLAY_TZ, ...opts }).format(new Date(value));
}

export function formatDate(value: string | number | Date, opts: Intl.DateTimeFormatOptions = {}): string {
  return formatDateTime(value, { year: "numeric", month: "2-digit", day: "2-digit", ...opts });
}

export function formatTime(value: string | number | Date, opts: Intl.DateTimeFormatOptions = {}): string {
  return formatDateTime(value, { hour: "2-digit", minute: "2-digit", second: "2-digit", ...opts });
}

/**
 * Converts a `<input type="datetime-local">` raw value (e.g.
 * "2026-09-12T14:30"), entered by the user as DISPLAY_TZ wall-clock time,
 * into a UTC ISO-8601 string for API query params. Returns "" for an
 * empty/invalid input.
 */
export function displayDateTimeToUtcIso(value: string): string {
  if (!value) return "";
  const withSeconds = value.length === 16 ? `${value}:00` : value;
  const asUtcWallClock = new Date(`${withSeconds}Z`);
  if (isNaN(asUtcWallClock.getTime())) return "";
  const utcMs = asUtcWallClock.getTime() + DISPLAY_TZ_UTC_OFFSET_HOURS * 60 * 60 * 1000;
  return new Date(utcMs).toISOString();
}

/**
 * The inverse of displayDateTimeToUtcIso: given any instant, returns the
 * DISPLAY_TZ wall-clock "YYYY-MM-DDTHH:mm" string an
 * `<input type="datetime-local">` needs as its value.
 */
export function displayDateTimeLocalValue(value: string | number | Date): string {
  const shifted = new Date(new Date(value).getTime() - DISPLAY_TZ_UTC_OFFSET_HOURS * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`;
}

/** lightweight-charts `localization.timeFormatter` — `time` is epoch seconds. */
export function chartTimeFormatter(time: number): string {
  return formatDateTime(time * 1000, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** lightweight-charts `timeScale.tickMarkFormatter` — `time` is epoch seconds. */
export function chartTickMarkFormatter(time: number): string {
  return formatDateTime(time * 1000, { hour: "2-digit", minute: "2-digit" });
}
