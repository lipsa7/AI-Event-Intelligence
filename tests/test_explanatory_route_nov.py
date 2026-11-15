"""12 Nov 2026: a fully grounded explanatory finding is shown on the source-backed route, labelled."""
import inspect

from frontend import dashboard
from services import research_orchestrator_service as ros


def test_explanatory_finding_goes_to_source_backed_route():
    src = inspect.getsource(ros.ResearchOrchestratorService)
    assert '"explanatory_finding_not_hidden_insight"' in src and 'shown_as"] = "explanatory_source_backed"' in src


def test_dashboard_labels_explanatory_finding():
    line = dashboard.source_backed_line({"reasons": ["explanatory_finding_not_hidden_insight"]})
    assert line.startswith("**Backed by its sources:** Explanatory finding")
