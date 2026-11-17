import React, { useState, useEffect } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { api } from '../api';
import { MonitorCheck, Shield, CheckCircle2, XCircle, AlertCircle, Terminal } from 'lucide-react';

export default function DeviceAuthPage() {
  const [searchParams] = useSearchParams();
  const initialCode = searchParams.get('code') || searchParams.get('user_code') || '';

  const [userCode, setUserCode] = useState(initialCode.toUpperCase());
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    if (initialCode) {
      setUserCode(initialCode.toUpperCase());
    }
  }, [initialCode]);

  const formatCodeInput = (val) => {
    const raw = val.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 8);
    if (raw.length > 4) {
      return `${raw.slice(0, 4)}-${raw.slice(4)}`;
    }
    return raw;
  };

  const handleAuthorize = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await api.confirmDeviceAuth(userCode, email, password);
      setSuccess(true);
    } catch (err) {
      setError(err.message || 'Failed to authorize device. Please check code and credentials.');
    } finally {
      setLoading(false);
    }
  };

  const handleDeny = async () => {
    if (!userCode) return;
    setLoading(true);
    setError('');
    try {
      await api.denyDeviceAuth(userCode);
      setDenied(true);
    } catch (err) {
      setError(err.message || 'Failed to deny request.');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <div className="w-full max-w-md glass-card rounded-2xl p-8 border border-emerald-500/30 text-center space-y-6">
          <div className="w-16 h-16 rounded-full bg-emerald-500/20 border border-emerald-500/40 mx-auto flex items-center justify-center text-emerald-400">
            <CheckCircle2 size={36} />
          </div>
          <h2 className="text-2xl font-bold text-white">Device Authorized</h2>
          <p className="text-sm text-slate-300">
            Your terminal session has been authenticated successfully. You can safely close this browser window and return to your command line.
          </p>
          <div className="p-4 rounded-xl bg-slate-900/80 border border-white/5 font-mono text-xs text-purple-300 text-left">
            <div className="flex items-center space-x-2 text-slate-400 mb-1">
              <Terminal size={14} />
              <span>Aetherion Terminal Ready</span>
            </div>
            <p className="text-slate-200">✓ Token securely provisioned to OS Keyring</p>
            <p className="text-slate-400">Run `aetherion doctor` to verify environment health.</p>
          </div>
        </div>
      </div>
    );
  }

  if (denied) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <div className="w-full max-w-md glass-card rounded-2xl p-8 border border-red-500/30 text-center space-y-4">
          <div className="w-16 h-16 rounded-full bg-red-500/20 border border-red-500/40 mx-auto flex items-center justify-center text-red-400">
            <XCircle size={36} />
          </div>
          <h2 className="text-2xl font-bold text-white">Authorization Denied</h2>
          <p className="text-sm text-slate-400">
            The device authorization request has been denied. The CLI session will be terminated.
          </p>
          <div className="pt-4">
            <Link to="/login" className="text-purple-400 hover:text-purple-300 text-sm font-medium">
              Return to Login
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md glass-card rounded-2xl p-8 border border-white/10 shadow-2xl">
        <div className="text-center mb-6">
          <div className="w-14 h-14 rounded-2xl bg-purple-600/20 border border-purple-500/30 mx-auto flex items-center justify-center text-purple-400 mb-4">
            <MonitorCheck size={28} />
          </div>
          <h1 className="text-2xl font-bold text-white">Authorize Terminal</h1>
          <p className="text-xs text-slate-400 mt-1">Confirm the user code displayed in your terminal</p>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 flex items-center space-x-2 text-red-400 text-sm">
            <AlertCircle size={16} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleAuthorize} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">User Code</label>
            <input
              type="text"
              required
              value={userCode}
              onChange={(e) => setUserCode(formatCodeInput(e.target.value))}
              placeholder="ABCD-1234"
              maxLength={9}
              className="w-full px-4 py-3 rounded-xl glass-input text-center font-mono text-xl tracking-widest text-purple-300 font-bold placeholder-slate-600"
            />
          </div>

          <div className="pt-2 border-t border-white/5 space-y-3">
            <p className="text-xs text-slate-400 font-medium">Confirm your Aetherion credentials:</p>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="developer@example.com"
                className="w-full px-3 py-2 rounded-lg glass-input text-sm text-white placeholder-slate-600"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Password</label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full px-3 py-2 rounded-lg glass-input text-sm text-white placeholder-slate-600"
              />
            </div>
          </div>

          <div className="pt-2 space-y-2">
            <button
              type="submit"
              disabled={loading || userCode.length < 8}
              className="w-full py-2.5 px-4 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium text-sm transition disabled:opacity-50 shadow-lg shadow-purple-600/20 cursor-pointer"
            >
              {loading ? 'Authorizing...' : 'Authorize Device'}
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={handleDeny}
              className="w-full py-2 px-4 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 text-xs font-medium transition cursor-pointer"
            >
              Deny Access
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
