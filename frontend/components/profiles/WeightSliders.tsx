"use client";

interface WeightSlidersProps {
  weights: {
    liquidity: number;
    market_structure: number;
    momentum: number;
    signal: number;
  };
  onChange: (weights: any) => void;
}

export function WeightSliders({ weights, onChange }: WeightSlidersProps) {
  const total =
    weights.liquidity +
    weights.market_structure +
    weights.momentum +
    weights.signal;

  const updateWeight = (key: string, value: number) => {
    onChange({ ...weights, [key]: value });
  };

  const sliders = [
    {
      key: "liquidity",
      label: "Liquidity",
      description: "Volume, spread, order book depth",
      color: "bg-blue-500",
    },
    {
      key: "market_structure",
      label: "Market Structure",
      description: "ADX, EMA alignment, ATR, trend strength",
      color: "bg-purple-500",
    },
    {
      key: "momentum",
      label: "Momentum",
      description: "RSI, MACD, Stochastic, Z-Score",
      color: "bg-green-500",
    },
    {
      key: "signal",
      label: "Signal",
      description: "Entry conditions, volume delta, funding",
      color: "bg-orange-500",
    },
  ];

  return (
    <div className="space-y-6">
      {/* Total indicator */}
      <div className="flex items-center justify-between p-3 bg-[var(--bg-secondary)] rounded-lg">
        <span className="text-[var(--text-secondary)] text-sm">Total Weight</span>
        <span
          className={`font-mono font-bold ${
            total === 100
              ? "text-[var(--color-profit)]"
              : "text-[var(--color-loss)]"
          }`}
        >
          {total}%
        </span>
      </div>

      {/* Weight sliders */}
      {sliders.map((slider) => (
        <div key={slider.key} className="space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <label className="font-medium text-[var(--text-primary)] text-sm">
                {slider.label}
              </label>
              <p className="text-[11px] text-[var(--text-tertiary)]">
                {slider.description}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="0"
                max="100"
                value={weights[slider.key as keyof typeof weights]}
                onChange={(e) =>
                  updateWeight(slider.key, parseInt(e.target.value) || 0)
                }
                className="input w-16 text-center text-sm"
                data-testid={`weight-input-${slider.key}`}
              />
              <span className="text-[var(--text-tertiary)] text-sm">%</span>
            </div>
          </div>

          <div className="relative">
            <input
              type="range"
              min="0"
              max="100"
              value={weights[slider.key as keyof typeof weights]}
              onChange={(e) =>
                updateWeight(slider.key, parseInt(e.target.value) || 0)
              }
              className="w-full h-2 rounded-lg appearance-none cursor-pointer"
              style={{
                background: `linear-gradient(to right, ${
                  slider.key === "liquidity"
                    ? "#3b82f6"
                    : slider.key === "market_structure"
                    ? "#a855f7"
                    : slider.key === "momentum"
                    ? "#22c55e"
                    : "#f97316"
                } ${weights[slider.key as keyof typeof weights]}%, var(--bg-secondary) ${
                  weights[slider.key as keyof typeof weights]
                }%)`,
              }}
              data-testid={`weight-slider-${slider.key}`}
            />
          </div>
        </div>
      ))}

      {/* Visual weight distribution */}
      <div className="mt-6">
        <label className="text-[var(--text-tertiary)] text-xs mb-2 block">
          Weight Distribution
        </label>
        <div className="h-4 rounded-full overflow-hidden flex">
          <div
            className="bg-blue-500 transition-all"
            style={{ width: `${(weights.liquidity / total) * 100 || 0}%` }}
          />
          <div
            className="bg-purple-500 transition-all"
            style={{ width: `${(weights.market_structure / total) * 100 || 0}%` }}
          />
          <div
            className="bg-green-500 transition-all"
            style={{ width: `${(weights.momentum / total) * 100 || 0}%` }}
          />
          <div
            className="bg-orange-500 transition-all"
            style={{ width: `${(weights.signal / total) * 100 || 0}%` }}
          />
        </div>
        <div className="flex justify-between mt-1 text-[10px] text-[var(--text-tertiary)]">
          <span>L: {weights.liquidity}%</span>
          <span>MS: {weights.market_structure}%</span>
          <span>Mo: {weights.momentum}%</span>
          <span>S: {weights.signal}%</span>
        </div>
      </div>

      {/* Presets */}
      <div className="pt-4 border-t border-[var(--border-default)]">
        <label className="text-[var(--text-tertiary)] text-xs mb-2 block">
          Quick Presets
        </label>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn btn-secondary text-xs px-3 py-1"
            onClick={() =>
              onChange({
                liquidity: 25,
                market_structure: 25,
                momentum: 25,
                signal: 25,
              })
            }
          >
            Balanced
          </button>
          <button
            className="btn btn-secondary text-xs px-3 py-1"
            onClick={() =>
              onChange({
                liquidity: 30,
                market_structure: 15,
                momentum: 40,
                signal: 15,
              })
            }
          >
            Momentum Focus
          </button>
          <button
            className="btn btn-secondary text-xs px-3 py-1"
            onClick={() =>
              onChange({
                liquidity: 20,
                market_structure: 35,
                momentum: 30,
                signal: 15,
              })
            }
          >
            Trend Focus
          </button>
          <button
            className="btn btn-secondary text-xs px-3 py-1"
            onClick={() =>
              onChange({
                liquidity: 40,
                market_structure: 20,
                momentum: 25,
                signal: 15,
              })
            }
          >
            Liquidity Focus
          </button>
        </div>
      </div>
    </div>
  );
}
