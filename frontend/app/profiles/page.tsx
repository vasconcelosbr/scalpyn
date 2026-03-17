"use client";

import { useEffect, useState } from "react";
import { Plus, Settings2, Trash2, Play, Copy, Layers } from "lucide-react";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { ProfileBuilder } from "@/components/profiles/ProfileBuilder";
import { ProfileCard } from "@/components/profiles/ProfileCard";

interface Profile {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  config: ProfileConfig;
  created_at: string;
  updated_at: string;
}

interface ProfileConfig {
  filters: {
    logic: string;
    conditions: Condition[];
  };
  scoring: {
    weights: {
      liquidity: number;
      market_structure: number;
      momentum: number;
      signal: number;
    };
  };
  signals: {
    logic: string;
    conditions: Condition[];
  };
}

interface Condition {
  field: string;
  operator: string;
  value: any;
  required?: boolean;
}

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [showBuilder, setShowBuilder] = useState(false);
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null);
  const [testResults, setTestResults] = useState<any>(null);

  const fetchProfiles = async () => {
    setLoading(true);
    try {
      const data = await apiGet("/profiles");
      setProfiles(data.profiles || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchProfiles();
  }, []);

  const handleCreate = () => {
    setEditingProfile(null);
    setShowBuilder(true);
  };

  const handleEdit = (profile: Profile) => {
    setEditingProfile(profile);
    setShowBuilder(true);
  };

  const handleDelete = async (profileId: string) => {
    if (!confirm("Are you sure you want to delete this profile?")) return;
    try {
      await apiDelete(`/profiles/${profileId}`);
      fetchProfiles();
    } catch (e: any) {
      alert(`Failed to delete: ${e.message}`);
    }
  };

  const handleTest = async (profileId: string) => {
    try {
      const result = await apiPost(`/profiles/${profileId}/test`);
      setTestResults(result);
    } catch (e: any) {
      alert(`Test failed: ${e.message}`);
    }
  };

  const handleSave = async (profileData: any) => {
    try {
      if (editingProfile) {
        await apiPost(`/profiles/${editingProfile.id}`, profileData);
      } else {
        await apiPost("/profiles", profileData);
      }
      setShowBuilder(false);
      setEditingProfile(null);
      fetchProfiles();
    } catch (e: any) {
      alert(`Failed to save: ${e.message}`);
    }
  };

  const handleDuplicate = async (profile: Profile) => {
    try {
      await apiPost("/profiles", {
        name: `${profile.name} (Copy)`,
        description: profile.description,
        config: profile.config,
      });
      fetchProfiles();
    } catch (e: any) {
      alert(`Failed to duplicate: ${e.message}`);
    }
  };

  if (showBuilder) {
    return (
      <ProfileBuilder
        profile={editingProfile}
        onSave={handleSave}
        onCancel={() => {
          setShowBuilder(false);
          setEditingProfile(null);
        }}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            Strategy Profiles
          </h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Define custom filters, scoring weights, and signal conditions.
          </p>
        </div>
        <button
          className="btn btn-primary"
          onClick={handleCreate}
          data-testid="create-profile-btn"
        >
          <Plus className="w-4 h-4 mr-2" />
          Create Profile
        </button>
      </div>

      {/* Test Results Modal */}
      {testResults && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[var(--bg-card)] rounded-lg p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto">
            <h3 className="text-lg font-bold mb-4 text-[var(--text-primary)]">
              Test Results: {testResults.profile_name}
            </h3>
            
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="bg-[var(--bg-secondary)] p-4 rounded-lg">
                <div className="text-[var(--text-tertiary)] text-xs mb-1">Assets Analyzed</div>
                <div className="text-2xl font-bold text-[var(--text-primary)]">
                  {testResults.summary?.total_assets || 0}
                </div>
              </div>
              <div className="bg-[var(--bg-secondary)] p-4 rounded-lg">
                <div className="text-[var(--text-tertiary)] text-xs mb-1">Passed Filters</div>
                <div className="text-2xl font-bold text-[var(--color-profit)]">
                  {testResults.summary?.after_filter || 0}
                </div>
              </div>
              <div className="bg-[var(--bg-secondary)] p-4 rounded-lg">
                <div className="text-[var(--text-tertiary)] text-xs mb-1">Filter Rate</div>
                <div className="text-2xl font-bold text-[var(--text-primary)]">
                  {testResults.summary?.filter_rate || "0%"}
                </div>
              </div>
              <div className="bg-[var(--bg-secondary)] p-4 rounded-lg">
                <div className="text-[var(--text-tertiary)] text-xs mb-1">Signals Triggered</div>
                <div className="text-2xl font-bold text-[var(--accent-primary)]">
                  {testResults.summary?.signals_triggered || 0}
                </div>
              </div>
            </div>

            <div className="mb-4">
              <h4 className="font-semibold mb-2 text-[var(--text-primary)]">Score Distribution</h4>
              <div className="flex gap-2">
                <span className="badge bullish">Strong Buy: {testResults.score_distribution?.strong_buy || 0}</span>
                <span className="badge range">Buy: {testResults.score_distribution?.buy || 0}</span>
                <span className="badge">Neutral: {testResults.score_distribution?.neutral || 0}</span>
                <span className="badge bearish">Avoid: {testResults.score_distribution?.avoid || 0}</span>
              </div>
            </div>

            <button
              className="btn btn-secondary w-full mt-4"
              onClick={() => setTestResults(null)}
            >
              Close
            </button>
          </div>
        </div>
      )}

      {/* Profiles Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-48 rounded-[var(--radius-lg)]" />
          ))}
        </div>
      ) : profiles.length === 0 ? (
        <div className="card border-dashed border-2 border-[var(--border-subtle)] bg-transparent">
          <div className="card-body text-center py-16">
            <Layers className="w-12 h-12 text-[var(--text-tertiary)] opacity-30 mx-auto mb-4" />
            <h3 className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">
              No Profiles Yet
            </h3>
            <p className="text-[var(--text-secondary)] text-[13px] max-w-sm mx-auto mb-6">
              Profiles define your trading strategy: filters, scoring weights, and signal conditions.
            </p>
            <button className="btn btn-primary" onClick={handleCreate}>
              <Plus className="w-4 h-4 mr-2" />
              Create First Profile
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {profiles.map((profile) => (
            <ProfileCard
              key={profile.id}
              profile={profile}
              onEdit={() => handleEdit(profile)}
              onDelete={() => handleDelete(profile.id)}
              onTest={() => handleTest(profile.id)}
              onDuplicate={() => handleDuplicate(profile)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
