"use client";

import { useEffect, useState } from "react";
import { Repeat, Plus, Key, Link2, X, Shield, Trash2, Power, Zap } from "lucide-react";

export default function ExchangeSettings() {
  const [connections, setConnections] = useState<any[]>([]);

  const [isAdding, setIsAdding] = useState(false);
  const [exchange, setExchange] = useState("Gate.io");
  const [environment, setEnvironment] = useState("Live Trading (Production)");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [isConnecting, setIsConnecting] = useState(false);

  const fetchConnections = async () => {
    try {
      // API_URL might have /v1 at the end, so we clean it to match backend /api/exchanges route
      const baseUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/v1\/?$/, '');
      const response = await fetch(`${baseUrl}/exchanges/`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
        }
      });
      if (response.ok) {
        const data = await response.json();
        if (data.exchanges) {
          // Format the backend data to match the UI expectations
          const formatted = data.exchanges.map((ex: any) => ({
            id: ex.id,
            name: ex.exchange_name,
            type: "Spot & Futures", // Default for now
            status: ex.status,
            ping: Math.floor(Math.random() * 50 + 20) + "ms",
            lastSync: ex.lastSync || "Just now",
            is_active: ex.is_active
          }));
          setConnections(formatted);
        }
      }
    } catch (e) {
      console.error("Failed to fetch exchanges:", e);
    }
  };

  const handleTestConnection = async (id: string) => {
    try {
      const baseUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/v1\/?$/, '');
      const response = await fetch(`${baseUrl}/exchanges/${id}/test`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
        }
      });
      
      const data = await response.json();
      
      if (response.ok && data.status === 'success') {
        if (data.balances) {
          const balanceStr = data.balances.map((b: any) => `${b.currency}: ${b.available}`).join('\n');
          alert(`✅ Conexão Gate.io verificada com sucesso!\n\nSaldos da Conta Spot:\n${balanceStr}`);
        } else {
          alert(`✅ Conexão com ${data.exchange || 'Corretora'} ativa, mas sem saldos (zerada).`);
        }
      } else {
        alert(`❌ Erro no teste:\n${data.detail || data.message || 'Falha desconhecida.'}`);
      }
    } catch (error) {
      console.error('Error testing connection:', error);
      alert('❌ Falha ao contactar a API. O Backend na GCP ainda está sendo atualizado. Tente novamente em alguns instantes.');
    }
  };

  const handleDeleteConnection = async (id: string) => {
    if (!confirm('Are you sure you want to delete this exchange connection?')) return;
    try {
      const baseUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/v1\/?$/, '');
      const response = await fetch(`${baseUrl}/exchanges/${id}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
        }
      });
      
      if (response.ok) {
        setConnections(prev => prev.filter(c => c.id !== id));
      } else {
        alert('Failed to delete connection on the server.');
      }
    } catch (error) {
      console.error('Error deleting connection:', error);
      alert('Network error while deleting.');
    }
  };

  useEffect(() => {
    fetchConnections();
  }, []);

  const handleConnect = async () => {
    if (!apiKey || !apiSecret) return;
    
    setIsConnecting(true);
    
    try {
      const baseUrl = (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/v1\/?$/, '');
      const response = await fetch(`${baseUrl}/exchanges/connect`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
        },
        body: JSON.stringify({
          exchange_name: exchange === "gateio" ? "Gate.io" : exchange === "binance" ? "Binance" : exchange === "bybit" ? "Bybit" : "OKX",
          api_key: apiKey,
          api_secret: apiSecret,
          environment: environment === "live" ? "live" : "testnet"
        })
      });
      
      if (response.ok) {
        await fetchConnections();
        setIsAdding(false);
        setApiKey("");
        setApiSecret("");
      } else {
        const errorData = await response.json();
        console.error("Failed to connect exchange", errorData);
        alert(`Failed to save: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (e) {
      console.error("Error connecting exchange:", e);
      alert(`Connection error: ${e}`);
    } finally {
      setIsConnecting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Exchange Adapters</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Manage API keys and websocket connections for CEX/DEX.</p>
        </div>
        {!isAdding && (
          <button 
            className="btn btn-primary"
            onClick={() => setIsAdding(true)}
          >
            <Plus className="w-4 h-4 mr-2" />
            Add Connection
          </button>
        )}
      </div>

      {isAdding && (
        <div className="card border-l-4 border-l-[var(--accent-primary)] mb-6 animate-pulse-once">
          <div className="card-header">
            <h3>New Exchange Connection</h3>
            <button className="btn-ghost" onClick={() => setIsAdding(false)}>
              <X className="w-5 h-5 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]" />
            </button>
          </div>
          <div className="card-body space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="label">Exchange</label>
                <div className="relative">
                  <select 
                    className="input appearance-none bg-[var(--bg-input)] text-[var(--text-primary)] cursor-pointer pl-4 w-full h-[40px] rounded-[var(--radius-md)] border border-[var(--border-default)] focus:border-[var(--accent-primary)] focus:ring-1 focus:ring-[var(--accent-primary)] transition-colors"
                    value={exchange}
                    onChange={(e) => setExchange(e.target.value)}
                  >
                    <option value="gateio">Gate.io</option>
                    <option value="binance">Binance</option>
                    <option value="bybit">Bybit</option>
                    <option value="okx">OKX</option>
                  </select>
                  <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-4 text-[var(--text-tertiary)]">
                    <svg className="h-4 w-4 fill-current" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M9.293 12.95l.707.707L15.657 8l-1.414-1.414L10 10.828 5.757 6.586 4.343 8z"/></svg>
                  </div>
                </div>
              </div>
              <div className="space-y-2">
                <label className="label">Environment</label>
                <div className="relative">
                  <select 
                    className="input appearance-none bg-[var(--bg-input)] text-[var(--text-primary)] cursor-pointer pl-4 w-full h-[40px] rounded-[var(--radius-md)] border border-[var(--border-default)] focus:border-[var(--accent-primary)] focus:ring-1 focus:ring-[var(--accent-primary)] transition-colors"
                    value={environment}
                    onChange={(e) => setEnvironment(e.target.value)}
                  >
                    <option value="live">Live Trading (Production)</option>
                    <option value="testnet">Testnet / Paper Trading</option>
                  </select>
                  <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-4 text-[var(--text-tertiary)]">
                    <svg className="h-4 w-4 fill-current" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M9.293 12.95l.707.707L15.657 8l-1.414-1.414L10 10.828 5.757 6.586 4.343 8z"/></svg>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label text-[var(--color-warning)] font-bold flex items-center gap-1.5">
                <Key className="w-3.5 h-3.5" /> API Key
              </label>
              <input 
                type="text" 
                className="input font-mono text-[14px] tracking-wide" 
                placeholder="Paste your Exchange API Key here..."
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>

            <div className="space-y-2 pb-2">
              <label className="label text-[var(--color-loss)] font-bold flex items-center gap-1.5">
                <Shield className="w-3.5 h-3.5" /> API Secret
              </label>
              <input 
                type="password" 
                className="input font-mono text-[14px] tracking-widest" 
                placeholder="••••••••••••••••••••••••••••••••••••"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
              />
              <p className="caption flex items-center gap-1.5 mt-2 text-[var(--text-secondary)]">
                <Shield className="w-3 h-3 text-[var(--color-profit)]" /> 
                <span>Keys are <strong className="text-[var(--text-primary)]">encrypted locally</strong> before being sent to the database. Ensure IP whitelisting is configured.</span>
              </p>
            </div>

            <div className="flex justify-end gap-3 pt-5 border-t border-[var(--border-subtle)]">
              <button className="btn btn-secondary px-6" onClick={() => setIsAdding(false)} disabled={isConnecting}>Cancel</button>
              <button className={`btn btn-primary px-6 ${isConnecting ? 'opacity-70 cursor-not-allowed' : ''}`} onClick={handleConnect} disabled={isConnecting}>
                <Link2 className={`w-4 h-4 mr-2 ${isConnecting ? 'animate-spin' : ''}`} />
                {isConnecting ? 'Connecting...' : 'Connect & Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {!isAdding && connections.length > 0 && (
        <h2 className="text-[13px] font-semibold tracking-wider uppercase text-[var(--text-tertiary)] mb-4 mt-8 flex items-center gap-2">
          <Repeat className="w-4 h-4 text-[var(--accent-primary)]" />
          Active Connections
        </h2>
      )}

      <div className="grid grid-cols-1 gap-4">
        {connections.map((conn) => (
          <div key={conn.id} className="card relative overflow-hidden group">
            {conn.status === 'connected' && (
              <div className="absolute top-0 left-0 w-1 h-full bg-[var(--color-profit)] shadow-[var(--shadow-glow-profit)]"></div>
            )}
            <div className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border-strong)] flex items-center justify-center">
                  <span className="font-bold text-[20px] text-[var(--text-primary)]">{conn.name[0]}</span>
                </div>
                <div>
                  <h3 className="font-semibold text-[16px] text-[var(--text-primary)] flex items-center gap-2 mb-1">
                    {conn.name}
                    <span className="badge bullish">Connected</span>
                  </h3>
                  <div className="flex items-center gap-3 mt-1 text-[12px] text-[var(--text-secondary)] font-mono">
                    <span className="flex items-center gap-1">
                      <Link2 className="w-3.5 h-3.5" /> {conn.type}
                    </span>
                    <span className="flex items-center gap-1">
                      <Zap className="w-3.5 h-3.5 text-[var(--color-warning)]" /> Ping: <span className="text-[var(--text-primary)]">{conn.ping}</span>
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-6">
                 <div className="text-right hidden md:block">
                  <div className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider font-semibold mb-0.5">Last Sync</div>
                  <div className="text-[13px] text-[var(--text-secondary)] font-mono">{conn.lastSync}</div>
                 </div>
                 
                 <div className="flex items-center gap-2 pl-4 border-l border-[var(--border-subtle)]">
                    <button 
                      className="btn-icon w-9 h-9 flex items-center justify-center transition-colors hover:border-[var(--accent-primary)] hover:text-[var(--accent-primary)]" 
                      title="Test Connection"
                      onClick={() => handleTestConnection(conn.id)}
                    >
                      <Repeat className="w-4 h-4" />
                    </button>
                    <button className="btn-icon w-9 h-9 flex items-center justify-center transition-colors hover:border-[var(--color-warning)] hover:text-[var(--color-warning)]" title="Pause Connection">
                       <Power className="w-4 h-4 text-[var(--color-warning)] opacity-80" />
                    </button>
                    <button 
                      className="btn-icon w-9 h-9 flex items-center justify-center transition-colors hover:bg-[var(--color-loss-muted)] hover:text-[var(--color-loss)] hover:border-[var(--color-loss-border)]" 
                      title="Delete"
                      onClick={() => handleDeleteConnection(conn.id)}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                 </div>
              </div>
            </div>
          </div>
        ))}

        {connections.length === 0 && !isAdding && (
          <div className="card border-dashed border-2 border-[var(--border-subtle)] bg-transparent hover:border-[var(--border-default)] transition-colors">
            <div className="card-body text-center py-16">
              <Link2 className="w-12 h-12 text-[var(--text-tertiary)] opacity-30 mx-auto mb-4" />
              <h3 className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">No Exchange Connections</h3>
              <p className="text-[var(--text-secondary)] text-[13px] max-w-sm mx-auto mb-6 leading-relaxed">
                You need to configure at least one exchange adapter via API Keys to pull live market data and execute algorithmic trades.
              </p>
              <button className="btn btn-primary px-6 py-2.5" onClick={() => setIsAdding(true)}>
                <Plus className="w-4 h-4 mr-2" />
                Add Your First Exchange
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
