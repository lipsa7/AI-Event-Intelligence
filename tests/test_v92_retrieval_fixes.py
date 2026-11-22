"""20 Nov 2026: retrieval failures found in RUN-87BF's log (14 Nov), the user's last real run."""
import pytest

from agents.evidence_discovery_agent import EvidenceIntegrityError


def _service():
    from services.hypothesis_evidence_service import HypothesisEvidenceService as H
    return H.__new__(H)


def test_outcome_query_is_about_the_case_not_its_date():
    svc = _service()
    asked = []
    svc._ask = lambda prompt, **k: (asked.append(prompt), {"query": "CISA Secure by Design pledge signatories compliance results"})[1]
    record = {"text": "Published: 08 May 2024. At RSAC 2024, 68 vendors signed CISA's Secure by Design pledge.",
              "historical_case": {"year": 2024, "date_quote": "Published: 08 May 2024"}}
    assert svc._outcome_query(record) == "CISA Secure by Design pledge signatories compliance results"
    assert "68 vendors signed CISA's Secure by Design pledge" in asked[0]


def test_outcome_query_fallback_drops_date_boilerplate():
    svc = _service()

    def fail(prompt, **k):
        raise RuntimeError("model down")
    svc._ask = fail
    q = svc._outcome_query({"text": "Published: 08 May 2024. 68 vendors signed CISA's Secure by Design pledge.",
                            "historical_case": {"year": 2024}})
    assert "Published" not in q and "08 May 2024" not in q and q.endswith("2024 outcome what happened next") and "CISA's" in q


def test_annotated_copy_of_the_same_evidence_no_longer_aborts_research():
    from agents.evidence_discovery_agent import EvidenceDiscoveryAgent as A
    agent = A.__new__(A)
    base = {"evidence_id": "EVID-1", "source_url": "https://x.example/a", "published_at": "2026-11-10",
            "source_excerpt": "Simons Foundation and OpenAI will fund mathematicians."}
    annotated = dict(base, precedent_for="Launching a joint initiative", gap_type="dependency")
    assert len(agent._normalize_existing_evidence([base, annotated])) == 1
    with pytest.raises(EvidenceIntegrityError):        # a real collision still stops it
        agent._normalize_existing_evidence([base, dict(base, source_excerpt="A different text entirely.")])


def test_page_review_requires_the_same_entity_and_mechanism():
    import inspect
    from services import hypothesis_evidence_service as H
    src = inspect.getsource(H)
    assert "SAME NAME, DIFFERENT ENTITY" in src and "ONLY about name confusion" in src
    assert "never reject it because its actors differ" in src and "MECHANISM" in src


def test_cookie_redirect_to_same_page_is_followed():
    """A site that sets a cookie and redirects to itself (RUN-87BF: 'HTTP 301' failures) now loads."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from services.source_fetch_service_direct import UrllibHTTPTransport

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if "consent=1" in (self.headers.get("Cookie") or ""):
                body = b"<html><body><p>" + b"The accord was signed. " * 40 + b"</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(301)
                self.send_header("Set-Cookie", "consent=1; Path=/")
                self.send_header("Location", self.path)
                self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        transport = UrllibHTTPTransport(allow_private_networks=True)
        out = transport.fetch(url=f"http://127.0.0.1:{server.server_port}/article", timeout_seconds=5,
                              max_download_bytes=100000)
        assert int(out.get("status_code") or out.get("status") or 200) == 200
        body = out.get("body") or out.get("content") or b""
        assert b"The accord was signed." in (body if isinstance(body, bytes) else str(body).encode())
    finally:
        server.shutdown()
