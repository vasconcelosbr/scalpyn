"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function ReplayRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/trading-desk/spot");
  }, [router]);
  return (
    <div className="flex items-center justify-center h-48 text-[var(--text-secondary)] text-[13px]">
      Redirecting…
    </div>
  );
}
