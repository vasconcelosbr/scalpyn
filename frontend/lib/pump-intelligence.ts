export type Metric={known:number;mean:number|null;median:number|null;min:number|null;max:number|null};
export type TargetMetrics={hits:number;misses:number;known:number;unknown:number;descriptive_hit_rate:number|null;
  complete_hits:number;complete_known:number;first_touch_censored:number;time_to_touch_seconds:Metric;
  time_interval_lower_seconds:Metric;time_interval_upper_seconds:Metric;time_unknown_among_hits:number;
  drawdown_before_touch_pct:Metric;mae_before_touch_pct:Metric;pre_touch_unknown:number;pre_touch_not_reached:number;pre_touch_not_measured:number};
export type Support={observations:number;episodes:number;instruments:number;days:number;from:string|null;to:string|null;
  coverage:{complete:number;pending:number;incomplete:number;with_gaps:number;boundary_ambiguous:number;order_ambiguous:number};
  targets:Record<string,TargetMetrics>};
export type Cohort={cohort_id:string;score_config_hash:string;label_spec_hash:string;feature_spec_hash:string;
  producer_config_hash:string;cost_policy_hash:string;label_version:string;reference_policy:string;horizon_minutes:number;
  baseline:Support;patterns:(Support&{pattern:string})[];score_bands:(Support&{pattern:string})[];exploration:Support|null};
export type Intelligence={recalculated:boolean;refresh_requested:boolean;refresh_policy:string;as_of:string;computed_at:string;data_through:string|null;labels_through:string|null;
  freshness:{status:string;age_seconds:number;refresh_failed:boolean};
  scope:{policy:string;whole_history:boolean;sample_limit:number;sampled_observations:number;temporal_buckets:number;window_hours:number;
    window_from:string;sample_truncated:boolean;byte_limited:boolean;read_bytes:number;cache_seconds:number;horizon_minutes:number;score_edges:number[];available_horizons:number[];capture_freshness_seconds:number};
  model:{status:string;reason:string;delta:number;probability:null;auto_promotion:boolean};gates:Record<string,boolean>;
  cohorts:Cohort[];training_runs:{run_id:string;started_at:string;status:string;payload:{reason?:string;duration_seconds?:number}}[]};

/** An older request can never commit after a newer selection/refresh. */
export function latestRequestGuard(){
  let sequence=0;
  return {begin:()=>++sequence,isCurrent:(id:number)=>id===sequence,invalidate:()=>{sequence++;}};
}

/** Prefer supported V4 descriptions; never choose by win rate or merge cohorts. */
export function preferredCohort(cohorts:Cohort[]){
  const known=cohorts.filter(c=>c.baseline.targets["0.8"].known>0);
  const v4=known.filter(c=>c.label_version==="pump_gross_touch_v4");
  const candidates=v4.length?v4:known.length?known:cohorts;
  return [...candidates].sort((a,b)=>b.baseline.targets["0.8"].known-a.baseline.targets["0.8"].known||b.baseline.observations-a.baseline.observations)[0];
}

export function isIntelligenceStale(data:Intelligence|null){
  return !data||data.freshness.status==="stale"||data.freshness.refresh_failed;
}
