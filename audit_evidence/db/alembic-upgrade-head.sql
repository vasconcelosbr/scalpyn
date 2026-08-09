INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Generating static SQL
INFO  [alembic.runtime.migration] Will assume transactional DDL.
BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 000_baseline_prod_schema

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE SEQUENCE IF NOT EXISTS decisions_log_id_seq;

CREATE SEQUENCE IF NOT EXISTS exchange_executions_id_seq;

CREATE SEQUENCE IF NOT EXISTS indicator_snapshots_id_seq;

CREATE SEQUENCE IF NOT EXISTS ml_experiment_labels_id_seq;

CREATE SEQUENCE IF NOT EXISTS ml_experiment_results_id_seq;

CREATE SEQUENCE IF NOT EXISTS position_lifecycle_id_seq;

CREATE SEQUENCE IF NOT EXISTS reconciled_gate_trades_id_seq;

CREATE SEQUENCE IF NOT EXISTS scalpyndata_id_seq;

INFO  [alembic.runtime.migration] Running upgrade  -> 000_baseline_prod_schema, Baseline migration � full production schema as of 2026-06-25.
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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

CREATE TABLE IF NOT EXISTS config_audit_log (
          id uuid NOT NULL,
          config_id uuid,
          changed_by uuid,
          previous_json jsonb,
          new_json jsonb NOT NULL,
          change_description text,
          changed_at timestamp with time zone
        );;

CREATE TABLE IF NOT EXISTS config_profiles (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          pool_id uuid,
          config_type character varying(50) NOT NULL,
          config_json jsonb NOT NULL,
          is_active boolean,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );;

CREATE TABLE IF NOT EXISTS custom_watchlists (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          name character varying(255) NOT NULL,
          description text,
          symbols jsonb NOT NULL,
          is_active boolean,
          created_at timestamp with time zone,
          updated_at timestamp with time zone
        );;

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
        );;

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
        );;

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
        );;

CREATE TABLE IF NOT EXISTS funding_rates (
          "time" timestamp with time zone NOT NULL,
          symbol character varying(20) NOT NULL,
          exchange character varying(50) NOT NULL,
          rate numeric(10,6)
        );;

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
        );;

CREATE TABLE IF NOT EXISTS indicators (
          "time" timestamp with time zone NOT NULL,
          symbol character varying(20) NOT NULL,
          timeframe character varying(10) NOT NULL,
          market_type character varying(10) NOT NULL DEFAULT 'spot'::character varying,
          indicators_json jsonb NOT NULL,
          scheduler_group character varying(20)
        );;

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
        );;

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
        );;

