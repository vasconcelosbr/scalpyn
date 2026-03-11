"use client";

import { useState } from "react";
import { Save, RefreshCw } from "lucide-react";

export function RiskConfigForm() {
  const [takeProfit, setTakeProfit] = useState(1.5);
  const [stopLoss, setStopLoss] = useState(1.5);
  const [maxPositions, setMaxPositions] = useState(5);
  const [dailyLoss, setDailyLoss] = useState(3.0);
  const [autoKill, setAutoKill] = useState(true);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    await new Promise(r => setTimeout(r, 800));
    setSaving(false);
  };

  return (
    <div className="card">
      <div className="card-header pb-4 border-b border-[var(--border-subtle)]">
        <div>
          <h2 className="text-[18px] font-bold tracking-tight">Risk Management Parameters</h2>
          <p className="text-[13px] text-[var(--text-secondary)] mt-1">Adjust systemic risk constraints. These act globally across all trading modules.</p>
        </div>
        <button 
          onClick={handleSave}
          disabled={saving}
          className="btn btn-primary"
        >
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? 'Saving...' : 'Save Configuration'}
        </button>
      </div>

      <div className="card-body p-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-8">
          
          {/* Sliders and Toggles (Left Column) */}
          <div className="md:col-span-3 space-y-8">
            
            <div className="flex items-center justify-between p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
              <div>
                <h4 className="font-semibold text-[14px] text-[var(--text-primary)]">Circuit Breaker (Auto-Kill)</h4>
                <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">Halt all execution logic immediately if Daily Loss Limit is breached.</p>
              </div>
              <div 
                className={`toggle ${autoKill ? 'active' : ''}`}
                onClick={() => setAutoKill(!autoKill)}
              >
                <div className="knob" />
              </div>
            </div>

            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <label className="text-[13px] font-semibold text-[var(--text-primary)]">Default Take Profit</label>
                <div className="input-group w-[100px]">
                  <input 
                    type="number" 
                    value={takeProfit} 
                    onChange={e => setTakeProfit(parseFloat(e.target.value))}
                    className="input numeric"
                    step="0.1"
                  />
                  <span className="suffix">%</span>
                </div>
              </div>
              <div className="slider-container">
                <input 
                  type="range" min="0.1" max="10.0" step="0.1" 
                  value={takeProfit} 
                  onChange={e => setTakeProfit(parseFloat(e.target.value))}
                  className="slider"
                  style={{ '--progress': `${(takeProfit / 10.0) * 100}%` } as any}
                />
              </div>
              <p className="caption">Target profit threshold for closing spot positions immediately.</p>
            </div>

            <div className="space-y-3 pt-2 border-t border-[var(--border-subtle)]">
              <div className="flex justify-between items-center">
                <label className="text-[13px] font-semibold text-[var(--text-primary)]">Dynamic Stop Loss (ATR)</label>
                <div className="input-group w-[100px]">
                  <input 
                    type="number" 
                    value={stopLoss} 
                    onChange={e => setStopLoss(parseFloat(e.target.value))}
                    className="input numeric"
                    step="0.1"
                  />
                  <span className="suffix">x</span>
                </div>
              </div>
              <div className="slider-container">
                <input 
                  type="range" min="0.5" max="5.0" step="0.1" 
                  value={stopLoss} 
                  onChange={e => setStopLoss(parseFloat(e.target.value))}
                  className="slider"
                  style={{ '--progress': `${((stopLoss - 0.5) / 4.5) * 100}%` } as any}
                />
              </div>
              <p className="caption">Volatility-adjusted stop trailing loss utilizing the Average True Range.</p>
            </div>

            <div className="space-y-3 pt-2 border-t border-[var(--border-subtle)]">
              <div className="flex justify-between items-center">
                <label className="text-[13px] font-semibold text-[var(--text-primary)]">Max Concurrent Positions</label>
                <div className="input-group w-[100px]">
                  <input 
                    type="number" 
                    value={maxPositions} 
                    onChange={e => setMaxPositions(parseInt(e.target.value))}
                    className="input numeric"
                    step="1"
                  />
                  <span className="suffix">POS</span>
                </div>
              </div>
              <div className="slider-container">
                <input 
                  type="range" min="1" max="20" step="1" 
                  value={maxPositions} 
                  onChange={e => setMaxPositions(parseInt(e.target.value))}
                  className="slider"
                  style={{ '--progress': `${(maxPositions / 20) * 100}%` } as any}
                />
              </div>
            </div>

            <div className="space-y-3 pt-2 border-t border-[var(--border-subtle)]">
              <div className="flex justify-between items-center">
                <label className="text-[13px] font-semibold text-[var(--color-loss)]">Daily Loss Limit (Global)</label>
                <div className="input-group w-[100px]">
                  <input 
                    type="number" 
                    value={dailyLoss} 
                    onChange={e => setDailyLoss(parseFloat(e.target.value))}
                    className="input numeric text-[var(--color-loss)] border-[var(--color-loss-muted)] focus:border-[var(--color-loss)]"
                    step="0.5"
                  />
                  <span className="suffix">%</span>
                </div>
              </div>
              <div className="slider-container">
                <input 
                  type="range" min="0.5" max="15.0" step="0.5" 
                  value={dailyLoss} 
                  onChange={e => setDailyLoss(parseFloat(e.target.value))}
                  className="slider"
                  style={{ 
                    '--progress': `${(dailyLoss / 15.0) * 100}%`,
                    '--accent-primary': 'var(--color-loss)',
                    '--shadow-glow-accent': 'var(--shadow-glow-loss)'
                  } as any}
                />
              </div>
            </div>

          </div>

          {/* Real-time Preview Panel (Right Column) */}
          <div className="md:col-span-2 space-y-4">
            <div className="bg-[var(--bg-elevated)] border border-[var(--border-strong)] rounded-[var(--radius-lg)] p-5 sticky top-24">
              <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-4 pb-3 border-b border-[var(--border-subtle)]">
                Risk Exposure Preview
              </h3>
              
              <div className="space-y-4">
                <div className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                  <div className="flex flex-col">
                    <span className="text-[12px] font-medium text-[var(--text-secondary)]">Assumed Capital</span>
                  </div>
                  <span className="data-value text-[16px] text-[var(--text-primary)]">$100,000</span>
                </div>

                <div className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                  <div className="flex flex-col">
                    <span className="text-[12px] font-medium text-[var(--text-secondary)]">Max Risk per Trade</span>
                    <span className="caption mt-0.5">Absolute loss at Stop Loss</span>
                  </div>
                  <span className="data-value text-[15px] text-[var(--text-primary)]">
                    ${(100000 * (1.0 / maxPositions) * (stopLoss * 0.01)).toFixed(2)}
                  </span>
                </div>
                
                <div className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                  <div className="flex flex-col">
                    <span className="text-[12px] font-medium text-[var(--text-secondary)]">Max Capital Deployed</span>
                    <span className="caption mt-0.5">{maxPositions} concurrent pairs x {(100/maxPositions).toFixed(1)}%</span>
                  </div>
                  <span className="data-value text-[15px] text-[var(--text-primary)]">$100,000</span>
                </div>

                <div className="flex justify-between items-end pt-2">
                  <div className="flex flex-col border-l-2 border-[var(--color-loss)] pl-3">
                    <span className="text-[12px] font-bold text-[var(--color-loss)]">Circuit Breaker Event</span>
                    <span className="caption mt-0.5">HALT TRADING AT</span>
                  </div>
                  <span className="data-value text-[18px] font-bold text-[var(--color-loss)]">
                    -${(100000 * (dailyLoss / 100)).toFixed(2)}
                  </span>
                </div>
              </div>
            </div>
            <div className="flex gap-2 w-full justify-end">
              <button className="btn btn-secondary">Reset Defaults</button>
            </div>
          </div>
          
        </div>
      </div>
    </div>
  );
}
