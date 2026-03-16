"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Bell, Send } from "lucide-react";
import { apiGet, apiPut, apiPost } from "@/lib/api";

export default function NotificationSettings() {
  const [settings, setSettings] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    apiGet("/notifications/settings").then((res) => {
      setSettings(res.settings || {});
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try { await apiPut("/notifications/settings", settings); } catch (e) { console.error(e); }
    setSaving(false);
  };

  const handleTestSlack = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      await apiPost("/notifications/test-slack", { webhook_url: settings.slack_webhook_url });
      setTestResult("success");
    } catch {
      setTestResult("error");
    }
    setTesting(false);
  };

  const update = (key: string, value: any) => setSettings((prev: any) => ({ ...prev, [key]: value }));

  if (loading) return <div className="p-8"><div className="skeleton h-96 w-full" /></div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Notifications</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Configure alert channels and event triggers.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Channels */}
      <div className="card">
        <div className="card-header"><h3>Channels</h3></div>
        <div className="card-body space-y-4">
          <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] rounded-[var(--radius-md)]">
            <div>
              <h4 className="font-semibold text-[14px]">Slack</h4>
              <p className="text-[12px] text-[var(--text-secondary)]">Receive alerts in a Slack channel</p>
            </div>
            <div className={`toggle ${settings.slack_enabled ? "active" : ""}`} onClick={() => update("slack_enabled", !settings.slack_enabled)}>
              <div className="knob" />
            </div>
          </div>
          {settings.slack_enabled && (
            <div className="space-y-2 pl-4">
              <label className="label">Webhook URL</label>
              <div className="flex gap-2">
                <input type="text" className="input text-[13px] flex-1" placeholder="https://hooks.slack.com/services/..." value={settings.slack_webhook_url || ""} onChange={(e) => update("slack_webhook_url", e.target.value)} />
                <button onClick={handleTestSlack} disabled={testing} className="btn btn-secondary px-3">
                  <Send className="w-3.5 h-3.5 mr-1" />{testing ? "Testing..." : "Test"}
                </button>
              </div>
              {testResult === "success" && <p className="text-[12px] text-[var(--color-profit)]">Test message sent successfully!</p>}
              {testResult === "error" && <p className="text-[12px] text-[var(--color-loss)]">Failed to send test message.</p>}
            </div>
          )}

          <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] rounded-[var(--radius-md)]">
            <div><h4 className="font-semibold text-[14px]">Email</h4><p className="text-[12px] text-[var(--text-secondary)]">Email alerts (coming soon)</p></div>
            <div className={`toggle ${settings.email_enabled ? "active" : ""}`} onClick={() => update("email_enabled", !settings.email_enabled)}>
              <div className="knob" />
            </div>
          </div>

          <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] rounded-[var(--radius-md)]">
            <div><h4 className="font-semibold text-[14px]">Push</h4><p className="text-[12px] text-[var(--text-secondary)]">Browser/mobile push (coming soon)</p></div>
            <div className={`toggle ${settings.push_enabled ? "active" : ""}`} onClick={() => update("push_enabled", !settings.push_enabled)}>
              <div className="knob" />
            </div>
          </div>
        </div>
      </div>

      {/* Events */}
      <div className="card">
        <div className="card-header"><h3>Event Triggers</h3></div>
        <div className="card-body space-y-3">
          {[
            { key: "notify_on_buy", label: "Buy order executed" },
            { key: "notify_on_sell", label: "Sell order / position closed" },
            { key: "notify_on_stop_loss", label: "Stop loss triggered" },
            { key: "notify_on_take_profit", label: "Take profit hit" },
            { key: "notify_on_circuit_breaker", label: "Circuit breaker activated" },
            { key: "daily_summary_enabled", label: "Daily P&L summary" },
          ].map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between py-2">
              <span className="text-[13px] text-[var(--text-primary)]">{label}</span>
              <div className={`toggle ${settings[key] ? "active" : ""}`} onClick={() => update(key, !settings[key])}>
                <div className="knob" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
