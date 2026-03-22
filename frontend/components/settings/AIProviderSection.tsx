'use client'
/**
 * AIProviderSection.tsx
 * Section of /settings/general for AI API key configuration.
 * Keys are NEVER returned by the backend — only key_hint is exposed.
 */
import { useState, useEffect } from 'react'
import {
  Brain, ExternalLink, Eye, EyeOff,
  CheckCircle2, XCircle, AlertCircle,
  RefreshCw, Trash2, Plus, Zap,
} from 'lucide-react'

interface ProviderStatus {
  provider: string
  name?: string
  key_hint: string | null
  label: string | null
  is_configured: boolean
  is_validated: boolean
  test_status: 'ok' | 'error' | 'pending' | null
  test_error: string | null
  last_tested_at: string | null
  tokens_used_month: number | null
  monthly_token_limit: number | null
  docs_url: string
}

const PMETA: Record<string, {
  icon: string; color: string; bg: string; border: string
  desc: string; usedBy: string[]; placeholder: string
}> = {
  anthropic: {
    icon: '◆', color: '#CC785C',
    bg: 'rgba(204,120,92,0.07)', border: 'rgba(204,120,92,0.2)',
    desc: 'Motor do Auto-Pilot IA, Preset IA e análises de regime.',
    usedBy: ['Auto-Pilot IA', 'Preset IA', 'Análise de regime'],
    placeholder: 'sk-ant-api03-...',
  },
  openai: {
    icon: '⬡', color: '#10A37F',
    bg: 'rgba(16,163,127,0.07)', border: 'rgba(16,163,127,0.2)',
    desc: 'Alternativa ao Anthropic. Fallback automático.',
    usedBy: ['Fallback IA'],
    placeholder: 'sk-proj-...',
  },
  gemini: {
    icon: '✦', color: '#4285F4',
    bg: 'rgba(66,133,244,0.07)', border: 'rgba(66,133,244,0.2)',
    desc: 'Google Gemini para análises complementares.',
    usedBy: ['Análises complementares'],
    placeholder: 'AIzaSy...',
  },
}

function authHeaders(): HeadersInit {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null
  return token
    ? { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    : { 'Content-Type': 'application/json' }
}

function Badge({ status }: { status: ProviderStatus }) {
  if (!status.is_configured)
    return <span style={{ fontSize: 11, padding: '3px 8px', borderRadius: 20, fontWeight: 600, background: 'rgba(255,255,255,0.04)', color: 'var(--text-tertiary)' }}>Não configurado</span>
  if (status.test_status === 'ok')
    return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, padding: '3px 8px', borderRadius: 20, fontWeight: 600, background: 'var(--color-profit-muted)', color: 'var(--color-profit)' }}><CheckCircle2 size={10} />Conectado</span>
  if (status.test_status === 'error')
    return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, padding: '3px 8px', borderRadius: 20, fontWeight: 600, background: 'var(--color-loss-muted)', color: 'var(--color-loss)' }}><XCircle size={10} />Erro</span>
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, padding: '3px 8px', borderRadius: 20, fontWeight: 600, background: 'var(--color-warning-muted)', color: 'var(--color-warning)' }}><AlertCircle size={10} />Não testado</span>
}

