"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function BlockRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/profiles");
  }, [router]);
  return (
    <div className="flex items-center justify-center h-48 text-[var(--text-secondary)] text-[13px]">
      Redirecting…
    </div>
  );
}
