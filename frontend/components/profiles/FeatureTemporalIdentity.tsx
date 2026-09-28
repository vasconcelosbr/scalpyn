import { profileTemporalIdentity, type ProfileSourcePolicies } from "@/lib/profileConditionState";

export function FeatureTemporalIdentity({ condition, policies, defaultTimeframe }: {
  condition: Record<string, any>;
  policies?: ProfileSourcePolicies;
  defaultTimeframe?: string;
}) {
  const identity = profileTemporalIdentity(condition, policies, defaultTimeframe);
  if (identity.source === "decision_context") return null;
  return (
    <span className="text-[11px] text-[var(--text-secondary)]" title={identity.detail} data-testid="feature-temporal-identity">
      {identity.label}
      {identity.needsValidation && <span className="ml-1 text-amber-500">· verificar contrato</span>}
    </span>
  );
}