function ProviderCard({ status, onRefresh }: { status: ProviderStatus; onRefresh: () => void }) {
  const meta = PMETA[status.provider] ?? PMETA.anthropic
  const isMain = status.provider === 'anthropic'
  const [apiKey, setApiKey] = useState('')
  const [label, setLabel] = useState(status.label ?? '')
  const [limit, setLimit] = useState(status.monthly_token_limit?.toString() ?? '')
  const [showKey, setShowKey] = useState(false)
  const [editing, setEditing] = useState(!status.is_configured)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const inputStyle: React.CSSProperties = {
    width: '100%', fontSize: 13, fontFamily: 'var(--font-mono)',
    background: 'var(--bg-input)', border: '1px solid var(--border-default)',
    borderRadius: 8, padding: '9px 40px 9px 12px',
    color: 'var(--text-primary)', outline: 'none', boxSizing: 'border-box',
  }

  const handleSave = async () => {
    if (!apiKey.trim()) { setError('Insira a API key.'); return }
    setSaving(true); setError(null); setTestResult(null)
    try {
      const res = await fetch(`/api/ai-keys/${status.provider}`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          api_key: apiKey.trim(),
          label: label || null,
          monthly_token_limit: limit ? parseInt(limit) : null,
        }),
      })
      if (!res.ok) {
        const e = await res.json()
        setError(e.detail ?? 'Erro ao salvar.')
        setSaving(false); return
      }
      setEditing(false); setApiKey(''); setSaving(false); onRefresh()
    } catch {
      setError('Erro de conexão.')
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true); setTestResult(null)
    try {
      const res = await fetch(`/api/ai-keys/${status.provider}/test`, {
        method: 'POST',
        headers: authHeaders(),
      })
      const data = await res.json()
      setTesting(false)
      setTestResult({ success: data.success, message: data.message })
      if (data.success) onRefresh()
    } catch {
      setTesting(false)
      setTestResult({ success: false, message: 'Erro de conexão.' })
    }
  }

  const handleDelete = async () => {
    if (!confirm(`Remover a chave ${status.name ?? status.provider}?`)) return
    await fetch(`/api/ai-keys/${status.provider}`, { method: 'DELETE', headers: authHeaders() })
    onRefresh()
  }

  return (
    <div style={{ background: status.is_configured ? meta.bg : 'var(--bg-elevated)', border: `1px solid ${status.is_configured ? meta.border : 'var(--border-default)'}`, borderRadius: 12, overflow: 'hidden', transition: 'all 200ms' }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, padding: '16px 20px', borderBottom: (editing || status.is_configured) ? '1px solid var(--border-subtle)' : 'none' }}>
        <div style={{ width: 40, height: 40, borderRadius: 10, flexShrink: 0, background: `${meta.color}18`, border: `1px solid ${meta.color}30`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, color: meta.color }}>{meta.icon}</div>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{status.name ?? status.provider}</span>
            {isMain && <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 20, fontWeight: 700, background: `${meta.color}18`, color: meta.color, letterSpacing: '0.04em' }}>PRINCIPAL</span>}
            <Badge status={status} />
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>{meta.desc}</p>
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {meta.usedBy.map(tag => (
              <span key={tag} style={{ fontSize: 10, padding: '2px 7px', borderRadius: 20, background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-subtle)', color: 'var(--text-tertiary)' }}>{tag}</span>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
          <a href={status.docs_url} target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-tertiary)', textDecoration: 'none' }}>
            <ExternalLink size={12} />Docs
          </a>
          {status.is_configured && !editing && (
            <>
              <button onClick={() => setEditing(true)} style={{ fontSize: 11, padding: '5px 10px', borderRadius: 6, background: 'transparent', border: '1px solid var(--border-default)', cursor: 'pointer', color: 'var(--text-secondary)' }}>Editar</button>
              <button onClick={handleDelete} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}><Trash2 size={13} color="var(--text-tertiary)" /></button>
            </>
          )}
          {!status.is_configured && !editing && (
            <button onClick={() => setEditing(true)} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, padding: '7px 12px', borderRadius: 7, background: 'var(--accent-primary-muted)', border: '1px solid var(--accent-primary-border)', cursor: 'pointer', color: 'var(--accent-primary)' }}>
              <Plus size={12} />Configurar
            </button>
          )}
        </div>
      </div>

      {/* Connected state */}
      {status.is_configured && !editing && (
        <div style={{ padding: '12px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>API Key</span>
              <code style={{ fontSize: 12, fontFamily: 'var(--font-mono)', background: 'var(--bg-input)', border: '1px solid var(--border-default)', borderRadius: 6, padding: '4px 10px', color: 'var(--text-secondary)' }}>{status.key_hint ?? '••••••••••••'}</code>
              {status.last_tested_at && (
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  testado {new Date(status.last_tested_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                </span>
              )}
            </div>
            {status.tokens_used_month != null && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Zap size={12} color="var(--text-tertiary)" />
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                  {(status.tokens_used_month / 1000).toFixed(0)}k tokens este mês{status.monthly_token_limit ? ` / ${(status.monthly_token_limit / 1_000_000).toFixed(1)}M limite` : ''}
                </span>
              </div>
            )}
            <button onClick={handleTest} disabled={testing} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 600, padding: '6px 12px', borderRadius: 6, background: 'transparent', border: '1px solid var(--border-default)', cursor: testing ? 'wait' : 'pointer', color: 'var(--text-secondary)' }}>
              <RefreshCw size={11} style={testing ? { animation: 'spin 1s linear infinite' } : {}} />
              {testing ? 'Testando...' : 'Testar conexão'}
            </button>
          </div>
          {testResult && (
            <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 7, fontSize: 12, background: testResult.success ? 'var(--color-profit-muted)' : 'var(--color-loss-muted)', border: `1px solid ${testResult.success ? 'var(--color-profit-border)' : 'var(--color-loss-border)'}`, color: testResult.success ? 'var(--color-profit)' : 'var(--color-loss)', display: 'flex', alignItems: 'center', gap: 7 }}>
              {testResult.success ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
              {testResult.message}
            </div>
          )}
          {status.test_status === 'error' && !testResult && status.test_error && (
            <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 7, fontSize: 12, background: 'var(--color-loss-muted)', border: '1px solid var(--color-loss-border)', color: 'var(--color-loss)' }}>{status.test_error}</div>
          )}
        </div>
      )}

      {/* Edit / add key form */}
      {editing && (
        <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>API Key</label>
            <div style={{ position: 'relative' }}>
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder={meta.placeholder}
                autoComplete="off"
                style={{ ...inputStyle, borderColor: error ? 'var(--color-loss-border)' : 'var(--border-default)' }}
              />
              <button onClick={() => setShowKey(!showKey)} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex' }}>
                {showKey ? <EyeOff size={15} color="var(--text-tertiary)" /> : <Eye size={15} color="var(--text-tertiary)" />}
              </button>
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Label (opcional)</label>
              <input type="text" value={label} onChange={e => setLabel(e.target.value)} placeholder="Ex: Produção" style={{ ...inputStyle, padding: '9px 12px', fontFamily: 'var(--font-sans,sans-serif)' }} />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Limite mensal (tokens)</label>
              <input type="number" value={limit} onChange={e => setLimit(e.target.value)} placeholder="Ex: 5000000" style={{ ...inputStyle, padding: '9px 12px' }} />
            </div>
          </div>
          {error && (
            <div style={{ fontSize: 12, padding: '8px 12px', borderRadius: 7, background: 'var(--color-loss-muted)', border: '1px solid var(--color-loss-border)', color: 'var(--color-loss)' }}>{error}</div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            {status.is_configured && (
              <button onClick={() => { setEditing(false); setApiKey(''); setError(null) }} style={{ padding: '8px 16px', fontSize: 12, borderRadius: 7, background: 'transparent', border: '1px solid var(--border-default)', cursor: 'pointer', color: 'var(--text-secondary)' }}>
                Cancelar
              </button>
            )}
            <button onClick={handleSave} disabled={saving || !apiKey.trim()} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 18px', fontSize: 12, fontWeight: 600, borderRadius: 7, background: 'var(--accent-primary)', border: 'none', cursor: saving ? 'wait' : 'pointer', color: '#fff', opacity: saving || !apiKey.trim() ? 0.6 : 1 }}>
              {saving ? <><RefreshCw size={12} style={{ animation: 'spin 1s linear infinite' }} />Salvando...</> : 'Salvar e conectar'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function AIProviderSection() {
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const res = await fetch('/api/ai-keys', { headers: authHeaders() })
      if (res.ok) setProviders(await res.json())
    } catch { /* silent */ }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  const anthropicOk = providers.find(p => p.provider === 'anthropic')?.is_validated

  if (loading)
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 80, borderRadius: 12 }} />)}
      </div>
    )

  return (
    <section>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <Brain size={16} color="var(--accent-primary)" />
        <h2 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>Integrações de IA</h2>
      </div>

      {!anthropicOk && (
        <div style={{ display: 'flex', gap: 10, padding: '12px 16px', marginBottom: 16, background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.2)', borderRadius: 8 }}>
          <AlertCircle size={15} color="var(--color-warning)" style={{ flexShrink: 0, marginTop: 1 }} />
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.6 }}>
            API Anthropic não configurada.{' '}
            <strong style={{ color: 'var(--text-primary)' }}>Auto-Pilot IA e Preset IA ficam desativados</strong>{' '}
            até que uma chave válida seja adicionada.
          </p>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {providers.map(p => <ProviderCard key={p.provider} status={p} onRefresh={load} />)}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
        input:-webkit-autofill {
          -webkit-box-shadow: 0 0 0 1000px var(--bg-input) inset;
          -webkit-text-fill-color: var(--text-primary);
        }
      `}</style>
    </section>
  )
}
