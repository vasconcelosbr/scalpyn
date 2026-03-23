'use client'

import { useState } from 'react'
import { Sparkles, TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'

interface PresetIAResult {
  regime:           string
  macro_risk:       string
  analysis_summary: string
  applied_configs:  string[]
  executed_at:      string
}

interface Props {
  profileId:   string
  profileRole: string | null | undefined
  size?:       'sm' | 'md'
  onSuccess?:  (result: PresetIAResult) => void
}

const REGIME_ICON: Record<string, any> = {
  BULL:            TrendingUp,
  BEAR:            TrendingDown,
  SIDEWAYS:        Minus,
  HIGH_VOLATILITY: AlertTriangle,
}

const REGIME_COLOR: Record<string, string> = {
  BULL:            '#34D399',
  BEAR:            '#F87171',
  SIDEWAYS:        '#FBBF24',
  HIGH_VOLATILITY: '#F97316',
}

const RISK_COLOR: Record<string, string> = {
  LOW:     '#34D399',
  MEDIUM:  '#FBBF24',
  HIGH:    '#F97316',
  EXTREME: '#F87171',
}

export default function PresetIAButton({ profileId, profileRole, size = 'md', onSuccess }: Props) {
  const [loading,    setLoading]    = useState(false)
  const [result,     setResult]     = useState<PresetIAResult | null>(null)
  const [error,      setError]      = useState<string | null>(null)

  const handleRun = async () => {
    if (!profileRole) { setError('Configure o papel (role) do profile antes de usar o Preset IA.'); return }
    setLoading(true); setError(null); setResult(null)
    try {
      const res  = await fetch(`/api/profiles/${profileId}/preset-ia`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) { setError(data.detail || 'Erro ao executar Preset IA.'); return }
      setResult(data)
      onSuccess?.(data)
    } catch {
      setError('Erro de conexão.')
    } finally {
      setLoading(false)
    }
  }

  const isSm = size === 'sm'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <button
        onClick={handleRun}
        disabled={loading || !profileRole}
        title={!profileRole ? 'Configure o papel do profile primeiro' : 'Configurar todos os layers com IA'}
        style={{
          display: 'flex', alignItems: 'center', gap: isSm ? 5 : 6,
          padding: isSm ? '6px 10px' : '8px 14px',
          fontSize: isSm ? 11 : 12, fontWeight: 700,
          borderRadius: isSm ? 7 : 8, border: 'none',
          cursor: loading || !profileRole ? 'not-allowed' : 'pointer',
          background: !profileRole ? 'rgba(255,255,255,0.04)' : loading
            ? 'linear-gradient(135deg,#6B3FA0,#4F7BF7)'
            : 'linear-gradient(135deg,#8B5CF6,#4F7BF7)',
          color: !profileRole ? 'var(--text-tertiary)' : '#fff',
          opacity: !profileRole ? 0.5 : 1,
          boxShadow: !profileRole || loading ? 'none' : '0 2px 12px rgba(139,92,246,0.3)',
        }}
      >
        <Sparkles size={isSm ? 11 : 13} style={loading ? { animation: 'spin 1s linear infinite' } : {}} />
        {loading ? 'Analisando...' : '✨ Preset IA'}
      </button>

      {error && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 7, fontSize: 11, padding: '8px 10px', borderRadius: 7, lineHeight: 1.5, background: 'var(--color-loss-muted)', border: '1px solid var(--color-loss-border)', color: 'var(--color-loss)' }}>
          <XCircle size={12} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div style={{ background: 'var(--bg-elevated)', border: '1px solid rgba(139,92,246,0.2)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', background: 'linear-gradient(135deg,rgba(139,92,246,0.08),rgba(79,123,247,0.06))', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
            <CheckCircle2 size={14} color="#8B5CF6" />
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', flex: 1 }}>Preset IA aplicado</span>
            {(() => {
              const Icon  = REGIME_ICON[result.regime] || Minus
              const color = REGIME_COLOR[result.regime] || '#8B92A5'
              return (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 20, background: `${color}15`, border: `1px solid ${color}30`, fontSize: 10, fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>
                  <Icon size={9} />{result.regime}
                </span>
              )
            })()}
            <span style={{ fontSize: 10, fontWeight: 700, color: RISK_COLOR[result.macro_risk] || '#8B92A5', fontFamily: 'var(--font-mono)' }}>
              {result.macro_risk}
            </span>
          </div>
          <div style={{ padding: '10px 12px' }}>
            <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.6 }}>{result.analysis_summary}</p>
            {result.applied_configs?.length > 0 && (
              <div style={{ display: 'flex', gap: 5, marginTop: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Configurado:</span>
                {result.applied_configs.map(cfg => (
                  <span key={cfg} style={{ fontSize: 10, padding: '2px 6px', borderRadius: 20, fontWeight: 600, background: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.2)', color: '#A78BFA' }}>{cfg}</span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}
