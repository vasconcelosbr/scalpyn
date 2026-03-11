import { RiskConfigForm } from "@/components/settings/RiskConfigForm";

export default function RiskSettingsPage() {
  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-[#E2E8F0]">Global Risk Configuration</h1>
        <p className="text-[#94A3B8] mt-1 text-sm">ZERO HARDCODE: All parameters dynamically control the execution engine.</p>
      </div>

      <RiskConfigForm />
    </div>
  );
}
