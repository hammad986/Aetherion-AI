import React, { useState, useEffect } from 'react';
import { Routes, Route, Link, useNavigate } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import DeviceAuthPage from './pages/DeviceAuthPage';
import RecoveryPage from './pages/RecoveryPage';
import LogoutPage from './pages/LogoutPage';
import TermsPage from './pages/TermsPage';
import PrivacyPage from './pages/PrivacyPage';
import { Terminal, Shield, Check, LogOut } from 'lucide-react';
import { api } from './api';

function HomePage() {
  const [token, setToken] = useState(localStorage.getItem('aetherion_token'));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauthComplete = params.get('oauth') === 'success';
    const nxToken = params.get('nx_token');

    async function loadUser() {
      try {
        let activeToken = token;
        if (nxToken) {
          activeToken = nxToken;
          localStorage.setItem('aetherion_token', activeToken);
          setToken(activeToken);
          window.history.replaceState({}, document.title, window.location.pathname);
        } else if (oauthComplete || !activeToken) {
          const refreshed = await api.refresh();
          activeToken = refreshed.access_token;
          localStorage.setItem('aetherion_token', activeToken);
          setToken(activeToken);
          window.history.replaceState({}, document.title, window.location.pathname);
        }

        if (!activeToken) {
          navigate('/login');
          return;
        }

        const data = await api.me(activeToken);
        setUser(data.user || data);
      } catch {
        localStorage.removeItem('aetherion_token');
        navigate('/login');
      } finally {
        setLoading(false);
      }
    }

    loadUser();
  }, [token, navigate]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-purple-500"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-lg glass-card rounded-2xl p-8 border border-white/10 shadow-2xl space-y-6">
        <div className="flex items-center justify-between border-b border-white/5 pb-4">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-purple-600/20 border border-purple-500/30 flex items-center justify-center text-purple-400">
              <Terminal size={20} />
            </div>
            <div>
              <h1 className="text-lg font-bold text-white">Aetherion Auth</h1>
              <p className="text-xs text-slate-400">Identity Portal</p>
            </div>
          </div>
          <Link
            to="/logout"
            className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition flex items-center space-x-1 text-xs"
          >
            <LogOut size={14} />
            <span>Sign out</span>
          </Link>
        </div>

        <div className="p-4 rounded-xl bg-purple-500/10 border border-purple-500/20 space-y-1">
          <p className="text-xs text-purple-300 font-medium">Logged in as:</p>
          <p className="text-base font-semibold text-white">{user?.email || 'Authenticated User'}</p>
          <p className="text-xs text-slate-400">User ID: {user?.id || user?.user_id || '1'}</p>
        </div>

        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-200">Terminal & CLI Access</h2>
          <div className="p-4 rounded-xl bg-black/40 border border-white/5 font-mono text-xs text-slate-300 space-y-2">
            <p className="text-slate-400"># Authenticate your local workstation CLI</p>
            <p className="text-purple-300 font-bold">$ aetherion login</p>
            <p className="text-slate-400"># Or start the interactive TUI</p>
            <p className="text-purple-300 font-bold">$ aetherion tui</p>
          </div>
        </div>

        <div className="flex justify-between items-center pt-2 text-xs text-slate-500 border-t border-white/5">
          <Link to="/activate" className="text-purple-400 hover:text-purple-300">
            Activate a new device
          </Link>
          <div className="space-x-3">
            <Link to="/terms" className="hover:text-slate-300">Terms</Link>
            <Link to="/privacy" className="hover:text-slate-300">Privacy</Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/activate" element={<DeviceAuthPage />} />
      <Route path="/device" element={<DeviceAuthPage />} />
      <Route path="/recovery" element={<RecoveryPage />} />
      <Route path="/reset-password" element={<RecoveryPage />} />
      <Route path="/logout" element={<LogoutPage />} />
      <Route path="/terms" element={<TermsPage />} />
      <Route path="/privacy" element={<PrivacyPage />} />
    </Routes>
  );
}
