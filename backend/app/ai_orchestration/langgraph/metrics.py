"""Prometheus telemetry for the durable systemic AI runtime."""

from prometheus_client import Counter, Gauge, Histogram


graph_runs_total = Counter("graph_runs_total", "Durable graph runs", ["graph_key", "status"])
graph_runs_running = Gauge("graph_runs_running", "Graph runs currently executing")
graph_runs_interrupted = Counter("graph_runs_interrupted", "Graph human interrupts", ["interrupt_type"])
graph_runs_resumed = Counter("graph_runs_resumed", "Graph resumes", ["decision"])
graph_runs_failed = Counter("graph_runs_failed", "Graph terminal failures", ["error_code"])
checkpoint_writes = Counter("checkpoint_writes", "Observed successful graph checkpoint boundaries")
checkpoint_resume_latency = Histogram("checkpoint_resume_latency_seconds", "Time spent resuming a checkpoint")
stale_graph_leases = Counter("stale_graph_leases_total", "Expired graph leases recovered")
node_duration = Histogram("graph_node_duration_seconds", "Graph node duration", ["node_name"])
node_retries = Counter("graph_node_retries_total", "Idempotent node re-entries", ["node_name"])
provider_calls = Counter("graph_provider_calls_total", "Provider calls delegated by graphs", ["provider"])
provider_cost = Counter("graph_provider_cost_usd_total", "Reconciled provider cost", ["provider"])
tool_calls = Counter("graph_tool_calls_total", "Typed tool calls", ["tool_name", "status"])
human_approval_latency = Histogram("graph_human_approval_latency_seconds", "Human gate latency", ["interrupt_type"])
shadow_wait_duration = Histogram("graph_shadow_wait_duration_seconds", "Shadow evidence wait duration")
decision_memory_hits = Counter("graph_decision_memory_hits_total", "Decision memory records retrieved")
cross_tenant_denials = Counter("graph_cross_tenant_denials_total", "Denied cross-tenant graph access")
live_write_denials = Counter("graph_live_write_denials_total", "Denied live-write graph actions")
spot_invariant_conflicts = Counter("graph_spot_invariant_conflicts_total", "Spot invariant conflicts")
