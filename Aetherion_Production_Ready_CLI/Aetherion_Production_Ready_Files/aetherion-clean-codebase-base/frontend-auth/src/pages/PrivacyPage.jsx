import React from 'react';
import { Link } from 'react-router-dom';
import { Lock, ArrowLeft } from 'lucide-react';

export default function PrivacyPage() {
  return (
    <div className="min-h-screen py-12 px-4 max-w-3xl mx-auto">
      <Link to="/" className="inline-flex items-center space-x-2 text-sm text-purple-400 hover:text-purple-300 mb-8">
        <ArrowLeft size={16} />
        <span>Back to Portal</span>
      </Link>
      <div className="glass-card rounded-2xl p-8 border border-white/10 space-y-6">
        <h1 className="text-2xl font-bold text-white">Privacy Policy</h1>
        <p className="text-xs text-slate-400">Effective Date: September 2026</p>
        
        <div className="space-y-4 text-sm text-slate-300 leading-relaxed">
          <p>
            Aetherion AI is designed with a strict privacy-first, local-workstation posture.
          </p>
          <h2 className="text-lg font-semibold text-white pt-2">1. Local Execution & Data Privacy</h2>
          <p>
            Your source code, file trees, and local git histories remain on your workstation. Only explicit user prompts and agent tool invocations are transmitted to your configured AI provider endpoints.
          </p>
          <h2 className="text-lg font-semibold text-white pt-2">2. Token Storage</h2>
          <p>
            Authentication tokens and API keys are stored in your operating system's credential vault (Windows Credential Manager, macOS Keychain, or Linux Secret Service) and are never logged or exported in telemetry.
          </p>
          <h2 className="text-lg font-semibold text-white pt-2">3. Telemetry & Diagnostics</h2>
          <p>
            Doctor checks and diagnostic commands execute locally and only transmit correlation health checks when explicitly requested via the CLI.
          </p>
        </div>
      </div>
    </div>
  );
}
