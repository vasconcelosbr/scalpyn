"""Baseline migration — full production schema as of 2026-06-25.

Replaces the 112-migration legacy chain that could not rebuild from zero.
Generated from pg_catalog extraction of production (sha256 9293892c…).

Revision ID: 000_baseline_prod_schema
Revises: 
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = '000_baseline_prod_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extensions
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS pgcrypto'))
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS pg_stat_statements'))

    # Sequences
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS decisions_log_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS exchange_executions_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS indicator_snapshots_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS ml_experiment_labels_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS ml_experiment_results_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS position_lifecycle_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS reconciled_gate_trades_id_seq'))
    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS scalpyndata_id_seq'))

    # Tables
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_provider_keys (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          provider character varying(50) NOT NULL,
          api_key_encrypted bytea NOT NULL,
          api_secret_encrypted bytea,
          key_hint character varying(20),
          label character varying(100),
          is_active boolean NOT NULL,
          is_validated boolean NOT NULL,
          last_used_at timestamp with time zone,
          last_tested_at timestamp with time zone,
          test_status character varying(20),
          test_error text,
          monthly_token_limit bigint,
          tokens_used_month bigint NOT NULL,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_skills (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          name character varying(120) NOT NULL,
          description text,
          role_key character varying(60),
          prompt_text text NOT NULL,
          is_active boolean NOT NULL,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS algorithm_forward_validations (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          suggestion_id uuid,
          model_id uuid,
          profile_id uuid NOT NULL,
          stage character varying(40) NOT NULL DEFAULT 'discovery'::character varying,
          validation_status character varying(40) NOT NULL DEFAULT 'exploratory_only'::character varying,
          metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          human_approved_by uuid,
          human_approved_at timestamp with time zone,
          rollback_payload jsonb,
          blocked_reason text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS alpha_scores (
          "time" timestamp with time zone NOT NULL,
          symbol character varying(20) NOT NULL,
          score numeric(5,2) NOT NULL,
          liquidity_score numeric(5,2),
          market_structure_score numeric(5,2),
          momentum_score numeric(5,2),
          signal_score numeric(5,2),
          components_json jsonb,
          alpha_score_v2 double precision,
          confidence_metrics jsonb,
          scoring_version character varying(20) DEFAULT 'v1'::character varying
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS asset_traces (
          id uuid NOT NULL,
          symbol character varying(50) NOT NULL,
          market_data_json jsonb,
          indicators_json jsonb,
          conditions_json jsonb,
          decision character varying(20),
          score double precision,
          strategy character varying(20),
          trace_id character varying(64),
          created_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS autopilot_audit_logs (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          profile_id uuid,
          action character varying(80) NOT NULL,
          reason text,
          regime character varying(30),
          perf_snapshot jsonb,
          config_before jsonb,
          config_after jsonb,
          version_id uuid,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          user_id uuid,
          reason_code character varying(80),
          target_config character varying(80),
          target_section character varying(80),
          performance_window jsonb,
          evidence_count integer,
          diff_json jsonb,
          mutation_applied boolean NOT NULL DEFAULT false,
          trigger_source character varying(40),
          celery_task_id character varying(255),
          profile_name character varying(255)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS autopilot_autonomy_policies (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          maximum_level integer NOT NULL DEFAULT 2,
          impact_limit_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          cooldown_seconds integer NOT NULL DEFAULT 0,
          max_changes_per_day integer NOT NULL DEFAULT 0,
          risk_budget_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          post_change_monitoring boolean NOT NULL DEFAULT true,
          auto_rollback_enabled boolean NOT NULL DEFAULT false,
          updated_by uuid,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS backoffice_alerts (
          id uuid NOT NULL,
          alert_type character varying(20) NOT NULL,
          category character varying(50),
          message text NOT NULL,
          details_json jsonb,
          status character varying(20),
          acknowledged_by uuid,
          acknowledged_at timestamp with time zone,
          resolved_at timestamp with time zone,
          created_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS config_audit_log (
          id uuid NOT NULL,
          config_id uuid,
          changed_by uuid,
          previous_json jsonb,
          new_json jsonb NOT NULL,
          change_description text,
          changed_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS config_profiles (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          pool_id uuid,
          config_type character varying(50) NOT NULL,
          config_json jsonb NOT NULL,
          is_active boolean,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS custom_watchlists (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          name character varying(255) NOT NULL,
          description text,
          symbols jsonb NOT NULL,
          is_active boolean,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS decisions_log (
          id bigint NOT NULL DEFAULT nextval('decisions_log_id_seq'::regclass),
          symbol character varying(20) NOT NULL,
          strategy character varying(50) NOT NULL,
          timeframe character varying(10),
          score double precision,
          decision character varying(10) NOT NULL,
          l1_pass boolean,
          l2_pass boolean,
          l3_pass boolean,
          reasons jsonb,
          metrics jsonb,
          latency_ms integer,
          direction character varying(10),
          event_type character varying(40),
          processed boolean NOT NULL,
          user_id uuid,
          created_at timestamp with time zone,
          trade_executed boolean,
          execution_type character varying(10),
          execution_entry_price double precision,
          execution_entry_time timestamp with time zone,
          outcome character varying(20),
          pnl_pct double precision,
          holding_seconds integer,
          profile_id uuid,
          profile_name character varying(255),
          profile_version timestamp with time zone,
          ranking_id uuid,
          model_id uuid,
          model_version character varying,
          model_lane character varying,
          probability double precision,
          threshold_used double precision,
          score_status character varying,
          gate_action character varying,
          reason_codes jsonb,
          orchestrator_payload jsonb,
          ml_gate_enabled boolean NOT NULL DEFAULT false
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS exchange_connections (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          exchange_name character varying(50) NOT NULL,
          api_key_encrypted bytea NOT NULL,
          api_secret_encrypted bytea NOT NULL,
          is_active boolean,
          execution_priority integer,
          last_connected_at timestamp with time zone,
          connection_status character varying(20),
          created_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS exchange_executions (
          id bigint NOT NULL DEFAULT nextval('exchange_executions_id_seq'::regclass),
          user_id uuid,
          exchange character varying(20) NOT NULL,
          market_type character varying(10) NOT NULL,
          trade_id character varying(64) NOT NULL,
          order_id character varying(64),
          symbol character varying(40) NOT NULL,
          side character varying(10) NOT NULL,
          role character varying(10),
          price numeric(28,12) NOT NULL,
          quantity numeric(28,12) NOT NULL,
          quote_quantity numeric(28,8),
          fee numeric(28,12),
          fee_currency character varying(20),
          executed_at timestamp with time zone NOT NULL,
          ingested_at timestamp with time zone NOT NULL,
          raw_payload jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS funding_rates (
          "time" timestamp with time zone NOT NULL,
          symbol character varying(20) NOT NULL,
          exchange character varying(50) NOT NULL,
          rate numeric(10,6)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS indicator_snapshots (
          id integer NOT NULL DEFAULT nextval('indicator_snapshots_id_seq'::regclass),
          symbol character varying(20) NOT NULL,
          "timestamp" timestamp with time zone NOT NULL,
          indicators_json jsonb NOT NULL,
          global_confidence numeric(5,4) NOT NULL,
          valid_indicators integer NOT NULL,
          total_indicators integer NOT NULL,
          validation_passed boolean NOT NULL,
          validation_errors jsonb,
          score numeric(10,2),
          score_confidence numeric(5,4),
          can_trade boolean NOT NULL
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS indicators (
          "time" timestamp with time zone NOT NULL,
          symbol character varying(20) NOT NULL,
          timeframe character varying(10) NOT NULL,
          market_type character varying(10) NOT NULL DEFAULT 'spot'::character varying,
          indicators_json jsonb NOT NULL,
          scheduler_group character varying(20)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS label_lab_runs (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          label_version character varying NOT NULL,
          target_window_seconds integer NOT NULL,
          source_filter character varying,
          status character varying NOT NULL,
          reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
          thresholds jsonb NOT NULL DEFAULT '{}'::jsonb,
          metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
          by_source jsonb NOT NULL DEFAULT '{}'::jsonb,
          triggered_by character varying,
          evaluated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS market_metadata (
          symbol character varying(20) NOT NULL,
          name character varying(255),
          market_cap numeric(20,2),
          volume_24h numeric(20,2),
          price numeric(20,8),
          price_change_24h numeric(10,4),
          ranking integer,
          spread_pct numeric(10,4),
          orderbook_depth_usdt numeric(20,2),
          last_updated timestamp with time zone,
          volume_24h_updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_experiment_features (
          shadow_trade_id uuid NOT NULL,
          symbol text NOT NULL,
          signal_at timestamp with time zone NOT NULL,
          features_json jsonb NOT NULL,
          derived_json jsonb,
          n_ohlcv_candles integer,
          run_at timestamp with time zone DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_experiment_labels (
          id bigint NOT NULL DEFAULT nextval('ml_experiment_labels_id_seq'::regclass),
          shadow_trade_id uuid NOT NULL,
          symbol text NOT NULL,
          signal_at timestamp with time zone NOT NULL,
          entry_candle_time timestamp with time zone,
          entry_price double precision,
          close_30m double precision,
          close_60m double precision,
          high_30m double precision,
          low_30m double precision,
          future_return_30m_net double precision,
          future_return_60m_net double precision,
          mfe_30m double precision,
          mae_30m double precision,
          cost_total double precision NOT NULL,
          pnl_pct_actual double precision,
          outcome text,
          run_at timestamp with time zone DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_experiment_results (
          id bigint NOT NULL DEFAULT nextval('ml_experiment_results_id_seq'::regclass),
          run_at timestamp with time zone DEFAULT now(),
          phase text NOT NULL,
          model_name text,
          split_label text,
          n_samples integer,
          spearman_ic double precision,
          spearman_p double precision,
          ev_top10 double precision,
          ev_top10_ci_lo double precision,
          ev_top10_ci_hi double precision,
          ev_base double precision,
          pct_positive_top10 double precision,
          go_direcional boolean,
          go_operacional boolean,
          metrics_json jsonb,
          config_json jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_model_registry (
          model_id uuid NOT NULL DEFAULT gen_random_uuid(),
          source_ml_model_id uuid,
          model_type character varying(30) NOT NULL,
          model_version character varying(80) NOT NULL,
          profile_id uuid,
          profile_name character varying(255),
          strategy_skill character varying(80) NOT NULL DEFAULT 'win_fast'::character varying,
          market_regime character varying(80) NOT NULL DEFAULT 'all'::character varying,
          dataset_version character varying(80),
          feature_schema_version character varying(80),
          label_version character varying(80),
          train_start timestamp with time zone,
          train_end timestamp with time zone,
          validation_start timestamp with time zone,
          validation_end timestamp with time zone,
          test_start timestamp with time zone,
          test_end timestamp with time zone,
          metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          threshold numeric,
          status character varying(30) NOT NULL DEFAULT 'candidate'::character varying,
          promoted_at timestamp with time zone,
          promoted_by uuid,
          rejection_reason text,
          artifact_path text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_models (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          version character varying(64) NOT NULL,
          status character varying(32) NOT NULL DEFAULT 'inactive'::character varying,
          hyperparams jsonb,
          train_samples integer,
          val_samples integer,
          test_samples integer,
          precision_score double precision,
          recall_score double precision,
          f1_score double precision,
          roc_auc double precision,
          win_fast_capture_rate double precision,
          false_positive_rate double precision,
          train_from timestamp with time zone,
          train_to timestamp with time zone,
          model_path text,
          decision_threshold double precision,
          activated_at timestamp with time zone,
          retired_at timestamp with time zone,
          notes text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          model_blob bytea,
          ev_score double precision,
          comparison_vs_previous jsonb,
          feature_columns_json jsonb,
          feature_columns_hash character varying(64),
          feature_count integer,
          feature_schema_version character varying(64),
          dataset_query_cutoff timestamp with time zone,
          profile_id uuid,
          profile_version timestamp with time zone,
          model_scope character varying(20) NOT NULL DEFAULT 'global'::character varying,
          training_scope character varying(32),
          dataset_hash character varying(64),
          query_hash character varying(64),
          source_filter character varying(32),
          label_version character varying(50),
          dataset_contract_id character varying(100),
          model_lane character varying(30),
          metrics_json jsonb,
          target_window_seconds integer
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_opportunity_rankings (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          run_id uuid NOT NULL,
          symbol character varying NOT NULL,
          profile_id uuid,
          watchlist_id uuid,
          decision_id bigint,
          model_lane character varying,
          model_id uuid,
          model_version character varying,
          dataset_contract_id character varying,
          promotion_gate_status character varying,
          win_fast_probability double precision,
          p_l1_win double precision,
          p_l3_profile_win double precision,
          final_priority_score double precision,
          rank_position integer,
          score_status character varying NOT NULL DEFAULT 'SKIPPED'::character varying,
          reason_code character varying,
          source character varying NOT NULL,
          features_snapshot jsonb,
          ranked_at timestamp with time zone NOT NULL DEFAULT now(),
          threshold_used double precision,
          gate_action character varying,
          used_by_gate boolean NOT NULL DEFAULT false,
          rank_percentile double precision,
          l1_ranker_mode character varying,
          selected_by_l1_ranker boolean,
          reason_codes jsonb,
          orchestrator_payload jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_predictions (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          model_id uuid,
          decision_id integer,
          shadow_trade_id uuid,
          symbol character varying NOT NULL,
          win_fast_probability double precision,
          model_approved boolean NOT NULL DEFAULT false,
          threshold_used double precision,
          scored_at timestamp with time zone NOT NULL DEFAULT now(),
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          model_lane character varying,
          reason_code character varying,
          score_status character varying NOT NULL DEFAULT 'SKIPPED'::character varying,
          promotion_gate_status character varying,
          gate_payload jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS notification_settings (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          slack_webhook_url text,
          slack_enabled boolean,
          push_enabled boolean,
          email_enabled boolean,
          notify_on_buy boolean,
          notify_on_sell boolean,
          notify_on_stop_loss boolean,
          notify_on_take_profit boolean,
          notify_on_circuit_breaker boolean,
          daily_summary_enabled boolean,
          daily_summary_time time without time zone,
          created_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv (
          "time" timestamp with time zone NOT NULL,
          symbol character varying(20) NOT NULL,
          exchange character varying(50) NOT NULL,
          timeframe character varying(10) NOT NULL,
          market_type character varying(10) NOT NULL DEFAULT 'spot'::character varying,
          open numeric(20,8),
          high numeric(20,8),
          low numeric(20,8),
          close numeric(20,8),
          volume numeric(20,4),
          quote_volume numeric(20,4)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS opportunity_snapshots (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          symbol character varying(30) NOT NULL,
          watchlist_id uuid,
          execution_id character varying(64),
          source character varying(30) NOT NULL DEFAULT 'L3_GATE'::character varying,
          timeframe character varying(10),
          price numeric,
          features_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          profiles_evaluated uuid[],
          profiles_approved uuid[],
          profiles_rejected uuid[],
          rejection_reasons jsonb,
          active_profiles_result_json jsonb,
          future_outcome character varying(20),
          future_pnl_pct numeric,
          future_time_to_tp_seconds integer,
          future_time_to_sl_seconds integer,
          future_mae_pct numeric,
          future_mfe_pct numeric,
          future_evaluated_at timestamp with time zone,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS orders (
          id uuid NOT NULL,
          trade_id uuid,
          user_id uuid,
          exchange_order_id character varying(255),
          symbol character varying(20) NOT NULL,
          side character varying(10) NOT NULL,
          order_type character varying(20) NOT NULL,
          price numeric(20,8),
          quantity numeric(20,8) NOT NULL,
          filled_quantity numeric(20,8),
          status character varying(20),
          exchange character varying(50) NOT NULL,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pipeline_metrics (
          id uuid NOT NULL,
          discovered integer,
          filtered integer,
          scored integer,
          signals_count integer,
          executed integer,
          approved integer,
          rejected integer,
          latency_ms double precision,
          error_count integer,
          strategy character varying(20),
          trace_id character varying(64),
          created_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pipeline_watchlist_assets (
          id uuid NOT NULL,
          watchlist_id uuid NOT NULL,
          symbol character varying(20) NOT NULL,
          current_price numeric(20,8),
          price_change_24h numeric(8,4),
          volume_24h numeric(20,2),
          market_cap numeric(20,2),
          alpha_score numeric(5,2),
          score_long numeric(5,2),
          score_short numeric(5,2),
          confidence_score numeric(5,2),
          futures_direction character varying(10),
          entry_long_blocked boolean NOT NULL,
          entry_short_blocked boolean NOT NULL,
          entered_at timestamp with time zone,
          refreshed_at timestamp with time zone,
          previous_level character varying(10),
          level_change_at timestamp with time zone,
          level_direction character varying(4),
          analysis_snapshot jsonb,
          execution_id uuid,
          engine_tag character varying(16)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pipeline_watchlist_rejections (
          id uuid NOT NULL,
          watchlist_id uuid NOT NULL,
          user_id uuid NOT NULL,
          profile_id uuid,
          symbol character varying(20) NOT NULL,
          stage character varying(10) NOT NULL,
          failed_type character varying(20) NOT NULL,
          failed_indicator character varying(255) NOT NULL,
          condition_text text NOT NULL,
          current_value jsonb,
          expected_value character varying(255),
          evaluation_trace jsonb,
          analysis_snapshot jsonb,
          execution_id uuid,
          recorded_at timestamp with time zone,
          engine_tag character varying(16)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pipeline_watchlists (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          name character varying(100) NOT NULL,
          level character varying(10) NOT NULL,
          market_mode character varying(10) NOT NULL,
          source_pool_id uuid,
          source_watchlist_id uuid,
          profile_id uuid,
          auto_refresh boolean,
          filters_json jsonb,
          last_scanned_at timestamp with time zone,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pool_coins (
          id uuid NOT NULL,
          pool_id uuid NOT NULL,
          symbol character varying(20) NOT NULL,
          market_type character varying(10),
          is_active boolean,
          is_approved boolean,
          is_tradable boolean,
          added_at timestamp with time zone,
          origin character varying(20),
          discovered_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pools (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          name character varying(255) NOT NULL,
          description text,
          is_active boolean,
          mode character varying(20),
          market_type character varying(20),
          profile_id uuid,
          overrides jsonb,
          autopilot_enabled boolean,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS position_lifecycle (
          id bigint NOT NULL DEFAULT nextval('position_lifecycle_id_seq'::regclass),
          user_id uuid,
          exchange character varying(20) NOT NULL,
          symbol character varying(40) NOT NULL,
          market_type character varying(10) NOT NULL,
          direction character varying(10) NOT NULL,
          opened_at timestamp with time zone NOT NULL,
          closed_at timestamp with time zone,
          holding_seconds integer,
          qty numeric(28,12) NOT NULL,
          avg_entry numeric(28,12) NOT NULL,
          avg_exit numeric(28,12),
          invested_usdt numeric(28,8) NOT NULL,
          final_usdt numeric(28,8),
          fees_total numeric(28,8) NOT NULL,
          pnl_usdt numeric(28,8),
          pnl_pct numeric(14,6),
          roi numeric(14,6),
          status character varying(20) NOT NULL,
          n_fills_in integer NOT NULL,
          n_fills_out integer NOT NULL,
          entry_trade_ids jsonb,
          exit_trade_ids jsonb,
          slippage_estimate numeric(14,6),
          maker_taker_ratio numeric(6,4),
          data_quality character varying(10) NOT NULL,
          created_at timestamp with time zone NOT NULL,
          updated_at timestamp with time zone NOT NULL
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS production_champion_control (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          profile_id uuid NOT NULL,
          market_regime character varying(80) NOT NULL DEFAULT 'all'::character varying,
          strategy_skill character varying(80) NOT NULL DEFAULT 'win_fast'::character varying,
          active_model_id uuid NOT NULL,
          active_model_type character varying(30) NOT NULL,
          active_threshold numeric NOT NULL,
          activated_at timestamp with time zone NOT NULL DEFAULT now(),
          activated_by uuid,
          previous_model_id uuid,
          rollback_available boolean NOT NULL DEFAULT true
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_audit_log (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          profile_id uuid NOT NULL,
          changed_by uuid,
          change_source character varying(50),
          change_description text,
          previous_config jsonb,
          new_config jsonb,
          previous_profile_version timestamp with time zone,
          new_profile_version timestamp with time zone,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_indicator_stats (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          run_id uuid NOT NULL,
          indicator character varying(60) NOT NULL,
          operator character varying(10),
          range_min numeric,
          range_max numeric,
          value_text character varying(60),
          bucket_label character varying(100) NOT NULL,
          total_cases integer DEFAULT 0,
          wins integer DEFAULT 0,
          losses integer DEFAULT 0,
          timeouts integer DEFAULT 0,
          win_rate numeric,
          loss_rate numeric,
          avg_pnl_pct numeric,
          avg_holding_seconds numeric,
          avg_winner_holding_seconds numeric,
          avg_mae_pct numeric,
          avg_mfe_pct numeric,
          tp_15m_rate numeric,
          tp_30m_rate numeric,
          tp_60m_rate numeric,
          lift_vs_base numeric,
          pnl_lift_vs_base numeric,
          winner_presence_pct numeric,
          loser_presence_pct numeric,
          confidence_score numeric,
          confidence_level character varying(20),
          role_detected character varying(30),
          source_profiles jsonb,
          evidence_json jsonb,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          source_profile_ids jsonb,
          validation_status character varying(40) NOT NULL DEFAULT 'exploratory_only'::character varying,
          actionability_status character varying(40) NOT NULL DEFAULT 'exploratory_only'::character varying,
          target_section character varying(80)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_audit_log (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          run_id uuid,
          suggestion_id uuid,
          combination_id uuid,
          event_type character varying(60) NOT NULL,
          event_description text,
          payload_json jsonb,
          result_json jsonb,
          model_provider character varying(30),
          model_name character varying(60),
          prompt_text text,
          response_text text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          before_json jsonb,
          after_json jsonb,
          diff_json jsonb,
          actor_user_id uuid,
          profile_name character varying(200),
          source_run_id uuid
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_associations (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          candidate_id uuid,
          watchlist_id uuid NOT NULL,
          previous_profile_id uuid,
          new_profile_id uuid,
          event_type character varying(30) NOT NULL,
          is_active boolean NOT NULL DEFAULT true,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_audit (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          actor_user_id uuid,
          cycle_id uuid,
          candidate_id uuid,
          profile_id uuid,
          profile_version timestamp with time zone,
          watchlist_id uuid,
          combination_id uuid,
          suggestion_id uuid,
          event_type character varying(80) NOT NULL,
          input_metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          thresholds_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          decision character varying(80),
          reason text,
          result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_candidates (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          cycle_id uuid,
          profile_id uuid NOT NULL,
          origin_profile_id uuid,
          previous_profile_id uuid,
          shadow_watchlist_id uuid,
          target_watchlist_id uuid,
          source_combination_id uuid,
          source_suggestion_id uuid,
          state character varying(40) NOT NULL,
          canonical_signature character varying(64) NOT NULL,
          canonical_rules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          version_number integer NOT NULL DEFAULT 1,
          shadow_started_at timestamp with time zone NOT NULL DEFAULT now(),
          review_after timestamp with time zone,
          observed_trades integer NOT NULL DEFAULT 0,
          observed_win_rate numeric(10,6),
          observed_avg_pnl_pct numeric(12,8),
          promotion_win_rate numeric(10,6),
          promotion_avg_pnl_pct numeric(12,8),
          promoted_at timestamp with time zone,
          rejected_at timestamp with time zone,
          rollback_at timestamp with time zone,
          decision_reason text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now(),
          approval_status character varying(30) NOT NULL DEFAULT 'pending'::character varying,
          approval_required boolean NOT NULL DEFAULT true,
          approved_by uuid,
          approved_at timestamp with time zone,
          approval_reason text,
          approval_source character varying(80),
          approval_snapshot_json jsonb,
          promotion_blocked_reason text,
          rollback_payload jsonb,
          live_activation_attempted_at timestamp with time zone,
          live_activated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_compensations (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          cycle_id uuid,
          candidate_id uuid,
          operation character varying(80) NOT NULL,
          payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          status character varying(30) NOT NULL DEFAULT 'PENDING'::character varying,
          last_error text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          resolved_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_cycles (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          window_start timestamp with time zone NOT NULL,
          idempotency_key character varying(180) NOT NULL,
          status character varying(40) NOT NULL,
          checkpoint character varying(80),
          analysis_run_id uuid,
          metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          errors_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          started_at timestamp with time zone NOT NULL DEFAULT now(),
          completed_at timestamp with time zone,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_reports (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          cycle_id uuid NOT NULL,
          report_json jsonb NOT NULL,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_settings (
          user_id uuid NOT NULL,
          enabled boolean NOT NULL DEFAULT false,
          settings_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          enabled_at timestamp with time zone,
          disabled_at timestamp with time zone,
          last_cycle_at timestamp with time zone,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_loss_families (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          canonical_signature character varying(64) NOT NULL,
          canonical_rules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          rejection_reason text NOT NULL,
          blocked_at timestamp with time zone NOT NULL,
          blocked_until timestamp with time zone NOT NULL,
          candidate_id uuid,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_runs (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          run_at timestamp with time zone NOT NULL DEFAULT now(),
          lookback_days integer NOT NULL,
          min_closed_trades integer NOT NULL DEFAULT 30,
          discovery_start_at timestamp with time zone,
          discovery_end_at timestamp with time zone,
          validation_start_at timestamp with time zone,
          validation_end_at timestamp with time zone,
          profiles_analyzed jsonb,
          total_profiles integer DEFAULT 0,
          total_shadow_trades integer DEFAULT 0,
          total_closed_trades integer DEFAULT 0,
          total_opportunity_snapshots integer DEFAULT 0,
          base_win_rate numeric,
          base_avg_pnl_pct numeric,
          base_tp_30m_rate numeric,
          status character varying(30) DEFAULT 'running'::character varying,
          engine_version character varying(30),
          settings_json jsonb,
          notes text,
          error_message text,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now(),
          trigger_source character varying(20)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_metrics (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          profile_id uuid NOT NULL,
          profile_name character varying(255),
          source character varying(30),
          period_start timestamp with time zone,
          period_end timestamp with time zone,
          total_trades integer NOT NULL DEFAULT 0,
          closed_trades integer NOT NULL DEFAULT 0,
          open_trades integer NOT NULL DEFAULT 0,
          wins integer NOT NULL DEFAULT 0,
          losses integer NOT NULL DEFAULT 0,
          timeouts integer NOT NULL DEFAULT 0,
          win_rate numeric(8,4),
          pnl_total_pct numeric(12,4),
          avg_pnl_pct numeric(8,4),
          avg_holding_seconds numeric(12,2),
          avg_winner_holding_seconds numeric(12,2),
          avg_mae_pct numeric(8,4),
          avg_mfe_pct numeric(8,4),
          tp_15m_rate numeric(8,4),
          tp_30m_rate numeric(8,4),
          tp_60m_rate numeric(8,4),
          confidence_level character varying(20),
          extra_json jsonb,
          calculated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_rule_combinations (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          run_id uuid NOT NULL,
          combination_hash character varying(64) NOT NULL,
          combination_type character varying(30) NOT NULL,
          setup_family character varying(30),
          suggested_name character varying(120),
          rules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
          signals_json jsonb,
          scoring_rules_json jsonb,
          block_rules_json jsonb,
          required_master_scoring_rules_json jsonb,
          source_profiles jsonb,
          total_cases integer DEFAULT 0,
          wins integer DEFAULT 0,
          losses integer DEFAULT 0,
          timeouts integer DEFAULT 0,
          win_rate numeric,
          loss_rate numeric,
          avg_pnl_pct numeric,
          avg_holding_seconds numeric,
          avg_winner_holding_seconds numeric,
          avg_mae_pct numeric,
          avg_mfe_pct numeric,
          tp_15m_rate numeric,
          tp_30m_rate numeric,
          tp_60m_rate numeric,
          lift_vs_base numeric,
          support numeric,
          confidence numeric,
          rule_lift numeric,
          leverage numeric,
          conviction numeric,
          champion_score numeric,
          confidence_level character varying(20),
          discovery_metrics_json jsonb,
          validation_metrics_json jsonb,
          degradation_pct numeric,
          overfit_risk boolean DEFAULT false,
          is_tested_live_shadow boolean DEFAULT false,
          status character varying(30) DEFAULT 'discovered'::character varying,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          source_profile_ids jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_suggestions (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          run_id uuid NOT NULL,
          source_combination_id uuid,
          suggested_profile_name character varying(255) NOT NULL,
          suggested_profile_description text,
          suggested_profile_family character varying(30),
          source_profiles jsonb,
          suggested_config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          suggested_signals_json jsonb,
          suggested_scoring_json jsonb,
          suggested_block_rules_json jsonb,
          required_master_scoring_rules_json jsonb,
          evidence_summary_json jsonb,
          quantitative_explanation text,
          ai_explanation text,
          risk_notes text,
          confidence_score numeric,
          confidence_level character varying(20),
          status character varying(30) DEFAULT 'pending'::character varying,
          created_profile_id uuid,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now(),
          source_type character varying(50),
          source_model_type character varying(30),
          source_model_id uuid,
          source_run_id uuid,
          profile_id uuid,
          profile_name character varying(255),
          source_profile_ids jsonb,
          target_section character varying(80),
          target_field character varying(120),
          current_value jsonb,
          proposed_value jsonb,
          diff_json jsonb,
          confidence numeric,
          lift numeric,
          evidence_count integer,
          expected_impact jsonb,
          risk_level character varying(20),
          validation_status character varying(40),
          actionability_status character varying(40),
          blocked_reason text,
          applied_at timestamp with time zone,
          reverted_at timestamp with time zone,
          reason text,
          rollback_payload jsonb,
          dataset_version character varying(80),
          feature_schema_version character varying(80),
          label_version character varying(80),
          suggestion_hash character varying(64),
          shadow_feedback_status character varying,
          shadow_feedback_json jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_versions (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          profile_id uuid NOT NULL,
          version_number integer NOT NULL,
          config jsonb NOT NULL DEFAULT '{}'::jsonb,
          regime character varying(30),
          ev_at_snapshot numeric(8,4),
          win_rate_at_snapshot numeric(6,4),
          fpr_at_snapshot numeric(6,4),
          n_samples integer,
          mutation_reason text,
          is_active boolean NOT NULL DEFAULT false,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profiles (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          name character varying(255) NOT NULL,
          description text,
          is_active boolean,
          config jsonb NOT NULL,
          profile_role character varying(50),
          pipeline_order character varying(3) NOT NULL,
          pipeline_label character varying(100),
          auto_pilot_enabled boolean,
          auto_pilot_config jsonb NOT NULL,
          preset_ia_last_run timestamp with time zone,
          preset_ia_config jsonb,
          created_at timestamp with time zone,
          updated_at timestamp with time zone,
          profile_type character varying(20) NOT NULL DEFAULT 'STANDARD'::character varying,
          profile_version timestamp with time zone,
          generated_by character varying(100),
          generated_from_suggestion_id uuid,
          is_shadow_only boolean NOT NULL DEFAULT false,
          live_trading_enabled boolean NOT NULL DEFAULT false
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS reconciled_gate_trades (
          id bigint NOT NULL DEFAULT nextval('reconciled_gate_trades_id_seq'::regclass),
          external_id character varying(100) NOT NULL,
          market_type character varying(10) NOT NULL,
          trade_tracking_id uuid,
          processed_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS rule_contribution (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          profile_id uuid,
          rule_hash character varying(64) NOT NULL,
          rule_type character varying(30),
          indicator character varying(60),
          operator character varying(10),
          value_text character varying(60),
          bucket_label character varying(60),
          total_cases integer NOT NULL DEFAULT 0,
          wins integer NOT NULL DEFAULT 0,
          losses integer NOT NULL DEFAULT 0,
          win_rate numeric(8,4),
          avg_pnl_pct numeric(8,4),
          avg_mae_pct numeric(8,4),
          avg_mfe_pct numeric(8,4),
          lift_vs_base numeric(8,4),
          confidence_score numeric(8,4),
          extra_json jsonb,
          calculated_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS scalpyndata (
          id integer NOT NULL DEFAULT nextval('scalpyndata_id_seq'::regclass)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_capture_skips (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          symbol character varying NOT NULL,
          promotion_at timestamp with time zone NOT NULL,
          skip_reason character varying NOT NULL,
          source_path character varying NOT NULL,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_trade_duplicate_audit (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          decision_id bigint NOT NULL,
          member_ids jsonb NOT NULL,
          canonical_id uuid NOT NULL,
          superseded_ids jsonb NOT NULL,
          outcomes jsonb NOT NULL,
          distinct_outcomes_count integer NOT NULL,
          conflict boolean NOT NULL,
          resolution_reason character varying NOT NULL,
          triggered_by character varying,
          detected_at timestamp with time zone NOT NULL DEFAULT now()
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_trades (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          decision_id bigint,
          user_id uuid NOT NULL,
          symbol character varying(20) NOT NULL,
          strategy character varying(50),
          direction character varying(10),
          amount_usdt double precision NOT NULL,
          entry_price double precision,
          entry_timestamp timestamp with time zone,
          tp_price double precision,
          sl_price double precision,
          tp_pct double precision,
          sl_pct double precision,
          timeout_candles integer,
          exit_price double precision,
          exit_timestamp timestamp with time zone,
          outcome character varying(20),
          pnl_pct double precision,
          pnl_usdt double precision,
          holding_seconds integer,
          status character varying(20) NOT NULL,
          skip_reason character varying(50),
          source character varying(20) NOT NULL,
          config_snapshot jsonb,
          features_snapshot jsonb,
          features_snapshot_exit jsonb,
          last_processed_time timestamp with time zone,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now(),
          completed_at timestamp with time zone,
          btc_price_at_entry numeric(18,8),
          btc_change_1h_pct numeric(8,4),
          funding_rate_at_entry numeric(10,6),
          n_concurrent_signals integer,
          min_price_post_entry double precision,
          max_price_post_entry double precision,
          max_drawdown_pct double precision,
          max_profit_pct double precision,
          mae_pct double precision,
          mfe_pct double precision,
          exit_metrics_json jsonb,
          price_after_1h double precision,
          price_after_2h double precision,
          price_after_4h double precision,
          price_after_12h double precision,
          price_after_24h double precision,
          max_profit_after_timeout_pct double precision,
          max_drawdown_after_timeout_pct double precision,
          delayed_tp boolean,
          delayed_tp_hours double precision,
          timeout_post_analysis_done boolean,
          ttt_enabled boolean,
          ttt_tp_pct double precision,
          ttt_timeout_minutes integer,
          ttt_outcome character varying(20),
          ttt_close_reason character varying(30),
          ttt_fast_win_bucket character varying(20),
          ttt_analysis_done boolean,
          elapsed_minutes double precision,
          time_to_tp_minutes double precision,
          profit_velocity double precision,
          profit_velocity_per_hour double precision,
          max_profit_first_15m double precision,
          max_profit_first_30m double precision,
          max_profit_first_60m double precision,
          candles_to_peak integer,
          candles_to_first_positive integer,
          mae_at timestamp with time zone,
          mfe_at timestamp with time zone,
          barrier_touched character varying(20),
          barrier_touched_at timestamp with time zone,
          intrabar_convention character varying(20),
          final_return_pct double precision,
          net_return_pct double precision,
          fee_roundtrip_pct_applied double precision,
          barrier_mode character varying(20),
          tp_pct_applied double precision,
          sl_pct_applied double precision,
          atr_pct_at_entry double precision,
          profile_id uuid,
          profile_version timestamp with time zone,
          profile_name character varying(255),
          strategy_type character varying(64),
          rules_snapshot jsonb,
          profile_status_at_entry character varying(32),
          final_priority_score double precision,
          ml_probability double precision,
          ml_model_id uuid,
          orchestrator_payload jsonb,
          watchlist_id uuid,
          watchlist_name character varying(150),
          watchlist_level character varying(10),
          source_watchlist_id uuid,
          lineage_confidence character varying(30),
          lineage_source character varying(50),
          lineage_resolved_at timestamp with time zone,
          model_lane character varying,
          ranking_id uuid,
          superseded_by_id uuid,
          model_version character varying,
          threshold_used double precision,
          score_status character varying,
          gate_action character varying,
          reason_codes jsonb,
          ml_gate_enabled boolean NOT NULL DEFAULT false
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trade_decisions (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          trace_id character varying(64) NOT NULL,
          user_id uuid,
          pool_id uuid,
          symbol character varying(20) NOT NULL,
          market_type character varying(10) NOT NULL,
          exchange character varying(50),
          decided_at timestamp with time zone NOT NULL DEFAULT now(),
          status character varying(20) NOT NULL,
          stage character varying(10) NOT NULL,
          reason text,
          blocking_rule character varying(255),
          rule_details jsonb,
          rules_matched jsonb,
          rules_failed jsonb,
          rules_skipped jsonb,
          score_breakdown jsonb,
          indicators_snapshot jsonb,
          latency_ms jsonb,
          trade_id uuid
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trade_simulations (
          id uuid NOT NULL,
          symbol character varying(20) NOT NULL,
          timestamp_entry timestamp with time zone NOT NULL,
          entry_price numeric(20,8) NOT NULL,
          tp_price numeric(20,8) NOT NULL,
          sl_price numeric(20,8) NOT NULL,
          exit_price numeric(20,8),
          exit_timestamp timestamp with time zone,
          result character varying(10) NOT NULL,
          time_to_result integer,
          direction character varying(10) NOT NULL,
          is_simulated boolean,
          source character varying(30),
          decision_type character varying(10) NOT NULL,
          decision_id bigint,
          features_snapshot jsonb,
          config_snapshot jsonb,
          created_at timestamp with time zone,
          mae_at timestamp with time zone,
          mfe_at timestamp with time zone,
          barrier_touched character varying(20),
          barrier_touched_at timestamp with time zone,
          intrabar_convention character varying(20),
          final_return_pct double precision,
          net_return_pct double precision,
          fee_roundtrip_pct_applied double precision,
          barrier_mode character varying(20),
          tp_pct_applied double precision,
          sl_pct_applied double precision,
          atr_pct_at_entry double precision,
          min_price_post_entry double precision,
          max_price_post_entry double precision,
          max_drawdown_pct double precision,
          max_profit_pct double precision,
          mae_pct double precision,
          mfe_pct double precision,
          exit_metrics_json jsonb
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trade_tracking (
          id uuid NOT NULL,
          decision_id bigint,
          symbol character varying(20) NOT NULL,
          market_type character varying(10) NOT NULL,
          position_side character varying(10) NOT NULL,
          is_simulated boolean NOT NULL,
          entry_price numeric(20,8) NOT NULL,
          entry_time timestamp with time zone NOT NULL,
          real_entry_price numeric(20,8),
          target_price numeric(20,8),
          stop_price numeric(20,8),
          status character varying(20) NOT NULL,
          external_id character varying(100),
          exit_price numeric(20,8),
          exit_time timestamp with time zone,
          outcome character varying(20),
          pnl_pct numeric(10,4),
          holding_seconds integer,
          exit_price_source character varying(20),
          exit_metrics_json jsonb,
          created_at timestamp with time zone NOT NULL
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trades (
          id uuid NOT NULL,
          user_id uuid,
          pool_id uuid,
          symbol character varying(20) NOT NULL,
          side character varying(10) NOT NULL,
          direction character varying(10),
          market_type character varying(10) NOT NULL,
          exchange character varying(50) NOT NULL,
          entry_price numeric(20,8) NOT NULL,
          exit_price numeric(20,8),
          quantity numeric(20,8) NOT NULL,
          invested_value numeric(20,2) NOT NULL,
          profit_loss numeric(20,2),
          profit_loss_pct numeric(10,4),
          fee numeric(20,8),
          status character varying(20),
          alpha_score_at_entry numeric(5,2),
          indicators_at_entry jsonb,
          take_profit_price numeric(20,8),
          stop_loss_price numeric(20,8),
          entry_at timestamp with time zone,
          exit_at timestamp with time zone,
          holding_seconds integer,
          exchange_order_id character varying(100),
          source character varying(30)
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
          id uuid NOT NULL,
          email character varying(255) NOT NULL,
          password_hash character varying(255) NOT NULL,
          name character varying(255) NOT NULL,
          role character varying(50),
          mfa_enabled boolean,
          mfa_secret character varying(255),
          is_active boolean,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS watchlist_profiles (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          watchlist_id character varying(100) NOT NULL,
          profile_type character varying(10) NOT NULL,
          profile_id uuid,
          is_enabled boolean,
          created_at timestamp with time zone
        );
    """))

    # Primary key and unique constraints
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ai_skill_user_name' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_skills ADD CONSTRAINT uq_ai_skill_user_name UNIQUE (user_id, name);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_autonomy_policies_user_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_autonomy_policies ADD CONSTRAINT autopilot_autonomy_policies_user_id_key UNIQUE (user_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_exchange_executions_dedup' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_executions ADD CONSTRAINT uq_exchange_executions_dedup UNIQUE (exchange, market_type, trade_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_labels_shadow_trade_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_labels ADD CONSTRAINT ml_experiment_labels_shadow_trade_id_key UNIQUE (shadow_trade_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ohlcv_time_symbol_exchange_timeframe' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ohlcv ADD CONSTRAINT uq_ohlcv_time_symbol_exchange_timeframe UNIQUE ("time", symbol, exchange, timeframe);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_pipeline_asset_watchlist_symbol' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_assets ADD CONSTRAINT uq_pipeline_asset_watchlist_symbol UNIQUE (watchlist_id, symbol);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_production_champion_scope' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT uq_production_champion_scope UNIQUE (profile_id, market_regime, strategy_skill);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_profile_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_profile_id_key UNIQUE (profile_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_idempotency_key_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_idempotency_key_key UNIQUE (idempotency_key);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_cycle_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_cycle_id_key UNIQUE (cycle_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_famil_user_id_canonical_signature_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_famil_user_id_canonical_signature_key UNIQUE (user_id, canonical_signature);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_reconciled_gate_trades_ext_market' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE reconciled_gate_trades ADD CONSTRAINT uq_reconciled_gate_trades_ext_market UNIQUE (external_id, market_type);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_simulation_symbol_entry_direction' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT uq_simulation_symbol_entry_direction UNIQUE (symbol, timestamp_entry, direction);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_exchange_order_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_exchange_order_id_key UNIQUE (exchange_order_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_email_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_provider_keys_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_provider_keys ADD CONSTRAINT ai_provider_keys_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_skills_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_skills ADD CONSTRAINT ai_skills_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alembic_version_pkc' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE alembic_version ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'asset_traces_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE asset_traces ADD CONSTRAINT asset_traces_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_audit_logs_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT autopilot_audit_logs_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_autonomy_policies_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_autonomy_policies ADD CONSTRAINT autopilot_autonomy_policies_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backoffice_alerts_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE backoffice_alerts ADD CONSTRAINT backoffice_alerts_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_audit_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_audit_log ADD CONSTRAINT config_audit_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_profiles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_profiles ADD CONSTRAINT config_profiles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'custom_watchlists_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE custom_watchlists ADD CONSTRAINT custom_watchlists_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT decisions_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'exchange_connections_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_connections ADD CONSTRAINT exchange_connections_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'exchange_executions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_executions ADD CONSTRAINT exchange_executions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'indicator_snapshots_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE indicator_snapshots ADD CONSTRAINT indicator_snapshots_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'label_lab_runs_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE label_lab_runs ADD CONSTRAINT label_lab_runs_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'market_metadata_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE market_metadata ADD CONSTRAINT market_metadata_pkey PRIMARY KEY (symbol);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_features_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_features ADD CONSTRAINT ml_experiment_features_pkey PRIMARY KEY (shadow_trade_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_labels_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_labels ADD CONSTRAINT ml_experiment_labels_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_results_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_results ADD CONSTRAINT ml_experiment_results_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_model_registry_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_model_registry ADD CONSTRAINT ml_model_registry_pkey PRIMARY KEY (model_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_models_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_models ADD CONSTRAINT ml_models_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_opportunity_rankings_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_opportunity_rankings ADD CONSTRAINT ml_opportunity_rankings_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_predictions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_predictions ADD CONSTRAINT ml_predictions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notification_settings_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE notification_settings ADD CONSTRAINT notification_settings_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'opportunity_snapshots_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE opportunity_snapshots ADD CONSTRAINT opportunity_snapshots_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE orders ADD CONSTRAINT orders_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_metrics_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_metrics ADD CONSTRAINT pipeline_metrics_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_assets_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_assets ADD CONSTRAINT pipeline_watchlist_assets_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pool_coins_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pool_coins ADD CONSTRAINT pool_coins_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pools_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pools ADD CONSTRAINT pools_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'position_lifecycle_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE position_lifecycle ADD CONSTRAINT position_lifecycle_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'production_champion_control_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT production_champion_control_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_audit_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_audit_log ADD CONSTRAINT profile_audit_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_indicator_stats_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_indicator_stats ADD CONSTRAINT profile_indicator_stats_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_audit_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_audit_log ADD CONSTRAINT profile_intelligence_audit_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_settings_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_settings ADD CONSTRAINT profile_intelligence_autopilot_settings_pkey PRIMARY KEY (user_id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_families_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_families_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_runs_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_runs ADD CONSTRAINT profile_intelligence_runs_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_metrics_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_metrics ADD CONSTRAINT profile_metrics_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_rule_combinations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_rule_combinations ADD CONSTRAINT profile_rule_combinations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_suggestions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT profile_suggestions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_versions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_versions ADD CONSTRAINT profile_versions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profiles ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reconciled_gate_trades_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE reconciled_gate_trades ADD CONSTRAINT reconciled_gate_trades_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rule_contribution_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE rule_contribution ADD CONSTRAINT rule_contribution_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scalpyndata_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE scalpyndata ADD CONSTRAINT scalpyndata_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_capture_skips_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_capture_skips ADD CONSTRAINT shadow_capture_skips_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trade_duplicate_audit_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trade_duplicate_audit ADD CONSTRAINT shadow_trade_duplicate_audit_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trades_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT shadow_trades_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_simulations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT trade_simulations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_tracking_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_tracking ADD CONSTRAINT trade_tracking_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_profiles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE watchlist_profiles ADD CONSTRAINT watchlist_profiles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$
    """))

    # Foreign key constraints
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_provider_keys_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_provider_keys ADD CONSTRAINT ai_provider_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_skills_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_skills ADD CONSTRAINT ai_skills_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_model_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_model_id_fkey FOREIGN KEY (model_id) REFERENCES ml_model_registry(model_id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_suggestion_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_suggestion_id_fkey FOREIGN KEY (suggestion_id) REFERENCES profile_suggestions(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_audit_logs_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT autopilot_audit_logs_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_audit_logs_version_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT autopilot_audit_logs_version_id_fkey FOREIGN KEY (version_id) REFERENCES profile_versions(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_autopilot_audit_logs_user_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT fk_autopilot_audit_logs_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backoffice_alerts_acknowledged_by_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE backoffice_alerts ADD CONSTRAINT backoffice_alerts_acknowledged_by_fkey FOREIGN KEY (acknowledged_by) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_audit_log_changed_by_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_audit_log ADD CONSTRAINT config_audit_log_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_audit_log_config_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_audit_log ADD CONSTRAINT config_audit_log_config_id_fkey FOREIGN KEY (config_id) REFERENCES config_profiles(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_profiles_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_profiles ADD CONSTRAINT config_profiles_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_profiles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_profiles ADD CONSTRAINT config_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'custom_watchlists_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE custom_watchlists ADD CONSTRAINT custom_watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_log_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT decisions_log_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_log_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT decisions_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_decisions_log_ranking_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT fk_decisions_log_ranking_id FOREIGN KEY (ranking_id) REFERENCES ml_opportunity_rankings(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'exchange_connections_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_connections ADD CONSTRAINT exchange_connections_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_model_registry_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_model_registry ADD CONSTRAINT ml_model_registry_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ml_models_profile' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_models ADD CONSTRAINT fk_ml_models_profile FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notification_settings_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE notification_settings ADD CONSTRAINT notification_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_trade_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE orders ADD CONSTRAINT orders_trade_id_fkey FOREIGN KEY (trade_id) REFERENCES trades(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE orders ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_assets_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_assets ADD CONSTRAINT pipeline_watchlist_assets_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_source_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_source_pool_id_fkey FOREIGN KEY (source_pool_id) REFERENCES pools(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_source_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_source_watchlist_id_fkey FOREIGN KEY (source_watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pool_coins_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pool_coins ADD CONSTRAINT pool_coins_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pools_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pools ADD CONSTRAINT pools_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pools_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pools ADD CONSTRAINT pools_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'production_champion_control_active_model_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT production_champion_control_active_model_id_fkey FOREIGN KEY (active_model_id) REFERENCES ml_model_registry(model_id) ON DELETE RESTRICT;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'production_champion_control_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT production_champion_control_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_audit_log_changed_by_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_audit_log ADD CONSTRAINT profile_audit_log_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_audit_log_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_audit_log ADD CONSTRAINT profile_audit_log_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_indicator_stats_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_indicator_stats ADD CONSTRAINT profile_indicator_stats_run_id_fkey FOREIGN KEY (run_id) REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associa_previous_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associa_previous_profile_id_fkey FOREIGN KEY (previous_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_new_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_new_profile_id_fkey FOREIGN KEY (new_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE RESTRICT;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_actor_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_combination_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_combination_id_fkey FOREIGN KEY (combination_id) REFERENCES profile_rule_combinations(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_suggestion_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_suggestion_id_fkey FOREIGN KEY (suggestion_id) REFERENCES profile_suggestions(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pi_autopilot_candidate_approved_by' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT fk_pi_autopilot_candidate_approved_by FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candi_source_combination_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candi_source_combination_id_fkey FOREIGN KEY (source_combination_id) REFERENCES profile_rule_combinations(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candid_source_suggestion_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candid_source_suggestion_id_fkey FOREIGN KEY (source_suggestion_id) REFERENCES profile_suggestions(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candida_previous_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candida_previous_profile_id_fkey FOREIGN KEY (previous_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candida_shadow_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candida_shadow_watchlist_id_fkey FOREIGN KEY (shadow_watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candida_target_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candida_target_watchlist_id_fkey FOREIGN KEY (target_watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidate_origin_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidate_origin_profile_id_fkey FOREIGN KEY (origin_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE RESTRICT;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_analysis_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_analysis_run_id_fkey FOREIGN KEY (analysis_run_id) REFERENCES profile_intelligence_runs(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_settings_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_settings ADD CONSTRAINT profile_intelligence_autopilot_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_families_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_families_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_families_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_families_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_metrics_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_metrics ADD CONSTRAINT profile_metrics_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_rule_combinations_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_rule_combinations ADD CONSTRAINT profile_rule_combinations_run_id_fkey FOREIGN KEY (run_id) REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_profile_suggestions_profile_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT fk_profile_suggestions_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_suggestions_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT profile_suggestions_run_id_fkey FOREIGN KEY (run_id) REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_suggestions_source_combination_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT profile_suggestions_source_combination_id_fkey FOREIGN KEY (source_combination_id) REFERENCES profile_rule_combinations(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_versions_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_versions ADD CONSTRAINT profile_versions_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profiles ADD CONSTRAINT profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reconciled_gate_trades_trade_tracking_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE reconciled_gate_trades ADD CONSTRAINT reconciled_gate_trades_trade_tracking_id_fkey FOREIGN KEY (trade_tracking_id) REFERENCES trade_tracking(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rule_contribution_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE rule_contribution ADD CONSTRAINT rule_contribution_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shadow_profile' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT fk_shadow_profile FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shadow_trades_ranking_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT fk_shadow_trades_ranking_id FOREIGN KEY (ranking_id) REFERENCES ml_opportunity_rankings(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shadow_trades_superseded_by_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT fk_shadow_trades_superseded_by_id FOREIGN KEY (superseded_by_id) REFERENCES shadow_trades(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trades_decision_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT shadow_trades_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES decisions_log(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trades_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT shadow_trades_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_trade_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_trade_id_fkey FOREIGN KEY (trade_id) REFERENCES trades(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_trade_simulations_decision_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT fk_trade_simulations_decision_id FOREIGN KEY (decision_id) REFERENCES decisions_log(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_simulations_decision_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT trade_simulations_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES decisions_log(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_tracking_decision_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_tracking ADD CONSTRAINT trade_tracking_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES decisions_log(id) ON DELETE SET NULL;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_profiles_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE watchlist_profiles ADD CONSTRAINT watchlist_profiles_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_profiles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE watchlist_profiles ADD CONSTRAINT watchlist_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$
    """))

    # Custom functions (needed before indexes that reference them)
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION public.pool_coins_sync_is_tradable()
         RETURNS trigger
         LANGUAGE plpgsql
        AS $function$
                BEGIN
                    IF NEW.is_approved IS DISTINCT FROM OLD.is_approved
                       AND NEW.is_tradable = OLD.is_tradable THEN
                        NEW.is_tradable := NEW.is_approved;
                    END IF;
                    RETURN NEW;
                END;
                $function$
    """))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION public.prevent_pi_autopilot_audit_mutation()
         RETURNS trigger
         LANGUAGE plpgsql
        AS $function$
                BEGIN
                    RAISE EXCEPTION 'profile_intelligence_autopilot_audit is append-only';
                END;
                $function$
    """))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION public.shadow_lab_hour_bucket(ts timestamp with time zone)
         RETURNS bigint
         LANGUAGE sql
         IMMUTABLE PARALLEL SAFE
        AS $function$
                   SELECT EXTRACT(EPOCH FROM ts)::bigint / 3600
               $function$
        ;
    """))

    # Triggers
    op.execute(sa.text("""
        CREATE TRIGGER pool_coins_is_approved_sync BEFORE UPDATE ON public.pool_coins FOR EACH ROW EXECUTE FUNCTION pool_coins_sync_is_tradable()
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_pi_autopilot_audit_immutable BEFORE DELETE OR UPDATE ON public.profile_intelligence_autopilot_audit FOR EACH ROW EXECUTE FUNCTION prevent_pi_autopilot_audit_mutation()
    """))

    # Indexes
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_provider_keys_user_id ON public.ai_provider_keys USING btree (user_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_skills_role_key ON public.ai_skills USING btree (role_key)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ai_skills_user_id ON public.ai_skills USING btree (user_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_forward_validation_model ON public.algorithm_forward_validations USING btree (model_id, stage)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_forward_validation_suggestion ON public.algorithm_forward_validations USING btree (suggestion_id, stage)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_alpha_scores_scoring_version ON public.alpha_scores USING btree (scoring_version)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_asset_traces_symbol ON public.asset_traces USING btree (symbol)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_asset_traces_trace_id ON public.asset_traces USING btree (trace_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_autopilot_audit_logs_profile_id ON public.autopilot_audit_logs USING btree (profile_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_autopilot_audit_logs_trigger_source ON public.autopilot_audit_logs USING btree (trigger_source)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_config_profiles_global_active ON public.config_profiles USING btree (user_id, config_type) WHERE ((pool_id IS NULL) AND (is_active = true))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON public.decisions_log USING btree (created_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_decision ON public.decisions_log USING btree (decision)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_log_outcome ON public.decisions_log USING btree (outcome) WHERE (outcome IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_log_processed ON public.decisions_log USING btree (processed) WHERE (processed = false)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_log_trade_executed ON public.decisions_log USING btree (trade_executed) WHERE (trade_executed = true)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_profile_created ON public.decisions_log USING btree (user_id, profile_id, created_at DESC) WHERE (profile_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_profile_id ON public.decisions_log USING btree (profile_id, created_at DESC) WHERE (profile_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_score ON public.decisions_log USING btree (score)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON public.decisions_log USING btree (symbol)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_decisions_log_ml_audit ON public.decisions_log USING btree (created_at DESC, model_lane, score_status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_decisions_log_model_id ON public.decisions_log USING btree (model_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_decisions_log_orchestrator_payload ON public.decisions_log USING gin (orchestrator_payload) WHERE (orchestrator_payload IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_decisions_log_ranking_id ON public.decisions_log USING btree (ranking_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_exchange_executions_order ON public.exchange_executions USING btree (order_id) WHERE (order_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_exchange_executions_symbol_time ON public.exchange_executions USING btree (symbol, executed_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_exchange_executions_user_time ON public.exchange_executions USING btree (user_id, executed_at DESC)"))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_can_trade ON public.indicator_snapshots USING btree (can_trade, "timestamp")
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_symbol_timestamp ON public.indicator_snapshots USING btree (symbol, "timestamp")
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_validation ON public.indicator_snapshots USING btree (validation_passed, "timestamp")
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol ON public.indicator_snapshots USING btree (symbol)"))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol_time ON public.indicator_snapshots USING btree (symbol, "timestamp" DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_timestamp ON public.indicator_snapshots USING btree ("timestamp")
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_indicators_futures_time ON public.indicators USING btree ("time" DESC) WHERE ((market_type)::text = 'futures'::text)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_indicators_symbol_group_time ON public.indicators USING btree (symbol, scheduler_group, "time" DESC)
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_indicators_time_symbol_timeframe ON public.indicators USING btree ("time", symbol, timeframe)
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_label_lab_runs_label_version_evaluated_at ON public.label_lab_runs USING btree (label_version, evaluated_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_ml_registry_scope ON public.ml_model_registry USING btree (profile_id, market_regime, strategy_skill)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_ml_registry_status ON public.ml_model_registry USING btree (status, model_type, created_at DESC)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_ml_registry_one_champion_scope ON public.ml_model_registry USING btree (COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid), market_regime, strategy_skill) WHERE ((status)::text = 'champion'::text)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_ml_models_dataset_hash ON public.ml_models USING btree (dataset_hash)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_ml_models_scope_profile ON public.ml_models USING btree (model_scope, profile_id, status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_models_label_version ON public.ml_models USING btree (label_version) WHERE (label_version IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_models_lane ON public.ml_models USING btree (model_lane) WHERE (model_lane IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_models_status ON public.ml_models USING btree (status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_models_version ON public.ml_models USING btree (version)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_audit ON public.ml_opportunity_rankings USING btree (ranked_at DESC, model_lane, score_status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_decision_id ON public.ml_opportunity_rankings USING btree (decision_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_model_id ON public.ml_opportunity_rankings USING btree (model_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_model_lane ON public.ml_opportunity_rankings USING btree (model_lane)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_orch_payload ON public.ml_opportunity_rankings USING gin (orchestrator_payload) WHERE (orchestrator_payload IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_run_id ON public.ml_opportunity_rankings USING btree (run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_symbol_ranked_at ON public.ml_opportunity_rankings USING btree (symbol, ranked_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_decision_id ON public.ml_predictions USING btree (decision_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_approved ON public.ml_predictions USING btree (model_approved)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_id ON public.ml_predictions USING btree (model_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_lane ON public.ml_predictions USING btree (model_lane)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_reason_code ON public.ml_predictions USING btree (reason_code)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_scored_at ON public.ml_predictions USING btree (scored_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ml_predictions_shadow_trade_id ON public.ml_predictions USING btree (shadow_trade_id)"))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_exchange_timeframe_time ON public.ohlcv USING btree (symbol, exchange, timeframe, "time" DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_timeframe_symbol_time ON public.ohlcv USING btree (timeframe, symbol, "time" DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_futures_time ON public.ohlcv USING btree (symbol, "time" DESC) WHERE ((market_type)::text = 'futures'::text)
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_ohlcv_symbol_exchange_timeframe_time ON public.ohlcv USING btree (symbol, exchange, timeframe, "time")
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_opp_snap_execution ON public.opportunity_snapshots USING btree (execution_id) WHERE (execution_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_opp_snap_features ON public.opportunity_snapshots USING gin (features_json)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_opp_snap_profiles_result ON public.opportunity_snapshots USING gin (active_profiles_result_json) WHERE (active_profiles_result_json IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_opp_snap_symbol_created ON public.opportunity_snapshots USING btree (symbol, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_opp_snap_user_created ON public.opportunity_snapshots USING btree (user_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_opp_snap_user_symbol_created ON public.opportunity_snapshots USING btree (user_id, symbol, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pipeline_metrics_trace_id ON public.pipeline_metrics USING btree (trace_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pool_coins_approved ON public.pool_coins USING btree (symbol, market_type) WHERE ((is_active = true) AND (is_approved = true))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pool_coins_tradable ON public.pool_coins USING btree (symbol, market_type) WHERE ((is_active = true) AND (is_tradable = true))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_position_lifecycle_status ON public.position_lifecycle USING btree (status) WHERE ((status)::text <> 'closed'::text)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_position_lifecycle_symbol_closed ON public.position_lifecycle USING btree (symbol, market_type, closed_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_position_lifecycle_user_closed ON public.position_lifecycle USING btree (user_id, closed_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_profile_audit_profile_created ON public.profile_audit_log USING btree (user_id, profile_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_profile_audit_profile_id ON public.profile_audit_log USING btree (profile_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_bucket ON public.profile_indicator_stats USING btree (indicator, bucket_label)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_role ON public.profile_indicator_stats USING btree (user_id, role_detected, confidence_score DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_run ON public.profile_indicator_stats USING btree (user_id, run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_audit_actor ON public.profile_intelligence_audit_log USING btree (actor_user_id, created_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_audit_run ON public.profile_intelligence_audit_log USING btree (run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_audit_source_run ON public.profile_intelligence_audit_log USING btree (source_run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_audit_sugg ON public.profile_intelligence_audit_log USING btree (suggestion_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_audit_user ON public.profile_intelligence_audit_log USING btree (user_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_autopilot_assoc_watchlist ON public.profile_intelligence_autopilot_associations USING btree (user_id, watchlist_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_autopilot_audit_user_created ON public.profile_intelligence_autopilot_audit USING btree (user_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_autopilot_candidates_signature ON public.profile_intelligence_autopilot_candidates USING btree (user_id, canonical_signature)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_autopilot_candidates_user_state ON public.profile_intelligence_autopilot_candidates USING btree (user_id, state, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_autopilot_cycles_user_window ON public.profile_intelligence_autopilot_cycles USING btree (user_id, window_start DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_loss_families_active ON public.profile_intelligence_loss_families USING btree (user_id, blocked_until DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_runs_user_run_at ON public.profile_intelligence_runs USING btree (user_id, run_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_runs_user_status ON public.profile_intelligence_runs USING btree (user_id, status, run_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pi_runs_trigger_source ON public.profile_intelligence_runs USING btree (trigger_source)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_profile_metrics_calculated ON public.profile_metrics USING btree (user_id, calculated_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_profile_metrics_profile_period ON public.profile_metrics USING btree (user_id, profile_id, period_end DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_comb_conf_score ON public.profile_rule_combinations USING btree (user_id, confidence_level, champion_score DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_comb_run ON public.profile_rule_combinations USING btree (user_id, run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_comb_score ON public.profile_rule_combinations USING btree (user_id, champion_score DESC)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_pi_comb_hash ON public.profile_rule_combinations USING btree (user_id, run_id, combination_hash)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_sugg_score ON public.profile_suggestions USING btree (user_id, confidence_score DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_sugg_status ON public.profile_suggestions USING btree (user_id, status, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_suggestion_source ON public.profile_suggestions USING btree (source_type, source_run_id, profile_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_pi_suggestion_validation ON public.profile_suggestions USING btree (validation_status, actionability_status, status)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_hash_per_user ON public.profile_suggestions USING btree (user_id, suggestion_hash) WHERE (suggestion_hash IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_profile_versions_profile_id ON public.profile_versions USING btree (profile_id, version_number DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_profiles_generated_by ON public.profiles USING btree (generated_by) WHERE (generated_by IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_profiles_type ON public.profiles USING btree (profile_type) WHERE (is_active = true)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_from_suggestion ON public.profiles USING btree (generated_from_suggestion_id) WHERE (generated_from_suggestion_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_reconciled_gate_trades_processed_at ON public.reconciled_gate_trades USING btree (processed_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_rule_contribution_hash ON public.rule_contribution USING btree (rule_hash)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_rule_contribution_profile ON public.rule_contribution USING btree (user_id, profile_id, calculated_at DESC) WHERE (profile_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_created_at ON public.shadow_capture_skips USING btree (created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_symbol ON public.shadow_capture_skips USING btree (symbol)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_user_id ON public.shadow_capture_skips USING btree (user_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trade_duplicate_audit_decision_id ON public.shadow_trade_duplicate_audit USING btree (decision_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile ON public.shadow_trades USING btree (profile_id, profile_version)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_source ON public.shadow_trades USING btree (source, profile_id, profile_version)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_status ON public.shadow_trades USING btree (profile_id, status, outcome)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shadow_trades_timeout_pending_analysis ON public.shadow_trades USING btree (outcome, timeout_post_analysis_done, exit_timestamp) WHERE (((outcome)::text = 'TIMEOUT'::text) AND (timeout_post_analysis_done = false))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shadow_trades_ttt_outcome ON public.shadow_trades USING btree (ttt_outcome, ttt_fast_win_bucket) WHERE (ttt_outcome IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_shadow_trades_ttt_pending ON public.shadow_trades USING btree (ttt_enabled, ttt_analysis_done, completed_at) WHERE ((ttt_enabled = true) AND ((ttt_analysis_done = false) OR (ttt_analysis_done IS NULL)))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_created_at ON public.shadow_trades USING btree (created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_decision_id ON public.shadow_trades USING btree (decision_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_lineage_confidence ON public.shadow_trades USING btree (lineage_confidence) WHERE (lineage_confidence IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_ml_audit ON public.shadow_trades USING btree (created_at DESC, model_lane, score_status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_orch_payload ON public.shadow_trades USING gin (orchestrator_payload) WHERE (orchestrator_payload IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_profile_watchlist ON public.shadow_trades USING btree (profile_id, watchlist_id) WHERE ((profile_id IS NOT NULL) AND (watchlist_id IS NOT NULL))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_ranking_id ON public.shadow_trades USING btree (ranking_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_source ON public.shadow_trades USING btree (source)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_status ON public.shadow_trades USING btree (status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_symbol ON public.shadow_trades USING btree (symbol)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_user_id ON public.shadow_trades USING btree (user_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_watchlist_id ON public.shadow_trades USING btree (watchlist_id) WHERE (watchlist_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_watchlist_level ON public.shadow_trades USING btree (watchlist_level, created_at DESC) WHERE (watchlist_level IS NOT NULL)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_lab_active_profile_symbol ON public.shadow_trades USING btree (profile_id, symbol, source) WHERE ((profile_id IS NOT NULL) AND ((status)::text = ANY ((ARRAY['RUNNING'::character varying, 'PENDING'::character varying])::text[])))"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_lab_profile_symbol_bucket ON public.shadow_trades USING btree (profile_id, symbol, source, shadow_lab_hour_bucket(created_at)) WHERE (profile_id IS NOT NULL)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_running_user_source ON public.shadow_trades USING btree (user_id, symbol, source) WHERE (((status)::text = 'RUNNING'::text) AND (profile_id IS NULL))"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_trades_decision_id_canonical ON public.shadow_trades USING btree (decision_id) WHERE ((decision_id IS NOT NULL) AND (superseded_by_id IS NULL))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trade_decisions_status_time ON public.trade_decisions USING btree (status, decided_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trade_decisions_symbol_time ON public.trade_decisions USING btree (symbol, decided_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trade_decisions_trace ON public.trade_decisions USING btree (trace_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trade_decisions_user_time ON public.trade_decisions USING btree (user_id, decided_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_decision_type ON public.trade_simulations USING btree (decision_type)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_direction ON public.trade_simulations USING btree (direction)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_exit_timestamp ON public.trade_simulations USING btree (exit_timestamp)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_result ON public.trade_simulations USING btree (result)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol ON public.trade_simulations USING btree (symbol)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol_timestamp ON public.trade_simulations USING btree (symbol, timestamp_entry)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_simulations_timestamp_entry ON public.trade_simulations USING btree (timestamp_entry)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_trade_simulations_shadow_decision_uniq ON public.trade_simulations USING btree (decision_id) WHERE (((source)::text = 'SHADOW'::text) AND (decision_id IS NOT NULL))"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_created_at ON public.trade_tracking USING btree (created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_decision_id ON public.trade_tracking USING btree (decision_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_exit_price_source ON public.trade_tracking USING btree (exit_price_source) WHERE (exit_price_source IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_external_id ON public.trade_tracking USING btree (external_id) WHERE (external_id IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_outcome ON public.trade_tracking USING btree (outcome) WHERE (outcome IS NOT NULL)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_status ON public.trade_tracking USING btree (status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_trade_tracking_symbol ON public.trade_tracking USING btree (symbol)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_tracking_decision ON public.trade_tracking USING btree (decision_id) WHERE (decision_id IS NOT NULL)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_trades_exchange_order_id ON public.trades USING btree (exchange_order_id) WHERE (exchange_order_id IS NOT NULL)"))


def downgrade() -> None:
    # Drop all tables (reverse order to handle FK deps)
    op.execute(sa.text('DROP SCHEMA public CASCADE'))
    op.execute(sa.text('CREATE SCHEMA public'))
    op.execute(sa.text('GRANT ALL ON SCHEMA public TO postgres'))
    op.execute(sa.text('GRANT ALL ON SCHEMA public TO public'))
