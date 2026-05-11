"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { ChevronDown, ChevronRight, ShieldOff } from "lucide-react";
import { apiGet } from "@/lib/api";

interface RejectionGroup {
  rule_key: string;
  status: string;
  hits: number;
  distinct_symbols: number;
  sample_symbols: string[] | null;
}

interface RejectionsResponse {
  since: string;
  window_seconds: number;
  cutoff: string;
  groups: RejectionGroup[];
  count: number;
}

const ENDPOINT = "/api/diagnostics/rejections/summary?since=24h";


/**
 * Section 4: collapsible rejection summary.
 *
 * Default-closed per spec. We DON'T paginate — the backend caps at
 * 200 groups already, and rendering 200 short rows is cheaper than
 * adding a paginator that hides aggregate insight.
 *
 * The progress bar is normalised against the *largest* hit count in
 * the response so the operator instantly sees the dominant blocker
 * even when total volume varies wildly between days.
 */
export function RejectionSummary() {
  const [open, setOpen] = useState(false);

  // SWR pause: only fetch when expanded. Until then the network is
  // entirely idle for this section.
  const { data, isLoading } = useSWR<RejectionsResponse>(
    open ? ENDPOINT : null,
    (url: string) => apiGet<RejectionsResponse>(url),
    {
      refreshInterval: open ? 60_000 : 0,
      revalidateOnFocus: false,
      dedupingInterval: 30_000,
      keepPreviousData: true,
    }
  );

  const groups = data?.groups ?? [];
  const maxHits = useMemo(
    () => groups.reduce((m, g) => Math.max(m, g.hits), 0),
    [groups]
  );

  return (
    <div className="card fade-in-up">
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          background: "transparent",
          border: 0,
          padding: 0,
          cursor: "pointer",
        }}
      >
        <div
          className="card-header"
          style={{
            cursor: "pointer",
            transition: "background var(--transition-fast)",
          }}
        >
          <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <ShieldOff size={16} style={{ color: "var(--color-warning)" }} />
            Top regras que mais bloquearam — últimas 24h
          </h3>
          {open && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--text-tertiary)",
              }}
            >
              {groups.length} grupos
            </span>
          )}
        </div>
      </button>

      {open && (
        <div style={{ padding: 12, maxHeight: 480, overflowY: "auto" }} className="custom-scrollbar">
          {isLoading && groups.length === 0 ? (
            <SkeletonRows />
          ) : groups.length === 0 ? (
            <div
              style={{
                padding: "32px 16px",
                textAlign: "center",
                color: "var(--text-tertiary)",
              }}
            >
              <div style={{ color: "var(--text-secondary)", fontSize: 13, fontWeight: 600 }}>
                Nenhuma reprovação na janela
              </div>
              <div style={{ fontSize: 12, marginTop: 4 }}>
                As últimas 24h estão limpas — nenhum gate bloqueou trades.
              </div>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ minWidth: 240 }}>Regra</th>
                  <th style={{ textAlign: "right" }}>Bloqueios</th>
                  <th style={{ textAlign: "right" }}>Símbolos</th>
                  <th style={{ minWidth: 200 }}>Distribuição</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g, i) => (
                  <tr key={`${g.rule_key}-${g.status}-${i}`}>
                    <td>
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: 13,
                            color: "var(--text-primary)",
                          }}
                        >
                          {g.rule_key}
                        </span>
                        <span
                          style={{
                            fontSize: 10,
                            color: "var(--text-tertiary)",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em",
                          }}
                        >
                          {g.status}
                        </span>
                      </div>
                    </td>
                    <td className="numeric" style={{ fontWeight: 600 }}>
                      {g.hits.toLocaleString()}
                    </td>
                    <td className="numeric">{g.distinct_symbols}</td>
                    <td>
                      <div
                        style={{
                          height: 6,
                          background: "var(--bg-hover)",
                          borderRadius: 3,
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            width: `${maxHits ? (g.hits / maxHits) * 100 : 0}%`,
                            height: "100%",
                            background: "var(--color-warning)",
                            transition: "width var(--transition-base)",
                          }}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

function SkeletonRows() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          style={{
            height: 36,
            borderRadius: 6,
            background:
              "linear-gradient(90deg, var(--bg-hover) 0%, var(--bg-active) 50%, var(--bg-hover) 100%)",
            backgroundSize: "200% 100%",
            animation: "shimmer 1.4s ease-in-out infinite",
          }}
        />
      ))}
    </div>
  );
}
