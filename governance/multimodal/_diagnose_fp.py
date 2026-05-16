"""Diagnostic script for FP analysis."""
from governance.multimodal.multimodal_governor import MultimodalGovernor, MultimodalRequest
from governance.multimodal.ocr_governor import OCRSource
from governance.multimodal.document_governor import DocumentIntake

# OK-001: Legitimate document
req = MultimodalRequest(
    intent="Please summarize this quarterly report",
    document=DocumentIntake(
        filename="q3_report.pdf",
        page_texts=["Q3 Revenue: 4.2M. Operating costs: 2.1M. Net profit: 2.1M."],
        metadata={"Author": "Finance", "Title": "Q3 2026 Report"},
        links=["https://company-internal.com/report/q3"],
        has_javascript=False, has_hidden_layers=False, has_attachments=False, page_count=1,
    )
)
d = MultimodalGovernor.evaluate(req)
print("OK-001:", d.final_recommendation, "triggered=", d.triggered_by)
print("  all_signals:", d.all_signals)
print("  canon_signals:", d.canon_decision.canonical_danger_signals)
print("  semantic_rec:", d.canon_decision.semantic_recommendation, "risk=", d.canon_decision.semantic_risk_score)
print("  fusion:", d.fusion_decision.modality_recommendations)
print()

# OK-002: benign screenshot
req2 = MultimodalRequest(
    intent="What code is shown in this screenshot?",
    ocr_extractions=[{"text": "def hello_world():\n    print(msg)", "source": OCRSource.SCREENSHOT, "confidence": 0.97}]
)
d2 = MultimodalGovernor.evaluate(req2)
print("OK-002:", d2.final_recommendation, "triggered=", d2.triggered_by)
print("  all_signals:", d2.all_signals)
print("  ocr triggered:", d2.ocr_results[0].triggered_by if d2.ocr_results else "none")
print()

# OK-003: Urgent technical
req3 = MultimodalRequest(
    intent="I need help quickly my deployment is failing and I need to debug this error urgently.",
    payload="kubectl get pods --all-namespaces"
)
d3 = MultimodalGovernor.evaluate(req3)
print("OK-003:", d3.final_recommendation, "triggered=", d3.triggered_by)
print("  cognitive signals:", d3.cognitive_result.signal_details)
print("  cognitive score:", d3.cognitive_result.compound_score)
print()

# OK-004: Authority in legit context
req4 = MultimodalRequest(
    intent="As the team lead I want to review the deployment logs for our service.",
    payload="kubectl logs deployment/nexora-api"
)
d4 = MultimodalGovernor.evaluate(req4)
print("OK-004:", d4.final_recommendation, "triggered=", d4.triggered_by)
print("  cognitive signals:", d4.cognitive_result.signal_details)
print("  cognitive score:", d4.cognitive_result.compound_score)
