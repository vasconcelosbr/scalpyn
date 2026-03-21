'use client';

import { useCallback, useId, useRef } from 'react';

interface SliderWithValueProps {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  prefix?: string;
  hint?: string;
  disabled?: boolean;
  decimals?: number;
}

export function SliderWithValue({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  prefix,
  hint,
  disabled = false,
  decimals = 0,
}: SliderWithValueProps) {
  const sliderId = useId();
  const numericInputRef = useRef<HTMLInputElement>(null);

  // Clamp + round to avoid floating point drift
  const clamp = useCallback(
    (raw: number) => {
      const factor = Math.pow(10, decimals);
      const clamped = Math.min(max, Math.max(min, raw));
      return Math.round(clamped * factor) / factor;
    },
    [min, max, decimals]
  );

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(clamp(parseFloat(e.target.value)));
  };

  const handleNumericChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = parseFloat(e.target.value);
    if (!isNaN(raw)) {
      onChange(clamp(raw));
    }
  };

  const handleNumericBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    const raw = parseFloat(e.target.value);
    if (isNaN(raw)) {
      // Reset to current value if user cleared the field
      e.target.value = value.toFixed(decimals);
    } else {
      onChange(clamp(raw));
    }
  };

  // Progress percentage for CSS gradient fill trick
  const progress = ((value - min) / (max - min)) * 100;

  const displayValue = value.toFixed(decimals);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        opacity: disabled ? 0.5 : 1,
        pointerEvents: disabled ? 'none' : undefined,
      }}
    >
      {/* Label row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <label
          htmlFor={sliderId}
          style={{
            fontSize: '13px',
            fontWeight: 500,
            color: 'var(--text-primary)',
            cursor: disabled ? 'not-allowed' : 'default',
            userSelect: 'none',
          }}
        >
          {label}
        </label>

        {/* Current value badge */}
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '13px',
            fontWeight: 600,
            letterSpacing: '-0.02em',
            color: 'var(--accent-primary)',
            background: 'var(--accent-primary-muted)',
            border: '1px solid var(--accent-primary-border)',
            borderRadius: 'var(--radius-sm)',
            padding: '2px 8px',
            whiteSpace: 'nowrap',
          }}
        >
          {prefix && <span style={{ opacity: 0.7 }}>{prefix}</span>}
          {displayValue}
          {unit && (
            <span style={{ opacity: 0.7, marginLeft: '2px' }}>{unit}</span>
          )}
        </span>
      </div>

      {/* Slider + numeric input row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {/* Slider */}
        <div style={{ flex: 1, position: 'relative', display: 'flex', alignItems: 'center' }}>
          <style>{`
            #${CSS.escape(sliderId)} {
              -webkit-appearance: none;
              appearance: none;
              width: 100%;
              height: 4px;
              border-radius: 2px;
              outline: none;
              background: linear-gradient(
                to right,
                var(--accent-primary) ${progress}%,
                var(--bg-hover) ${progress}%
              );
              cursor: pointer;
            }
            #${CSS.escape(sliderId)}::-webkit-slider-thumb {
              -webkit-appearance: none;
              appearance: none;
              width: 16px;
              height: 16px;
              border-radius: 50%;
              background: var(--accent-primary);
              border: 2px solid var(--bg-surface);
              box-shadow: 0 0 0 0 rgba(79, 123, 247, 0.4);
              cursor: pointer;
              transition: transform var(--transition-fast), box-shadow var(--transition-fast);
            }
            #${CSS.escape(sliderId)}::-webkit-slider-thumb:hover {
              transform: scale(1.25);
              box-shadow: 0 0 0 4px rgba(79, 123, 247, 0.2);
            }
            #${CSS.escape(sliderId)}:focus-visible::-webkit-slider-thumb {
              box-shadow: 0 0 0 4px rgba(79, 123, 247, 0.35);
            }
            #${CSS.escape(sliderId)}::-moz-range-thumb {
              width: 14px;
              height: 14px;
              border-radius: 50%;
              background: var(--accent-primary);
              border: 2px solid var(--bg-surface);
              cursor: pointer;
              transition: transform var(--transition-fast);
            }
            #${CSS.escape(sliderId)}::-moz-range-thumb:hover {
              transform: scale(1.25);
            }
            #${CSS.escape(sliderId)}::-moz-range-track {
              height: 4px;
              border-radius: 2px;
              background: var(--bg-hover);
            }
            #${CSS.escape(sliderId)}::-moz-range-progress {
              height: 4px;
              border-radius: 2px;
              background: var(--accent-primary);
            }
          `}</style>
          <input
            id={sliderId}
            type="range"
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={handleSliderChange}
            disabled={disabled}
          />
        </div>

        {/* Direct numeric input */}
        <input
          ref={numericInputRef}
          type="number"
          min={min}
          max={max}
          step={step}
          defaultValue={displayValue}
          key={displayValue} // force re-render when value changes externally
          onChange={handleNumericChange}
          onBlur={handleNumericBlur}
          disabled={disabled}
          aria-label={`${label} value`}
          style={{
            width: '72px',
            height: '32px',
            padding: '0 8px',
            background: 'var(--bg-input)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-sm)',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            fontWeight: 500,
            color: 'var(--text-primary)',
            textAlign: 'right',
            outline: 'none',
            transition: 'border-color var(--transition-fast), box-shadow var(--transition-fast)',
            // Remove native number spinners
            MozAppearance: 'textfield',
          } as React.CSSProperties}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent-primary)';
            e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-primary-muted)';
          }}
          onBlurCapture={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-default)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        />

        {/* Unit label beside input */}
        {unit && (
          <span
            style={{
              fontSize: '12px',
              fontWeight: 500,
              color: 'var(--text-tertiary)',
              whiteSpace: 'nowrap',
              minWidth: '16px',
              marginLeft: '-8px',
            }}
          >
            {unit}
          </span>
        )}
      </div>

      {/* Hint text */}
      {hint && (
        <p
          style={{
            fontSize: '11px',
            color: 'var(--text-tertiary)',
            lineHeight: 1.5,
            margin: 0,
          }}
        >
          {hint}
        </p>
      )}
    </div>
  );
}
