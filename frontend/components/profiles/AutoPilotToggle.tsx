'use client'

import { useState } from 'react'
import { Brain, ChevronDown, ChevronUp, Clock } from 'lucide-react'

interface Props {
  profileId: string
  enabled:   boolean
  lastRun?:  string | null
  onToggle?: (enabled: boolean) => void
}

function getAuthHeaders(): HeadersInit {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null
  return token
    ? { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }
    : { 'Content-Type': 'application/json' }
}

export default function AutoPilotToggle({ profileId, enabled, lastRun, onToggle }: Props) {
  const [loading,   setLoading]   = useState(false)
  const [isEnabled, setIsEnabled] = useState(enabled)
  const [expanded,  setExpanded]  = useState(false)

  const handleToggle = async (value: boolean) => {
    setLoading(true)
    try {
      const res = await fetch(`/api/profiles/${profileId}/auto-pilot`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({ enabled: value }),
      })
      if (res.ok) { setIsEnabled(value); onToggle?.(value) }
    } finally {
      setLoading(false)
    }
  }

  const handleTrigger = async () => {
    setLoading(true)
    try {
      await fetch(`/api/profiles/${profileId}/auto-pilot/trigger`, { 
        method: 'POST',
        headers: getAuthHeaders(),
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      background: isEnabled ? 'rgba(52,211,153,0.06)' : 'var(--bg-elevated)',
      border: `1px solid ${isEnabled ? 'rgba(52,211,153,0.2)' : 'var(--border-default)'}`,
      borderRadius: 8, overflow: 'hidden', transition: 'all 200ms',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px' }}>
        <Brain size={13} color={isEnabled ? '#34D399' : 'var(--text-tertiary)'} />
        <span style={{ fontSize: 11, fontWeight: 600, flex: 1, color: isEnabled ? '#34D399' : 'var(--text-secondary)' }}>
          Auto-Pilot IA
        </span>
        {lastRun && (
          <span style={{ fontSize: 10, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            {new Date(lastRun).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
        <button onClick={() => setExpanded(!expanded)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, display: 'flex' }}>
          {expanded ? <ChevronUp size={12} color="var(--text-tertiary)" /> : <ChevronDown size={12} color="var(--text-tertiary)" />}
        </button>
        <button
          onClick={() => handleToggle(!isEnabled)}
          disabled={loading}
          style={{ position: 'relative', width: 34, height: 19, borderRadius: 10, border: 'none', cursor: loading ? 'wait' : 'pointer', background: isEnabled ? '#34D399' : 'rgba(255,255,255,0.1)', transition: 'background 200ms', flexShrink: 0 }}
        >
          <span style={{ position: 'absolute', width: 13, height: 13, borderRadius: '50%', background: '#fff', top: 3, left: isEnabled ? 18 : 3, transition: 'left 200ms' }} />
        </button>
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.04)', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Clock size={11} color="var(--text-tertiary)" />
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Janelas: 00:30 · 13:00 UTC</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={handleTrigger}
              disabled={loading || !isEnabled}
              style={{ flex: 1, fontSize: 11, fontWeight: 600, padding: '6px 8px', borderRadius: 6, border: 'none', background: isEnabled ? 'rgba(52,211,153,0.12)' : 'rgba(255,255,255,0.04)', color: isEnabled ? '#34D399' : 'var(--text-tertiary)', cursor: !isEnabled ? 'not-allowed' : 'pointer' }}
            >
              Analisar agora
            </button>
            <a
              href={`/profiles/${profileId}?tab=auto_pilot`}
              style={{ flex: 1, fontSize: 11, fontWeight: 600, textDecoration: 'none', padding: '6px 8px', borderRadius: 6, textAlign: 'center', background: 'var(--accent-primary-muted)', border: '1px solid var(--accent-primary-border)', color: 'var(--accent-primary)' }}
            >
              Configurar
            </a>
          </div>
        </div>
      )}
    </div>
  )
}
