"""
governance/certification_harness.py — Full Governance Certification Pipeline
=============================================================================
Integrates all governance subsystems into a single orchestrated pipeline:

  1. Run Continuous Evaluation (EvaluationEngine)
  2. Run Adversarial Red-Team Campaign (RedTeamPlatform)
  3. Ingest into Drift Detector (DriftDetector)
  4. Issue Trust Certificate (TrustCertificationEngine)
  5. Publish to Audit Log (GovernanceAuditLog)

This is the single entry-point for:
  • CI/CD governance gates
  • Scheduled certification runs
  • On-demand operator verification
  • Deployment readiness checks
"""

import sys
import time
import logging
from typing import Optional

logger = logging.getLogger("nexora.governance.harness")


def run_certification_pipeline(
    verbose: bool = True,
    set_drift_baseline: bool = False,
) -> dict:
    """
    Executes the full governance certification pipeline.
    Returns a structured result dict suitable for CI/CD exit-code gating.
    """
    from governance.evaluation_engine    import get_evaluation_engine
    from governance.red_team             import get_red_team
    from governance.drift_detector       import get_drift_detector
    from governance.trust_certification  import get_certification_engine
    from governance.audit_forensics      import get_audit_log

    audit = get_audit_log()
    results: dict = {}

    _banner("AETHERION CONSTITUTIONAL GOVERNANCE CERTIFICATION PIPELINE", verbose)

    # ── Phase 1: Continuous Evaluation ───────────────────────────────────────
    _section("Phase 1 — Continuous Evaluation (10 probes)", verbose)
    engine = get_evaluation_engine()
    eval_report = engine.run()
    results["eval"] = eval_report.to_dict()
    audit.ingest_eval_report(eval_report)

    if verbose:
        sc = eval_report.scorecard
        print(f"  Pass Rate : {eval_report.pass_rate:.1%} ({eval_report.passed}/{eval_report.total_probes})")
        print(f"  Avg Latency: {eval_report.avg_latency_ms:.2f}ms")
        for dim, score in sc.items():
            status = "✓" if score >= 0.9 else ("⚠" if score >= 0.7 else "✗")
            print(f"  [{status}] {dim:<18} {score:.1%}")
        if eval_report.failures:
            print(f"\n  FAILURES:")
            for f in eval_report.failures:
                print(f"    ✗ {f.probe_id}: {f.failure_reason}")

    # ── Phase 2: Red-Team Campaign ────────────────────────────────────────────
    _section("Phase 2 — Adversarial Red-Team Campaign (18 attacks)", verbose)
    rt = get_red_team()
    campaign = rt.run_campaign()
    results["red_team"] = campaign.to_dict()
    audit.ingest_campaign_report(campaign)

    if verbose:
        print(f"  Total Attacks : {campaign.total_attacks}")
        print(f"  Blocked       : {campaign.blocked}")
        print(f"  Bypassed      : {campaign.bypassed}")
        print(f"  Bypass Rate   : {campaign.bypass_rate:.1%}")
        if campaign.bypassed > 0:
            print("\n  !! BYPASS DETECTED !!")
            for b in campaign.bypasses:
                print(f"    ✗ [{b.attack_id}] severity={b.severity:.2f} outcome={b.outcome.value}")
        else:
            print("  ✓ No bypasses detected")
        print("\n  By Category:")
        for cat, counts in campaign.by_category.items():
            b = counts.get("bypassed", 0)
            status = "✗" if b > 0 else "✓"
            print(f"  [{status}] {cat[:35]:<35} bypassed={b}/{counts['total']}")

    # ── Phase 3: Drift Detection ───────────────────────────────────────────────
    _section("Phase 3 — Behavioral Drift Detection", verbose)
    dd = get_drift_detector()
    dd.ingest_eval_report(eval_report)
    dd.ingest_campaign_report(campaign)
    drift = dd.evaluate_drift()
    results["drift"] = drift.to_dict()
    audit.ingest_drift_report(drift)

    if set_drift_baseline and eval_report.pass_rate >= 0.9:
        dd.set_baseline_from_report(eval_report)
        if verbose:
            print("  ✓ Drift baseline updated from current evaluation.")

    if verbose:
        icon = {"NONE": "✓", "WATCH": "⚠", "WARNING": "⚠⚠", "CRITICAL": "✗✗"}.get(drift.severity, "?")
        print(f"  [{icon}] Drift Severity: {drift.severity}")
        print(f"  {drift.explanation}")
        if drift.drifted_dimensions:
            print(f"  Drifted: {drift.drifted_dimensions}")
        print(f"  Recommendation: {drift.recommendation}")

    # ── Phase 4: Trust Certification ──────────────────────────────────────────
    _section("Phase 4 — Trust Certification", verbose)
    ce = get_certification_engine()
    cert = ce.certify(
        eval_report=eval_report,
        campaign_report=campaign,
        drift_report=drift,
    )
    results["certificate"] = cert.to_dict()
    audit.ingest_certificate(cert)

    if verbose:
        level_icon = {
            "CERTIFIED": "✓✓", "CONDITIONALLY_CERTIFIED": "✓",
            "PROVISIONAL": "⚠", "SUSPENDED": "✗", "REVOKED": "✗✗"
        }.get(cert.level.value, "?")
        print(f"  [{level_icon}] Certificate ID : {cert.cert_id}")
        print(f"      Level         : {cert.level.value}")
        print(f"      Trust Tier    : {cert.tier.value}")
        print(f"      Valid Until   : {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(cert.valid_until))}")
        print(f"      Digest        : {cert.integrity_digest[:24]}...")
        if cert.caveats:
            print("      Caveats:")
            for c in cert.caveats:
                print(f"        • {c}")

    # ── Phase 5: Audit Summary ────────────────────────────────────────────────
    _section("Phase 5 — Governance Audit Summary", verbose)
    status = audit.live_status()
    results["audit_status"] = status
    if verbose:
        print(f"  Total Governance Events: {status['total_governance_events']}")
        for evt, count in status["event_counts"].items():
            print(f"    {evt:<25} : {count}")
        if status["bypass_detected"]:
            print("  !! BYPASS EVENTS IN AUDIT LOG !!")

    # ── Final Verdict ─────────────────────────────────────────────────────────
    _banner("PIPELINE COMPLETE", verbose)
    certified = cert.is_valid() and cert.level.value in ("CERTIFIED", "CONDITIONALLY_CERTIFIED")
    results["certified"] = certified
    results["deployment_gate"] = "PASS" if certified else "BLOCK"

    if verbose:
        gate = "✓ DEPLOYMENT GATE: PASS" if certified else "✗ DEPLOYMENT GATE: BLOCK"
        print(f"\n  {gate}")
        print(f"  Certificate Level: {cert.level.value}")
        print(f"  Trust Tier: {cert.tier.value}")

    return results


def _banner(title: str, verbose: bool) -> None:
    if not verbose:
        return
    bar = "=" * 70
    print(f"\n{bar}")
    print(f"  {title}")
    print(f"{bar}")

def _section(title: str, verbose: bool) -> None:
    if verbose:
        print(f"\n── {title}")


# ── CI/CD Entry Point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Aetherion Constitutional Governance Certification")
    p.add_argument("--set-baseline", action="store_true", help="Capture baseline from this run")
    p.add_argument("--quiet", action="store_true", help="Suppress output")
    args = p.parse_args()

    result = run_certification_pipeline(
        verbose=not args.quiet,
        set_drift_baseline=args.set_baseline,
    )
    sys.exit(0 if result.get("certified") else 1)
