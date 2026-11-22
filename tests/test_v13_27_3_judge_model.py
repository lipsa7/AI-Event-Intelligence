"""v13.27.3: the judge is the largest installed CHAT model - never an embedding model."""
from services import outside_judge


class _Tags:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, url, timeout=3):
        rows = self.rows
        return type("R", (), {"json": lambda _self: {"models": rows}})()


def test_embedding_model_listed_first_is_skipped_and_largest_chat_model_used(monkeypatch):
    monkeypatch.delenv("OUTSIDE_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_JUDGE_MODEL", raising=False)
    outside_judge.reset()
    laptop = [{"name": "nomic-embed-text:latest", "size": 274_000_000}, {"name": "qwen2.5:1.5b", "size": 986_000_000},
              {"name": "qwen2.5:3b", "size": 1_900_000_000}, {"name": "llama3.1:8b", "size": 4_900_000_000}]
    assert outside_judge.model(get=_Tags(laptop)) == "llama3.1:8b"
    outside_judge.reset()
    assert outside_judge.model(get=_Tags([{"name": "nomic-embed-text:latest", "size": 1}])) == ""
    outside_judge.reset()
