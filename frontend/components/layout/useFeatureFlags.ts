"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

export type FeatureFlags = { pump_radar_ui?: boolean };

export function useFeatureFlags() {
  const [flags, setFlags] = useState<FeatureFlags>({});
  useEffect(() => {
    let active = true;
    apiGet<FeatureFlags>("/config/flags")
      .then((value) => { if (active) setFlags(value); })
      .catch(() => { if (active) setFlags({}); });
    return () => { active = false; };
  }, []);
  return flags;
}
