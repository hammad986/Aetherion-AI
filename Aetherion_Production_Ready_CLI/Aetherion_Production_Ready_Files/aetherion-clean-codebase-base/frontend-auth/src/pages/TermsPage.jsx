import React from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck, ArrowLeft } from 'lucide-react';

export default function TermsPage() {
  return (
    <div className="min-h-screen py-12 px-4 max-w-3xl mx-auto">
      <Link to="/" className="inline-flex items-center space-x-2 text-sm text-purple-400 hover:text-purple-300 mb-8">
        <ArrowLeft size={16} />
        <span>Back to Portal</span>
      </Link>
      <div className="glass-card rounded-2xl p-8 border border-white/10 space-y-6">
        <h1 className="text-2xl font-bold text-white">Terms of Service</h1>
        <p className="text-xs text-slate-400">Effective Date: September 2026</p>
        
        <div className="space-y-4 text-sm text-slate-300 leading-relaxed">
          <p>
            Welcome to Aetherion AI. By using the Aetherion CLI, TUI, backend services, or authentication portal, you agree to these Terms of Service.
          </p>
          <h2 className="text-lg font-semibold text-white pt-2">1. Autonomous Code Execution</h2>
          <p>
            Aetherion executes code, builds environments, and modifies repositories at your direction. You maintain full responsibility for verifying generated changes, tests, and diffs before committing to production.
          </p>
          <h2 className="text-lg font-semibold text-white pt-2">2. Credentials & Tokens</h2>
          <p>
            You are responsible for safeguarding your personal access tokens, device authorizations, and provider API keys. Tokens are persisted on your workstation via your operating system's native secure keyring.
          </p>
          <h2 className="text-lg font-semibold text-white pt-2">3. Acceptable Use</h2>
          <p>
            You agree not to use Aetherion for malicious payload generation, denial-of-service attempts, unauthorized credential extraction, or security circumvention.
          </p>
        </div>
      </div>
    </div>
  );
}
