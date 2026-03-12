"use client";

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '/api';

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function validate(): string | null {
    if (!name.trim()) return 'Name is required';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return 'Enter a valid email address';
    if (password.length < 6) return 'Password must be at least 6 characters';
    if (password !== confirmPassword) return 'Passwords do not match';
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail ?? 'Registration failed');
      }
      router.push('/login');
    } catch (err: any) {
      setError(err.message ?? 'Registration failed');
    } finally {
      setLoading(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '10px 12px',
    background: 'var(--bg-input)',
    border: '1px solid var(--border-default)',
    borderRadius: 'var(--radius-md)',
    color: 'var(--text-primary)',
    fontSize: '14px',
    outline: 'none',
    boxSizing: 'border-box',
  };

  const labelStyle: React.CSSProperties = {
    display: 'block',
    marginBottom: '6px',
    fontSize: '13px',
    fontWeight: '500',
    color: 'var(--text-secondary)',
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-base)',
        padding: '24px',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '400px',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-xl)',
          padding: '40px',
          boxShadow: 'var(--shadow-lg)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <h1
            style={{
              fontSize: '24px',
              fontWeight: '700',
              color: 'var(--text-primary)',
              letterSpacing: '-0.02em',
            }}
          >
            Scalpyn
          </h1>
          <p style={{ marginTop: '8px', color: 'var(--text-secondary)', fontSize: '14px' }}>
            Create your account
          </p>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ marginBottom: '16px' }}>
            <label htmlFor="name" style={labelStyle}>Name</label>
            <input
              id="name"
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Your name"
              style={inputStyle}
            />
          </div>

          <div style={{ marginBottom: '16px' }}>
            <label htmlFor="email" style={labelStyle}>Email</label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              style={inputStyle}
            />
          </div>

          <div style={{ marginBottom: '16px' }}>
            <label htmlFor="password" style={labelStyle}>Password</label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Min. 6 characters"
              style={inputStyle}
            />
          </div>

          <div style={{ marginBottom: '24px' }}>
            <label htmlFor="confirmPassword" style={labelStyle}>Confirm Password</label>
            <input
              id="confirmPassword"
              type="password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Repeat password"
              style={inputStyle}
            />
          </div>

          {error && (
            <div
              style={{
                marginBottom: '16px',
                padding: '10px 12px',
                background: 'var(--color-loss-muted)',
                border: '1px solid var(--color-loss-border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--color-loss)',
                fontSize: '13px',
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '11px',
              background: loading ? 'var(--accent-primary-muted)' : 'var(--accent-primary)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              color: loading ? 'var(--accent-primary)' : '#fff',
              fontSize: '14px',
              fontWeight: '600',
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background 0.15s',
            }}
          >
            {loading ? 'Creating account…' : 'Create Account'}
          </button>
        </form>

        <p
          style={{
            marginTop: '24px',
            textAlign: 'center',
            fontSize: '13px',
            color: 'var(--text-secondary)',
          }}
        >
          Already have an account?{' '}
          <Link
            href="/login"
            style={{ color: 'var(--accent-primary)', textDecoration: 'none', fontWeight: '500' }}
          >
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
