"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[GlobalError]", error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          background: "#06070A",
          color: "#E8ECF4",
          fontFamily: "system-ui, sans-serif",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          margin: 0,
          flexDirection: "column",
          gap: "16px",
          padding: "24px",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            background: "#0F1117",
            border: "1px solid #2A2D3A",
            borderRadius: "8px",
            padding: "24px",
            maxWidth: "600px",
            width: "100%",
          }}
        >
          <h1
            style={{
              fontSize: "16px",
              fontWeight: 700,
              color: "#F87171",
              margin: "0 0 8px",
            }}
          >
            Erro crítico na aplicação
          </h1>
          <p
            style={{
              fontSize: "13px",
              color: "#8B92A5",
              margin: "0 0 16px",
            }}
          >
            {error?.message || "Erro desconhecido"}
          </p>
          {error?.stack && (
            <pre
              style={{
                fontSize: "11px",
                color: "#555B6E",
                background: "#06070A",
                padding: "12px",
                borderRadius: "4px",
                overflow: "auto",
                margin: "0 0 16px",
                maxHeight: "200px",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {error.stack}
            </pre>
          )}
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              onClick={reset}
              style={{
                padding: "8px 16px",
                background: "#1E2130",
                border: "1px solid #2A2D3A",
                borderRadius: "6px",
                color: "#E8ECF4",
                cursor: "pointer",
                fontSize: "13px",
              }}
            >
              Tentar novamente
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: "8px 16px",
                background: "transparent",
                border: "1px solid #2A2D3A",
                borderRadius: "6px",
                color: "#8B92A5",
                cursor: "pointer",
                fontSize: "13px",
              }}
            >
              Recarregar página
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
