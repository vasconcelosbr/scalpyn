"use client";

import { useEffect } from "react";

/** Registers the service worker in production for PWA offline support. */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      "serviceWorker" in navigator &&
      process.env.NODE_ENV === "production"
    ) {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .catch(() => {/* SW registration failure is non-fatal */});
    }
  }, []);

  return null;
}
