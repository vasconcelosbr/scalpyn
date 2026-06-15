-- =============================================================================
-- Market Skills Engine — Database Migrations
-- Sprint 1: Regime History
-- Sprint 2: Skill Profiles + Regime-Skill Mapping
-- Sprint 3: Shadow Trades tagging
-- Sprint 4: Backtest Results
-- =============================================================================

-- ── Sprint 1: Regime History ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS regime_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    regime VARCHAR(30) NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    source VARCHAR(20) NOT NULL DEFAULT 'macro',  -- macro | per_asset | hybrid
    indicators_snapshot JSONB,
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_regime_history_detected
    ON regime_history (detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_regime_history_regime
    ON regime_history (regime, detected_at DESC);


-- ── Sprint 2: Skill Profiles ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS skill_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    skill_key VARCHAR(50) NOT NULL,         -- mean_reversion, trend_following, etc.
    name VARCHAR(100) NOT NULL,
    description TEXT,
    config JSONB NOT NULL DEFAULT '{}',     -- scoring rules, thresholds, block_rules
    regime_affinity JSONB DEFAULT '[]',     -- list of regimes this skill is good for
    is_active BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,       -- system template vs user-customized
    performance_history JSONB DEFAULT '{}', -- {regime: {ev, wr, n, last_updated}}
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_skill_profiles_user
    ON skill_profiles (user_id, is_active);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_profiles_user_key
    ON skill_profiles (user_id, skill_key) WHERE is_active = true;


-- ── Sprint 2: Regime-Skill Mapping ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS regime_skill_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    regime VARCHAR(30) NOT NULL,
    primary_skill_id UUID REFERENCES skill_profiles(id) ON DELETE SET NULL,
    secondary_skill_id UUID REFERENCES skill_profiles(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, regime)
);


-- ── Sprint 3: Tag shadow trades with skill/regime ───────────────────────────

ALTER TABLE shadow_trades ADD COLUMN IF NOT EXISTS skill_used VARCHAR(50);
ALTER TABLE shadow_trades ADD COLUMN IF NOT EXISTS regime_at_entry VARCHAR(30);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_skill
    ON shadow_trades (skill_used) WHERE skill_used IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_shadow_trades_regime_entry
    ON shadow_trades (regime_at_entry) WHERE regime_at_entry IS NOT NULL;


-- ── Sprint 4: Backtest Results ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS backtest_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    profile_id UUID,
    days INT NOT NULL,
    strategy_a VARCHAR(50) DEFAULT 'current_rules',
    strategy_b VARCHAR(50) DEFAULT 'market_skills',
    results JSONB NOT NULL,  -- full comparison report
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_results_user
    ON backtest_results (user_id, created_at DESC);
