"use client";

import { Bell } from "lucide-react";

export default function NotificationSettings() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Notifications</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Configure Telegram, Email and Webhook alerts.</p>
        </div>
      </div>

      <div className="card">
        <div className="card-body flex flex-col items-center justify-center py-20 text-center">
          <Bell className="w-12 h-12 text-[var(--text-tertiary)] mb-4 opacity-50" />
          <h3 className="text-lg font-bold text-[var(--text-primary)] mb-2">Module Under Construction</h3>
          <p className="text-[var(--text-secondary)] max-w-md">
            The Notification bindings module is currently being wired to the backend API.
          </p>
        </div>
      </div>
    </div>
  );
}
