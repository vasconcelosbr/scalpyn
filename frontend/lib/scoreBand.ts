/**
 * Robust-engine score band helpers (Task #187).
 *
 * The robust engine emits `alpha_score` on a 0–100 scale (see
 * `backend/app/services/robust_indicators/score.py:245`). The Score
 * Breakdown UI must derive both the label *and* the color from the same
 * thresholds so users never see a "GOOD" label next to a yellow bar (or
 * vice-versa).
 *
 * Thresholds mirror `ScoreEngine._classify` in the backend
 * (`backend/app/services/score_engine.py`):
 *   - score >= 80  → strong_buy
 *   - score >= 65  → buy
 *   - score >= 40  → neutral
 *   - score <  40  → avoid
 *
 * `blocked` is a hard override used by Pipeline rows where a critical
 * filter rejected the asset — score is irrelevant in that case, render
 * red + BLOCKED.
 */

export type ScoreBandKey = "blocked" | "avoid" | "neutral" | "buy" | "strong_buy" | "unknown";

export interface ScoreBandStyle {
  /** Machine-readable band identifier. */
  key: ScoreBandKey;
  /** Short uppercase label for badges (e.g. "AVOID", "GOOD"). */
  label: string;
  /** Hex color for bar fill / text. */
  color: string;
}

export function scoreBand(
  score: number | null | undefined,
  blocked: boolean = false,
): ScoreBandStyle {
  if (blocked) {
    return { key: "blocked", label: "BLOCKED", color: "#F87171" };
  }
  if (score == null || Number.isNaN(score)) {
    return { key: "unknown", label: "—", color: "#64748B" };
  }
  if (score >= 80) return { key: "strong_buy", label: "STRONG", color: "#34D399" };
  if (score >= 65) return { key: "buy",        label: "GOOD",   color: "#4ADE80" };
  if (score >= 40) return { key: "neutral",    label: "MIXED",  color: "#FBBF24" };
  return            { key: "avoid",       label: "AVOID",  color: "#F87171" };
}

/** Clamp score to [0, 100] for use as a bar-width percentage. */
export function scorePct(score: number | null | undefined): number {
  if (score == null || Number.isNaN(score)) return 0;
  return Math.max(0, Math.min(100, score));
}

/** Tooltip text used wherever the robust score is displayed. */
export const SCORE_TOOLTIP =
  "Confidence-weighted score (0–100). Cada regra matched contribui " +
  "(pontos × confidence do indicador) ÷ pontos totais possíveis × 100. " +
  "Faixas: avoid <40 · neutral 40–64 · buy 65–79 · strong_buy ≥80.";

export const RULES_TOOLTIP =
  "Soma confidence-weighted (Σ pontos × confidence do indicador) sobre " +
  "o total possível. Reconciliando com o Score: " +
  "Score ≈ (pts ponderados ÷ pts totais) × 100. " +
  "Quando aparece (legacy), o snapshot é antigo e mostra apenas pontos nominais.";
