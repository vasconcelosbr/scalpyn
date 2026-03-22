"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Signal Rules page has been merged into Block Rules → Entry Triggers tab.
 * Redirect transparently so existing bookmarks continue to work.
 */
export default function SignalRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/settings/block?tab=entry_triggers");
  }, [router]);

  return (
    <div className="flex items-center justify-center h-48 text-[var(--text-secondary)] text-[13px]">
      Redirecting to Block Rules → Entry Triggers…
    </div>
  );
}
