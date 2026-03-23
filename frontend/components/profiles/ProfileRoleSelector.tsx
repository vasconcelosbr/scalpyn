'use client'

import { Globe, Filter, Target, ShoppingCart, ChevronRight } from 'lucide-react'

export type ProfileRole =
  | 'universe_filter'
  | 'primary_filter'
  | 'score_engine'
  | 'acquisition_queue'

const ROLES = [
  {
    value:       'universe_filter' as ProfileRole,
    label:       'Filtro de Universo',
    short:       'POOL',
    icon:        Globe,
    color:       '#8B92A5',
    bg:          'rgba(139,146,165,0.08)',
    border:      'rgba(139,146,165,0.2)',
    description: 'Filtros básicos da corretora. Define quais ativos entram no universo analisado.',
    configures:  ['Volume mínimo', 'Quote currency', 'Listing age'],
    order:       0,
  },
  {
    value:       'primary_filter' as ProfileRole,
    label:       'Filtro Primário',
    short:       'L1',
    icon:        Filter,
    color:       '#4F7BF7',
    bg:          'rgba(79,123,247,0.08)',
    border:      'rgba(79,123,247,0.2)',
    description: 'Análise primária de qualidade. Elimina ativos sem condições adequadas de trading.',
    configures:  ['ATR mínimo', 'Spread máximo', 'Volume relativo', 'ADX'],
    order:       1,
  },
  {
    value:       'score_engine' as ProfileRole,
    label:       'Score Engine',
    short:       'L2',
    icon:        Target,
    color:       '#FBBF24',
    bg:          'rgba(251,191,36,0.08)',
    border:      'rgba(251,191,36,0.2)',
    description: 'Score refinado 0-100. Define pesos e regras que ranqueiam as oportunidades.',
    configures:  ['Weights', 'Scoring rules', 'Thresholds'],
    order:       2,
  },
  {
    value:       'acquisition_queue' as ProfileRole,
    label:       'Fila de Execução',
    short:       'L3',
    icon:        ShoppingCart,
    color:       '#34D399',
    bg:          'rgba(52,211,153,0.08)',
    border:      'rgba(52,211,153,0.2)',
    description: 'Entry triggers e blocos de veto. Lista final de criptos elegíveis para compra.',
    configures:  ['Entry triggers', 'Hard blocks', 'Risk params'],
    order:       3,
  },
]

export function RoleBadge({ role }: { role: ProfileRole | null | undefined }) {
  if (!role) return null
  const meta = ROLES.find(r => r.value === role)
  if (!meta) return null
  const Icon = meta.icon
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '3px 8px', borderRadius: 20,
      background: meta.bg, border: `1px solid ${meta.border}`,
      fontSize: 10, fontWeight: 700,
      color: meta.color, fontFamily: 'var(--font-mono)',
    }}>
      <Icon size={9} />{meta.short}
    </span>
  )
}

interface Props {
  value:    ProfileRole | null | undefined
  onChange: (role: ProfileRole) => void
  disabled?: boolean
}

export default function ProfileRoleSelector({ value, onChange, disabled = false }: Props) {
  return (
    <div>
      <label style={{
        display: 'block', fontSize: 11, fontWeight: 600,
        color: 'var(--text-tertiary)', marginBottom: 10,
        textTransform: 'uppercase', letterSpacing: '0.05em',
      }}>
        Papel deste profile no funil
      </label>

      {/* Pipeline breadcrumb */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12,
        padding: '8px 12px', background: 'var(--bg-elevated)',
        border: '1px solid var(--border-subtle)', borderRadius: 8,
      }}>
        {ROLES.map((role, idx) => {
          const Icon = role.icon
          const isActive = value === role.value
          return (
            <div key={role.value} style={{ display: 'flex', alignItems: 'center', gap: 6, flex: idx < ROLES.length - 1 ? 1 : 0 }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 8px', borderRadius: 6,
                background: isActive ? role.bg : 'transparent',
                border: `1px solid ${isActive ? role.border : 'transparent'}`,
              }}>
                <Icon size={12} color={isActive ? role.color : 'var(--text-tertiary)'} />
                <span style={{ fontSize: 11, fontWeight: isActive ? 700 : 500, color: isActive ? role.color : 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                  {role.short}
                </span>
              </div>
              {idx < ROLES.length - 1 && <ChevronRight size={12} color="var(--text-tertiary)" style={{ flexShrink: 0 }} />}
            </div>
          )
        })}
      </div>

      {/* Role cards */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {ROLES.map(role => {
          const Icon = role.icon
          const selected = value === role.value
          return (
            <button
              key={role.value}
              onClick={() => !disabled && onChange(role.value)}
              disabled={disabled}
              style={{
                display: 'flex', flexDirection: 'column', gap: 8,
                padding: '12px 14px', borderRadius: 10,
                cursor: disabled ? 'not-allowed' : 'pointer',
                background: selected ? role.bg : 'var(--bg-elevated)',
                border: `1.5px solid ${selected ? role.color : 'var(--border-default)'}`,
                textAlign: 'left', transition: 'all 150ms',
                opacity: disabled ? 0.6 : 1,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{
                  width: 28, height: 28, borderRadius: 7, flexShrink: 0,
                  background: selected ? `${role.color}20` : 'rgba(255,255,255,0.04)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <Icon size={14} color={selected ? role.color : 'var(--text-tertiary)'} />
                </div>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: selected ? role.color : 'var(--text-primary)' }}>{role.label}</div>
                  <div style={{ fontSize: 10, fontWeight: 600, color: selected ? role.color : 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', opacity: 0.8 }}>
                    {role.short} · Stage {role.order}
                  </div>
                </div>
              </div>
              <p style={{ fontSize: 11, color: selected ? 'var(--text-primary)' : 'var(--text-tertiary)', margin: 0, lineHeight: 1.5 }}>
                {role.description}
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {role.configures.map(item => (
                  <span key={item} style={{
                    fontSize: 10, padding: '2px 6px', borderRadius: 20,
                    background: selected ? `${role.color}15` : 'rgba(255,255,255,0.04)',
                    color: selected ? role.color : 'var(--text-tertiary)',
                    border: `1px solid ${selected ? `${role.color}30` : 'var(--border-subtle)'}`,
                  }}>{item}</span>
                ))}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
