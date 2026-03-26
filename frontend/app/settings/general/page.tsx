"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Settings, BarChart2, Eye, EyeOff, CheckCircle2, XCircle, AlertCircle, ExternalLink, Plus, Trash2 } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";
import AIProviderSection from "@/components/settings/AIProviderSection";

function authHeaders(): HeadersInit {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  return token
    ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
    : { "Content-Type": "application/json" };
}

interface CMCStatus {
  is_configured: boolean;
  key_hint: string | null;
  test_status: "ok" | "error" | null;
  test_error: string | null;
  last_tested_at: string | null;
}

function CMCProviderCard() {
  const [status, setStatus] = useState<CMCStatus>({
    is_configured: false, key_hint: null,
    test_status: null, test_error: null, last_tested_at: null,
  });
  const [editing, setEditing] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/ai-keys", { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        const cmc = data.find((p: { provider: string }) => p.provider === "coinmarketcap");
        if (cmc) {
          setStatus({
            is_configured: cmc.is_configured,
            key_hint: cmc.key_hint,
            test_status: cmc.test_status,
            test_error: cmc.test_error,
            last_tested_at: cmc.last_tested_at,
          });
          setEditing(!cmc.is_configured);
        }
      }
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    if (!apiKey.trim()) { setError("Insira a API key."); return; }
    setSaving(true); setError(null); setTestResult(null);
    try {
      const res = await fetch("/api/ai-keys/coinmarketcap", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ api_key: apiKey.trim() }),
      });
      if (!res.ok) {
        const e = await res.json();
        setError(e.detail ?? "Erro ao salvar.");
        setSaving(false); return;
      }
      setEditing(false); setApiKey(""); setSaving(false);
      load();
    } catch { setError("Erro de conexão."); setSaving(false); }
  };

  const handleTest = async () => {
    setTesting(true); setTestResult(null);
    try {
      const res = await fetch("/api/ai-keys/coinmarketcap/test", { method: "POST", headers: authHeaders() });
      const data = await res.json();
      setTestResult({ success: data.success, message: data.message });
      if (data.success) load();
    } catch { setTestResult({ success: false, message: "Erro de conexão." }); }
    setTesting(false);
  };

  const handleDelete = async () => {
    if (!confirm("Remover a chave CoinMarketCap?")) return;
    await fetch("/api/ai-keys/coinmarketcap", { method: "DELETE", headers: authHeaders() });
    load();
  };

  const inputStyle: React.CSSProperties = {
    width: "100%", fontSize: 13, fontFamily: "var(--font-mono)",
    background: "var(--bg-input)", border: "1px solid var(--border-default)",
    borderRadius: 8, padding: "9px 40px 9px 12px",
    color: "var(--text-primary)", outline: "none", boxSizing: "border-box",
  };

  const accentColor = "#F7931A";
  const isOk = status.test_status === "ok";

  if (loading) return <div className="skeleton" style={{ height: 72, borderRadius: 12 }} />;

  return (
    <div style={{
      background: status.is_configured ? `${accentColor}08` : "var(--bg-elevated)",
      border: `1px solid ${status.is_configured ? `${accentColor}28` : "var(--border-default)"}`,
      borderRadius: 12, overflow: "hidden", transition: "all 200ms",
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, padding: "16px 20px", borderBottom: (editing || status.is_configured) ? "1px solid var(--border-subtle)" : "none" }}>
        <div style={{ width: 40, height: 40, borderRadius: 10, flexShrink: 0, background: `${accentColor}18`, border: `1px solid ${accentColor}30`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20 }}>
          ₿
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>CoinMarketCap</span>
            {status.is_configured && isOk && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, padding: "3px 8px", borderRadius: 20, fontWeight: 600, background: "var(--color-profit-muted)", color: "var(--color-profit)" }}>
                <CheckCircle2 size={10} />Conectado
              </span>
            )}
            {status.is_configured && status.test_status === "error" && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, padding: "3px 8px", borderRadius: 20, fontWeight: 600, background: "var(--color-loss-muted)", color: "var(--color-loss)" }}>
                <XCircle size={10} />Erro
              </span>
            )}
            {status.is_configured && !status.test_status && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, padding: "3px 8px", borderRadius: 20, fontWeight: 600, background: "var(--color-warning-muted)", color: "var(--color-warning)" }}>
                <AlertCircle size={10} />Não testado
              </span>
            )}
            {!status.is_configured && (
              <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 20, fontWeight: 600, background: "rgba(255,255,255,0.04)", color: "var(--text-tertiary)" }}>Não configurado</span>
            )}
          </div>
          <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0, lineHeight: 1.5 }}>
            <strong style={{ color: "var(--text-primary)" }}>Opcional.</strong> Gate.io já fornece market cap gratuitamente. CMC melhora a precisão dos dados para moedas com menor liquidez.
          </p>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            {["Melhoria de precisão", "Fallback Gate.io ativo", "30min"].map(tag => (
              <span key={tag} style={{ fontSize: 10, padding: "2px 7px", borderRadius: 20, background: "rgba(255,255,255,0.04)", border: "1px solid var(--border-subtle)", color: "var(--text-tertiary)" }}>{tag}</span>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          <a href="https://pro.coinmarketcap.com/account" target="_blank" rel="noopener noreferrer" style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-tertiary)", textDecoration: "none" }}>
            <ExternalLink size={12} />Docs
          </a>
          {status.is_configured && !editing && (
            <>
              <button onClick={() => setEditing(true)} style={{ fontSize: 11, padding: "5px 10px", borderRadius: 6, background: "transparent", border: "1px solid var(--border-default)", cursor: "pointer", color: "var(--text-secondary)" }}>Editar</button>
              <button onClick={handleDelete} style={{ background: "none", border: "none", cursor: "pointer", padding: 4 }}><Trash2 size={13} color="var(--text-tertiary)" /></button>
            </>
          )}
          {!status.is_configured && !editing && (
            <button onClick={() => setEditing(true)} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 600, padding: "7px 12px", borderRadius: 7, background: "var(--accent-primary-muted)", border: "1px solid var(--accent-primary-border)", cursor: "pointer", color: "var(--accent-primary)" }}>
              <Plus size={12} />Configurar
            </button>
          )}
        </div>
      </div>

      {/* Connected state */}
      {status.is_configured && !editing && (
        <div style={{ padding: "12px 20px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>API Key</span>
              <code style={{ fontSize: 12, fontFamily: "var(--font-mono)", background: "var(--bg-input)", border: "1px solid var(--border-default)", borderRadius: 6, padding: "4px 10px", color: "var(--text-secondary)" }}>
                {status.key_hint ?? "••••••••••••"}
              </code>
              {status.last_tested_at && (
                <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  testado {new Date(status.last_tested_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                </span>
              )}
            </div>
            <button onClick={handleTest} disabled={testing} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, fontWeight: 600, padding: "6px 12px", borderRadius: 6, background: "transparent", border: "1px solid var(--border-default)", cursor: testing ? "wait" : "pointer", color: "var(--text-secondary)" }}>
              <RefreshCw size={11} style={testing ? { animation: "spin 1s linear infinite" } : {}} />
              {testing ? "Testando..." : "Testar conexão"}
            </button>
          </div>
          {testResult && (
            <div style={{ marginTop: 10, padding: "8px 12px", borderRadius: 7, fontSize: 12, background: testResult.success ? "var(--color-profit-muted)" : "var(--color-loss-muted)", border: `1px solid ${testResult.success ? "var(--color-profit-border)" : "var(--color-loss-border)"}`, color: testResult.success ? "var(--color-profit)" : "var(--color-loss)", display: "flex", alignItems: "center", gap: 7 }}>
              {testResult.success ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
              {testResult.message}
            </div>
          )}
        </div>
      )}

      {/* Edit / add key form */}
      {editing && (
        <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <label style={{ display: "block", fontSize: 11, fontWeight: 600, color: "var(--text-tertiary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em" }}>API Key</label>
            <div style={{ position: "relative" }}>
              <input
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="Cole sua CMC API key aqui..."
                autoComplete="off"
                style={{ ...inputStyle, borderColor: error ? "var(--color-loss-border)" : "var(--border-default)" }}
              />
              <button onClick={() => setShowKey(!showKey)} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex" }}>
                {showKey ? <EyeOff size={15} color="var(--text-tertiary)" /> : <Eye size={15} color="var(--text-tertiary)" />}
              </button>
            </div>
            <p style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "6px 0 0" }}>
              Obtenha sua chave em{" "}
              <a href="https://pro.coinmarketcap.com/account" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent-primary)" }}>pro.coinmarketcap.com</a>.
              O plano gratuito (Basic) é suficiente.
            </p>
          </div>
          {error && (
            <div style={{ fontSize: 12, padding: "8px 12px", borderRadius: 7, background: "var(--color-loss-muted)", border: "1px solid var(--color-loss-border)", color: "var(--color-loss)" }}>{error}</div>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            {status.is_configured && (
              <button onClick={() => { setEditing(false); setApiKey(""); setError(null); }} style={{ padding: "8px 16px", fontSize: 12, borderRadius: 7, background: "transparent", border: "1px solid var(--border-default)", cursor: "pointer", color: "var(--text-secondary)" }}>
                Cancelar
              </button>
            )}
            <button onClick={handleSave} disabled={saving || !apiKey.trim()} style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 18px", fontSize: 12, fontWeight: 600, borderRadius: 7, background: "var(--accent-primary)", border: "none", cursor: saving ? "wait" : "pointer", color: "#fff", opacity: saving || !apiKey.trim() ? 0.6 : 1 }}>
              {saving ? <><RefreshCw size={12} style={{ animation: "spin 1s linear infinite" }} />Salvando...</> : "Salvar e conectar"}
            </button>
          </div>
        </div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }`}</style>
    </div>
  );
}

export default function GeneralSettings() {
  const { config, updateConfig, isLoading } = useConfig("universe");
  const [local, setLocal] = useState({
    min_volume_24h: 5000000,
    min_market_cap: 50000000,
    accepted_pairs: ["USDT"],
    accepted_exchanges: ["gate"],
    max_assets: 100,
    refresh_interval_hours: 24,
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) setLocal({ ...local, ...config });
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig(local); } catch (e) { console.error(e); }
    setSaving(false);
  };

  if (isLoading) return <div className="p-8"><div className="skeleton h-96 w-full" /></div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">General Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Configure the asset universe and global platform settings.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Universe of Assets */}
      <div className="card">
        <div className="card-header"><h3>Universe of Assets</h3></div>
        <div className="card-body">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="label">Min 24h Volume (USD)</label>
              <div className="input-group">
                <input type="number" className="input numeric" value={local.min_volume_24h} onChange={(e) => setLocal({ ...local, min_volume_24h: parseInt(e.target.value) || 0 })} />
                <span className="suffix">USD</span>
              </div>
              <p className="caption">Only track assets with at least this much daily volume.</p>
            </div>

            <div className="space-y-2">
              <label className="label">Min Market Cap (USD)</label>
              <div className="input-group">
                <input type="number" className="input numeric" value={local.min_market_cap} onChange={(e) => setLocal({ ...local, min_market_cap: parseInt(e.target.value) || 0 })} />
                <span className="suffix">USD</span>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label">Max Assets to Track</label>
              <div className="slider-container">
                <input type="range" min={10} max={500} value={local.max_assets} onChange={(e) => setLocal({ ...local, max_assets: parseInt(e.target.value) })} className="slider" />
                <span className="slider-value">{local.max_assets}</span>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label">Refresh Interval</label>
              <div className="input-group">
                <input type="number" className="input numeric" value={local.refresh_interval_hours} onChange={(e) => setLocal({ ...local, refresh_interval_hours: parseInt(e.target.value) || 24 })} />
                <span className="suffix">hours</span>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label">Quote Pairs</label>
              <input type="text" className="input text-[13px]" value={local.accepted_pairs.join(", ")} onChange={(e) => setLocal({ ...local, accepted_pairs: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </div>

            <div className="space-y-2">
              <label className="label">Exchanges</label>
              <input type="text" className="input text-[13px]" value={local.accepted_exchanges.join(", ")} onChange={(e) => setLocal({ ...local, accepted_exchanges: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </div>
          </div>
        </div>
      </div>

      {/* Data Providers */}
      <div className="card">
        <div className="card-header">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <BarChart2 size={15} color="var(--accent-primary)" />
            <h3 style={{ margin: 0 }}>Provedores de Dados de Mercado</h3>
          </div>
        </div>
        <div className="card-body">
          <CMCProviderCard />
        </div>
      </div>

      {/* AI Provider Keys */}
      <div className="card">
        <div className="card-body">
          <AIProviderSection />
        </div>
      </div>
    </div>
  );
}