CREATE TABLE IF NOT EXISTS ml_experiment_features (
          shadow_trade_id uuid NOT NULL,
          symbol text NOT NULL,
          signal_at timestamp with time zone NOT NULL,
          features_json jsonb NOT NULL,
          derived_json jsonb,
          n_ohlcv_candles integer,
          run_at timestamp with time zone DEFAULT now()
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_reports (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          cycle_id uuid NOT NULL,
          report_json jsonb NOT NULL,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );;

CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_settings (
          user_id uuid NOT NULL,
          enabled boolean NOT NULL DEFAULT false,
          settings_json jsonb NOT NULL DEFAULT '{}'::jsonb,
          enabled_at timestamp with time zone,
          disabled_at timestamp with time zone,
          last_cycle_at timestamp with time zone,
          created_at timestamp with time zone NOT NULL DEFAULT now(),
          updated_at timestamp with time zone NOT NULL DEFAULT now()
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

CREATE TABLE IF NOT EXISTS reconciled_gate_trades (
          id bigint NOT NULL DEFAULT nextval('reconciled_gate_trades_id_seq'::regclass),
          external_id character varying(100) NOT NULL,
          market_type character varying(10) NOT NULL,
          trade_tracking_id uuid,
          processed_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP
        );;

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
        );;

CREATE TABLE IF NOT EXISTS scalpyndata (
          id integer NOT NULL DEFAULT nextval('scalpyndata_id_seq'::regclass)
        );;

CREATE TABLE IF NOT EXISTS shadow_capture_skips (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          symbol character varying NOT NULL,
          promotion_at timestamp with time zone NOT NULL,
          skip_reason character varying NOT NULL,
          source_path character varying NOT NULL,
          created_at timestamp with time zone NOT NULL DEFAULT now()
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

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
        );;

CREATE TABLE IF NOT EXISTS watchlist_profiles (
          id uuid NOT NULL,
          user_id uuid NOT NULL,
          watchlist_id character varying(100) NOT NULL,
          profile_type character varying(10) NOT NULL,
          profile_id uuid,
          is_enabled boolean,
          created_at timestamp with time zone
        );;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ai_skill_user_name' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_skills ADD CONSTRAINT uq_ai_skill_user_name UNIQUE (user_id, name);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_autonomy_policies_user_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_autonomy_policies ADD CONSTRAINT autopilot_autonomy_policies_user_id_key UNIQUE (user_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_exchange_executions_dedup' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_executions ADD CONSTRAINT uq_exchange_executions_dedup UNIQUE (exchange, market_type, trade_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_labels_shadow_trade_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_labels ADD CONSTRAINT ml_experiment_labels_shadow_trade_id_key UNIQUE (shadow_trade_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ohlcv_time_symbol_exchange_timeframe' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ohlcv ADD CONSTRAINT uq_ohlcv_time_symbol_exchange_timeframe UNIQUE ("time", symbol, exchange, timeframe);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_pipeline_asset_watchlist_symbol' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_assets ADD CONSTRAINT uq_pipeline_asset_watchlist_symbol UNIQUE (watchlist_id, symbol);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_production_champion_scope' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT uq_production_champion_scope UNIQUE (profile_id, market_regime, strategy_skill);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_profile_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_profile_id_key UNIQUE (profile_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_idempotency_key_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_idempotency_key_key UNIQUE (idempotency_key);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_cycle_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_cycle_id_key UNIQUE (cycle_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_famil_user_id_canonical_signature_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_famil_user_id_canonical_signature_key UNIQUE (user_id, canonical_signature);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_reconciled_gate_trades_ext_market' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE reconciled_gate_trades ADD CONSTRAINT uq_reconciled_gate_trades_ext_market UNIQUE (external_id, market_type);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_simulation_symbol_entry_direction' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT uq_simulation_symbol_entry_direction UNIQUE (symbol, timestamp_entry, direction);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_exchange_order_id_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_exchange_order_id_key UNIQUE (exchange_order_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_email_key' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_provider_keys_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_provider_keys ADD CONSTRAINT ai_provider_keys_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_skills_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_skills ADD CONSTRAINT ai_skills_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alembic_version_pkc' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE alembic_version ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'asset_traces_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE asset_traces ADD CONSTRAINT asset_traces_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_audit_logs_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT autopilot_audit_logs_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_autonomy_policies_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_autonomy_policies ADD CONSTRAINT autopilot_autonomy_policies_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backoffice_alerts_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE backoffice_alerts ADD CONSTRAINT backoffice_alerts_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_audit_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_audit_log ADD CONSTRAINT config_audit_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_profiles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_profiles ADD CONSTRAINT config_profiles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'custom_watchlists_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE custom_watchlists ADD CONSTRAINT custom_watchlists_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT decisions_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'exchange_connections_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_connections ADD CONSTRAINT exchange_connections_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'exchange_executions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_executions ADD CONSTRAINT exchange_executions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'indicator_snapshots_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE indicator_snapshots ADD CONSTRAINT indicator_snapshots_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'label_lab_runs_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE label_lab_runs ADD CONSTRAINT label_lab_runs_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'market_metadata_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE market_metadata ADD CONSTRAINT market_metadata_pkey PRIMARY KEY (symbol);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_features_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_features ADD CONSTRAINT ml_experiment_features_pkey PRIMARY KEY (shadow_trade_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_labels_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_labels ADD CONSTRAINT ml_experiment_labels_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_results_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_experiment_results ADD CONSTRAINT ml_experiment_results_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_model_registry_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_model_registry ADD CONSTRAINT ml_model_registry_pkey PRIMARY KEY (model_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_models_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_models ADD CONSTRAINT ml_models_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_opportunity_rankings_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_opportunity_rankings ADD CONSTRAINT ml_opportunity_rankings_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_predictions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_predictions ADD CONSTRAINT ml_predictions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notification_settings_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE notification_settings ADD CONSTRAINT notification_settings_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'opportunity_snapshots_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE opportunity_snapshots ADD CONSTRAINT opportunity_snapshots_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE orders ADD CONSTRAINT orders_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_metrics_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_metrics ADD CONSTRAINT pipeline_metrics_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_assets_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_assets ADD CONSTRAINT pipeline_watchlist_assets_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pool_coins_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pool_coins ADD CONSTRAINT pool_coins_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pools_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pools ADD CONSTRAINT pools_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'position_lifecycle_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE position_lifecycle ADD CONSTRAINT position_lifecycle_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'production_champion_control_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT production_champion_control_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_audit_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_audit_log ADD CONSTRAINT profile_audit_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_indicator_stats_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_indicator_stats ADD CONSTRAINT profile_indicator_stats_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_audit_log_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_audit_log ADD CONSTRAINT profile_intelligence_audit_log_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_settings_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_settings ADD CONSTRAINT profile_intelligence_autopilot_settings_pkey PRIMARY KEY (user_id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_families_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_families_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_runs_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_runs ADD CONSTRAINT profile_intelligence_runs_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_metrics_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_metrics ADD CONSTRAINT profile_metrics_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_rule_combinations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_rule_combinations ADD CONSTRAINT profile_rule_combinations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_suggestions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT profile_suggestions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_versions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_versions ADD CONSTRAINT profile_versions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profiles ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reconciled_gate_trades_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE reconciled_gate_trades ADD CONSTRAINT reconciled_gate_trades_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rule_contribution_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE rule_contribution ADD CONSTRAINT rule_contribution_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scalpyndata_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE scalpyndata ADD CONSTRAINT scalpyndata_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_capture_skips_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_capture_skips ADD CONSTRAINT shadow_capture_skips_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trade_duplicate_audit_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trade_duplicate_audit ADD CONSTRAINT shadow_trade_duplicate_audit_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trades_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT shadow_trades_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_simulations_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT trade_simulations_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_tracking_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_tracking ADD CONSTRAINT trade_tracking_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_profiles_pkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE watchlist_profiles ADD CONSTRAINT watchlist_profiles_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_provider_keys_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_provider_keys ADD CONSTRAINT ai_provider_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_skills_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ai_skills ADD CONSTRAINT ai_skills_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_model_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_model_id_fkey FOREIGN KEY (model_id) REFERENCES ml_model_registry(model_id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'algorithm_forward_validations_suggestion_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE algorithm_forward_validations ADD CONSTRAINT algorithm_forward_validations_suggestion_id_fkey FOREIGN KEY (suggestion_id) REFERENCES profile_suggestions(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_audit_logs_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT autopilot_audit_logs_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'autopilot_audit_logs_version_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT autopilot_audit_logs_version_id_fkey FOREIGN KEY (version_id) REFERENCES profile_versions(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_autopilot_audit_logs_user_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE autopilot_audit_logs ADD CONSTRAINT fk_autopilot_audit_logs_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backoffice_alerts_acknowledged_by_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE backoffice_alerts ADD CONSTRAINT backoffice_alerts_acknowledged_by_fkey FOREIGN KEY (acknowledged_by) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_audit_log_changed_by_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_audit_log ADD CONSTRAINT config_audit_log_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_audit_log_config_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_audit_log ADD CONSTRAINT config_audit_log_config_id_fkey FOREIGN KEY (config_id) REFERENCES config_profiles(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_profiles_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_profiles ADD CONSTRAINT config_profiles_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'config_profiles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE config_profiles ADD CONSTRAINT config_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'custom_watchlists_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE custom_watchlists ADD CONSTRAINT custom_watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_log_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT decisions_log_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_log_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT decisions_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_decisions_log_ranking_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE decisions_log ADD CONSTRAINT fk_decisions_log_ranking_id FOREIGN KEY (ranking_id) REFERENCES ml_opportunity_rankings(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'exchange_connections_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE exchange_connections ADD CONSTRAINT exchange_connections_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_model_registry_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_model_registry ADD CONSTRAINT ml_model_registry_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ml_models_profile' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE ml_models ADD CONSTRAINT fk_ml_models_profile FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notification_settings_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE notification_settings ADD CONSTRAINT notification_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_trade_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE orders ADD CONSTRAINT orders_trade_id_fkey FOREIGN KEY (trade_id) REFERENCES trades(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE orders ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_assets_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_assets ADD CONSTRAINT pipeline_watchlist_assets_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlist_rejections_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD CONSTRAINT pipeline_watchlist_rejections_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_source_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_source_pool_id_fkey FOREIGN KEY (source_pool_id) REFERENCES pools(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_source_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_source_watchlist_id_fkey FOREIGN KEY (source_watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_watchlists_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pipeline_watchlists ADD CONSTRAINT pipeline_watchlists_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pool_coins_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pool_coins ADD CONSTRAINT pool_coins_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pools_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pools ADD CONSTRAINT pools_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pools_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE pools ADD CONSTRAINT pools_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'production_champion_control_active_model_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT production_champion_control_active_model_id_fkey FOREIGN KEY (active_model_id) REFERENCES ml_model_registry(model_id) ON DELETE RESTRICT;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'production_champion_control_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE production_champion_control ADD CONSTRAINT production_champion_control_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_audit_log_changed_by_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_audit_log ADD CONSTRAINT profile_audit_log_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_audit_log_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_audit_log ADD CONSTRAINT profile_audit_log_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_indicator_stats_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_indicator_stats ADD CONSTRAINT profile_indicator_stats_run_id_fkey FOREIGN KEY (run_id) REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associa_previous_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associa_previous_profile_id_fkey FOREIGN KEY (previous_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_new_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_new_profile_id_fkey FOREIGN KEY (new_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_associations_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_associations ADD CONSTRAINT profile_intelligence_autopilot_associations_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE RESTRICT;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_actor_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_combination_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_combination_id_fkey FOREIGN KEY (combination_id) REFERENCES profile_rule_combinations(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_suggestion_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_suggestion_id_fkey FOREIGN KEY (suggestion_id) REFERENCES profile_suggestions(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_audit_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_audit ADD CONSTRAINT profile_intelligence_autopilot_audit_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pi_autopilot_candidate_approved_by' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT fk_pi_autopilot_candidate_approved_by FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candi_source_combination_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candi_source_combination_id_fkey FOREIGN KEY (source_combination_id) REFERENCES profile_rule_combinations(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candid_source_suggestion_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candid_source_suggestion_id_fkey FOREIGN KEY (source_suggestion_id) REFERENCES profile_suggestions(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candida_previous_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candida_previous_profile_id_fkey FOREIGN KEY (previous_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candida_shadow_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candida_shadow_watchlist_id_fkey FOREIGN KEY (shadow_watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candida_target_watchlist_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candida_target_watchlist_id_fkey FOREIGN KEY (target_watchlist_id) REFERENCES pipeline_watchlists(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidate_origin_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidate_origin_profile_id_fkey FOREIGN KEY (origin_profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE RESTRICT;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_candidates_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_candidates ADD CONSTRAINT profile_intelligence_autopilot_candidates_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_compensations_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_compensations ADD CONSTRAINT profile_intelligence_autopilot_compensations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_analysis_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_analysis_run_id_fkey FOREIGN KEY (analysis_run_id) REFERENCES profile_intelligence_runs(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_cycles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_cycles ADD CONSTRAINT profile_intelligence_autopilot_cycles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_cycle_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_cycle_id_fkey FOREIGN KEY (cycle_id) REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_reports_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_reports ADD CONSTRAINT profile_intelligence_autopilot_reports_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_autopilot_settings_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_autopilot_settings ADD CONSTRAINT profile_intelligence_autopilot_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_families_candidate_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_families_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_intelligence_loss_families_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_intelligence_loss_families ADD CONSTRAINT profile_intelligence_loss_families_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_metrics_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_metrics ADD CONSTRAINT profile_metrics_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_rule_combinations_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_rule_combinations ADD CONSTRAINT profile_rule_combinations_run_id_fkey FOREIGN KEY (run_id) REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_profile_suggestions_profile_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT fk_profile_suggestions_profile_id FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_suggestions_run_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT profile_suggestions_run_id_fkey FOREIGN KEY (run_id) REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_suggestions_source_combination_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_suggestions ADD CONSTRAINT profile_suggestions_source_combination_id_fkey FOREIGN KEY (source_combination_id) REFERENCES profile_rule_combinations(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profile_versions_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profile_versions ADD CONSTRAINT profile_versions_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE profiles ADD CONSTRAINT profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reconciled_gate_trades_trade_tracking_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE reconciled_gate_trades ADD CONSTRAINT reconciled_gate_trades_trade_tracking_id_fkey FOREIGN KEY (trade_tracking_id) REFERENCES trade_tracking(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rule_contribution_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE rule_contribution ADD CONSTRAINT rule_contribution_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shadow_profile' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT fk_shadow_profile FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shadow_trades_ranking_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT fk_shadow_trades_ranking_id FOREIGN KEY (ranking_id) REFERENCES ml_opportunity_rankings(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shadow_trades_superseded_by_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT fk_shadow_trades_superseded_by_id FOREIGN KEY (superseded_by_id) REFERENCES shadow_trades(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trades_decision_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT shadow_trades_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES decisions_log(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_trades_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE shadow_trades ADD CONSTRAINT shadow_trades_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_trade_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_trade_id_fkey FOREIGN KEY (trade_id) REFERENCES trades(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_decisions_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_decisions ADD CONSTRAINT trade_decisions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_trade_simulations_decision_id' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT fk_trade_simulations_decision_id FOREIGN KEY (decision_id) REFERENCES decisions_log(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_simulations_decision_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_simulations ADD CONSTRAINT trade_simulations_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES decisions_log(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_tracking_decision_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trade_tracking ADD CONSTRAINT trade_tracking_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES decisions_log(id) ON DELETE SET NULL;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_pool_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_pool_id_fkey FOREIGN KEY (pool_id) REFERENCES pools(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trades_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE trades ADD CONSTRAINT trades_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_profiles_profile_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE watchlist_profiles ADD CONSTRAINT watchlist_profiles_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_profiles_user_id_fkey' AND connamespace = 'public'::regnamespace) THEN
                ALTER TABLE watchlist_profiles ADD CONSTRAINT watchlist_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END
        $$;

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
                $function$;

CREATE OR REPLACE FUNCTION public.prevent_pi_autopilot_audit_mutation()
         RETURNS trigger
         LANGUAGE plpgsql
        AS $function$
                BEGIN
                    RAISE EXCEPTION 'profile_intelligence_autopilot_audit is append-only';
                END;
                $function$;

CREATE OR REPLACE FUNCTION public.shadow_lab_hour_bucket(ts timestamp with time zone)
         RETURNS bigint
         LANGUAGE sql
         IMMUTABLE PARALLEL SAFE
        AS $function$
                   SELECT EXTRACT(EPOCH FROM ts)::bigint / 3600
               $function$
        ;;

CREATE TRIGGER pool_coins_is_approved_sync BEFORE UPDATE ON public.pool_coins FOR EACH ROW EXECUTE FUNCTION pool_coins_sync_is_tradable();

CREATE TRIGGER trg_pi_autopilot_audit_immutable BEFORE DELETE OR UPDATE ON public.profile_intelligence_autopilot_audit FOR EACH ROW EXECUTE FUNCTION prevent_pi_autopilot_audit_mutation();

CREATE INDEX IF NOT EXISTS ix_ai_provider_keys_user_id ON public.ai_provider_keys USING btree (user_id);

CREATE INDEX IF NOT EXISTS ix_ai_skills_role_key ON public.ai_skills USING btree (role_key);

CREATE INDEX IF NOT EXISTS ix_ai_skills_user_id ON public.ai_skills USING btree (user_id);

CREATE INDEX IF NOT EXISTS idx_forward_validation_model ON public.algorithm_forward_validations USING btree (model_id, stage);

CREATE INDEX IF NOT EXISTS idx_forward_validation_suggestion ON public.algorithm_forward_validations USING btree (suggestion_id, stage);

CREATE INDEX IF NOT EXISTS idx_alpha_scores_scoring_version ON public.alpha_scores USING btree (scoring_version);

CREATE INDEX IF NOT EXISTS ix_asset_traces_symbol ON public.asset_traces USING btree (symbol);

CREATE INDEX IF NOT EXISTS ix_asset_traces_trace_id ON public.asset_traces USING btree (trace_id);

CREATE INDEX IF NOT EXISTS ix_autopilot_audit_logs_profile_id ON public.autopilot_audit_logs USING btree (profile_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_autopilot_audit_logs_trigger_source ON public.autopilot_audit_logs USING btree (trigger_source);

CREATE UNIQUE INDEX IF NOT EXISTS uq_config_profiles_global_active ON public.config_profiles USING btree (user_id, config_type) WHERE ((pool_id IS NULL) AND (is_active = true));

CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON public.decisions_log USING btree (created_at);

CREATE INDEX IF NOT EXISTS idx_decisions_decision ON public.decisions_log USING btree (decision);

CREATE INDEX IF NOT EXISTS idx_decisions_log_outcome ON public.decisions_log USING btree (outcome) WHERE (outcome IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_decisions_log_processed ON public.decisions_log USING btree (processed) WHERE (processed = false);

CREATE INDEX IF NOT EXISTS idx_decisions_log_trade_executed ON public.decisions_log USING btree (trade_executed) WHERE (trade_executed = true);

CREATE INDEX IF NOT EXISTS idx_decisions_profile_created ON public.decisions_log USING btree (user_id, profile_id, created_at DESC) WHERE (profile_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_decisions_profile_id ON public.decisions_log USING btree (profile_id, created_at DESC) WHERE (profile_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_decisions_score ON public.decisions_log USING btree (score);

CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON public.decisions_log USING btree (symbol);

CREATE INDEX IF NOT EXISTS ix_decisions_log_ml_audit ON public.decisions_log USING btree (created_at DESC, model_lane, score_status);

CREATE INDEX IF NOT EXISTS ix_decisions_log_model_id ON public.decisions_log USING btree (model_id);

CREATE INDEX IF NOT EXISTS ix_decisions_log_orchestrator_payload ON public.decisions_log USING gin (orchestrator_payload) WHERE (orchestrator_payload IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_decisions_log_ranking_id ON public.decisions_log USING btree (ranking_id);

CREATE INDEX IF NOT EXISTS ix_exchange_executions_order ON public.exchange_executions USING btree (order_id) WHERE (order_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_exchange_executions_symbol_time ON public.exchange_executions USING btree (symbol, executed_at DESC);

CREATE INDEX IF NOT EXISTS ix_exchange_executions_user_time ON public.exchange_executions USING btree (user_id, executed_at DESC);

CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_can_trade ON public.indicator_snapshots USING btree (can_trade, "timestamp");

CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_symbol_timestamp ON public.indicator_snapshots USING btree (symbol, "timestamp");

CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_validation ON public.indicator_snapshots USING btree (validation_passed, "timestamp");

CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol ON public.indicator_snapshots USING btree (symbol);

CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol_time ON public.indicator_snapshots USING btree (symbol, "timestamp" DESC);

CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_timestamp ON public.indicator_snapshots USING btree ("timestamp");

CREATE INDEX IF NOT EXISTS ix_indicators_futures_time ON public.indicators USING btree ("time" DESC) WHERE ((market_type)::text = 'futures'::text);

CREATE INDEX IF NOT EXISTS ix_indicators_symbol_group_time ON public.indicators USING btree (symbol, scheduler_group, "time" DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_indicators_time_symbol_timeframe ON public.indicators USING btree ("time", symbol, timeframe);

CREATE INDEX IF NOT EXISTS ix_label_lab_runs_label_version_evaluated_at ON public.label_lab_runs USING btree (label_version, evaluated_at);

CREATE INDEX IF NOT EXISTS idx_ml_registry_scope ON public.ml_model_registry USING btree (profile_id, market_regime, strategy_skill);

CREATE INDEX IF NOT EXISTS idx_ml_registry_status ON public.ml_model_registry USING btree (status, model_type, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ml_registry_one_champion_scope ON public.ml_model_registry USING btree (COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid), market_regime, strategy_skill) WHERE ((status)::text = 'champion'::text);

CREATE INDEX IF NOT EXISTS idx_ml_models_dataset_hash ON public.ml_models USING btree (dataset_hash);

CREATE INDEX IF NOT EXISTS idx_ml_models_scope_profile ON public.ml_models USING btree (model_scope, profile_id, status);

CREATE INDEX IF NOT EXISTS ix_ml_models_label_version ON public.ml_models USING btree (label_version) WHERE (label_version IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_ml_models_lane ON public.ml_models USING btree (model_lane) WHERE (model_lane IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_ml_models_status ON public.ml_models USING btree (status);

CREATE INDEX IF NOT EXISTS ix_ml_models_version ON public.ml_models USING btree (version);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_audit ON public.ml_opportunity_rankings USING btree (ranked_at DESC, model_lane, score_status);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_decision_id ON public.ml_opportunity_rankings USING btree (decision_id);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_model_id ON public.ml_opportunity_rankings USING btree (model_id);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_model_lane ON public.ml_opportunity_rankings USING btree (model_lane);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_orch_payload ON public.ml_opportunity_rankings USING gin (orchestrator_payload) WHERE (orchestrator_payload IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_run_id ON public.ml_opportunity_rankings USING btree (run_id);

CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_symbol_ranked_at ON public.ml_opportunity_rankings USING btree (symbol, ranked_at);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_decision_id ON public.ml_predictions USING btree (decision_id);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_approved ON public.ml_predictions USING btree (model_approved);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_id ON public.ml_predictions USING btree (model_id);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_lane ON public.ml_predictions USING btree (model_lane);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_reason_code ON public.ml_predictions USING btree (reason_code);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_scored_at ON public.ml_predictions USING btree (scored_at DESC);

CREATE INDEX IF NOT EXISTS ix_ml_predictions_shadow_trade_id ON public.ml_predictions USING btree (shadow_trade_id);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_exchange_timeframe_time ON public.ohlcv USING btree (symbol, exchange, timeframe, "time" DESC);

CREATE INDEX IF NOT EXISTS idx_ohlcv_timeframe_symbol_time ON public.ohlcv USING btree (timeframe, symbol, "time" DESC);

CREATE INDEX IF NOT EXISTS ix_ohlcv_futures_time ON public.ohlcv USING btree (symbol, "time" DESC) WHERE ((market_type)::text = 'futures'::text);

CREATE UNIQUE INDEX IF NOT EXISTS ix_ohlcv_symbol_exchange_timeframe_time ON public.ohlcv USING btree (symbol, exchange, timeframe, "time");

CREATE INDEX IF NOT EXISTS idx_opp_snap_execution ON public.opportunity_snapshots USING btree (execution_id) WHERE (execution_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_opp_snap_features ON public.opportunity_snapshots USING gin (features_json);

CREATE INDEX IF NOT EXISTS idx_opp_snap_profiles_result ON public.opportunity_snapshots USING gin (active_profiles_result_json) WHERE (active_profiles_result_json IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_opp_snap_symbol_created ON public.opportunity_snapshots USING btree (symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_opp_snap_user_created ON public.opportunity_snapshots USING btree (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_opp_snap_user_symbol_created ON public.opportunity_snapshots USING btree (user_id, symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_pipeline_metrics_trace_id ON public.pipeline_metrics USING btree (trace_id);

CREATE INDEX IF NOT EXISTS ix_pool_coins_approved ON public.pool_coins USING btree (symbol, market_type) WHERE ((is_active = true) AND (is_approved = true));

CREATE INDEX IF NOT EXISTS ix_pool_coins_tradable ON public.pool_coins USING btree (symbol, market_type) WHERE ((is_active = true) AND (is_tradable = true));

CREATE INDEX IF NOT EXISTS ix_position_lifecycle_status ON public.position_lifecycle USING btree (status) WHERE ((status)::text <> 'closed'::text);

CREATE INDEX IF NOT EXISTS ix_position_lifecycle_symbol_closed ON public.position_lifecycle USING btree (symbol, market_type, closed_at DESC);

CREATE INDEX IF NOT EXISTS ix_position_lifecycle_user_closed ON public.position_lifecycle USING btree (user_id, closed_at DESC);

CREATE INDEX IF NOT EXISTS idx_profile_audit_profile_created ON public.profile_audit_log USING btree (user_id, profile_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_profile_audit_profile_id ON public.profile_audit_log USING btree (profile_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_bucket ON public.profile_indicator_stats USING btree (indicator, bucket_label);

CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_role ON public.profile_indicator_stats USING btree (user_id, role_detected, confidence_score DESC);

CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_run ON public.profile_indicator_stats USING btree (user_id, run_id);

CREATE INDEX IF NOT EXISTS idx_pi_audit_actor ON public.profile_intelligence_audit_log USING btree (actor_user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_pi_audit_run ON public.profile_intelligence_audit_log USING btree (run_id);

CREATE INDEX IF NOT EXISTS idx_pi_audit_source_run ON public.profile_intelligence_audit_log USING btree (source_run_id);

CREATE INDEX IF NOT EXISTS idx_pi_audit_sugg ON public.profile_intelligence_audit_log USING btree (suggestion_id);

CREATE INDEX IF NOT EXISTS idx_pi_audit_user ON public.profile_intelligence_audit_log USING btree (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_autopilot_assoc_watchlist ON public.profile_intelligence_autopilot_associations USING btree (user_id, watchlist_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_autopilot_audit_user_created ON public.profile_intelligence_autopilot_audit USING btree (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_autopilot_candidates_signature ON public.profile_intelligence_autopilot_candidates USING btree (user_id, canonical_signature);

CREATE INDEX IF NOT EXISTS idx_pi_autopilot_candidates_user_state ON public.profile_intelligence_autopilot_candidates USING btree (user_id, state, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_autopilot_cycles_user_window ON public.profile_intelligence_autopilot_cycles USING btree (user_id, window_start DESC);

CREATE INDEX IF NOT EXISTS idx_pi_loss_families_active ON public.profile_intelligence_loss_families USING btree (user_id, blocked_until DESC);

CREATE INDEX IF NOT EXISTS idx_pi_runs_user_run_at ON public.profile_intelligence_runs USING btree (user_id, run_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_runs_user_status ON public.profile_intelligence_runs USING btree (user_id, status, run_at DESC);

CREATE INDEX IF NOT EXISTS ix_pi_runs_trigger_source ON public.profile_intelligence_runs USING btree (trigger_source);

CREATE INDEX IF NOT EXISTS idx_profile_metrics_calculated ON public.profile_metrics USING btree (user_id, calculated_at DESC);

CREATE INDEX IF NOT EXISTS idx_profile_metrics_profile_period ON public.profile_metrics USING btree (user_id, profile_id, period_end DESC);

CREATE INDEX IF NOT EXISTS idx_pi_comb_conf_score ON public.profile_rule_combinations USING btree (user_id, confidence_level, champion_score DESC);

CREATE INDEX IF NOT EXISTS idx_pi_comb_run ON public.profile_rule_combinations USING btree (user_id, run_id);

CREATE INDEX IF NOT EXISTS idx_pi_comb_score ON public.profile_rule_combinations USING btree (user_id, champion_score DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pi_comb_hash ON public.profile_rule_combinations USING btree (user_id, run_id, combination_hash);

CREATE INDEX IF NOT EXISTS idx_pi_sugg_score ON public.profile_suggestions USING btree (user_id, confidence_score DESC);

CREATE INDEX IF NOT EXISTS idx_pi_sugg_status ON public.profile_suggestions USING btree (user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_suggestion_source ON public.profile_suggestions USING btree (source_type, source_run_id, profile_id);

CREATE INDEX IF NOT EXISTS idx_pi_suggestion_validation ON public.profile_suggestions USING btree (validation_status, actionability_status, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_hash_per_user ON public.profile_suggestions USING btree (user_id, suggestion_hash) WHERE (suggestion_hash IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_profile_versions_profile_id ON public.profile_versions USING btree (profile_id, version_number DESC);

CREATE INDEX IF NOT EXISTS idx_profiles_generated_by ON public.profiles USING btree (generated_by) WHERE (generated_by IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_profiles_type ON public.profiles USING btree (profile_type) WHERE (is_active = true);

CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_from_suggestion ON public.profiles USING btree (generated_from_suggestion_id) WHERE (generated_from_suggestion_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_reconciled_gate_trades_processed_at ON public.reconciled_gate_trades USING btree (processed_at DESC);

CREATE INDEX IF NOT EXISTS idx_rule_contribution_hash ON public.rule_contribution USING btree (rule_hash);

CREATE INDEX IF NOT EXISTS idx_rule_contribution_profile ON public.rule_contribution USING btree (user_id, profile_id, calculated_at DESC) WHERE (profile_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_created_at ON public.shadow_capture_skips USING btree (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_symbol ON public.shadow_capture_skips USING btree (symbol);

CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_user_id ON public.shadow_capture_skips USING btree (user_id);

CREATE INDEX IF NOT EXISTS ix_shadow_trade_duplicate_audit_decision_id ON public.shadow_trade_duplicate_audit USING btree (decision_id);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile ON public.shadow_trades USING btree (profile_id, profile_version);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_source ON public.shadow_trades USING btree (source, profile_id, profile_version);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_status ON public.shadow_trades USING btree (profile_id, status, outcome);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_timeout_pending_analysis ON public.shadow_trades USING btree (outcome, timeout_post_analysis_done, exit_timestamp) WHERE (((outcome)::text = 'TIMEOUT'::text) AND (timeout_post_analysis_done = false));

CREATE INDEX IF NOT EXISTS idx_shadow_trades_ttt_outcome ON public.shadow_trades USING btree (ttt_outcome, ttt_fast_win_bucket) WHERE (ttt_outcome IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_ttt_pending ON public.shadow_trades USING btree (ttt_enabled, ttt_analysis_done, completed_at) WHERE ((ttt_enabled = true) AND ((ttt_analysis_done = false) OR (ttt_analysis_done IS NULL)));

CREATE INDEX IF NOT EXISTS ix_shadow_trades_created_at ON public.shadow_trades USING btree (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_decision_id ON public.shadow_trades USING btree (decision_id);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_lineage_confidence ON public.shadow_trades USING btree (lineage_confidence) WHERE (lineage_confidence IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_ml_audit ON public.shadow_trades USING btree (created_at DESC, model_lane, score_status);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_orch_payload ON public.shadow_trades USING gin (orchestrator_payload) WHERE (orchestrator_payload IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_profile_watchlist ON public.shadow_trades USING btree (profile_id, watchlist_id) WHERE ((profile_id IS NOT NULL) AND (watchlist_id IS NOT NULL));

CREATE INDEX IF NOT EXISTS ix_shadow_trades_ranking_id ON public.shadow_trades USING btree (ranking_id);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_source ON public.shadow_trades USING btree (source);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_status ON public.shadow_trades USING btree (status);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_symbol ON public.shadow_trades USING btree (symbol);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_user_id ON public.shadow_trades USING btree (user_id);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_watchlist_id ON public.shadow_trades USING btree (watchlist_id) WHERE (watchlist_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_watchlist_level ON public.shadow_trades USING btree (watchlist_level, created_at DESC) WHERE (watchlist_level IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_lab_active_profile_symbol ON public.shadow_trades USING btree (profile_id, symbol, source) WHERE ((profile_id IS NOT NULL) AND ((status)::text = ANY ((ARRAY['RUNNING'::character varying, 'PENDING'::character varying])::text[])));

CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_lab_profile_symbol_bucket ON public.shadow_trades USING btree (profile_id, symbol, source, shadow_lab_hour_bucket(created_at)) WHERE (profile_id IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_running_user_source ON public.shadow_trades USING btree (user_id, symbol, source) WHERE (((status)::text = 'RUNNING'::text) AND (profile_id IS NULL));

CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_trades_decision_id_canonical ON public.shadow_trades USING btree (decision_id) WHERE ((decision_id IS NOT NULL) AND (superseded_by_id IS NULL));

CREATE INDEX IF NOT EXISTS ix_trade_decisions_status_time ON public.trade_decisions USING btree (status, decided_at DESC);

CREATE INDEX IF NOT EXISTS ix_trade_decisions_symbol_time ON public.trade_decisions USING btree (symbol, decided_at DESC);

CREATE INDEX IF NOT EXISTS ix_trade_decisions_trace ON public.trade_decisions USING btree (trace_id);

CREATE INDEX IF NOT EXISTS ix_trade_decisions_user_time ON public.trade_decisions USING btree (user_id, decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_trade_simulations_decision_type ON public.trade_simulations USING btree (decision_type);

CREATE INDEX IF NOT EXISTS idx_trade_simulations_direction ON public.trade_simulations USING btree (direction);

CREATE INDEX IF NOT EXISTS idx_trade_simulations_exit_timestamp ON public.trade_simulations USING btree (exit_timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_simulations_result ON public.trade_simulations USING btree (result);

CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol ON public.trade_simulations USING btree (symbol);

CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol_timestamp ON public.trade_simulations USING btree (symbol, timestamp_entry);

INFO  [alembic.runtime.migration] Running upgrade 000_baseline_prod_schema -> 113_pi_live_engine, Profile Intelligence Live Engine � 8 new tables + runs columns.
CREATE INDEX IF NOT EXISTS idx_trade_simulations_timestamp_entry ON public.trade_simulations USING btree (timestamp_entry);

CREATE UNIQUE INDEX IF NOT EXISTS ix_trade_simulations_shadow_decision_uniq ON public.trade_simulations USING btree (decision_id) WHERE (((source)::text = 'SHADOW'::text) AND (decision_id IS NOT NULL));

CREATE INDEX IF NOT EXISTS idx_trade_tracking_created_at ON public.trade_tracking USING btree (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_trade_tracking_decision_id ON public.trade_tracking USING btree (decision_id);

CREATE INDEX IF NOT EXISTS idx_trade_tracking_exit_price_source ON public.trade_tracking USING btree (exit_price_source) WHERE (exit_price_source IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_trade_tracking_external_id ON public.trade_tracking USING btree (external_id) WHERE (external_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_trade_tracking_outcome ON public.trade_tracking USING btree (outcome) WHERE (outcome IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_trade_tracking_status ON public.trade_tracking USING btree (status);

CREATE INDEX IF NOT EXISTS idx_trade_tracking_symbol ON public.trade_tracking USING btree (symbol);

CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_tracking_decision ON public.trade_tracking USING btree (decision_id) WHERE (decision_id IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS ix_trades_exchange_order_id ON public.trades USING btree (exchange_order_id) WHERE (exchange_order_id IS NOT NULL);

INSERT INTO alembic_version (version_num) VALUES ('000_baseline_prod_schema') RETURNING alembic_version.version_num;

-- Running upgrade 000_baseline_prod_schema -> 113_pi_live_engine

CREATE TABLE IF NOT EXISTS profile_intelligence_heartbeats (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NULL,
            engine_status   VARCHAR(40) NOT NULL DEFAULT 'IDLE',
            current_phase   VARCHAR(60) NOT NULL DEFAULT 'IDLE',
            heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            next_cycle_at   TIMESTAMPTZ NULL,
            worker_name     VARCHAR(120) NULL,
            commit_hash     VARCHAR(64) NULL,
            metadata        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_pi_heartbeat_at
        ON profile_intelligence_heartbeats (heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS profile_intelligence_activity_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NULL,
            event_type      VARCHAR(60) NOT NULL,
            phase           VARCHAR(60) NOT NULL,
            severity        VARCHAR(20) NOT NULL DEFAULT 'info',
            message         TEXT NOT NULL,
            profile_id      UUID NULL,
            profile_name    VARCHAR(255) NULL,
            payload         JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_pi_activity_created
        ON profile_intelligence_activity_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pi_activity_profile
        ON profile_intelligence_activity_log (profile_id, created_at DESC)
        WHERE profile_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS profile_indicator_performance (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id                  UUID NOT NULL,
            profile_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            profile_name            VARCHAR(255) NULL,
            indicator_name          VARCHAR(80) NOT NULL,
            bucket                  VARCHAR(120) NULL,
            sample_count            INTEGER NOT NULL,
            win_count               INTEGER NOT NULL DEFAULT 0,
            loss_count              INTEGER NOT NULL DEFAULT 0,
            win_rate                NUMERIC NULL,
            avg_pnl_pct             NUMERIC NULL,
            ev_pct                  NUMERIC NULL,
            avg_mae_pct             NUMERIC NULL,
            avg_mfe_pct             NUMERIC NULL,
            avg_holding_seconds     NUMERIC NULL,
            lift_vs_profile         NUMERIC NULL,
            fpr                     NUMERIC NULL,
            metadata                JSONB NOT NULL DEFAULT '{}',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_pi_ind_perf_run_profile
        ON profile_indicator_performance (run_id, profile_id);

CREATE INDEX IF NOT EXISTS idx_pi_ind_perf_profile_indicator
        ON profile_indicator_performance (profile_id, indicator_name, created_at DESC);

CREATE TABLE IF NOT EXISTS profile_hard_negative_patterns (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id              UUID NOT NULL,
            profile_id          UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            profile_name        VARCHAR(255) NULL,
            pattern_key         VARCHAR(120) NOT NULL,
            pattern_payload     JSONB NOT NULL DEFAULT '{}',
            sample_count        INTEGER NOT NULL,
            loss_count          INTEGER NOT NULL,
            fp_rate             NUMERIC NULL,
            avg_loss_pct        NUMERIC NULL,
            suggested_penalty   JSONB NULL,
            status              VARCHAR(30) NOT NULL DEFAULT 'OBSERVED',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_pi_hard_neg_profile
        ON profile_hard_negative_patterns (profile_id, created_at DESC);

CREATE TABLE IF NOT EXISTS profile_adjustment_suggestions (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id                  UUID NOT NULL,
            profile_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            profile_name            VARCHAR(255) NULL,
            suggestion_type         VARCHAR(60) NOT NULL,
            target_section          VARCHAR(80) NOT NULL,
            target_field            VARCHAR(120) NULL,
            current_value           JSONB NULL,
            suggested_value         JSONB NOT NULL,
            reason                  TEXT NOT NULL,
            evidence                JSONB NOT NULL DEFAULT '{}',
            confidence              NUMERIC NULL,
            expected_impact         JSONB NULL,
            status                  VARCHAR(40) NOT NULL DEFAULT 'PENDING_SHADOW_VALIDATION',
            mutation_applied        BOOLEAN NOT NULL DEFAULT false,
            requires_human_approval BOOLEAN NOT NULL DEFAULT true,
            rollback_payload        JSONB NULL,
            created_by              VARCHAR(60) NOT NULL DEFAULT 'profile_intelligence',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NULL,
            CONSTRAINT chk_adj_sugg_mutation
                CHECK (mutation_applied = false OR requires_human_approval = true),
            CONSTRAINT chk_adj_sugg_type_not_create
                CHECK (suggestion_type NOT IN ('CREATE_PROFILE','DUPLICATE_PROFILE','PROMOTE_LIVE','ENABLE_LIVE'))
        );

CREATE INDEX IF NOT EXISTS idx_adj_sugg_profile_status
        ON profile_adjustment_suggestions (profile_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS profile_adjustment_versions (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_id               UUID NOT NULL REFERENCES profile_adjustment_suggestions(id) ON DELETE CASCADE,
            profile_id                  UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            version_status              VARCHAR(40) NOT NULL,
            before_snapshot             JSONB NOT NULL,
            after_snapshot              JSONB NOT NULL,
            diff                        JSONB NOT NULL DEFAULT '{}',
            shadow_validation_status    VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            mutation_applied            BOOLEAN NOT NULL DEFAULT false,
            applied_at                  TIMESTAMPTZ NULL,
            applied_by                  VARCHAR(120) NULL,
            rollback_available          BOOLEAN NOT NULL DEFAULT true,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_adj_ver_suggestion
        ON profile_adjustment_versions (suggestion_id);

CREATE TABLE IF NOT EXISTS profile_ai_reviews (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id              UUID NULL,
            status              VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at        TIMESTAMPTZ NULL,
            next_review_at      TIMESTAMPTZ NULL,
            model_name          VARCHAR(60) NULL,
            prompt_hash         VARCHAR(64) NULL,
            tokens_input        INTEGER NULL,
            tokens_output       INTEGER NULL,
            summary             TEXT NULL,
            findings            JSONB NOT NULL DEFAULT '{}',
            recommendations     JSONB NOT NULL DEFAULT '[]',
            contradictions      JSONB NOT NULL DEFAULT '[]',
            risk_flags          JSONB NOT NULL DEFAULT '[]',
            raw_response_ref    TEXT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_ai_review_requested_at
        ON profile_ai_reviews (requested_at DESC);

CREATE TABLE IF NOT EXISTS autopilot_pending_actions (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_id           UUID NULL REFERENCES profile_adjustment_suggestions(id) ON DELETE SET NULL,
            profile_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            action_type             VARCHAR(60) NOT NULL,
            action_status           VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            target_scope            VARCHAR(30) NOT NULL DEFAULT 'SHADOW',
            mutation_applied        BOOLEAN NOT NULL DEFAULT false,
            requires_human_approval BOOLEAN NOT NULL DEFAULT true,
            payload                 JSONB NOT NULL DEFAULT '{}',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NULL,
            CONSTRAINT chk_apa_mutation
                CHECK (mutation_applied = false OR requires_human_approval = true),
            CONSTRAINT chk_apa_action_type_not_create
                CHECK (action_type NOT IN ('CREATE_PROFILE','DUPLICATE_PROFILE','PROMOTE_LIVE','ENABLE_LIVE'))
        );

CREATE INDEX IF NOT EXISTS idx_apa_profile_status
        ON autopilot_pending_actions (profile_id, action_status, created_at DESC);

ALTER TABLE profile_intelligence_runs
            ADD COLUMN IF NOT EXISTS run_type VARCHAR(30) NULL,
            ADD COLUMN IF NOT EXISTS suggestions_generated INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ai_review_requested BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS ai_review_id UUID NULL,
            ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ NULL;

UPDATE alembic_version SET version_num='113_pi_live_engine' WHERE alembic_version.version_num = '000_baseline_prod_schema';

-- Running upgrade 113_pi_live_engine -> 114_watchlist_priority

CREATE OR REPLACE VIEW watchlist_performance_priority_base_view AS
        WITH trade_metrics AS (
            SELECT
                st.user_id,
                st.profile_id,
                COALESCE(MAX(st.profile_name), MAX(p.name)) AS profile_name,
                st.watchlist_id,
                COALESCE(MAX(st.watchlist_name), MAX(pw.name)) AS watchlist_name,
                COALESCE(MAX(st.watchlist_level), MAX(pw.level), 'L3') AS level,
                st.source,
                COUNT(*)::bigint AS total_trades,
                COUNT(*) FILTER (WHERE st.status IN ('PENDING', 'RUNNING'))::bigint AS open_trades,
                COUNT(*) FILTER (WHERE st.status = 'COMPLETED' AND st.pnl_pct IS NOT NULL)::bigint AS completed_trades,
                COUNT(*) FILTER (WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0)::bigint AS wins,
                COUNT(*) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0
                      AND st.holding_seconds IS NOT NULL
                      AND st.holding_seconds <= (ranking_config.config_json #>> '{thresholds,tp4h_seconds}')::integer
                )::bigint AS tp_4h_wins,
                COALESCE(SUM(st.pnl_pct) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct IS NOT NULL
                ), 0)::double precision AS pnl_pct_sum,
                COUNT(st.pnl_pct) FILTER (WHERE st.status = 'COMPLETED')::bigint AS pnl_count,
                COALESCE(SUM(st.pnl_usdt) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_usdt IS NOT NULL
                ), 0)::double precision AS pnl_total_usdt,
                COALESCE(SUM(st.holding_seconds) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0 AND st.holding_seconds IS NOT NULL
                ), 0)::double precision AS holding_win_sum,
                COUNT(st.holding_seconds) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0
                )::bigint AS holding_win_count,
                MIN(st.created_at) AS first_trade,
                MAX(st.created_at) AS last_trade
            FROM shadow_trades st
            LEFT JOIN profiles p ON p.id = st.profile_id
            LEFT JOIN pipeline_watchlists pw ON pw.id = st.watchlist_id
            JOIN config_profiles ranking_config
              ON ranking_config.user_id = st.user_id
             AND ranking_config.pool_id IS NULL
             AND ranking_config.config_type = 'watchlist_performance_ranking'
             AND ranking_config.is_active = true
            WHERE st.profile_id IS NOT NULL
            GROUP BY st.user_id, st.profile_id, st.watchlist_id, st.source
        ), entities AS (
            SELECT
                pw.user_id,
                pw.profile_id,
                p.name AS profile_name,
                pw.id AS watchlist_id,
                pw.name AS watchlist_name,
                pw.level,
                NULL::varchar AS source,
                0::bigint AS total_trades,
                0::bigint AS open_trades,
                0::bigint AS completed_trades,
                0::bigint AS wins,
                0::bigint AS tp_4h_wins,
                0::double precision AS pnl_pct_sum,
                0::bigint AS pnl_count,
                0::double precision AS pnl_total_usdt,
                0::double precision AS holding_win_sum,
                0::bigint AS holding_win_count,
                NULL::timestamptz AS first_trade,
                NULL::timestamptz AS last_trade
            FROM pipeline_watchlists pw
            JOIN profiles p ON p.id = pw.profile_id
            WHERE UPPER(pw.level) = 'L3'
            UNION
            SELECT
                p.user_id, p.id, p.name, NULL::uuid, NULL::varchar, 'L3', NULL::varchar,
                0::bigint, 0::bigint, 0::bigint, 0::bigint, 0::bigint,
                0::double precision, 0::bigint, 0::double precision,
                0::double precision, 0::bigint, NULL::timestamptz, NULL::timestamptz
            FROM profiles p
            WHERE p.is_shadow_only = true
              AND NOT EXISTS (
                  SELECT 1 FROM pipeline_watchlists pw
                  WHERE pw.profile_id = p.id AND UPPER(pw.level) = 'L3'
              )
        )
        SELECT * FROM trade_metrics
        UNION ALL
        SELECT * FROM entities;

INSERT INTO config_profiles
            (id, user_id, pool_id, config_type, config_json, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(), u.id, NULL, 'watchlist_performance_ranking',
            jsonb_build_object(
                'version', 1,
                'source_filter', jsonb_build_array('L3', 'L3_LAB'),
                'weights', jsonb_build_object('pnl', 35, 'win_rate', 20, 'sample', 15, 'tp4h', 15, 'pnl_total', 10),
                'normalization', jsonb_build_object('avg_pnl_pct_target', 1.0, 'sample_target', 500, 'pnl_total_usdt_target', 1000),
                'limits', jsonb_build_object('score_min', 0, 'score_max', 100, 'pnl_component_min', -20),
                'penalties', jsonb_build_object(
                    'holding_over_4h', 5, 'holding_over_8h', 10,
                    'low_n_under_30', 30, 'low_n_under_50', 15, 'low_n_under_100', 5,
                    'negative_avg_pnl', 25, 'negative_total_pnl', 10
                ),
                'thresholds', jsonb_build_object(
                    'sample_low_n', 30, 'sample_low', 50, 'sample_medium', 100, 'sample_high', 300,
                    'priority_a_plus', 75, 'priority_a', 60, 'priority_b', 45, 'priority_c', 30,
                    'low_n_score_cap', 44.99,
                    'good_win_rate', 0.50, 'good_tp4h_rate', 0.40, 'shadow_tp4h_rate', 0.20,
                    'tp4h_seconds', 14400, 'holding_warning_seconds', 14400, 'holding_severe_seconds', 28800
                )
            ),
            true, now(), now()
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1 FROM config_profiles cp
            WHERE cp.user_id = u.id
              AND cp.pool_id IS NULL
              AND cp.config_type = 'watchlist_performance_ranking'
              AND cp.is_active = true
        );

UPDATE alembic_version SET version_num='114_watchlist_priority' WHERE alembic_version.version_num = '113_pi_live_engine';
INFO  [alembic.runtime.migration] Running upgrade 113_pi_live_engine -> 114_watchlist_priority, Watchlist performance priority base view and DB-backed score config.
INFO  [alembic.runtime.migration] Running upgrade 114_watchlist_priority -> 115_autopilot_shadow_calibration, Autopilot shadow calibration: run errors table + fix requires_human_approval.
INFO  [alembic.runtime.migration] Running upgrade 115_autopilot_shadow_calibration -> 116_ai_review_safety, Fail-closed AI review contract and reclassification audit.
INFO  [alembic.runtime.migration] Running upgrade 116_ai_review_safety -> 117_ai_review_audit_name, Align AI review reclassification audit table with the operational contract.
INFO  [alembic.runtime.migration] Running upgrade 117_ai_review_audit_name -> 118_ai_review_analysis_context, AI Critic analysis context: audit trail for source, period, filters, sample.
INFO  [alembic.runtime.migration] Running upgrade 118_ai_review_analysis_context -> 119_shadow_closure_audit, Shadow trade closure audit table.

-- Running upgrade 114_watchlist_priority -> 115_autopilot_shadow_calibration

UPDATE profile_adjustment_suggestions
        SET requires_human_approval = false
        WHERE requires_human_approval = true
          AND status IN ('PENDING_SHADOW_VALIDATION', 'SHADOW_APPLIED', 'SHADOW_VALIDATING')
          AND mutation_applied = false;

UPDATE autopilot_pending_actions
        SET requires_human_approval = false
        WHERE requires_human_approval = true
          AND target_scope = 'SHADOW'
          AND mutation_applied = false;

CREATE TABLE IF NOT EXISTS autopilot_run_errors (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id uuid NULL,
            phase text NOT NULL DEFAULT 'shadow_calibration',
            error_code text NOT NULL DEFAULT 'UNKNOWN',
            severity text NOT NULL DEFAULT 'error',
            profile_id uuid NULL,
            suggestion_id uuid NULL,
            action_id uuid NULL,
            message text NOT NULL,
            stack_trace text NULL,
            payload jsonb NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS ix_autopilot_run_errors_run_id
        ON autopilot_run_errors(run_id);

CREATE INDEX IF NOT EXISTS ix_autopilot_run_errors_profile_id
        ON autopilot_run_errors(profile_id);

UPDATE alembic_version SET version_num='115_autopilot_shadow_calibration' WHERE alembic_version.version_num = '114_watchlist_priority';

-- Running upgrade 115_autopilot_shadow_calibration -> 116_ai_review_safety

CREATE TABLE IF NOT EXISTS profile_ai_review_reclassification_audit (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            review_id UUID NOT NULL REFERENCES profile_ai_reviews(id) ON DELETE RESTRICT,
            old_status VARCHAR(30) NOT NULL,
            new_status VARCHAR(30) NOT NULL,
            reason TEXT NOT NULL,
            fix_deployed_at TIMESTAMPTZ NOT NULL,
            review_snapshot JSONB NOT NULL,
            actor VARCHAR(120) NOT NULL,
            reclassified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_ai_review_reclassification UNIQUE (review_id)
        );

CREATE INDEX IF NOT EXISTS idx_ai_review_reclassification_at
                            ON profile_ai_review_reclassification_audit (reclassified_at DESC);

CREATE OR REPLACE FUNCTION enforce_completed_ai_review_contract()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'COMPLETED' AND (
                COALESCE(NEW.tokens_input, 0) <= 0
                OR COALESCE(NEW.tokens_output, 0) <= 0
                OR NULLIF(BTRIM(COALESCE(NEW.summary, '')), '') IS NULL
                OR NULLIF(BTRIM(COALESCE(NEW.model_name, '')), '') IS NULL
                OR NEW.completed_at IS NULL
            ) THEN
                RAISE EXCEPTION 'COMPLETED AI review violates fail-closed persistence contract';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_completed_ai_review_contract ON profile_ai_reviews;

CREATE TRIGGER trg_completed_ai_review_contract
                            BEFORE INSERT OR UPDATE ON profile_ai_reviews
                            FOR EACH ROW EXECUTE FUNCTION enforce_completed_ai_review_contract();

UPDATE alembic_version SET version_num='116_ai_review_safety' WHERE alembic_version.version_num = '115_autopilot_shadow_calibration';

-- Running upgrade 116_ai_review_safety -> 117_ai_review_audit_name

ALTER TABLE IF EXISTS profile_ai_review_reclassification_audit
        RENAME TO profile_ai_reviews_reclassification_audit;

UPDATE alembic_version SET version_num='117_ai_review_audit_name' WHERE alembic_version.version_num = '116_ai_review_safety';

-- Running upgrade 117_ai_review_audit_name -> 118_ai_review_analysis_context

ALTER TABLE profile_ai_reviews
        ADD COLUMN IF NOT EXISTS analysis_context jsonb;

ALTER TABLE profile_ai_reviews
        ADD COLUMN IF NOT EXISTS context_payload_hash text;

ALTER TABLE profile_ai_reviews
        ADD COLUMN IF NOT EXISTS context_query_hash text;

UPDATE profile_ai_reviews
        SET analysis_context = '{"_legacy": true, "note": "review created before analysis_context was tracked"}'::jsonb
        WHERE analysis_context IS NULL
          AND status = 'COMPLETED'
          AND tokens_input > 0;

UPDATE alembic_version SET version_num='118_ai_review_analysis_context' WHERE alembic_version.version_num = '117_ai_review_audit_name';

-- Running upgrade 118_ai_review_analysis_context -> 119_shadow_closure_audit

CREATE TABLE IF NOT EXISTS shadow_trade_closure_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            shadow_trade_id uuid NOT NULL,
            source text,
            symbol text,
            previous_status text,
            entry_price numeric,
            exit_price numeric,
            tp_price numeric,
            sl_price numeric,
            pnl_pct numeric,
            pnl_usdt numeric,
            closure_reason text NOT NULL,
            price_source text,
            price_timestamp timestamptz,
            price_age_seconds int,
INFO  [alembic.runtime.migration] Running upgrade 119_shadow_closure_audit -> 120_mutation_audit_enrichment, Mutation audit enrichment.
INFO  [alembic.runtime.migration] Running upgrade 120_mutation_audit_enrichment -> 121_shadow_validation_cycle, Phase 3 shadow validation cycle.
INFO  [alembic.runtime.migration] Running upgrade 121_shadow_validation_cycle -> 122_backfill_ranking_dec_id, Backfill decision_id in ml_opportunity_rankings via heuristic JOIN.
INFO  [alembic.runtime.migration] Running upgrade 122_backfill_ranking_dec_id -> b2780092b9ca, add_ml_contracts_and_gates
            closer_run_id uuid,
            payload jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS ix_stca_shadow_trade_id ON shadow_trade_closure_audit (shadow_trade_id);

CREATE INDEX IF NOT EXISTS ix_stca_source_reason ON shadow_trade_closure_audit (source, closure_reason);

CREATE INDEX IF NOT EXISTS ix_stca_created_at ON shadow_trade_closure_audit (created_at);

CREATE INDEX IF NOT EXISTS ix_stca_closer_run_id ON shadow_trade_closure_audit (closer_run_id);

UPDATE alembic_version SET version_num='119_shadow_closure_audit' WHERE alembic_version.version_num = '118_ai_review_analysis_context';

-- Running upgrade 119_shadow_closure_audit -> 120_mutation_audit_enrichment

ALTER TABLE profile_intelligence_audit_log
            ADD COLUMN IF NOT EXISTS profile_id       uuid NULL,
            ADD COLUMN IF NOT EXISTS mutation_applied boolean NULL,
            ADD COLUMN IF NOT EXISTS mutation_status  text NULL,
            ADD COLUMN IF NOT EXISTS dry_run          boolean NULL;

CREATE INDEX IF NOT EXISTS idx_pi_audit_profile_id ON profile_intelligence_audit_log (profile_id);

CREATE INDEX IF NOT EXISTS idx_pi_audit_mutation_status ON profile_intelligence_audit_log (mutation_status);

ALTER TABLE autopilot_audit_logs
            ADD COLUMN IF NOT EXISTS dry_run         boolean NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS mutation_status text NULL;

CREATE TABLE IF NOT EXISTS profile_indicator_mutation_links (
            id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_performance_id   uuid NULL,
            profile_id                 uuid NOT NULL,
            profile_name               text NULL,
            indicator_name             text NOT NULL,
            bucket                     text NOT NULL,
            run_id                     uuid NULL,
            suggestion_id              uuid NULL,
            autopilot_audit_log_id     uuid NULL,
            profile_adjustment_version_id uuid NULL,
            mutation_action            text NOT NULL,
            mutation_status            text NOT NULL,
            mutation_applied           boolean NOT NULL DEFAULT false,
            dry_run                    boolean NOT NULL DEFAULT true,
            evidence_json              jsonb NOT NULL DEFAULT '{}'::jsonb,
            diff_json                  jsonb NOT NULL DEFAULT '{}'::jsonb,
            ai_reason                  text NULL,
            autopilot_reason           text NULL,
            created_at                 timestamptz NOT NULL DEFAULT now()
        );

CREATE INDEX IF NOT EXISTS idx_piml_profile_id_created_at ON profile_indicator_mutation_links (profile_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_piml_indicator_bucket ON profile_indicator_mutation_links (indicator_name, bucket);

CREATE INDEX IF NOT EXISTS idx_piml_mutation_status ON profile_indicator_mutation_links (mutation_status);

CREATE INDEX IF NOT EXISTS idx_piml_autopilot_audit_log_id ON profile_indicator_mutation_links (autopilot_audit_log_id);

UPDATE alembic_version SET version_num='120_mutation_audit_enrichment' WHERE alembic_version.version_num = '119_shadow_closure_audit';

-- Running upgrade 120_mutation_audit_enrichment -> 121_shadow_validation_cycle

ALTER TABLE profile_adjustment_versions
            ADD COLUMN IF NOT EXISTS validated_at     TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS win_rate_before  NUMERIC(6,4) NULL,
            ADD COLUMN IF NOT EXISTS win_rate_after   NUMERIC(6,4) NULL,
            ADD COLUMN IF NOT EXISTS validation_reason TEXT NULL;

UPDATE profile_adjustment_versions
        SET shadow_validation_status = 'INVALIDATED',
            rollback_available       = false,
            validation_reason        = 'tombstoned_2026-06-28: '
                                       'current_value=null AND uniform_diff_65_70 '
                                       'AND phase3_not_implemented'
        WHERE shadow_validation_status = 'PENDING_VALIDATION';

UPDATE autopilot_pending_actions
        SET action_status = 'CANCELLED',
            updated_at    = now()
        WHERE action_status = 'PROCESSING'
          AND suggestion_id IN (
              SELECT id FROM profile_adjustment_suggestions
              WHERE status = 'SHADOW_APPLIED'
          );

UPDATE profile_adjustment_suggestions
        SET status     = 'SUPERSEDED',
            updated_at = now()
        WHERE status = 'SHADOW_APPLIED'
          AND id IN (
              SELECT suggestion_id
              FROM profile_adjustment_versions
              WHERE shadow_validation_status = 'INVALIDATED'
          );

UPDATE alembic_version SET version_num='121_shadow_validation_cycle' WHERE alembic_version.version_num = '120_mutation_audit_enrichment';

-- Running upgrade 121_shadow_validation_cycle -> 122_backfill_ranking_dec_id

UPDATE ml_opportunity_rankings r
        SET decision_id = (
            SELECT d.id
            FROM decisions_log d
            WHERE d.symbol = r.symbol
              AND d.created_at BETWEEN r.ranked_at - interval '5 seconds'
                                   AND r.ranked_at + interval '5 seconds'
            ORDER BY ABS(EXTRACT(EPOCH FROM (d.created_at - r.ranked_at)))
            LIMIT 1
        )
        WHERE r.decision_id IS NULL;

UPDATE alembic_version SET version_num='122_backfill_ranking_dec_id' WHERE alembic_version.version_num = '121_shadow_validation_cycle';

-- Running upgrade 122_backfill_ranking_dec_id -> b2780092b9ca

ALTER TABLE ml_models ADD COLUMN target_window_seconds INTEGER;

ALTER TABLE ml_models ADD COLUMN label_contract_id UUID;

ALTER TABLE ml_models ADD COLUMN dataset_contract_id UUID;

ALTER TABLE ml_models ADD COLUMN feature_contract_id UUID;

ALTER TABLE ml_models ADD COLUMN tp_pct NUMERIC;

ALTER TABLE ml_models ADD COLUMN sl_pct NUMERIC;

ALTER TABLE ml_models ADD COLUMN fee_roundtrip_pct NUMERIC;

ALTER TABLE ml_models ADD COLUMN label_net_of_fees BOOLEAN;

ALTER TABLE ml_models ADD COLUMN barrier_mode VARCHAR(50);

ALTER TABLE ml_models ADD COLUMN intrabar_policy VARCHAR(50);

ALTER TABLE ml_models ADD COLUMN ohlcv_timeframe VARCHAR(10);

ALTER TABLE ml_models ADD COLUMN maturity_policy VARCHAR(50);

ALTER TABLE ml_models ADD COLUMN macro_features_enabled BOOLEAN DEFAULT 'false';

ALTER TABLE ml_models ADD COLUMN test_metrics_json JSONB;

CREATE TABLE ml_readiness_gate_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    run_id VARCHAR(255) NOT NULL,
    model_lane VARCHAR(50),
    readiness_status VARCHAR(50) NOT NULL,
    block_reason TEXT,
    positive_rate_train NUMERIC,
    positive_rate_val NUMERIC,
    positive_rate_test NUMERIC,
    dead_feature_ratio NUMERIC,
    psi_max NUMERIC,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_ml_readiness_gate_runs_created_at ON ml_readiness_gate_runs (created_at);

CREATE TABLE ml_dataset_readiness_reports (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    dataset_id VARCHAR(255) NOT NULL,
    total_features INTEGER NOT NULL,
    dead_features INTEGER NOT NULL,
    dead_feature_ratio NUMERIC NOT NULL,
    min_coverage NUMERIC NOT NULL,
    readiness_status VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_ml_dataset_readiness_reports_dataset_id ON ml_dataset_readiness_reports (dataset_id);

CREATE TABLE ml_feature_observations (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    dataset_id VARCHAR(255) NOT NULL,
    feature_name VARCHAR(255) NOT NULL,
    value NUMERIC,
    source_timestamp TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE,
    source_group VARCHAR(50),
    stale BOOLEAN,
    formula_version VARCHAR(50),
    coverage_status VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_ml_feature_observations_dataset_id ON ml_feature_observations (dataset_id);

CREATE TABLE ml_feature_drift_reports (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    dataset_id VARCHAR(255) NOT NULL,
    feature_name VARCHAR(255) NOT NULL,
    psi_train_test NUMERIC,
    importance NUMERIC,
    source_timestamp_coverage NUMERIC,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

UPDATE alembic_version SET version_num='b2780092b9ca' WHERE alembic_version.version_num = '122_backfill_ranking_dec_id';

INFO  [alembic.runtime.migration] Running upgrade b2780092b9ca -> c001v52activ, set activated_at for v52 (was NULL after direct-DB promotion)
-- Running upgrade b2780092b9ca -> c001v52activ

UPDATE ml_models
           SET activated_at = created_at
         WHERE version = '52'
           AND status = 'active'
           AND activated_at IS NULL;

UPDATE alembic_version SET version_num='c001v52activ' WHERE alembic_version.version_num = 'b2780092b9ca';

INFO  [alembic.runtime.migration] Running upgrade c001v52activ -> c002rastreat, ML audit tables: label/feature/dataset contracts, training dataset, threshold curve, gate results
-- Running upgrade c001v52activ -> c002rastreat

CREATE TABLE ml_label_contracts (
    id VARCHAR(32) NOT NULL,
    name VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL,
    description TEXT,
    sql_expression TEXT,
    target_window_seconds INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT uq_label_contract_name_version UNIQUE (name, version)
);

CREATE TABLE ml_feature_contracts (
    id VARCHAR(32) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    feature_columns_hash VARCHAR(64),
    feature_count INTEGER,
    feature_columns_json JSONB,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT uq_feature_contract_schema_version UNIQUE (schema_version)
);

CREATE TABLE ml_dataset_contracts (
    id VARCHAR(32) NOT NULL,
    label_contract_id VARCHAR(32),
    feature_contract_id VARCHAR(32),
    source_filter VARCHAR(128),
    model_lane VARCHAR(32),
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE TABLE ml_training_dataset (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    model_id UUID,
    dataset_contract_id VARCHAR(32),
    source_filter VARCHAR(128),
    n_samples INTEGER,
    n_positive INTEGER,
    n_negative INTEGER,
    positive_rate FLOAT,
    cutoff_at TIMESTAMP WITH TIME ZONE,
    train_from TIMESTAMP WITH TIME ZONE,
    train_to TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE TABLE ml_threshold_curve (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    model_id UUID NOT NULL,
    threshold FLOAT NOT NULL,
    precision_score FLOAT,
    recall_score FLOAT,
    fpr FLOAT,
    f1_score FLOAT,
    n_positive INTEGER,
    n_negative INTEGER,
    is_selected BOOLEAN,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE TABLE ml_promotion_gate_results (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    model_id UUID NOT NULL,
    gate_version VARCHAR(32),
    status VARCHAR(16) NOT NULL,
    reasons_json JSONB,
    input_json JSONB,
    evaluated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE TABLE ml_model_predictions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    model_id UUID NOT NULL,
    model_lane VARCHAR(32),
    model_version VARCHAR(32),
    decision_id BIGINT,
    shadow_trade_id UUID,
    symbol VARCHAR(32),
    profile_id UUID,
    win_fast_probability FLOAT,
    threshold_used FLOAT,
    model_approved BOOLEAN,
    p_l1_win FLOAT,
    p_l3_profile_win FLOAT,
    features_snapshot JSONB,
    score_status VARCHAR(16),
    scored_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (id)
);

INSERT INTO ml_label_contracts (id, name, version, description, sql_expression, target_window_seconds)
        VALUES
        ('is_win_fast_v1_30m', 'is_win_fast_v1', '1.0',
         'Fast TP within 30min � original label (ttt buckets 0-15m and 15-30m)',
         'ttt_fast_win_bucket IN (''WIN_0_15M'',''WIN_15_30M'') AND ttt_analysis_done = TRUE',
         1800),
        ('is_tp_4h_v1_30m', 'is_tp_4h_v1', '1.0',
         'Fast TP <= 30min � label for v52 training (TRAIN_CUTOFF 2026-06-25T19:45)',
         'ttt_fast_win_bucket IN (''WIN_0_15M'',''WIN_15_30M'') AND ttt_analysis_done = TRUE',
         1800)
        ON CONFLICT DO NOTHING;

UPDATE alembic_version SET version_num='c002rastreat' WHERE alembic_version.version_num = 'c001v52activ';

INFO  [alembic.runtime.migration] Running upgrade c002rastreat -> c003shadow_idx_narrowfix, narrow ux_shadow_running_user_source to completed_at IS NULL
-- Running upgrade c002rastreat -> c003shadow_idx_narrowfix

DROP INDEX IF EXISTS ux_shadow_running_user_source;

CREATE UNIQUE INDEX ux_shadow_running_user_source
        ON shadow_trades (user_id, symbol, source)
        WHERE profile_id IS NULL AND completed_at IS NULL;

UPDATE alembic_version SET version_num='c003shadow_idx_narrowfix' WHERE alembic_version.version_num = 'c002rastreat';

INFO  [alembic.runtime.migration] Running upgrade c003shadow_idx_narrowfix -> 123_current_l3_rejected_rankings, Attach current L3 rejected shadows to their profile and rank them.
-- Running upgrade c003shadow_idx_narrowfix -> 123_current_l3_rejected_rankings

WITH candidates AS (
            SELECT st.id,
                   pw.profile_id,
                   p.name AS profile_name,
                   p.updated_at AS profile_version,
                   ROW_NUMBER() OVER (
                       PARTITION BY pw.profile_id, st.symbol, st.source,
                                    shadow_lab_hour_bucket(st.created_at)
                       ORDER BY st.created_at, st.id
                   ) AS bucket_rank
            FROM shadow_trades AS st
            JOIN pipeline_watchlists AS pw
              ON pw.user_id = st.user_id
             AND pw.id = st.watchlist_id
             AND UPPER(pw.level) = 'L3'
            JOIN profiles AS p
              ON p.id = pw.profile_id
             AND p.user_id = pw.user_id
            WHERE st.source = 'L3_REJECTED'
              AND st.profile_id IS NULL
              AND st.created_at >= pw.created_at
        )
        UPDATE shadow_trades AS st
           SET profile_id = pw.profile_id,
               profile_name = pw.profile_name,
               profile_version = pw.profile_version,
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_123_current_watchlist_pair',
               lineage_resolved_at = now()
          FROM candidates AS pw
         WHERE st.id = pw.id
           AND pw.bucket_rank = 1
           AND NOT EXISTS (
               SELECT 1
               FROM shadow_trades AS existing
               WHERE existing.profile_id = pw.profile_id
                 AND existing.symbol = st.symbol
                 AND existing.source = st.source
                 AND shadow_lab_hour_bucket(existing.created_at)
                     = shadow_lab_hour_bucket(st.created_at)
           );

UPDATE config_profiles
           SET config_json = jsonb_set(
                   config_json,
                   '{source_filter}',
                   COALESCE(config_json->'source_filter', '[]'::jsonb)
                     || '["L3_REJECTED"]'::jsonb,
                   true
               ),
               updated_at = now()
         WHERE config_type = 'watchlist_performance_ranking'
           AND is_active = true
           AND pool_id IS NULL
           AND NOT COALESCE(config_json->'source_filter', '[]'::jsonb)
                   @> '["L3_REJECTED"]'::jsonb;

UPDATE alembic_version SET version_num='123_current_l3_rejected_rankings' WHERE alembic_version.version_num = 'c003shadow_idx_narrowfix';

INFO  [alembic.runtime.migration] Running upgrade 123_current_l3_rejected_rankings -> 124_repair_current_l3_rankings, Repair current L3 rejected shadow ranking backfill.
-- Running upgrade 123_current_l3_rejected_rankings -> 124_repair_current_l3_rankings

WITH raw_candidates AS (
            SELECT st.id,
                   st.status,
                   st.created_at,
                   st.symbol,
                   st.source,
                   pw.profile_id,
                   p.name AS profile_name,
                   p.updated_at AS profile_version
            FROM shadow_trades AS st
            JOIN pipeline_watchlists AS pw
              ON pw.user_id = st.user_id
             AND pw.id = st.watchlist_id
             AND UPPER(pw.level) = 'L3'
            JOIN profiles AS p
              ON p.id = pw.profile_id
             AND p.user_id = pw.user_id
            WHERE st.source = 'L3_REJECTED'
              AND st.profile_id IS NULL
              AND st.created_at >= pw.created_at
        ),
        active_candidates AS (
            SELECT raw.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY raw.profile_id, raw.symbol, raw.source
                       ORDER BY raw.created_at, raw.id
                   ) AS active_rank
            FROM raw_candidates AS raw
            WHERE raw.status IN ('RUNNING', 'PENDING')
        ),
        active_deduplicated AS (
            SELECT raw.*
            FROM raw_candidates AS raw
            LEFT JOIN active_candidates AS active ON active.id = raw.id
            WHERE raw.status NOT IN ('RUNNING', 'PENDING')
               OR active.active_rank = 1
        ),
        candidates AS (
            SELECT deduped.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY deduped.profile_id,
                                    deduped.symbol,
                                    deduped.source,
                                    shadow_lab_hour_bucket(deduped.created_at)
                       ORDER BY deduped.created_at, deduped.id
                   ) AS bucket_rank
            FROM active_deduplicated AS deduped
        )
        UPDATE shadow_trades AS st
           SET profile_id = candidate.profile_id,
               profile_name = candidate.profile_name,
               profile_version = candidate.profile_version,
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_124_current_watchlist_pair',
               lineage_resolved_at = now()
          FROM candidates AS candidate
         WHERE st.id = candidate.id
           AND candidate.bucket_rank = 1
           AND NOT EXISTS (
               SELECT 1
               FROM shadow_trades AS existing
               WHERE existing.profile_id = candidate.profile_id
                 AND existing.symbol = candidate.symbol
                 AND existing.source = candidate.source
                 AND shadow_lab_hour_bucket(existing.created_at)
                     = shadow_lab_hour_bucket(candidate.created_at)
           )
           AND (
               candidate.status NOT IN ('RUNNING', 'PENDING')
               OR NOT EXISTS (
                   SELECT 1
                   FROM shadow_trades AS existing_active
                   WHERE existing_active.profile_id = candidate.profile_id
                     AND existing_active.symbol = candidate.symbol
                     AND existing_active.source = candidate.source
                     AND existing_active.status IN ('RUNNING', 'PENDING')
               )
           );

UPDATE config_profiles
           SET config_json = jsonb_set(
                   config_json,
                   '{source_filter}',
                   COALESCE(config_json->'source_filter', '[]'::jsonb)
                     || '["L3_REJECTED"]'::jsonb,
                   true
               ),
               updated_at = now()
         WHERE config_type = 'watchlist_performance_ranking'
           AND is_active = true
           AND pool_id IS NULL
           AND NOT COALESCE(config_json->'source_filter', '[]'::jsonb)
                   @> '["L3_REJECTED"]'::jsonb;

UPDATE alembic_version SET version_num='124_repair_current_l3_rankings' WHERE alembic_version.version_num = '123_current_l3_rejected_rankings';

-- Running upgrade 124_repair_current_l3_rankings -> 125_shadow_profile_lineage

DROP INDEX IF EXISTS uq_shadow_lab_profile_symbol_bucket;

CREATE UNIQUE INDEX uq_shadow_lab_profile_symbol_bucket
            ON shadow_trades (
                profile_id,
                symbol,
                source,
                shadow_lab_hour_bucket(created_at)
            )
         WHERE profile_id IS NOT NULL
           AND source <> 'L1_SPECTRUM';

UPDATE shadow_trades AS st
           SET profile_id = pw.profile_id,
               profile_name = p.name,
               profile_version = COALESCE(p.profile_version, p.updated_at),
               strategy_type = 'PROFILE_L1',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_125_watchlist_profile',
               lineage_resolved_at = now()
          FROM pipeline_watchlists AS pw
          JOIN profiles AS p
            ON p.id = pw.profile_id
           AND p.user_id = pw.user_id
         WHERE st.watchlist_id = pw.id
           AND st.user_id = pw.user_id
           AND st.profile_id IS NULL
           AND pw.profile_id IS NOT NULL
           AND st.source = 'L1_SPECTRUM';

UPDATE shadow_trades AS st
           SET profile_id = dl.profile_id,
               profile_name = COALESCE(dl.profile_name, p.name),
               profile_version = COALESCE(dl.profile_version, p.profile_version),
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_125_decision_profile',
               lineage_resolved_at = now()
          FROM decisions_log AS dl
          JOIN profiles AS p
            ON p.id = dl.profile_id
           AND p.user_id = dl.user_id
         WHERE st.decision_id = dl.id
           AND st.user_id = dl.user_id
           AND st.profile_id IS NULL
           AND dl.profile_id IS NOT NULL
           AND st.source = 'L3';

WITH incident_decisions(decision_id, watchlist_id) AS (
            VALUES
                (121012, '29b62873-abb8-4538-a2a3-5456043c0e2f'::uuid),
                (121021, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121029, '51f51586-2b4c-4208-a09b-c8f3a10d2097'::uuid),
                (121030, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121031, 'aa0f91a8-d096-4337-a70f-72724243e213'::uuid),
                (121032, '1e797675-881b-404b-993f-a417a6e506b1'::uuid),
                (121033, '9100210c-58f5-4852-88e8-29d68bb228c7'::uuid)
        )
        UPDATE decisions_log AS dl
           SET profile_id = pw.profile_id,
               profile_name = p.name,
               profile_version = COALESCE(p.profile_version, p.updated_at)
          FROM incident_decisions AS incident
          JOIN pipeline_watchlists AS pw
            ON pw.id = incident.watchlist_id
          JOIN profiles AS p
            ON p.id = pw.profile_id
           AND p.user_id = pw.user_id
         WHERE dl.id = incident.decision_id
           AND dl.user_id = pw.user_id
           AND dl.profile_id IS NULL
           AND dl.strategy = 'L3';

WITH incident_decisions(decision_id, watchlist_id) AS (
            VALUES
                (121012, '29b62873-abb8-4538-a2a3-5456043c0e2f'::uuid),
                (121021, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121029, '51f51586-2b4c-4208-a09b-c8f3a10d2097'::uuid),
                (121030, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121031, 'aa0f91a8-d096-4337-a70f-72724243e213'::uuid),
                (121032, '1e797675-881b-404b-993f-a417a6e506b1'::uuid),
                (121033, '9100210c-58f5-4852-88e8-29d68bb228c7'::uuid)
        )
        UPDATE shadow_trades AS st
           SET watchlist_id = pw.id,
               watchlist_name = pw.name,
               watchlist_level = pw.level,
               source_watchlist_id = pw.source_watchlist_id,
               profile_id = pw.profile_id,
               profile_name = p.name,
               profile_version = COALESCE(p.profile_version, p.updated_at),
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_125_archived_request_log',
               lineage_resolved_at = now()
          FROM incident_decisions AS incident
          JOIN pipeline_watchlists AS pw
            ON pw.id = incident.watchlist_id
          JOIN profiles AS p
            ON p.id = pw.profile_id
           AND p.user_id = pw.user_id
         WHERE st.decision_id = incident.decision_id
           AND st.user_id = pw.user_id
           AND st.profile_id IS NULL
           AND st.source = 'L3';

UPDATE alembic_version SET version_num='125_shadow_profile_lineage' WHERE alembic_version.version_num = '124_repair_current_l3_rankings';

-- Running upgrade 125_shadow_profile_lineage -> 126_profile_intelligence_copilot

CREATE TABLE copilot_sessions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE,
    context JSONB DEFAULT '{}'::jsonb NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX idx_copilot_sessions_user_started ON copilot_sessions (user_id, started_at);

CREATE TABLE copilot_messages (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    session_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(session_id) REFERENCES copilot_sessions (id) ON DELETE CASCADE
);

CREATE INDEX idx_copilot_messages_session_created ON copilot_messages (session_id, created_at);

INFO  [alembic.runtime.migration] Running upgrade 124_repair_current_l3_rankings -> 125_shadow_profile_lineage, Backfill exact profile lineage for shadow trades.
INFO  [alembic.runtime.migration] Running upgrade 125_shadow_profile_lineage -> 126_profile_intelligence_copilot, Profile Intelligence operational Co-Pilot.
CREATE TABLE copilot_query_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    session_id UUID,
    query_text TEXT NOT NULL,
    query_hash VARCHAR(64) NOT NULL,
    query_type VARCHAR(30) NOT NULL,
    reason TEXT,
    parameters JSONB DEFAULT '{}'::jsonb NOT NULL,
    status VARCHAR(30) NOT NULL,
    rows_returned INTEGER,
    execution_ms INTEGER,
    result_preview JSONB,
    result_truncated BOOLEAN DEFAULT false NOT NULL,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES copilot_sessions (id) ON DELETE SET NULL
);

CREATE INDEX idx_copilot_query_runs_user_created ON copilot_query_runs (user_id, created_at);

CREATE INDEX idx_copilot_query_runs_session ON copilot_query_runs (session_id, created_at);

CREATE TABLE copilot_action_plans (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    session_id UUID,
    action_type VARCHAR(80) NOT NULL,
    target_type VARCHAR(60) NOT NULL,
    target_id VARCHAR(100),
    objective TEXT NOT NULL,
    evidence JSONB DEFAULT '{}'::jsonb NOT NULL,
    proposed_diff JSONB DEFAULT '[]'::jsonb NOT NULL,
    execution_payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    risk_assessment TEXT,
    rollback_plan JSONB DEFAULT '{}'::jsonb NOT NULL,
    target_state_hash VARCHAR(64),
    status VARCHAR(30) DEFAULT 'DRY_RUN' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE,
    approved_by UUID,
    approval_text VARCHAR(80),
    executed_at TIMESTAMP WITH TIME ZONE,
    execution_result JSONB,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES copilot_sessions (id) ON DELETE SET NULL,
    FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX idx_copilot_actions_user_status ON copilot_action_plans (user_id, status, created_at);

CREATE TABLE copilot_skills (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    name VARCHAR(160) NOT NULL,
    skill_type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    version INTEGER DEFAULT '1' NOT NULL,
    status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL,
    confidence NUMERIC(5, 4),
    source VARCHAR(160),
    requires_approval BOOLEAN DEFAULT false NOT NULL,
    approved_by UUID,
    approved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_copilot_skill_user_name_version UNIQUE (user_id, name, version),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX idx_copilot_skills_retrieval ON copilot_skills (user_id, status, skill_type);

CREATE TABLE copilot_audit_logs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    session_id UUID,
    event_type VARCHAR(80) NOT NULL,
    actor_user_id UUID,
    action_plan_id UUID,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES copilot_sessions (id) ON DELETE SET NULL,
    FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY(action_plan_id) REFERENCES copilot_action_plans (id) ON DELETE SET NULL
);

CREATE INDEX idx_copilot_audit_user_created ON copilot_audit_logs (user_id, created_at);

UPDATE alembic_version SET version_num='126_profile_intelligence_copilot' WHERE alembic_version.version_num = '125_shadow_profile_lineage';

INFO  [alembic.runtime.migration] Running upgrade 126_profile_intelligence_copilot -> 127_shadow_fs_immutable, Guard shadow trade feature snapshots.
-- Running upgrade 126_profile_intelligence_copilot -> 127_shadow_fs_immutable

UPDATE config_profiles
           SET config_json = config_json
               || jsonb_build_object(
                    'ml_backfill_marker_key',
                    COALESCE(config_json->>'ml_backfill_marker_key', '_directional_backfill'),
                    'ml_backfilled_feature_names',
                    COALESCE(
                        config_json->'ml_backfilled_feature_names',
                        '[
                          "adx_slope_3",
                          "rsi_slope_3",
                          "rsi_slope_5",
                          "macd_hist_slope_3",
                          "macd_hist_slope_5",
                          "higher_highs_5",
                          "higher_lows_5",
                          "vwap_reclaim_bool",
                          "ema21_ema50_distance_pct",
                          "di_plus_minus_diff"
                        ]'::jsonb
                    )
                  ),
               updated_at = now()
         WHERE config_type = 'ml';

ALTER TABLE ml_dataset_readiness_reports
        ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE OR REPLACE FUNCTION prevent_shadow_features_snapshot_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.features_snapshot IS NOT NULL
             AND OLD.features_snapshot <> '{}'::jsonb
             AND NEW.features_snapshot IS DISTINCT FROM OLD.features_snapshot THEN
            RAISE EXCEPTION 'shadow_trades.features_snapshot is immutable after INSERT'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$;;

DROP TRIGGER IF EXISTS trg_shadow_features_snapshot_immutable ON shadow_trades;

CREATE TRIGGER trg_shadow_features_snapshot_immutable
        BEFORE UPDATE OF features_snapshot ON shadow_trades
        FOR EACH ROW
        EXECUTE FUNCTION prevent_shadow_features_snapshot_update();

UPDATE alembic_version SET version_num='127_shadow_fs_immutable' WHERE alembic_version.version_num = '126_profile_intelligence_copilot';

INFO  [alembic.runtime.migration] Running upgrade 127_shadow_fs_immutable -> 128_shadow_force_close, Configure shadow force-close policy.
-- Running upgrade 127_shadow_fs_immutable -> 128_shadow_force_close

UPDATE config_profiles
           SET config_json = config_json
               || jsonb_build_object(
                    'shadow_max_open_age_hours',
                    COALESCE(config_json->'shadow_max_open_age_hours', '18'::jsonb),
                    'shadow_force_close_policy',
                    COALESCE(
                        config_json->'shadow_force_close_policy',
                        '"TIMEOUT_LAST_KNOWN_PRICE"'::jsonb
                    )
                  ),
               updated_at = now()
         WHERE config_type = 'ml'
           AND is_active = true;

UPDATE alembic_version SET version_num='128_shadow_force_close' WHERE alembic_version.version_num = '127_shadow_fs_immutable';

INFO  [alembic.runtime.migration] Running upgrade 128_shadow_force_close -> 129_crypto_ev_score, Crypto EV operational score snapshots.
-- Running upgrade 128_shadow_force_close -> 129_crypto_ev_score

CREATE TABLE crypto_ev_l3_replay_flags (
    shadow_trade_id UUID NOT NULL,
    computed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    would_pass_l3 BOOLEAN,
    replay_status TEXT NOT NULL,
    l3_config_version TEXT NOT NULL,
    replay_reason TEXT NOT NULL,
    replay_details JSONB DEFAULT '{}'::jsonb NOT NULL,
    PRIMARY KEY (shadow_trade_id),
    CONSTRAINT ck_crypto_ev_l3_replay_status CHECK (replay_status IN ('PASSED','FAILED','UNREPLAYABLE')),
    CONSTRAINT ck_crypto_ev_l3_replay_status_bool CHECK ((replay_status = 'PASSED' AND would_pass_l3 IS true) OR (replay_status = 'FAILED' AND would_pass_l3 IS false) OR (replay_status = 'UNREPLAYABLE' AND would_pass_l3 IS NULL)),
    FOREIGN KEY(shadow_trade_id) REFERENCES shadow_trades (id) ON DELETE CASCADE
);

CREATE INDEX idx_crypto_ev_l3_replay_flags_pass ON crypto_ev_l3_replay_flags (would_pass_l3, computed_at);

CREATE TABLE crypto_ev_snapshots (
    id BIGSERIAL NOT NULL,
    computed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    symbol TEXT NOT NULL,
    view TEXT NOT NULL,
    window_hours INTEGER NOT NULL,
    n_trades INTEGER NOT NULL,
    n_excluded_no_pnl INTEGER DEFAULT 0 NOT NULL,
    n_excluded_unreplayable INTEGER DEFAULT 0 NOT NULL,
    ev_symbol NUMERIC,
    ev_prior NUMERIC NOT NULL,
    atr_bucket TEXT NOT NULL,
    shrinkage_k INTEGER NOT NULL,
    w NUMERIC NOT NULL,
    ev_shrunk NUMERIC NOT NULL,
    score NUMERIC NOT NULL,
    state TEXT NOT NULL,
    ml_component_applied BOOLEAN DEFAULT false NOT NULL,
    ml_component_value NUMERIC,
    ml_model_version TEXT,
    config_version TEXT NOT NULL,
    l3_config_version TEXT,
    audit_json JSONB DEFAULT '{}'::jsonb NOT NULL,
    PRIMARY KEY (id, computed_at),
    CONSTRAINT ck_crypto_ev_snapshots_view CHECK (view IN ('executable','spectrum')),
    CONSTRAINT ck_crypto_ev_snapshots_state CHECK (state IN ('FAVORABLE','NEUTRAL','RISKY','AVOID','INSUFFICIENT_DATA'))
);

CREATE INDEX idx_crypto_ev_snapshots_symbol_current ON crypto_ev_snapshots (symbol, computed_at DESC);

CREATE INDEX idx_crypto_ev_snapshots_view_current ON crypto_ev_snapshots (view, symbol, computed_at DESC);

CREATE OR REPLACE VIEW crypto_ev_current AS
        SELECT DISTINCT ON (symbol, view)
               id, computed_at, symbol, view, window_hours, n_trades,
               n_excluded_no_pnl, n_excluded_unreplayable, ev_symbol, ev_prior, atr_bucket,
               shrinkage_k, w, ev_shrunk, score, state,
               ml_component_applied, ml_component_value, ml_model_version,
               config_version, l3_config_version, audit_json
          FROM crypto_ev_snapshots
         ORDER BY symbol, view, computed_at DESC, id DESC;

INSERT INTO config_profiles (id, user_id, pool_id, config_type, config_json, is_active, created_at, updated_at)
            SELECT gen_random_uuid(), u.id, NULL, 'crypto_ev', CAST(NULL AS jsonb), true, now(), now()
              FROM users u
             WHERE NOT EXISTS (
                   SELECT 1 FROM config_profiles cp
                    WHERE cp.user_id = u.id
                      AND cp.pool_id IS NULL
                      AND cp.config_type = 'crypto_ev'
             );

UPDATE alembic_version SET version_num='129_crypto_ev_score' WHERE alembic_version.version_num = '128_shadow_force_close';

INFO  [alembic.runtime.migration] Running upgrade 129_crypto_ev_score -> 130_pool_asset_exclusions, Persist operator removals so automatic pool discovery cannot restore them.
-- Running upgrade 129_crypto_ev_score -> 130_pool_asset_exclusions

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE pool_asset_exclusions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    pool_id UUID NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    reason VARCHAR(32) DEFAULT 'manual_removal' NOT NULL,
    created_by UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_pool_asset_exclusions_pool_symbol UNIQUE (pool_id, symbol),
    FOREIGN KEY(pool_id) REFERENCES pools (id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_pool_asset_exclusions_pool_id ON pool_asset_exclusions (pool_id);

UPDATE alembic_version SET version_num='130_pool_asset_exclusions' WHERE alembic_version.version_num = '129_crypto_ev_score';

INFO  [alembic.runtime.migration] Running upgrade 130_pool_asset_exclusions -> 131_ml_governance_v2, ML governance v2, immutable snapshot lineage, and evidence registry.
-- Running upgrade 130_pool_asset_exclusions -> 131_ml_governance_v2

ALTER TABLE ml_models
            ADD COLUMN IF NOT EXISTS descriptive_status VARCHAR(48),
            ADD COLUMN IF NOT EXISTS predictive_status VARCHAR(48),
            ADD COLUMN IF NOT EXISTS calibration_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS rule_generation_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS autopilot_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS execution_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS governance_reason JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE ml_threshold_curve
            ADD COLUMN IF NOT EXISTS coverage NUMERIC,
            ADD COLUMN IF NOT EXISTS specificity NUMERIC,
            ADD COLUMN IF NOT EXISTS mcc NUMERIC,
            ADD COLUMN IF NOT EXISTS net_ev NUMERIC,
            ADD COLUMN IF NOT EXISTS pnl NUMERIC,
            ADD COLUMN IF NOT EXISTS lift NUMERIC;

ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS event_id UUID,
            ADD COLUMN IF NOT EXISTS snapshot_id UUID,
            ADD COLUMN IF NOT EXISTS exchange VARCHAR(32),
            ADD COLUMN IF NOT EXISTS timeframe VARCHAR(16),
            ADD COLUMN IF NOT EXISTS profile_version_id UUID,
            ADD COLUMN IF NOT EXISTS score_engine_version_id UUID,
            ADD COLUMN IF NOT EXISTS feature_schema_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS label_contract_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS barrier_contract_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS features_captured_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS label_resolved_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS features_coverage NUMERIC(7,6),
            ADD COLUMN IF NOT EXISTS oldest_indicator_age_s INTEGER,
            ADD COLUMN IF NOT EXISTS market_data_confidence NUMERIC(7,6),
            ADD COLUMN IF NOT EXISTS feature_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS profile_config_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS score_engine_config_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS lineage_status VARCHAR(32),
            ADD COLUMN IF NOT EXISTS eligible_for_training BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS ix_shadow_trades_snapshot_id ON shadow_trades (snapshot_id);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_event_id ON shadow_trades (event_id);

CREATE INDEX IF NOT EXISTS ix_shadow_trades_training_eligible
            ON shadow_trades (created_at, source)
         WHERE eligible_for_training = true;

ALTER TABLE profile_versions
            ADD COLUMN IF NOT EXISTS parent_version_id UUID,
            ADD COLUMN IF NOT EXISTS config_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS score_engine_version_id UUID,
            ADD COLUMN IF NOT EXISTS source_cycle_id UUID,
            ADD COLUMN IF NOT EXISTS source_recommendation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS status VARCHAR(24),
            ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS rollback_to_version_id UUID,
            ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(160);

CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_versions_idempotency_key ON profile_versions (idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_versions_one_champion ON profile_versions (profile_id) WHERE status = 'CHAMPION';

CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_versions_one_shadow ON profile_versions (profile_id) WHERE status = 'SHADOW';

CREATE TABLE score_engine_versions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    parent_version_id UUID,
    config_hash VARCHAR(64) NOT NULL,
    rules JSONB NOT NULL,
    weights JSONB NOT NULL,
    thresholds JSONB NOT NULL,
    selected_rule_ids JSONB DEFAULT '[]'::jsonb NOT NULL,
    status VARCHAR(24) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_score_engine_versions_config_hash UNIQUE (config_hash)
);

CREATE TABLE ml_evidence_registry (
    evidence_id UUID DEFAULT gen_random_uuid() NOT NULL,
    cycle_id UUID,
    profile_id UUID,
    profile_version_id UUID,
    model_id UUID,
    source_type VARCHAR(16) NOT NULL,
    source_version VARCHAR(80) NOT NULL,
    dataset_hash VARCHAR(64) NOT NULL,
    window_from TIMESTAMP WITH TIME ZONE NOT NULL,
    window_to TIMESTAMP WITH TIME ZONE NOT NULL,
    target_path TEXT NOT NULL,
    indicator VARCHAR(80) NOT NULL,
    operator VARCHAR(24) NOT NULL,
    lower NUMERIC,
    upper NUMERIC,
    baseline_metric NUMERIC,
    candidate_metric NUMERIC,
    delta_metric NUMERIC,
    expected_ev NUMERIC,
    ci95_lower NUMERIC NOT NULL,
    ci95_upper NUMERIC NOT NULL,
    raw_n INTEGER NOT NULL,
    effective_n NUMERIC NOT NULL,
    independent_windows INTEGER NOT NULL,
    symbols INTEGER NOT NULL,
    confidence NUMERIC(7, 6) NOT NULL,
    status VARCHAR(16) NOT NULL,
    limitations JSONB DEFAULT '[]'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (evidence_id),
    CONSTRAINT uq_ml_evidence_cycle_source_target UNIQUE (cycle_id, source_type, source_version, target_path)
);

CREATE INDEX ix_ml_evidence_profile_status ON ml_evidence_registry (profile_id, status, created_at);

UPDATE ml_models
           SET descriptive_status = 'DESCRIPTIVE_VALIDATED',
               predictive_status = 'PREDICTIVE_REJECTED',
               calibration_authority = false,
               rule_generation_authority = false,
               autopilot_authority = false,
               execution_authority = false,
               governance_reason = jsonb_build_object(
                   'classification', 'v77_independent_audit',
                   'reasons', jsonb_build_array(
                       'holdout_auc_below_random',
                       'holdout_fpr_excessive',
                       'f1_below_always_positive_baseline',
                       'negative_ev_all_tested_thresholds'
                   )
               ),
               metrics_json = COALESCE(metrics_json, '{}'::jsonb) || jsonb_build_object(
                   'governance_v2', jsonb_build_object(
                       'descriptive_status', 'DESCRIPTIVE_VALIDATED',
                       'predictive_status', 'PREDICTIVE_REJECTED',
                       'calibration_authority', false,
                       'rule_generation_authority', false,
                       'autopilot_authority', false,
                       'execution_authority', false
                   )
               )
         WHERE id = 'e3dd7497-0747-4132-84b3-98571bd4b7f3'::uuid
           AND version = '77';

UPDATE config_profiles
           SET config_json = COALESCE(config_json, '{}'::jsonb) || jsonb_build_object(
               'ml_predictive_gate_v2', COALESCE((config_json->>'ml_predictive_gate_v2')::boolean, false),
               'profile_versioning_v2', COALESCE((config_json->>'profile_versioning_v2')::boolean, false),
               'calibration_evidence_registry_v1', COALESCE((config_json->>'calibration_evidence_registry_v1')::boolean, false),
               'calibration_orchestrator_v1', COALESCE((config_json->>'calibration_orchestrator_v1')::boolean, false),
               'autopilot_calibration_v1', COALESCE((config_json->>'autopilot_calibration_v1')::boolean, false),
               'counterfactual_outcomes_v1', COALESCE((config_json->>'counterfactual_outcomes_v1')::boolean, false),
               'ev_score_v2', COALESCE((config_json->>'ev_score_v2')::boolean, false),
               'ml_frontend_status_v2', COALESCE((config_json->>'ml_frontend_status_v2')::boolean, false)
           )
         WHERE config_type = 'ml';

INSERT INTO ml_label_contracts (
            id, name, version, description, sql_expression, target_window_seconds
        ) VALUES
            ('net_return_pct_v2', 'target_net_return_pct', '2.0',
             'Economic regression target after fees, spread, slippage and funding',
             'gross_return_pct - fees_pct - spread_pct - slippage_pct - funding_pct', NULL),
            ('tp_before_sl_v2', 'target_tp_before_sl', '2.0',
             'Barrier classification under a versioned barrier contract',
             'resolution = ''TP''', NULL),
            ('mfe_pct_v1', 'target_mfe_pct', '1.0',
             'Maximum favorable excursion regression target', 'mfe_pct', NULL),
            ('mae_pct_v1', 'target_mae_pct', '1.0',
             'Maximum adverse excursion regression target', 'mae_pct', NULL),
            ('time_to_tp_v1', 'target_time_to_tp', '1.0',
             'Time to take-profit survival/regression target', 'time_to_tp_s', NULL)
        ON CONFLICT DO NOTHING;

UPDATE alembic_version SET version_num='131_ml_governance_v2' WHERE alembic_version.version_num = '130_pool_asset_exclusions';

INFO  [alembic.runtime.migration] Running upgrade 131_ml_governance_v2 -> 132_calibration_orchestration_v2, Calibration orchestration, versioned EV, and state timeline.
-- Running upgrade 131_ml_governance_v2 -> 132_calibration_orchestration_v2

CREATE TABLE calibration_recommendations (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    cycle_id UUID,
    profile_id UUID NOT NULL,
    base_profile_version_id UUID NOT NULL,
    recommendation_type VARCHAR(48) NOT NULL,
    target_path TEXT NOT NULL,
    current_value JSONB NOT NULL,
    proposed_value JSONB NOT NULL,
    bounded_change JSONB NOT NULL,
    evidence_refs JSONB NOT NULL,
    expected_impact JSONB NOT NULL,
    risk VARCHAR(16) NOT NULL,
    confidence NUMERIC(7, 6) NOT NULL,
    validation_required VARCHAR(32) NOT NULL,
    rollback_condition TEXT NOT NULL,
    status VARCHAR(24) NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(base_profile_version_id) REFERENCES profile_versions (id) ON DELETE RESTRICT,
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_calibration_recommendations_profile_status ON calibration_recommendations (profile_id, status, created_at);

CREATE TABLE calibration_proposals (
    id UUID NOT NULL,
    recommendation_id UUID NOT NULL,
    user_id UUID NOT NULL,
    profile_id UUID NOT NULL,
    base_profile_version_id UUID NOT NULL,
    challenger_profile_id UUID,
    challenger_profile_version_id UUID,
    autopilot_candidate_id UUID,
    state VARCHAR(32) NOT NULL,
    before_config JSONB NOT NULL,
    after_config JSONB NOT NULL,
    diff JSONB NOT NULL,
    expected_impact JSONB NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(recommendation_id) REFERENCES calibration_recommendations (id) ON DELETE RESTRICT,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(base_profile_version_id) REFERENCES profile_versions (id) ON DELETE RESTRICT,
    FOREIGN KEY(challenger_profile_id) REFERENCES profiles (id) ON DELETE SET NULL,
    FOREIGN KEY(challenger_profile_version_id) REFERENCES profile_versions (id) ON DELETE SET NULL,
    FOREIGN KEY(autopilot_candidate_id) REFERENCES profile_intelligence_autopilot_candidates (id) ON DELETE SET NULL,
    UNIQUE (recommendation_id),
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_calibration_proposals_profile_state ON calibration_proposals (profile_id, state, created_at);

CREATE TABLE calibration_state_events (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    cycle_id UUID,
    profile_id UUID NOT NULL,
    recommendation_id UUID,
    proposal_id UUID,
    previous_state VARCHAR(32),
    new_state VARCHAR(32) NOT NULL,
    actor VARCHAR(80) NOT NULL,
    reason TEXT NOT NULL,
    metrics JSONB DEFAULT '{}'::jsonb NOT NULL,
    artifact_refs JSONB DEFAULT '[]'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(recommendation_id) REFERENCES calibration_recommendations (id) ON DELETE SET NULL,
    FOREIGN KEY(proposal_id) REFERENCES calibration_proposals (id) ON DELETE SET NULL
);

CREATE INDEX ix_calibration_state_events_profile_created ON calibration_state_events (profile_id, created_at);

CREATE TABLE calibration_results (
    id UUID NOT NULL,
    proposal_id UUID NOT NULL,
    champion_version_id UUID NOT NULL,
    challenger_version_id UUID NOT NULL,
    window_from TIMESTAMP WITH TIME ZONE NOT NULL,
    window_to TIMESTAMP WITH TIME ZONE NOT NULL,
    metrics_before JSONB NOT NULL,
    metrics_after JSONB NOT NULL,
    expected_delta JSONB NOT NULL,
    realized_delta JSONB NOT NULL,
    decision VARCHAR(16) NOT NULL,
    decision_reasons JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(proposal_id) REFERENCES calibration_proposals (id) ON DELETE CASCADE,
    FOREIGN KEY(champion_version_id) REFERENCES profile_versions (id) ON DELETE RESTRICT,
    FOREIGN KEY(challenger_version_id) REFERENCES profile_versions (id) ON DELETE RESTRICT,
    CONSTRAINT uq_calibration_result_window UNIQUE (proposal_id, window_from, window_to)
);

CREATE TABLE profile_version_ev_scores (
    id UUID NOT NULL,
    profile_id UUID NOT NULL,
    profile_version_id UUID NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    window_from TIMESTAMP WITH TIME ZONE NOT NULL,
    window_to TIMESTAMP WITH TIME ZONE NOT NULL,
    raw_n INTEGER NOT NULL,
    effective_n NUMERIC NOT NULL,
    net_ev NUMERIC,
    ci95_lower NUMERIC,
    ci95_upper NUMERIC,
    win_rate NUMERIC,
    drawdown NUMERIC,
    stability NUMERIC,
    score NUMERIC,
    status VARCHAR(24) NOT NULL,
    audit_json JSONB NOT NULL,
    computed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id) ON DELETE CASCADE,
    CONSTRAINT uq_profile_version_ev_window UNIQUE (profile_version_id, timeframe, window_from, window_to)
);

CREATE INDEX ix_profile_version_ev_current ON profile_version_ev_scores (profile_id, computed_at);

CREATE TABLE crypto_profile_ev_scores (
    id UUID NOT NULL,
    profile_id UUID NOT NULL,
    profile_version_id UUID NOT NULL,
    symbol TEXT NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    window_from TIMESTAMP WITH TIME ZONE NOT NULL,
    window_to TIMESTAMP WITH TIME ZONE NOT NULL,
    raw_n INTEGER NOT NULL,
    effective_n NUMERIC NOT NULL,
    expected_ev NUMERIC,
    realized_ev NUMERIC,
    confidence NUMERIC,
    score NUMERIC,
    status VARCHAR(24) NOT NULL,
    audit_json JSONB NOT NULL,
    computed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id) ON DELETE CASCADE,
    CONSTRAINT uq_crypto_profile_ev_window UNIQUE (profile_version_id, symbol, timeframe, window_from, window_to)
);

CREATE INDEX ix_crypto_profile_ev_current ON crypto_profile_ev_scores (symbol, computed_at);

UPDATE alembic_version SET version_num='132_calibration_orchestration_v2' WHERE alembic_version.version_num = '131_ml_governance_v2';

INFO  [alembic.runtime.migration] Running upgrade 132_calibration_orchestration_v2 -> 133_native_feature_capture, Native point-in-time feature capture contract.
-- Running upgrade 132_calibration_orchestration_v2 -> 133_native_feature_capture

ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS feature_extractor_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS capture_contract_version VARCHAR(80);

CREATE OR REPLACE FUNCTION prevent_shadow_native_capture_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(
              NEW.features_snapshot, NEW.features_captured_at, NEW.feature_hash,
              NEW.feature_extractor_version, NEW.feature_schema_version,
              NEW.capture_contract_version, NEW.symbol, NEW.exchange, NEW.timeframe,
              NEW.source, NEW.profile_id, NEW.ranking_id, NEW.decision_id
          ) IS DISTINCT FROM ROW(
              OLD.features_snapshot, OLD.features_captured_at, OLD.feature_hash,
              OLD.feature_extractor_version, OLD.feature_schema_version,
              OLD.capture_contract_version, OLD.symbol, OLD.exchange, OLD.timeframe,
              OLD.source, OLD.profile_id, OLD.ranking_id, OLD.decision_id
          ) THEN
            RAISE EXCEPTION 'shadow native capture contract is immutable after INSERT'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$;

DROP TRIGGER IF EXISTS trg_shadow_native_capture_immutable ON shadow_trades;

CREATE TRIGGER trg_shadow_native_capture_immutable
        BEFORE UPDATE ON shadow_trades
        FOR EACH ROW EXECUTE FUNCTION prevent_shadow_native_capture_update();

UPDATE alembic_version SET version_num='133_native_feature_capture' WHERE alembic_version.version_num = '132_calibration_orchestration_v2';

INFO  [alembic.runtime.migration] Running upgrade 133_native_feature_capture -> 134_fase1_integrity_cert, Fase 1 � integridade certificada e monitora��o cont�nua.
-- Running upgrade 133_native_feature_capture -> 134_fase1_integrity_cert

CREATE TABLE ml_data_certification_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    run_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    window_from TIMESTAMP WITH TIME ZONE NOT NULL,
    window_to TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(8) NOT NULL,
    invariants JSONB NOT NULL,
    cumulative JSONB,
    PRIMARY KEY (id)
);

CREATE INDEX ix_ml_data_certification_runs_run_at ON ml_data_certification_runs (run_at);

ALTER TABLE ml_training_dataset ADD COLUMN win_threshold_s INTEGER;

INSERT INTO ml_dataset_contracts (id, source_filter, model_lane, description)
        VALUES
            ('ds_l1_spectrum_atrdyn_v2', 'L1_SPECTRUM', 'L1_SPECTRUM',
             'Fase 1 (D1=A) � popula��o can�nica de treino: source=L1_SPECTRUM, '
             'barrier_mode=ATR_DYNAMIC, barrier_contract=shadow_atr_dynamic_v2 '
             '(TP=ATR�shadow_atr_multiplier_tp, SL=ATR�shadow_atr_multiplier_sl, '
             'clamp [shadow_barrier_min_pct, shadow_barrier_max_pct]); '
             'label=positive_net_return_v1; win threshold exclusivamente via '
             'config ml_win_fast_threshold_seconds; fronteira ml_dataset_valid_from.'),
            ('ds_l3_profile_v1', 'L3', 'L3_PROFILE',
             'Lane CatBoost L3 aprovados (pr�-Fase 1, registrado para continuidade).'),
            ('ds_l3_lab_profile_v1', 'L3_LAB', 'L3_LAB_PROFILE',
             'Lane CatBoost Strategy Lab (pr�-Fase 1, registrado para continuidade).'),
            ('ds_l3_intelligence_v1', 'L3_REJECTED', 'L3_INTELLIGENCE',
             'Lane diagn�stica de rejeitados (pr�-Fase 1, registrado para continuidade).'),
            ('ds_l3_approved_intel_v1', 'L3', 'L3_APPROVED_INTELLIGENCE',
             'Lane advisory de aprovados (pr�-Fase 1, registrado para continuidade).'),
            ('ds_l3_contextual_intel_v1', 'L3,L3_REJECTED', 'L3_CONTEXTUAL_INTELLIGENCE',
             'Lane advisory contextual (pr�-Fase 1, registrado para continuidade).')
        ON CONFLICT DO NOTHING;

INSERT INTO ml_label_contracts (
            id, name, version, description, sql_expression, target_window_seconds
        ) VALUES (
            'positive_net_return_v1', 'positive_net_return', '1.0',
            'Label positivo quando o retorno l�quido de fees � positivo. '
            'Barreira: shadow_atr_dynamic_v2 (D1=A, Fase 1) � TP e SL '
            'ATR-din�micos e sim�tricos: TP=ATR�1.5 (shadow_atr_multiplier_tp), '
            'SL=ATR�1.5 (shadow_atr_multiplier_sl), clamp [0.5, 3.0] '
            '(shadow_barrier_min_pct/shadow_barrier_max_pct). Substitui o '
            'artefato estrutural do v1 (TP fixo 0.6% sob SL ATR-din�mico). '
            'valid_from = ml_dataset_valid_from (timestamp do deploy da Fase 1); '
            'dados anteriores permanecem intocados, apenas deixam de ser '
            'popula��o can�nica. win threshold: ml_win_fast_threshold_seconds.',
            'net_return_pct > 0', NULL
        )
        ON CONFLICT DO NOTHING;

UPDATE alembic_version SET version_num='134_fase1_integrity_cert' WHERE alembic_version.version_num = '133_native_feature_capture';

INFO  [alembic.runtime.migration] Running upgrade 134_fase1_integrity_cert -> 135_l1_dedup_constraint, Fase 1.3 (Passo 2) � idempot�ncia do capture L1_SPECTRUM.
-- Running upgrade 134_fase1_integrity_cert -> 135_l1_dedup_constraint

DELETE FROM shadow_trades s
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id, symbol, entry_timestamp
                       ORDER BY created_at ASC
                   ) AS rn
            FROM shadow_trades
            WHERE source = 'L1_SPECTRUM' AND entry_timestamp IS NOT NULL
        ) d
        WHERE s.id = d.id AND d.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_l1_symbol_entry
        ON shadow_trades (user_id, symbol, entry_timestamp)
        WHERE source = 'L1_SPECTRUM' AND entry_timestamp IS NOT NULL;

UPDATE alembic_version SET version_num='135_l1_dedup_constraint' WHERE alembic_version.version_num = '134_fase1_integrity_cert';

INFO  [alembic.runtime.migration] Running upgrade 135_l1_dedup_constraint -> 136_l1_lane_contract_v2, L1-only lane eligibility contract and certified collection frontier.
-- Running upgrade 135_l1_dedup_constraint -> 136_l1_lane_contract_v2

UPDATE config_profiles
               SET config_json =
                   jsonb_set(
                     jsonb_set(
                       jsonb_set(
                         config_json,
                         '{ml_feature_contract}',
                         COALESCE(config_json->'ml_feature_contract', '{}'::jsonb)
                           || jsonb_build_object(
                                'L1_SPECTRUM',
                                CAST('
{
  "version": "l1_spectrum_entry_v2",
  "min_row_coverage": 0.7,
  "required": [
    "taker_ratio",
    "volume_delta",
    "rsi",
    "macd_histogram_pct",
    "macd_histogram_slope",
    "adx",
    "adx_acceleration",
    "spread_pct",
    "volume_spike",
    "bb_width",
    "atr_pct",
    "ema9_gt_ema21",
    "orderbook_depth_usdt",
    "vwap_distance_pct",
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff",
    "adx_slope_3",
    "vwap_reclaim_bool",
    "higher_highs_5",
    "higher_lows_5"
  ],
  "optional": [
    "volume_24h_usdt",
    "flow_strength",
    "momentum_strength",
    "delta_normalized",
    "ema_distance_pct",
    "ema50_distance_pct",
    "ema200_distance_pct"
  ]
}
' AS jsonb)
                              ),
                         true
                       ),
                       '{ml_l1_feature_contract_version}',
                       to_jsonb('l1_spectrum_entry_v2'::text),
                       true
                     ),
                     '{ml_l1_feature_exclusions}',
                     CAST('
[
  "liquidity_score",
  "market_structure_score",
  "momentum_score",
  "signal_score",
  "di_trend",
  "trend_alignment",
  "ema50_gt_ema200"
]
' AS jsonb),
                     true
                   )
                   || jsonb_build_object(
                        'ml_l1_dataset_valid_from',
                        to_char(
                          clock_timestamp() AT TIME ZONE 'UTC',
                          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                        )
                      ),
                   updated_at = clock_timestamp()
             WHERE config_type = 'ml'
               AND is_active IS TRUE;

UPDATE alembic_version SET version_num='136_l1_lane_contract_v2' WHERE alembic_version.version_num = '135_l1_dedup_constraint';

-- Running upgrade 136_l1_lane_contract_v2 -> 137_profile_bayesian

INFO  [alembic.runtime.migration] Running upgrade 136_l1_lane_contract_v2 -> 137_profile_bayesian, Profile Bayesian Intelligence isolated persistence.
CREATE TABLE profile_bayesian_dataset_snapshots (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    profile_id UUID NOT NULL,
    profile_version_id UUID,
    dataset_hash VARCHAR(64) NOT NULL,
    policy_hash VARCHAR(64) NOT NULL,
    window_from TIMESTAMP WITH TIME ZONE NOT NULL,
    window_to TIMESTAMP WITH TIME ZONE NOT NULL,
    row_count INTEGER NOT NULL,
    observation_ids JSONB NOT NULL,
    manifest JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id) ON DELETE SET NULL,
    CONSTRAINT uq_profile_bayesian_dataset_hash UNIQUE (user_id, dataset_hash),
    CONSTRAINT ck_profile_bayesian_dataset_row_count CHECK (row_count >= 0),
    CONSTRAINT ck_profile_bayesian_dataset_window CHECK (window_to >= window_from)
);

CREATE INDEX ix_profile_bayesian_dataset_profile_created ON profile_bayesian_dataset_snapshots (profile_id, created_at);

CREATE TABLE profile_bayesian_analysis_runs (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    profile_id UUID NOT NULL,
    profile_version_id UUID,
    dataset_snapshot_id UUID,
    idempotency_key VARCHAR(180) NOT NULL,
    status VARCHAR(40) NOT NULL,
    diagnostic_status VARCHAR(32),
    random_seed BIGINT NOT NULL,
    code_version VARCHAR(80) NOT NULL,
    git_commit VARCHAR(64),
    model_config JSONB NOT NULL,
    sampler_config JSONB NOT NULL,
    dependency_versions JSONB NOT NULL,
    filters JSONB NOT NULL,
    warnings JSONB DEFAULT '[]'::jsonb NOT NULL,
    error_message TEXT,
    requested_by UUID NOT NULL,
    task_id VARCHAR(80),
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE RESTRICT,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES profile_bayesian_dataset_snapshots (id) ON DELETE SET NULL,
    CONSTRAINT ck_profile_bayesian_analysis_status CHECK (status IN ('PENDING', 'BUILDING_DATASET', 'VALIDATING_DATA', 'SAMPLING', 'RUNNING_DIAGNOSTICS', 'ANALYZING_POSTERIOR', 'COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED')),
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_profile_bayesian_analysis_profile_created ON profile_bayesian_analysis_runs (profile_id, created_at);

CREATE TABLE profile_bayesian_indicator_effects (
    id UUID NOT NULL,
    analysis_run_id UUID NOT NULL,
    profile_id UUID NOT NULL,
    indicator VARCHAR(80) NOT NULL,
    regime VARCHAR(80),
    effect_direction VARCHAR(16) NOT NULL,
    estimated_tp_lift NUMERIC(16, 10),
    estimated_pnl_lift NUMERIC(16, 10),
    probability_positive_effect NUMERIC(8, 7),
    credible_interval_95 JSONB NOT NULL,
    direct_sample_size INTEGER NOT NULL,
    shared_sample_size INTEGER NOT NULL,
    effective_sample_size NUMERIC(16, 4),
    evidence_grade VARCHAR(24) NOT NULL,
    diagnostic_status VARCHAR(32) NOT NULL,
    recommendation VARCHAR(40) NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_run_id) REFERENCES profile_bayesian_analysis_runs (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    CONSTRAINT uq_profile_bayesian_effect_scope UNIQUE (analysis_run_id, indicator, regime),
    CONSTRAINT ck_profile_bayesian_effect_direct_n CHECK (direct_sample_size >= 0),
    CONSTRAINT ck_profile_bayesian_effect_shared_n CHECK (shared_sample_size >= 0)
);

CREATE INDEX ix_profile_bayesian_effect_profile_grade ON profile_bayesian_indicator_effects (profile_id, evidence_grade, created_at);

CREATE TABLE profile_bayesian_diagnostics (
    id UUID NOT NULL,
    analysis_run_id UUID NOT NULL,
    model_name VARCHAR(80) NOT NULL,
    status VARCHAR(32) NOT NULL,
    rhat_max NUMERIC(12, 8),
    effective_sample_size_min NUMERIC(16, 4),
    divergences INTEGER DEFAULT '0' NOT NULL,
    posterior_predictive_check JSONB NOT NULL,
    credible_intervals JSONB NOT NULL,
    sampling_warnings JSONB NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_run_id) REFERENCES profile_bayesian_analysis_runs (id) ON DELETE CASCADE,
    CONSTRAINT uq_profile_bayesian_diagnostic_model UNIQUE (analysis_run_id, model_name),
    CONSTRAINT ck_profile_bayesian_diagnostic_divergences CHECK (divergences >= 0)
);

CREATE TABLE profile_optimization_studies (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    profile_id UUID NOT NULL,
    analysis_run_id UUID NOT NULL,
    idempotency_key VARCHAR(180) NOT NULL,
    status VARCHAR(32) NOT NULL,
    sampler VARCHAR(40) NOT NULL,
    directions JSONB NOT NULL,
    search_space JSONB NOT NULL,
    constraints JSONB NOT NULL,
    windows JSONB NOT NULL,
    random_seed BIGINT NOT NULL,
    total_trials INTEGER DEFAULT '0' NOT NULL,
    valid_trials INTEGER DEFAULT '0' NOT NULL,
    warnings JSONB DEFAULT '[]'::jsonb NOT NULL,
    error_message TEXT,
    task_id VARCHAR(80),
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(analysis_run_id) REFERENCES profile_bayesian_analysis_runs (id) ON DELETE RESTRICT,
    CONSTRAINT ck_profile_optimization_total_trials CHECK (total_trials >= 0),
    CONSTRAINT ck_profile_optimization_valid_trials CHECK (valid_trials >= 0),
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_profile_optimization_profile_created ON profile_optimization_studies (profile_id, created_at);

CREATE TABLE profile_optimization_trials (
    id UUID NOT NULL,
    study_id UUID NOT NULL,
    trial_number INTEGER NOT NULL,
    status VARCHAR(24) NOT NULL,
    parameters JSONB NOT NULL,
    objective_values JSONB NOT NULL,
    metrics JSONB NOT NULL,
    constraint_violations JSONB NOT NULL,
    is_valid BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(study_id) REFERENCES profile_optimization_studies (id) ON DELETE CASCADE,
    CONSTRAINT uq_profile_optimization_trial_number UNIQUE (study_id, trial_number),
    CONSTRAINT ck_profile_optimization_trial_number CHECK (trial_number >= 0)
);

CREATE TABLE profile_optimization_trial_metrics (
    id UUID NOT NULL,
    trial_id UUID NOT NULL,
    metric_name VARCHAR(80) NOT NULL,
    metric_value NUMERIC(24, 10),
    metric_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(trial_id) REFERENCES profile_optimization_trials (id) ON DELETE CASCADE,
    CONSTRAINT uq_profile_optimization_trial_metric UNIQUE (trial_id, metric_name)
);

CREATE TABLE profile_bayesian_candidate_links (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    profile_id UUID NOT NULL,
    base_profile_version_id UUID,
    analysis_run_id UUID NOT NULL,
    optimization_study_id UUID,
    autopilot_candidate_id UUID,
    source VARCHAR(64) NOT NULL,
    status VARCHAR(40) NOT NULL,
    changes JSONB NOT NULL,
    evidence JSONB NOT NULL,
    validation_metrics JSONB NOT NULL,
    shadow_metrics JSONB NOT NULL,
    approval_status VARCHAR(30) NOT NULL,
    approved_by UUID,
    approved_at TIMESTAMP WITH TIME ZONE,
    rollback_reference JSONB,
    idempotency_key VARCHAR(180) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(base_profile_version_id) REFERENCES profile_versions (id) ON DELETE SET NULL,
    FOREIGN KEY(analysis_run_id) REFERENCES profile_bayesian_analysis_runs (id) ON DELETE RESTRICT,
    FOREIGN KEY(optimization_study_id) REFERENCES profile_optimization_studies (id) ON DELETE SET NULL,
    FOREIGN KEY(autopilot_candidate_id) REFERENCES profile_intelligence_autopilot_candidates (id) ON DELETE SET NULL,
    FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_profile_bayesian_candidate_status CHECK (status IN ('DRAFT', 'ANALYZED', 'REPLAY_PENDING', 'REPLAY_RUNNING', 'REPLAY_FAILED', 'REPLAY_REJECTED', 'VALIDATED', 'SHADOW_PENDING', 'SHADOW_RUNNING', 'SHADOW_REJECTED', 'AWAITING_HUMAN_APPROVAL', 'APPROVED', 'REJECTED', 'ACTIVATED', 'ROLLED_BACK')),
    CONSTRAINT ck_profile_bayesian_candidate_source CHECK (source = 'PROFILE_BAYESIAN_INTELLIGENCE'),
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_profile_bayesian_candidate_profile_status ON profile_bayesian_candidate_links (profile_id, status, created_at);

CREATE TABLE profile_bayesian_audit_events (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    actor_user_id UUID,
    profile_id UUID NOT NULL,
    analysis_run_id UUID,
    study_id UUID,
    candidate_link_id UUID,
    event_type VARCHAR(80) NOT NULL,
    previous_status VARCHAR(40),
    new_status VARCHAR(40),
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE RESTRICT,
    FOREIGN KEY(analysis_run_id) REFERENCES profile_bayesian_analysis_runs (id) ON DELETE SET NULL,
    FOREIGN KEY(study_id) REFERENCES profile_optimization_studies (id) ON DELETE SET NULL,
    FOREIGN KEY(candidate_link_id) REFERENCES profile_bayesian_candidate_links (id) ON DELETE SET NULL
);

CREATE INDEX ix_profile_bayesian_audit_profile_created ON profile_bayesian_audit_events (profile_id, created_at);

UPDATE alembic_version SET version_num='137_profile_bayesian' WHERE alembic_version.version_num = '136_l1_lane_contract_v2';

INFO  [alembic.runtime.migration] Running upgrade 137_profile_bayesian -> 138_l1_readiness_governance, Separate L1 exploratory fitting from promotion-ready retraining.
-- Running upgrade 137_profile_bayesian -> 138_l1_readiness_governance

WITH target AS (
          SELECT
            id,
            user_id,
            config_json AS previous_json,
            config_json ||
jsonb_build_object(
  'ml_l1_exploratory_fit_min_eligible_rows', 400,
  'ml_l1_retrain_min_eligible_rows', 1500,
  'ml_l1_readiness_contract_version', 'l1_trainer_mature_v1',
  'ml_l1_frontier_reset_requires_audit', true
)
 AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT
          gen_random_uuid(), id, user_id, previous_json, new_json,
          'ML L1 readiness governance: exploratory=400; official=1500; '
          'frontier unchanged; promotion and execution remain gated',
          clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json;

UPDATE config_profiles
        SET config_json = config_json ||
jsonb_build_object(
  'ml_l1_exploratory_fit_min_eligible_rows', 400,
  'ml_l1_retrain_min_eligible_rows', 1500,
  'ml_l1_readiness_contract_version', 'l1_trainer_mature_v1',
  'ml_l1_frontier_reset_requires_audit', true
)
,
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE;

UPDATE alembic_version SET version_num='138_l1_readiness_governance' WHERE alembic_version.version_num = '137_profile_bayesian';

INFO  [alembic.runtime.migration] Running upgrade 138_l1_readiness_governance -> 139_shadow_monitor_fairness, Add a dedicated fair-scheduling cursor to the shadow monitor.
-- Running upgrade 138_l1_readiness_governance -> 139_shadow_monitor_fairness

ALTER TABLE shadow_trades ADD COLUMN monitor_checked_at TIMESTAMP WITH TIME ZONE;

UPDATE alembic_version SET version_num='139_shadow_monitor_fairness' WHERE alembic_version.version_num = '138_l1_readiness_governance';

INFO  [alembic.runtime.migration] Running upgrade 139_shadow_monitor_fairness -> 140_shadow_detailed_report, Shadow detailed report snapshots and AI analysis jobs.
-- Running upgrade 139_shadow_monitor_fairness -> 140_shadow_detailed_report

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE shadow_trade_report_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    filters JSONB NOT NULL,
    filters_hash VARCHAR(64) NOT NULL,
    trade_ids_hash VARCHAR(64) NOT NULL,
    timezone VARCHAR(80) NOT NULL,
    total_trades INTEGER NOT NULL,
    status VARCHAR(30) NOT NULL,
    completeness JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX idx_shadow_report_runs_user_created ON shadow_trade_report_runs (user_id, created_at);

CREATE TABLE shadow_trade_report_items (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    report_run_id UUID NOT NULL,
    shadow_trade_id UUID NOT NULL,
    position INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(report_run_id) REFERENCES shadow_trade_report_runs (id) ON DELETE CASCADE,
    FOREIGN KEY(shadow_trade_id) REFERENCES shadow_trades (id) ON DELETE CASCADE,
    CONSTRAINT uq_shadow_report_item_position UNIQUE (report_run_id, position),
    CONSTRAINT uq_shadow_report_item_trade UNIQUE (report_run_id, shadow_trade_id)
);

CREATE INDEX idx_shadow_report_items_run_position ON shadow_trade_report_items (report_run_id, position);

CREATE TABLE shadow_trade_analysis_jobs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    scope VARCHAR(30) NOT NULL,
    shadow_trade_id UUID,
    report_run_id UUID,
    provider VARCHAR(40) NOT NULL,
    model VARCHAR(160) NOT NULL,
    prompt_version VARCHAR(40) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    status VARCHAR(30) NOT NULL,
    result_json JSONB,
    raw_response TEXT,
    usage JSONB NOT NULL,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(shadow_trade_id) REFERENCES shadow_trades (id) ON DELETE SET NULL,
    FOREIGN KEY(report_run_id) REFERENCES shadow_trade_report_runs (id) ON DELETE SET NULL,
    CONSTRAINT uq_shadow_analysis_user_idempotency UNIQUE (user_id, idempotency_key)
);

CREATE INDEX idx_shadow_analysis_jobs_status ON shadow_trade_analysis_jobs (status, created_at);

CREATE INDEX idx_shadow_analysis_jobs_user_created ON shadow_trade_analysis_jobs (user_id, created_at);

UPDATE alembic_version SET version_num='140_shadow_detailed_report' WHERE alembic_version.version_num = '139_shadow_monitor_fairness';

INFO  [alembic.runtime.migration] Running upgrade 140_shadow_detailed_report -> 141_l3_profile_consolidation, Add opt-in uniqueness for consolidated canonical L3 shadows.
-- Running upgrade 140_shadow_detailed_report -> 141_l3_profile_consolidation

SET LOCAL lock_timeout = '10s';

ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS l3_consolidation_enforced BOOLEAN
            NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_l3_consolidated_active
                ON shadow_trades (user_id, symbol, direction)
             WHERE source = 'L3'
               AND l3_consolidation_enforced = TRUE
               AND status IN ('PENDING', 'RUNNING');

UPDATE alembic_version SET version_num='141_l3_profile_consolidation' WHERE alembic_version.version_num = '140_shadow_detailed_report';

INFO  [alembic.runtime.migration] Running upgrade 141_l3_profile_consolidation -> 142_feature_source_lineage, Add immutable causal feature-source timestamp to shadow captures.
-- Running upgrade 141_l3_profile_consolidation -> 142_feature_source_lineage

SET LOCAL lock_timeout = '10s';

ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS feature_source_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS feature_source_times JSONB;

CREATE OR REPLACE FUNCTION prevent_shadow_native_capture_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(
      NEW.features_snapshot, NEW.feature_source_at, NEW.feature_source_times,
      NEW.features_captured_at, NEW.feature_hash,
      NEW.feature_extractor_version, NEW.feature_schema_version,
      NEW.capture_contract_version, NEW.symbol, NEW.exchange, NEW.timeframe,
      NEW.source, NEW.profile_id, NEW.ranking_id, NEW.decision_id
  ) IS DISTINCT FROM ROW(
      OLD.features_snapshot, OLD.feature_source_at, OLD.feature_source_times,
      OLD.features_captured_at, OLD.feature_hash,
      OLD.feature_extractor_version, OLD.feature_schema_version,
      OLD.capture_contract_version, OLD.symbol, OLD.exchange, OLD.timeframe,
      OLD.source, OLD.profile_id, OLD.ranking_id, OLD.decision_id
  ) THEN
    RAISE EXCEPTION 'shadow native capture contract is immutable after INSERT'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

UPDATE alembic_version SET version_num='142_feature_source_lineage' WHERE alembic_version.version_num = '141_l3_profile_consolidation';

INFO  [alembic.runtime.migration] Running upgrade 142_feature_source_lineage -> 143_l3_training_governance, Configure causal 30-day L3_PROFILE candidate training gates.
-- Running upgrade 142_feature_source_lineage -> 143_l3_training_governance

WITH target AS (
          SELECT id, user_id, config_json AS previous_json,

config_json
|| jsonb_build_object(
  'ml_l3_training_contract_version', 'l3_profile_30d_causal_v1',
  'ml_catboost_retrain_min_eligible_rows', 2000,
  'ml_catboost_train_size_ratio', 0.60,
  'ml_catboost_validation_size_ratio', 0.20,
  'ml_catboost_test_size_ratio', 0.20,
  'ml_catboost_min_train_samples', 1000,
  'ml_catboost_min_validation_samples', 200,
  'ml_catboost_min_test_samples', 200,
  'ml_catboost_early_stopping_rounds', 30,
  'ml_catboost_max_boundary_candidates', 200,
  'ml_catboost_base_params', jsonb_build_object(
      'task_type', 'CPU',
      'loss_function', 'Logloss',
      'eval_metric', 'AUC',
      'nan_mode', 'Min',
      'od_type', 'Iter',
      'use_best_model', true,
      'bootstrap_type', 'MVS',
      'subsample', 0.8
  ),
  'ml_optuna_max_trials', 100,
  'ml_optuna_timeout_seconds', 600,
  'ml_training_seed', 42
)
|| jsonb_build_object(
  'ml_optuna_search_space',
  jsonb_set(
    COALESCE(config_json->'ml_optuna_search_space', '{}'::jsonb),
    '{catboost}',

jsonb_build_object(
  'iterations', jsonb_build_object('type', 'int', 'low', 200, 'high', 600),
  'learning_rate', jsonb_build_object(
      'type', 'float', 'low', 0.01, 'high', 0.15, 'log', true
  ),
  'depth', jsonb_build_object('type', 'int', 'low', 3, 'high', 6),
  'l2_leaf_reg', jsonb_build_object('type', 'float', 'low', 3.0, 'high', 10.0),
  'min_data_in_leaf', jsonb_build_object('type', 'int', 'low', 20, 'high', 100),
  'random_strength', jsonb_build_object('type', 'float', 'low', 1.0, 'high', 10.0)
)
,
    true
  )
)
 AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT gen_random_uuid(), id, user_id, previous_json, new_json,
               'ML L3_PROFILE 30d causal candidate training governance v1', clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json;

UPDATE config_profiles
        SET config_json =
config_json
|| jsonb_build_object(
  'ml_l3_training_contract_version', 'l3_profile_30d_causal_v1',
  'ml_catboost_retrain_min_eligible_rows', 2000,
  'ml_catboost_train_size_ratio', 0.60,
  'ml_catboost_validation_size_ratio', 0.20,
  'ml_catboost_test_size_ratio', 0.20,
  'ml_catboost_min_train_samples', 1000,
  'ml_catboost_min_validation_samples', 200,
  'ml_catboost_min_test_samples', 200,
  'ml_catboost_early_stopping_rounds', 30,
  'ml_catboost_max_boundary_candidates', 200,
  'ml_catboost_base_params', jsonb_build_object(
      'task_type', 'CPU',
      'loss_function', 'Logloss',
      'eval_metric', 'AUC',
      'nan_mode', 'Min',
      'od_type', 'Iter',
      'use_best_model', true,
      'bootstrap_type', 'MVS',
      'subsample', 0.8
  ),
  'ml_optuna_max_trials', 100,
  'ml_optuna_timeout_seconds', 600,
  'ml_training_seed', 42
)
|| jsonb_build_object(
  'ml_optuna_search_space',
  jsonb_set(
    COALESCE(config_json->'ml_optuna_search_space', '{}'::jsonb),
    '{catboost}',

jsonb_build_object(
  'iterations', jsonb_build_object('type', 'int', 'low', 200, 'high', 600),
  'learning_rate', jsonb_build_object(
      'type', 'float', 'low', 0.01, 'high', 0.15, 'log', true
  ),
  'depth', jsonb_build_object('type', 'int', 'low', 3, 'high', 6),
  'l2_leaf_reg', jsonb_build_object('type', 'float', 'low', 3.0, 'high', 10.0),
  'min_data_in_leaf', jsonb_build_object('type', 'int', 'low', 20, 'high', 100),
  'random_strength', jsonb_build_object('type', 'float', 'low', 1.0, 'high', 10.0)
)
,
    true
  )
)
,
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE;

UPDATE alembic_version SET version_num='143_l3_training_governance' WHERE alembic_version.version_num = '142_feature_source_lineage';

-- Running upgrade 143_l3_training_governance -> 144_social_intelligence

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INFO  [alembic.runtime.migration] Running upgrade 143_l3_training_governance -> 144_social_intelligence, Add immutable Social Intelligence runs and per-asset observations.
CREATE TABLE social_intelligence_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    contract_version VARCHAR(64) NOT NULL,
    external_run_id VARCHAR(128) NOT NULL,
    source VARCHAR(64) NOT NULL,
    model VARCHAR(128) NOT NULL,
    prompt_version VARCHAR(128) NOT NULL,
    window_start TIMESTAMP WITH TIME ZONE NOT NULL,
    window_end TIMESTAMP WITH TIME ZONE NOT NULL,
    collected_at TIMESTAMP WITH TIME ZONE NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    accepted_count INTEGER DEFAULT '0' NOT NULL,
    rejected_count INTEGER DEFAULT '0' NOT NULL,
    validation_errors JSONB DEFAULT '[]'::jsonb NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_social_runs_window_order CHECK (window_start < window_end),
    CONSTRAINT ck_social_runs_collected_after_window CHECK (window_end <= collected_at),
    CONSTRAINT uq_social_runs_source_external UNIQUE (source, external_run_id)
);

CREATE INDEX ix_social_runs_window_end ON social_intelligence_runs (window_end);

CREATE TABLE social_asset_observations (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    run_id UUID NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    attention_score FLOAT NOT NULL,
    sentiment_score FLOAT NOT NULL,
    confidence FLOAT NOT NULL,
    sentiment_label VARCHAR(32) NOT NULL,
    recommendation VARCHAR(32) NOT NULL,
    summary TEXT NOT NULL,
    narratives JSONB DEFAULT '[]'::jsonb NOT NULL,
    anomalies JSONB DEFAULT '[]'::jsonb NOT NULL,
    metrics JSONB DEFAULT '{}'::jsonb NOT NULL,
    sources JSONB NOT NULL,
    contract_version VARCHAR(64) NOT NULL,
    window_start TIMESTAMP WITH TIME ZONE NOT NULL,
    window_end TIMESTAMP WITH TIME ZONE NOT NULL,
    collected_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_social_attention_range CHECK (attention_score >= 0 AND attention_score <= 100),
    CONSTRAINT ck_social_sentiment_range CHECK (sentiment_score >= 0 AND sentiment_score <= 100),
    CONSTRAINT ck_social_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT uq_social_observations_run_symbol UNIQUE (run_id, symbol),
    FOREIGN KEY(run_id) REFERENCES social_intelligence_runs (id) ON DELETE RESTRICT
);

CREATE INDEX ix_social_observations_symbol_window ON social_asset_observations (symbol, window_end);

INSERT INTO config_profiles
            (id, user_id, pool_id, config_type, config_json, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            u.id,
            NULL,
            'social_score',
            jsonb_build_object(
                'enabled', false,
                'spot_weight', 0.20,
                'futures_weight', 0.20,
                'max_age_seconds', 86400,
                'mode', 'symmetric',
                'formula_version', 'confidence_adjusted_v1'
            ),
            true,
            now(),
            now()
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1
            FROM config_profiles cp
            WHERE cp.user_id = u.id
              AND cp.pool_id IS NULL
              AND cp.config_type = 'social_score'
              AND cp.is_active = true
        );

UPDATE alembic_version SET version_num='144_social_intelligence' WHERE alembic_version.version_num = '143_l3_training_governance';

INFO  [alembic.runtime.migration] Running upgrade 144_social_intelligence -> 145_l3_historical_lineage, Configure read-only historical L3 dataset lineage resolution.
-- Running upgrade 144_social_intelligence -> 145_l3_historical_lineage

WITH target AS (
          SELECT id, user_id, config_json AS previous_json,

config_json || jsonb_build_object(
  'ml_l3_historical_lineage_enabled', true,
  'ml_l3_historical_lineage_contract_version', 'decision_snapshot_ts_v1',
  'ml_l3_historical_capture_contracts', jsonb_build_array('point-in-time-v1'),
  'ml_l3_historical_timestamp_aliases', jsonb_build_array('ts', 'timestamp'),
  'ml_l3_historical_untrusted_source_groups', jsonb_build_array('live_injection'),
  'ml_l3_historical_neutralized_features', jsonb_build_array(
      'taker_ratio', 'volume_delta', 'flow_strength', 'delta_normalized'
  ),
  'ml_l3_historical_unresolved_feature_policy', 'neutralize',
  'ml_l3_historical_label_anchor', 'decision_created_at'
)
 AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT gen_random_uuid(), id, user_id, previous_json, new_json,
               'ML L3_PROFILE historical decision-snapshot lineage v1', clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json;

UPDATE config_profiles
        SET config_json =
config_json || jsonb_build_object(
  'ml_l3_historical_lineage_enabled', true,
  'ml_l3_historical_lineage_contract_version', 'decision_snapshot_ts_v1',
  'ml_l3_historical_capture_contracts', jsonb_build_array('point-in-time-v1'),
  'ml_l3_historical_timestamp_aliases', jsonb_build_array('ts', 'timestamp'),
  'ml_l3_historical_untrusted_source_groups', jsonb_build_array('live_injection'),
  'ml_l3_historical_neutralized_features', jsonb_build_array(
      'taker_ratio', 'volume_delta', 'flow_strength', 'delta_normalized'
  ),
  'ml_l3_historical_unresolved_feature_policy', 'neutralize',
  'ml_l3_historical_label_anchor', 'decision_created_at'
)
,
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE;

UPDATE alembic_version SET version_num='145_l3_historical_lineage' WHERE alembic_version.version_num = '144_social_intelligence';

INFO  [alembic.runtime.migration] Running upgrade 145_l3_historical_lineage -> 146_l3_1200_validation, Configure the governed L3_PROFILE 1,200-row candidate validation gate.
-- Running upgrade 145_l3_historical_lineage -> 146_l3_1200_validation

WITH target AS (
          SELECT id, user_id, config_json AS previous_json,

config_json || jsonb_build_object(
  'ml_l3_training_contract_version',
      'l3_profile_30d_causal_1200_validation_v1',
  'ml_catboost_retrain_min_eligible_rows', 1200,
  'ml_catboost_min_train_samples', 600,
  'ml_catboost_min_validation_samples', 200,
  'ml_catboost_min_test_samples', 200
)
 AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT gen_random_uuid(), id, user_id, previous_json, new_json,
               'ML L3_PROFILE 1200-row candidate validation gate', clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json;

UPDATE config_profiles
        SET config_json =
config_json || jsonb_build_object(
  'ml_l3_training_contract_version',
      'l3_profile_30d_causal_1200_validation_v1',
  'ml_catboost_retrain_min_eligible_rows', 1200,
  'ml_catboost_min_train_samples', 600,
  'ml_catboost_min_validation_samples', 200,
  'ml_catboost_min_test_samples', 200
)
,
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE;

UPDATE alembic_version SET version_num='146_l3_1200_validation' WHERE alembic_version.version_num = '145_l3_historical_lineage';

-- Running upgrade 146_l3_1200_validation -> 147_systemic_ai_foundation

INFO  [alembic.runtime.migration] Running upgrade 146_l3_1200_validation -> 147_systemic_ai_foundation, Tenant-scoped systemic AI foundation and regenerative ledger.
CREATE TABLE ai_prompt_versions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    prompt_key VARCHAR(160) NOT NULL,
    semantic_version VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL,
    system_template TEXT NOT NULL,
    user_template TEXT NOT NULL,
    input_schema_json JSONB NOT NULL,
    output_schema_json JSONB NOT NULL,
    tool_policy_json JSONB DEFAULT '{}'::jsonb NOT NULL,
    provider_constraints_json JSONB DEFAULT '{}'::jsonb NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    created_by UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    approved_by UUID,
    approved_at TIMESTAMP WITH TIME ZONE,
    deprecated_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_prompt_key_version UNIQUE (prompt_key, semantic_version),
    CONSTRAINT ck_ai_prompt_status CHECK (status IN ('DRAFT','APPROVED','DEPRECATED')),
    UNIQUE (content_hash),
    FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE OR REPLACE FUNCTION prevent_approved_ai_prompt_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'APPROVED' AND (
            NEW.system_template IS DISTINCT FROM OLD.system_template OR
            NEW.user_template IS DISTINCT FROM OLD.user_template OR
            NEW.input_schema_json IS DISTINCT FROM OLD.input_schema_json OR
            NEW.output_schema_json IS DISTINCT FROM OLD.output_schema_json OR
            NEW.tool_policy_json IS DISTINCT FROM OLD.tool_policy_json OR
            NEW.provider_constraints_json IS DISTINCT FROM OLD.provider_constraints_json OR
            NEW.content_hash IS DISTINCT FROM OLD.content_hash
          ) THEN RAISE EXCEPTION 'approved AI prompt content is immutable'; END IF;
          RETURN NEW;
        END $$;;

CREATE TRIGGER trg_ai_prompt_immutable
        BEFORE UPDATE ON ai_prompt_versions FOR EACH ROW
        EXECUTE FUNCTION prevent_approved_ai_prompt_mutation();

INSERT INTO ai_prompt_versions (
                id, prompt_key, semantic_version, status, system_template, user_template,
                input_schema_json, output_schema_json, tool_policy_json,
                provider_constraints_json, content_hash, created_at, approved_at
            ) VALUES (
                '3753772b-72a9-5a8e-804c-c43743cb67c4'::uuid, 'profile-suggestion-explanation', '1.0.0', 'APPROVED',
                'You explain Scalpyn profile suggestions using only supplied evidence. Never invent metrics.', 'Question: {question}
Suggestion evidence: {evidence}
Return the approved JSON schema.',
                '{"type":"object"}'::jsonb, '{"type":"object","required":["analysis","recommendations"],"properties":{"analysis":{"type":"object"},"recommendations":{"type":"array","items":{"type":"object"}},"warnings":{"type":"array","items":{"type":"string"}},"limitations":{"type":"array","items":{"type":"string"}}},"additionalProperties":true}'::jsonb,
                '{"allowlist":[],"live_write":false}'::jsonb, '{"required_capabilities":["text","structured_output"]}'::jsonb,
                '025298155cbb1ebda6462f40d1832d5cae41380ebe2d83cf332a550cf3eefb70', '2026-08-09T01:07:33.633686+00:00'::timestamptz,
                '2026-08-09T01:07:33.633686+00:00'::timestamptz
            );

INSERT INTO ai_prompt_versions (
                id, prompt_key, semantic_version, status, system_template, user_template,
                input_schema_json, output_schema_json, tool_policy_json,
                provider_constraints_json, content_hash, created_at, approved_at
            ) VALUES (
                'ec917cc2-fd51-5d96-b0a3-8f7fb4c79a54'::uuid, 'shadow-detailed-analysis', '1.0.0', 'APPROVED',
                'You audit tenant-scoped Shadow trades. Association is not causation. Cite trade IDs.', 'Question: {question}
Frozen dataset: {dataset}
Configuration: {configuration}
Return the approved JSON schema.',
                '{"type":"object"}'::jsonb, '{"type":"object","required":["analysis","recommendations"],"properties":{"analysis":{"type":"object"},"recommendations":{"type":"array","items":{"type":"object"}},"warnings":{"type":"array","items":{"type":"string"}},"limitations":{"type":"array","items":{"type":"string"}}},"additionalProperties":true}'::jsonb,
                '{"allowlist":[],"live_write":false}'::jsonb, '{"required_capabilities":["text","structured_output"]}'::jsonb,
                '1ac42def5ba244dab9b4208a7b61267000945822f54a244d19f6f9db286f5918', '2026-08-09T01:07:33.633686+00:00'::timestamptz,
                '2026-08-09T01:07:33.633686+00:00'::timestamptz
            );

INSERT INTO ai_prompt_versions (
                id, prompt_key, semantic_version, status, system_template, user_template,
                input_schema_json, output_schema_json, tool_policy_json,
                provider_constraints_json, content_hash, created_at, approved_at
            ) VALUES (
                '004029aa-80b1-5701-8d15-8b98f3261ace'::uuid, 'ai-critic', '1.0.0', 'APPROVED',
                'You are the analysis-only Scalpyn AI Critic. You have no mutation or live authority.', 'Question: {question}
Canonical context: {dataset}
Return the approved JSON schema.',
                '{"type":"object"}'::jsonb, '{"type":"object","required":["analysis","recommendations"],"properties":{"analysis":{"type":"object"},"recommendations":{"type":"array","items":{"type":"object"}},"warnings":{"type":"array","items":{"type":"string"}},"limitations":{"type":"array","items":{"type":"string"}}},"additionalProperties":true}'::jsonb,
                '{"allowlist":[],"live_write":false}'::jsonb, '{"required_capabilities":["text","structured_output"]}'::jsonb,
                'c25a857c6bf0eac5a737f0f7f79d87ea8d8c75342290bf5bbf4e62fb85b6733a', '2026-08-09T01:07:33.633686+00:00'::timestamptz,
                '2026-08-09T01:07:33.633686+00:00'::timestamptz
            );

INSERT INTO ai_prompt_versions (
                id, prompt_key, semantic_version, status, system_template, user_template,
                input_schema_json, output_schema_json, tool_policy_json,
                provider_constraints_json, content_hash, created_at, approved_at
            ) VALUES (
                'f59cf24c-d9d1-52de-96fa-79f61c8a1ce0'::uuid, 'copilot', '1.0.0', 'APPROVED',
                'You are Scalpyn Co-Pilot. Tool policy is enforced by code; live writes are denied.', 'Question: {question}
Screen context: {context}
Return the approved JSON schema.',
                '{"type":"object"}'::jsonb, '{"type":"object","required":["analysis","recommendations"],"properties":{"analysis":{"type":"object"},"recommendations":{"type":"array","items":{"type":"object"}},"warnings":{"type":"array","items":{"type":"string"}},"limitations":{"type":"array","items":{"type":"string"}}},"additionalProperties":true}'::jsonb,
                '{"allowlist":["shadow.get_performance_summary","profiles.get_effective_configuration","audit.get_change_lineage"],"live_write":false}'::jsonb, '{"required_capabilities":["text","structured_output"]}'::jsonb,
                'c610df011b2d59d2bbe4f37d63d6302281406313972e4c135393b051df416331', '2026-08-09T01:07:33.633686+00:00'::timestamptz,
                '2026-08-09T01:07:33.633686+00:00'::timestamptz
            );

CREATE TABLE ai_model_aliases (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    alias VARCHAR(160) NOT NULL,
    provider VARCHAR(40) NOT NULL,
    real_model_id VARCHAR(200) NOT NULL,
    valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    valid_to TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL,
    capabilities JSONB DEFAULT '[]'::jsonb NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_model_alias_provider UNIQUE (provider, alias)
);

CREATE TABLE ai_model_resolutions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    requested_provider VARCHAR(40),
    requested_model VARCHAR(200),
    configured_provider VARCHAR(40),
    configured_model VARCHAR(200),
    effective_provider VARCHAR(40) NOT NULL,
    effective_model VARCHAR(200) NOT NULL,
    catalog_snapshot_hash VARCHAR(64) NOT NULL,
    capabilities JSONB NOT NULL,
    resolution_policy_version VARCHAR(80) NOT NULL,
    resolution_reason VARCHAR(120) NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_model_resolution_tenant_resolved ON ai_model_resolutions (tenant_id, resolved_at);

CREATE TABLE ai_configuration_bundles (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    profile_id UUID,
    profile_version_id UUID,
    score_engine_version_id UUID,
    lineage_refs JSONB DEFAULT '{}'::jsonb NOT NULL,
    bundle_json JSONB NOT NULL,
    bundle_hash VARCHAR(64) NOT NULL,
    lineage_status VARCHAR(40) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE SET NULL,
    FOREIGN KEY(profile_version_id) REFERENCES profile_versions (id) ON DELETE SET NULL,
    FOREIGN KEY(score_engine_version_id) REFERENCES score_engine_versions (id) ON DELETE SET NULL
);

CREATE INDEX ix_ai_bundle_tenant_created ON ai_configuration_bundles (tenant_id, created_at);

CREATE TABLE ai_dataset_snapshots (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    contract_version VARCHAR(80) NOT NULL,
    source_tables JSONB NOT NULL,
    source_labels JSONB NOT NULL,
    event_identity_contract VARCHAR(160) NOT NULL,
    outcome_contract VARCHAR(160) NOT NULL,
    time_anchor VARCHAR(80) NOT NULL,
    window_start TIMESTAMP WITH TIME ZONE NOT NULL,
    window_end TIMESTAMP WITH TIME ZONE NOT NULL,
    filters JSONB NOT NULL,
    exclusions JSONB NOT NULL,
    row_count INTEGER NOT NULL,
    row_ids_hash VARCHAR(64) NOT NULL,
    query_hash VARCHAR(64) NOT NULL,
    dataset_hash VARCHAR(64) NOT NULL,
    configuration_bundle_id UUID NOT NULL,
    quality_status VARCHAR(48) NOT NULL,
    quality_findings JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_ai_dataset_tenant_created ON ai_dataset_snapshots (tenant_id, created_at);

CREATE TABLE ai_requests (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    requested_by_user_id UUID,
    origin_module VARCHAR(120) NOT NULL,
    origin_view VARCHAR(200),
    analysis_mode VARCHAR(40) NOT NULL,
    authority VARCHAR(40) NOT NULL,
    question_hash VARCHAR(64) NOT NULL,
    correlation_id VARCHAR(160) NOT NULL,
    model_resolution_id UUID NOT NULL,
    prompt_version_id UUID NOT NULL,
    dataset_snapshot_id UUID NOT NULL,
    configuration_bundle_id UUID NOT NULL,
    request_json JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_request_tenant_correlation UNIQUE (tenant_id, correlation_id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY(model_resolution_id) REFERENCES ai_model_resolutions (id) ON DELETE RESTRICT,
    FOREIGN KEY(prompt_version_id) REFERENCES ai_prompt_versions (id) ON DELETE RESTRICT,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_ai_request_tenant_created ON ai_requests (tenant_id, created_at);

CREATE TABLE ai_jobs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID NOT NULL,
    purpose VARCHAR(120) NOT NULL,
    dedupe_key VARCHAR(64) NOT NULL,
    status VARCHAR(40) NOT NULL,
    queued_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    heartbeat_at TIMESTAMP WITH TIME ZONE,
    lease_owner VARCHAR(160),
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    attempt INTEGER DEFAULT '0' NOT NULL,
    max_attempts INTEGER DEFAULT '3' NOT NULL,
    retry_after TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    terminal_reason VARCHAR(160),
    last_error_code VARCHAR(80),
    last_error_safe_message TEXT,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_job_tenant_dedupe UNIQUE (tenant_id, dedupe_key),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_job_tenant_status_time ON ai_jobs (tenant_id, status, queued_at);

CREATE INDEX ix_ai_job_lease_expiry ON ai_jobs (status, lease_expires_at);

CREATE TABLE ai_results (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID NOT NULL,
    status VARCHAR(40) NOT NULL,
    result_json JSONB NOT NULL,
    terminal_reason VARCHAR(160),
    completed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_result_tenant_request UNIQUE (tenant_id, ai_request_id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE CASCADE
);

CREATE TABLE ai_usage_records (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID NOT NULL,
    provider VARCHAR(40) NOT NULL,
    model VARCHAR(200) NOT NULL,
    module VARCHAR(120) NOT NULL,
    tokens_input INTEGER NOT NULL,
    tokens_output INTEGER NOT NULL,
    estimated_cost NUMERIC(18, 8) DEFAULT '0' NOT NULL,
    actual_cost NUMERIC(18, 8) DEFAULT '0' NOT NULL,
    currency VARCHAR(8) DEFAULT 'USD' NOT NULL,
    pricing_snapshot_version VARCHAR(80) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_usage_tenant_created ON ai_usage_records (tenant_id, created_at);

CREATE TABLE ai_budget_policies (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    provider VARCHAR(40) NOT NULL,
    model VARCHAR(200),
    module VARCHAR(120),
    daily_token_limit INTEGER,
    monthly_token_limit INTEGER,
    request_token_limit INTEGER NOT NULL,
    null_limit_policy VARCHAR(20) DEFAULT 'DENY' NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_budget_scope UNIQUE (tenant_id, provider, model, module),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE ai_tool_call_audits (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID NOT NULL,
    tool_name VARCHAR(160) NOT NULL,
    tool_version VARCHAR(40) NOT NULL,
    side_effect VARCHAR(40) NOT NULL,
    status VARCHAR(40) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    output_hash VARCHAR(64),
    denial_reason VARCHAR(160),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_tool_audit_tenant_created ON ai_tool_call_audits (tenant_id, created_at);

CREATE TABLE decision_hypotheses (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_decision_hypotheses_tenant_created ON decision_hypotheses (tenant_id, created_at);

CREATE TABLE ai_change_sets (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_ai_change_sets_tenant_created ON ai_change_sets (tenant_id, created_at);

CREATE TABLE regeneration_runs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_regeneration_runs_tenant_created ON regeneration_runs (tenant_id, created_at);

CREATE TABLE experiment_links (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_experiment_links_tenant_created ON experiment_links (tenant_id, created_at);

CREATE TABLE decision_memory (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_decision_memory_tenant_created ON decision_memory (tenant_id, created_at);

CREATE TABLE context_fingerprints (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_context_fingerprints_tenant_created ON context_fingerprints (tenant_id, created_at);

CREATE TABLE mutation_fingerprints (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_mutation_fingerprints_tenant_created ON mutation_fingerprints (tenant_id, created_at);

CREATE TABLE causal_evidence_refs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    tenant_id UUID NOT NULL,
    ai_request_id UUID,
    dataset_snapshot_id UUID,
    configuration_bundle_id UUID,
    authority VARCHAR(40) DEFAULT 'ANALYSIS_ONLY' NOT NULL,
    status VARCHAR(40) DEFAULT 'DRAFT' NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(tenant_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY(ai_request_id) REFERENCES ai_requests (id) ON DELETE SET NULL,
    FOREIGN KEY(dataset_snapshot_id) REFERENCES ai_dataset_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY(configuration_bundle_id) REFERENCES ai_configuration_bundles (id) ON DELETE RESTRICT
);

CREATE INDEX ix_causal_evidence_refs_tenant_created ON causal_evidence_refs (tenant_id, created_at);

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN tenant_id UUID;

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN ai_request_id UUID;

CREATE INDEX ix_shadow_trade_analysis_jobs_tenant_ai_request ON shadow_trade_analysis_jobs (tenant_id, ai_request_id);

ALTER TABLE profile_ai_reviews ADD COLUMN tenant_id UUID;

ALTER TABLE profile_ai_reviews ADD COLUMN ai_request_id UUID;

CREATE INDEX ix_profile_ai_reviews_tenant_ai_request ON profile_ai_reviews (tenant_id, ai_request_id);

ALTER TABLE profile_suggestions ADD COLUMN tenant_id UUID;

ALTER TABLE profile_suggestions ADD COLUMN ai_request_id UUID;

CREATE INDEX ix_profile_suggestions_tenant_ai_request ON profile_suggestions (tenant_id, ai_request_id);

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN heartbeat_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN lease_owner VARCHAR(160);

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN lease_expires_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN attempt INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN max_attempts INTEGER DEFAULT '3' NOT NULL;

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN retry_after TIMESTAMP WITH TIME ZONE;

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN terminal_reason VARCHAR(160);

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN last_error_code VARCHAR(80);

ALTER TABLE shadow_trade_analysis_jobs ADD COLUMN last_error_safe_message TEXT;

ALTER TABLE shadow_trades ADD COLUMN configuration_bundle_id UUID;

CREATE INDEX ix_shadow_trades_configuration_bundle ON shadow_trades (configuration_bundle_id);

UPDATE alembic_version SET version_num='147_systemic_ai_foundation' WHERE alembic_version.version_num = '146_l3_1200_validation';

INFO  [alembic.runtime.migration] Running upgrade 147_systemic_ai_foundation -> 148_langgraph_runtime, Tenant-safe LangGraph runtime metadata and dedicated checkpoint schema.
-- Running upgrade 147_systemic_ai_foundation -> 148_langgraph_runtime

CREATE SCHEMA IF NOT EXISTS langgraph_runtime;

CREATE TABLE ai_graph_definitions (
    id UUID NOT NULL,
    graph_key VARCHAR(120) NOT NULL,
    semantic_version VARCHAR(40) NOT NULL,
    state_schema_version VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    code_revision VARCHAR(80) NOT NULL,
    node_manifest JSONB NOT NULL,
    edge_manifest JSONB NOT NULL,
    tool_policy_version VARCHAR(80) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE,
    deprecated_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_ai_graph_definition_key_version UNIQUE (graph_key, semantic_version),
    CONSTRAINT ck_ai_graph_definition_status CHECK (status IN ('DRAFT','APPROVED','DEPRECATED','BLOCKED')),
    UNIQUE (content_hash)
);

Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\__main__.py", line 4, in <module>
    main(prog="alembic")
    ~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\config.py", line 1047, in main
    CommandLine(prog=prog).main(argv=argv)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\config.py", line 1037, in main
    self.run_cmd(cfg, options)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\config.py", line 971, in run_cmd
    fn(
    ~~^
        config,
        ^^^^^^^
        *[getattr(options, k, None) for k in positional],
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        **{k: getattr(options, k, None) for k in kwarg},
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\command.py", line 483, in upgrade
    script.run_env()
    ~~~~~~~~~~~~~~^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\script\base.py", line 545, in run_env
    util.load_python_file(self.dir, "env.py")
    ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\util\pyfiles.py", line 116, in load_python_file
    module = load_module_py(module_id, path)
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\util\pyfiles.py", line 136, in load_module_py
    spec.loader.exec_module(module)  # type: ignore
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "C:\Users\ricar\Documents\Codex\2026-08-08\scalpyn-systemic-multimodule-langgraph\backend\alembic\env.py", line 102, in <module>
    run_migrations_offline()
    ~~~~~~~~~~~~~~~~~~~~~~^^
  File "C:\Users\ricar\Documents\Codex\2026-08-08\scalpyn-systemic-multimodule-langgraph\backend\alembic\env.py", line 34, in run_migrations_offline
    context.run_migrations()
    ~~~~~~~~~~~~~~~~~~~~~~^^
  File "<string>", line 8, in run_migrations
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\runtime\environment.py", line 969, in run_migrations
    self.get_context().run_migrations(**kw)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\runtime\migration.py", line 626, in run_migrations
    step.migration_fn(**kw)
    ~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Users\ricar\Documents\Codex\2026-08-08\scalpyn-systemic-multimodule-langgraph\backend\alembic\versions\148_langgraph_runtime.py", line 112, in upgrade
    op.bulk_insert(definitions, _definition_rows())
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<string>", line 8, in bulk_insert
  File "<string>", line 3, in bulk_insert
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\operations\ops.py", line 2561, in bulk_insert
    operations.invoke(op)
    ~~~~~~~~~~~~~~~~~^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\operations\base.py", line 452, in invoke
    return fn(self, operation)
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\operations\toimpl.py", line 250, in bulk_insert
    operations.impl.bulk_insert(  # type: ignore[union-attr]
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        operation.table, operation.rows, multiinsert=operation.multiinsert
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\ddl\impl.py", line 497, in bulk_insert
    self._exec(
    ~~~~~~~~~~^
        table.insert()
        ^^^^^^^^^^^^^^
    ...<14 lines>...
        )
        ^
    )
    ^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\ddl\impl.py", line 236, in _exec
    compiled = construct.compile(dialect=self.dialect, **compile_kw)
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\elements.py", line 311, in compile
    return self._compiler(dialect, **kw)
           ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\elements.py", line 323, in _compiler
    return dialect.statement_compiler(dialect, self, **kw)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\compiler.py", line 1462, in __init__
    Compiled.__init__(self, dialect, statement, **kwargs)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\compiler.py", line 902, in __init__
    self.string = self.process(self.statement, **compile_kwargs)
                  ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\compiler.py", line 948, in process
    return obj._compiler_dispatch(self, **kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\visitors.py", line 138, in _compiler_dispatch
    return meth(self, **kw)  # type: ignore  # noqa: E501
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\compiler.py", line 5948, in visit_insert
    crud_params_struct = crud._get_crud_params(
        self,
    ...<4 lines>...
        **kw,
    )
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\crud.py", line 315, in _get_crud_params
    use_insertmanyvalues, use_sentinel_columns = _scan_cols(
                                                 ~~~~~~~~~~^
        compiler,
        ^^^^^^^^^
    ...<9 lines>...
        kw,
        ^^^
    )
    ^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\crud.py", line 711, in _scan_cols
    _append_param_parameter(
    ~~~~~~~~~~~~~~~~~~~~~~~^
        compiler,
        ^^^^^^^^^
    ...<12 lines>...
        kw,
        ^^^
    )
    ^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\crud.py", line 937, in _append_param_parameter
    value = _handle_values_anonymous_param(
        compiler,
    ...<9 lines>...
        **kw,
    )
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\crud.py", line 517, in _handle_values_anonymous_param
    return value._compiler_dispatch(compiler, **kw)
           ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\ext\compiler.py", line 539, in <lambda>
    lambda *arg, **kw: existing(*arg, **kw),
                       ~~~~~~~~^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\ext\compiler.py", line 592, in __call__
    expr = fn(element, compiler, **kw)
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\alembic\util\sqla_compat.py", line 461, in _render_literal_bindparam
    return compiler.render_literal_bindparam(element, **kw)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\compiler.py", line 3895, in render_literal_bindparam
    return self.render_literal_value(value, bindparam.type)
           ~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\dialects\postgresql\base.py", line 2113, in render_literal_value
    value = super().render_literal_value(value, type_)
  File "C:\Users\ricar\AppData\Local\Programs\Python\Python314\Lib\site-packages\sqlalchemy\sql\compiler.py", line 3930, in render_literal_value
    raise exc.CompileError(
    ...<3 lines>...
    )
sqlalchemy.exc.CompileError: No literal value renderer is available for literal value "['load_request', 'authorize_tenant', 'resolve_provider_model', 'resolve_prompt', 'freeze_canonical_dataset', 'resolve_configuration_bundle', 'run_data ... (82 characters truncated) ... yped_tools', 'execute_readonly_tools', 'assemble_evidence', 'invoke_provider', 'validate_structured_output', 'persist_result_usage_audit', 'complete']" with datatype JSONB
