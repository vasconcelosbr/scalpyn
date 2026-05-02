"use client";

import { useState, useCallback, useEffect } from "react";
import { Activity, ExternalLink, RefreshCw, AlertCircle } from "lucide-react";

const LOAD_TIMEOUT_MS = 12_000;

const C = {
  elevated: "#12131A",
  border: "rgba(255,255,255,0.07)",
  textPrimary: "#E8ECF4",
  textSecondary: "#8B92A5",
  textTertiary: "#555B6E",
  blue: "#4F7BF7",
  warn: "#F59E0B",
} as const;

const GRAFANA_DASHBOARD_UID = "scalpyn-trading-engine";

function buildSrc(baseUrl: string): string {
  const trimmed = baseUrl.replace(/\/$/, "");
  const params = new URLSearchParams({
    orgId: "1",
    kiosk: "tv",
    theme: "dark",
    refresh: "30s",
    from: "now-1h",
    to: "now",
  });
  return `${trimmed}/d/${GRAFANA_DASHBOARD_UID}?${params.toString()}`;
}

export default function MonitoringTab() {
  const grafanaUrl = process.env.NEXT_PUBLIC_GRAFANA_URL;
  const [loaded, setLoaded] = useState(false);
  const [errored, setErrored] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const handleRetry = useCallback(() => {
    setLoaded(false);
    setErrored(false);
    setReloadKey((k) => k + 1);
  }, []);

  // Cross-origin iframes never fire `onError` for HTTP failures (the
  // browser hides the response from the parent for security), so we
  // fall back to a timeout: if `onLoad` hasn't fired in 12 s, surface
  // the error UI so the operator gets a retry button instead of an
  // indefinite spinner.
  useEffect(() => {
    if (!grafanaUrl) return;
    if (loaded || errored) return;
    const handle = window.setTimeout(() => setErrored(true), LOAD_TIMEOUT_MS);
    return () => window.clearTimeout(handle);
  }, [grafanaUrl, loaded, errored, reloadKey]);

  if (!grafanaUrl) {
    return (
      <div
        className="flex flex-col items-center justify-center text-center px-6 py-20 rounded-2xl"
        style={{
          background: C.elevated,
          border: `1px solid ${C.border}`,
          minHeight: "60vh",
        }}
      >
        <Activity size={36} className="mb-4 opacity-30" style={{ color: C.textTertiary }} />
        <h3 className="text-base font-semibold mb-2" style={{ color: C.textPrimary }}>
          Monitoring is not configured yet
        </h3>
        <p className="text-[13px] max-w-md mb-4" style={{ color: C.textSecondary }}>
          The <code className="px-1.5 py-0.5 rounded" style={{ background: "rgba(255,255,255,0.06)" }}>NEXT_PUBLIC_GRAFANA_URL</code> environment
          variable is not set. Provision a Grafana instance and set this variable to the
          public Grafana URL to enable the embedded monitoring dashboard.
        </p>
        <div
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium"
          style={{
            background: "rgba(79,123,247,0.12)",
            color: C.blue,
            border: "1px solid rgba(79,123,247,0.20)",
          }}
        >
          See <code className="font-mono">docs/grafana/README.md</code> §8 in the repo
        </div>
      </div>
    );
  }

  const iframeSrc = buildSrc(grafanaUrl);

  return (
    <div className="space-y-3">
      <div className="md:hidden">
        <div
          className="flex items-start gap-2 px-4 py-3 rounded-xl text-[12px]"
          style={{
            background: "rgba(245,158,11,0.08)",
            border: "1px solid rgba(245,158,11,0.20)",
            color: C.warn,
          }}
        >
          <AlertCircle size={14} className="flex-shrink-0 mt-0.5" />
          Best viewed on desktop. The Grafana dashboard is dense and may render
          poorly on narrow viewports.
        </div>
      </div>

      <div
        className="relative overflow-hidden rounded-2xl"
        style={{
          background: C.elevated,
          border: `1px solid ${C.border}`,
          height: "min(80vh, 1200px)",
          minHeight: 480,
        }}
      >
        {!loaded && !errored && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-3"
            style={{ background: C.elevated, color: C.textSecondary }}
          >
            <RefreshCw size={22} className="animate-spin" style={{ color: C.blue }} />
            <p className="text-[12px]">Loading Grafana dashboard…</p>
          </div>
        )}

        {errored && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center text-center gap-3 px-6"
            style={{ background: C.elevated, color: C.textSecondary }}
          >
            <AlertCircle size={28} style={{ color: C.warn }} />
            <p className="text-sm font-semibold" style={{ color: C.textPrimary }}>
              Grafana failed to load
            </p>
            <p className="text-[12px] max-w-md" style={{ color: C.textTertiary }}>
              Check that <code>NEXT_PUBLIC_GRAFANA_URL</code> is reachable from your
              browser and that anonymous viewer access is enabled. Current value:
              <br />
              <code className="text-[11px] break-all">{grafanaUrl}</code>
            </p>
            <button
              onClick={handleRetry}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium transition-opacity hover:opacity-80"
              style={{
                background: "rgba(79,123,247,0.12)",
                color: C.blue,
                border: "1px solid rgba(79,123,247,0.20)",
              }}
            >
              <RefreshCw size={13} />
              Retry
            </button>
          </div>
        )}

        <iframe
          key={reloadKey}
          src={iframeSrc}
          title="Scalpyn Trading Engine — Grafana"
          loading="lazy"
          referrerPolicy="no-referrer-when-downgrade"
          sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
          onLoad={() => {
            setLoaded(true);
            setErrored(false);
          }}
          onError={() => setErrored(true)}
          style={{
            width: "100%",
            height: "100%",
            border: "none",
            display: errored ? "none" : "block",
            background: C.elevated,
          }}
        />
      </div>

      <div
        className="flex items-center justify-between flex-wrap gap-2 text-[11px]"
        style={{ color: C.textTertiary }}
      >
        <span>
          Embedded from{" "}
          <code style={{ color: C.textSecondary }}>{grafanaUrl}</code> · anonymous viewer
        </span>
        <a
          href={iframeSrc}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 hover:opacity-80"
          style={{ color: C.blue }}
        >
          Open in Grafana <ExternalLink size={11} />
        </a>
      </div>
    </div>
  );
}
