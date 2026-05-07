import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { scoreBand, scorePct } from '../scoreBand';

describe('scoreBand thresholds (Task #187)', () => {
  it('returns BLOCKED with red color when blocked, regardless of score', () => {
    const b = scoreBand(95, true);
    assert.equal(b.key, 'blocked');
    assert.equal(b.label, 'BLOCKED');
    assert.equal(b.color, '#F87171');
  });

  it('returns AVOID (red) for score < 40', () => {
    for (const s of [0, 9.6, 39.99]) {
      const b = scoreBand(s);
      assert.equal(b.key, 'avoid', `score=${s}`);
      assert.equal(b.label, 'AVOID', `score=${s}`);
      assert.equal(b.color, '#F87171', `score=${s}`);
    }
  });

  it('returns MIXED (yellow) for 40 <= score < 65', () => {
    for (const s of [40, 50, 64.99]) {
      const b = scoreBand(s);
      assert.equal(b.key, 'neutral', `score=${s}`);
      assert.equal(b.label, 'MIXED', `score=${s}`);
      assert.equal(b.color, '#FBBF24', `score=${s}`);
    }
  });

  it('returns GOOD (green) for 65 <= score < 80', () => {
    for (const s of [65, 70, 79.99]) {
      const b = scoreBand(s);
      assert.equal(b.key, 'buy', `score=${s}`);
      assert.equal(b.label, 'GOOD', `score=${s}`);
    }
  });

  it('returns STRONG for score >= 80', () => {
    for (const s of [80, 90, 100]) {
      const b = scoreBand(s);
      assert.equal(b.key, 'strong_buy', `score=${s}`);
      assert.equal(b.label, 'STRONG', `score=${s}`);
    }
  });

  it('handles null/NaN as unknown (gray)', () => {
    assert.equal(scoreBand(null).key, 'unknown');
    assert.equal(scoreBand(undefined).key, 'unknown');
    assert.equal(scoreBand(NaN).key, 'unknown');
  });

  it('label and color are always derived from the same threshold set', () => {
    // Regression for Task #187 review: previously RejectedAssetTable used
    // 70/45 thresholds for color while label used 80/65/40 — leading to
    // "GOOD" badges next to yellow bars. Both must come from this single
    // source of truth.
    const cases: Array<[number, string, string]> = [
      [9.6,  'AVOID',  '#F87171'],
      [40,   'MIXED',  '#FBBF24'],
      [65,   'GOOD',   '#4ADE80'],
      [80,   'STRONG', '#34D399'],
    ];
    for (const [s, label, color] of cases) {
      const b = scoreBand(s);
      assert.equal(b.label, label);
      assert.equal(b.color, color);
    }
  });
});

describe('scorePct clamping (Task #187)', () => {
  it('clamps to [0, 100] and treats null/NaN as 0', () => {
    assert.equal(scorePct(-10), 0);
    assert.equal(scorePct(0), 0);
    assert.equal(scorePct(50), 50);
    assert.equal(scorePct(100), 100);
    assert.equal(scorePct(150), 100);
    assert.equal(scorePct(null), 0);
    assert.equal(scorePct(NaN), 0);
  });

  it('renders the canonical 9.6/40-of-120 fixture as ~10% red bar with AVOID label', () => {
    // The exact scenario from the bug report: alpha_score 9.6 with 4 rules
    // matched out of 8 (40 of 120 nominal pts). The bar must reflect the
    // robust score (~10% wide, red), not the rule-pass-rate (~33%).
    const score = 9.6;
    const band = scoreBand(score);
    const pct = scorePct(score);
    assert.equal(band.label, 'AVOID');
    assert.equal(band.color, '#F87171');
    assert.equal(pct, 9.6);
    // Sanity: the rule-pass ratio (33.3%) must NOT be what we render.
    assert.notEqual(pct, 33.33);
  });
});
