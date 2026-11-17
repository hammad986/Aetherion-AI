import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { LogOut, CheckCircle2 } from 'lucide-react';

export default function LogoutPage() {
  useEffect(() => {
    localStorage.removeItem('aetherion_token');
    api.logout().catch(() => {});
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md glass-card rounded-2xl p-8 border border-white/10 shadow-2xl text-center space-y-4">
        <div className="w-14 h-14 rounded-2xl bg-purple-600/20 border border-purple-500/30 mx-auto flex items-center justify-center text-purple-400 mb-2">
          <LogOut size={26} />
        </div>
        <h2 className="text-2xl font-bold text-white">Signed Out</h2>
        <p className="text-sm text-slate-400">
          You have been signed out from this browser session.
        </p>
        <div className="pt-4">
          <Link to="/login" className="px-5 py-2.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-sm font-medium transition inline-block">
            Sign In Again
          </Link>
        </div>
      </div>
    </div>
  );
}
