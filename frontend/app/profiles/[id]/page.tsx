'use client'

import { useState, useEffect } from 'react'
import { useParams, useSearchParams, useRouter } from 'next/navigation'
import { ArrowLeft, Save, Play, Settings, Brain, Zap, Filter, Target } from 'lucide-react'
import { apiGet, apiPut } from '@/lib/api'
import { ProfileBuilder } from '@/components/profiles/ProfileBuilder'

interface Profile {
  id: string
  name: string
  description?: string
  is_active: boolean
  profile_role?: string | null
  config?: any
  auto_pilot_enabled?: boolean
  auto_pilot_config?: any
}

export default function ProfileEditPage() {
  const params = useParams()
  const searchParams = useSearchParams()
  const router = useRouter()
  const profileId = params.id as string
  const initialTab = searchParams.get('tab') || 'config'

  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState(initialTab)
  const [autoPilotConfig, setAutoPilotConfig] = useState<any>({
    enabled: false,
    schedules: [],
    event_triggers: {},
  })

  useEffect(() => {
    loadProfile()
  }, [profileId])

  const loadProfile = async () => {
    setLoading(true)
    try {
      const data = await apiGet(`/profiles/${profileId}`)
      setProfile(data)
      if (data.auto_pilot_config) {
        setAutoPilotConfig(data.auto_pilot_config)
      }
    } catch (e) {
      console.error('Failed to load profile:', e)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async (profileData: any) => {
    setSaving(true)
    try {
      await apiPut(`/profiles/${profileId}`, profileData)
      router.push('/profiles')
    } catch (e: any) {
      alert(`Erro ao salvar: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleSaveAutoPilot = async () => {
    setSaving(true)
    try {
      await apiPut(`/profiles/${profileId}/auto-pilot`, autoPilotConfig)
      alert('Auto-Pilot configurado com sucesso!')
    } catch (e: any) {
      alert(`Erro: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 24 }}>
        <div className="skeleton" style={{ height: 40, width: 200, borderRadius: 8, marginBottom: 24 }} />
        <div className="skeleton" style={{ height: 400, borderRadius: 12 }} />
      </div>
    )
  }

  if (!profile) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <p style={{ color: 'var(--text-tertiary)' }}>Profile não encontrado.</p>
        <button onClick={() => router.push('/profiles')} style={{ marginTop: 16 }} className="btn btn-secondary">
          Voltar
        </button>
      </div>
    )
  }

  const tabs = [
    { id: 'config', label: 'Configuração', icon: Settings },
    { id: 'auto_pilot', label: 'Auto-Pilot IA', icon: Brain },
  ]

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <button
          onClick={() => router.push('/profiles')}
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-default)',
            borderRadius: 8,
            padding: 8,
            cursor: 'pointer',
            display: 'flex',
          }}
        >
          <ArrowLeft size={18} color="var(--text-secondary)" />
        </button>
        <div style={{ flex: 1 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
            {profile.name}
          </h1>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px 0 0' }}>
            Editar configurações do profile
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div style={{
        display: 'flex',
        gap: 4,
        marginBottom: 24,
        padding: 4,
        background: 'var(--bg-elevated)',
        borderRadius: 10,
        width: 'fit-content',
      }}>
        {tabs.map(tab => {
          const Icon = tab.icon
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '8px 16px',
                fontSize: 13,
                fontWeight: isActive ? 600 : 500,
                borderRadius: 8,
                border: 'none',
                cursor: 'pointer',
                background: isActive ? 'var(--accent-primary)' : 'transparent',
                color: isActive ? '#fff' : 'var(--text-secondary)',
                transition: 'all 150ms',
              }}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Tab Content */}
      {activeTab === 'config' && (
        <ProfileBuilder
          profile={profile}
          onSave={handleSave}
          onCancel={() => router.push('/profiles')}
        />
      )}

      {activeTab === 'auto_pilot' && (
        <div className="card">
          <div className="card-body" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
              <div style={{
                width: 44,
                height: 44,
                borderRadius: 12,
                background: 'rgba(52,211,153,0.1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}>
                <Brain size={22} color="#34D399" />
              </div>
              <div>
                <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
                  Auto-Pilot IA
                </h2>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '4px 0 0' }}>
                  Configure análise automática baseada em IA
                </p>
              </div>
            </div>

            {/* Enable Toggle */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '16px 20px',
              background: autoPilotConfig.enabled ? 'rgba(52,211,153,0.06)' : 'var(--bg-secondary)',
              border: `1px solid ${autoPilotConfig.enabled ? 'rgba(52,211,153,0.2)' : 'var(--border-default)'}`,
              borderRadius: 10,
              marginBottom: 20,
            }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                  Habilitar Auto-Pilot
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                  Análise automática será executada nos horários configurados
                </div>
              </div>
              <button
                onClick={() => setAutoPilotConfig({ ...autoPilotConfig, enabled: !autoPilotConfig.enabled })}
                style={{
                  position: 'relative',
                  width: 48,
                  height: 26,
                  borderRadius: 13,
                  border: 'none',
                  cursor: 'pointer',
                  background: autoPilotConfig.enabled ? '#34D399' : 'rgba(255,255,255,0.1)',
                  transition: 'background 200ms',
                }}
              >
                <span style={{
                  position: 'absolute',
                  width: 20,
                  height: 20,
                  borderRadius: '50%',
                  background: '#fff',
                  top: 3,
                  left: autoPilotConfig.enabled ? 25 : 3,
                  transition: 'left 200ms',
                }} />
              </button>
            </div>

            {/* Schedule */}
            <div style={{ marginBottom: 20 }}>
              <label style={{
                display: 'block',
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--text-tertiary)',
                marginBottom: 8,
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
              }}>
                Horários de execução (UTC)
              </label>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {['00:30', '06:00', '12:00', '18:00'].map(time => {
                  const isSelected = autoPilotConfig.schedules?.includes(time)
                  return (
                    <button
                      key={time}
                      onClick={() => {
                        const schedules = autoPilotConfig.schedules || []
                        if (isSelected) {
                          setAutoPilotConfig({
                            ...autoPilotConfig,
                            schedules: schedules.filter((t: string) => t !== time),
                          })
                        } else {
                          setAutoPilotConfig({
                            ...autoPilotConfig,
                            schedules: [...schedules, time],
                          })
                        }
                      }}
                      style={{
                        padding: '8px 16px',
                        fontSize: 13,
                        fontWeight: 600,
                        fontFamily: 'var(--font-mono)',
                        borderRadius: 8,
                        border: `1px solid ${isSelected ? '#34D399' : 'var(--border-default)'}`,
                        background: isSelected ? 'rgba(52,211,153,0.1)' : 'transparent',
                        color: isSelected ? '#34D399' : 'var(--text-secondary)',
                        cursor: 'pointer',
                      }}
                    >
                      {time}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Event Triggers */}
            <div style={{ marginBottom: 24 }}>
              <label style={{
                display: 'block',
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--text-tertiary)',
                marginBottom: 8,
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
              }}>
                Gatilhos de evento
              </label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[
                  { key: 'on_regime_change', label: 'Mudança de regime de mercado' },
                  { key: 'on_high_volatility', label: 'Alta volatilidade detectada' },
                  { key: 'on_macro_event', label: 'Evento macro relevante' },
                ].map(trigger => {
                  const isEnabled = autoPilotConfig.event_triggers?.[trigger.key]
                  return (
                    <label
                      key={trigger.key}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        padding: '12px 16px',
                        background: 'var(--bg-secondary)',
                        borderRadius: 8,
                        cursor: 'pointer',
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={isEnabled}
                        onChange={(e) => {
                          setAutoPilotConfig({
                            ...autoPilotConfig,
                            event_triggers: {
                              ...autoPilotConfig.event_triggers,
                              [trigger.key]: e.target.checked,
                            },
                          })
                        }}
                        style={{ width: 16, height: 16 }}
                      />
                      <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>
                        {trigger.label}
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>

            {/* Save Button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                onClick={handleSaveAutoPilot}
                disabled={saving}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 20px',
                  fontSize: 13,
                  fontWeight: 600,
                  borderRadius: 8,
                  border: 'none',
                  background: 'var(--accent-primary)',
                  color: '#fff',
                  cursor: saving ? 'wait' : 'pointer',
                  opacity: saving ? 0.7 : 1,
                }}
              >
                <Save size={14} />
                {saving ? 'Salvando...' : 'Salvar Auto-Pilot'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
