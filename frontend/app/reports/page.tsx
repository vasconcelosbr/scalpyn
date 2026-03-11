"use client";

import { FileText, Download } from "lucide-react";

export default function ReportsPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Reporting & Analytics</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Generate historical performance tear sheets and tax documents.</p>
        </div>
        <button className="btn btn-primary" disabled>
          <Download className="w-4 h-4 mr-2" />
          Export CSV
        </button>
      </div>

      <div className="card">
        <div className="card-body flex flex-col items-center justify-center py-20 text-center">
          <FileText className="w-12 h-12 text-[var(--text-tertiary)] mb-4 opacity-50" />
          <h3 className="text-lg font-bold text-[var(--text-primary)] mb-2">Module Under Construction</h3>
          <p className="text-[var(--text-secondary)] max-w-md">
            The Historical Analytics & Reports generator module is currently being wired to the backend TimescaleDB cluster.
          </p>
        </div>
      </div>
    </div>
  );
}
