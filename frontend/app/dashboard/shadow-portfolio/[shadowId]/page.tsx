"use client";

import { useParams } from "next/navigation";

import { ShadowTradeDetailScreen } from "@/components/shadow-portfolio/ShadowTradeDetailScreen";

export default function ShadowTradeDetailPage() {
  const params = useParams<{ shadowId: string }>();
  return <ShadowTradeDetailScreen key={params.shadowId} shadowId={params.shadowId} />;
}
