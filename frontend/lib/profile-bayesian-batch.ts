export const ALL_PROFILES_VALUE = "__all_profiles__";

export type BayesianProfileOption = {
  profile_id: string;
  profile_name: string;
};

export function deduplicateProfileOptions(
  profiles: BayesianProfileOption[],
): BayesianProfileOption[] {
  const unique = new Map<string, BayesianProfileOption>();
  for (const profile of profiles) {
    if (!profile.profile_id || unique.has(profile.profile_id)) continue;
    unique.set(profile.profile_id, profile);
  }
  return [...unique.values()];
}

export function analysisTargets(
  selection: string,
  profiles: BayesianProfileOption[],
): BayesianProfileOption[] {
  if (selection === ALL_PROFILES_VALUE) return profiles;
  const selected = profiles.find((profile) => profile.profile_id === selection);
  return selected ? [selected] : [];
}

export function batchIdempotencyKey(batchId: string, profileId: string): string {
  return `profile-batch:${batchId}:${profileId}`;
}
