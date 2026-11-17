import React, { useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { api } from '../api';
import { KeyRound, CheckCircle2, AlertCircle } from 'lucide-react';

export default function RecoveryPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const handleForgot = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await api.forgotPassword(email);
      setMessage(res.message || 'If an account exists with that email, a password reset link has been dispatched.');
    } catch (err) {
      setError(err.message || 'Failed to request password reset.');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (e) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setError('');
    setLoading(true);

    try {
      const res = await api.resetPassword(token, password);
      setMessage(res.message || 'Password has been reset successfully! You can now log in.');
    } catch (err) {
      setError(err.message || 'Password reset failed or token is expired.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md glass-card rounded-2xl p-8 border border-white/10 shadow-2xl">
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-purple-600/20 border border-purple-500/30 mx-auto flex items-center justify-center text-purple-400 mb-3">
            <KeyRound size={24} />
          </div>
          <h1 className="text-2xl font-bold text-white">
            {token ? 'Reset Password' : 'Password Recovery'}
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            {token ? 'Enter your new credentials below' : 'Enter your email to receive recovery instructions'}
          </p>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 flex items-center space-x-2 text-red-400 text-sm">
            <AlertCircle size={16} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {message ? (
          <div className="text-center space-y-4">
            <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-sm">
              {message}
            </div>
            <Link to="/login" className="inline-block text-sm text-purple-400 hover:text-purple-300 font-medium">
              Back to Sign in
            </Link>
          </div>
        ) : token ? (
          <form onSubmit={handleReset} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">New Password</label>
              <input
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Minimum 8 characters"
                className="w-full px-4 py-2.5 rounded-lg glass-input text-sm text-white placeholder-slate-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Confirm New Password</label>
              <input
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Repeat password"
                className="w-full px-4 py-2.5 rounded-lg glass-input text-sm text-white placeholder-slate-500"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 px-4 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium text-sm transition disabled:opacity-50 shadow-lg shadow-purple-600/20 cursor-pointer"
            >
              {loading ? 'Resetting...' : 'Save New Password'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleForgot} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Account Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="developer@example.com"
                className="w-full px-4 py-2.5 rounded-lg glass-input text-sm text-white placeholder-slate-500"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 px-4 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium text-sm transition disabled:opacity-50 shadow-lg shadow-purple-600/20 cursor-pointer"
            >
              {loading ? 'Sending...' : 'Send Recovery Link'}
            </button>
            <div className="text-center pt-2">
              <Link to="/login" className="text-xs text-slate-400 hover:text-white">
                Remember your password? Sign in
              </Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
