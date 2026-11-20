"""v13.10: settings in .env reach the run (20 Nov run had no call cap although .env set 480)."""
import os

from services import project_env


def test_env_file_settings_are_loaded_and_process_env_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nEVENT_INTELLIGENCE_MAX_GEMINI_CALLS=480\nexport EXA_FALLBACK="1"\n'
                   'Gemini API Key = "x"\nEVENT_INTELLIGENCE_DEEP_MAX_RUNTIME_SECONDS=4800 # note\n'
                   'ALREADY_SET=from-file\n', encoding="utf-8")
    for name in ("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", "EXA_FALLBACK", "EVENT_INTELLIGENCE_DEEP_MAX_RUNTIME_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-process")
    names = project_env.load(env)
    assert os.environ["EVENT_INTELLIGENCE_MAX_GEMINI_CALLS"] == "480"
    assert os.environ["EXA_FALLBACK"] == "1"
    assert os.environ["EVENT_INTELLIGENCE_DEEP_MAX_RUNTIME_SECONDS"] == "4800"
    assert os.environ["ALREADY_SET"] == "from-process"            # the process environment wins
    assert "ALREADY_SET" not in names and "Gemini API Key" not in names   # odd names skipped


def test_orchestrator_loads_env_before_its_constants():
    import inspect
    from services import research_orchestrator_service as ros
    source = inspect.getsource(ros)
    assert source.index("_project_env.load()") < source.index("def _env_int(")
