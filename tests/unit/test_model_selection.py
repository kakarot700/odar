"""Model-id selection for the Anthropic adapter (offline)."""

from odar.agent import DEFAULT_ANTHROPIC_MODEL, resolve_anthropic_model


def test_default_model_is_current():
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5-5"


def test_env_override(monkeypatch):
    monkeypatch.setenv("ODAR_ANTHROPIC_MODEL", "some-compatible-model")
    assert resolve_anthropic_model() == "some-compatible-model"


def test_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("ODAR_ANTHROPIC_MODEL", "env-model")
    assert resolve_anthropic_model("explicit-model") == "explicit-model"


def test_blank_values_fall_back(monkeypatch):
    monkeypatch.setenv("ODAR_ANTHROPIC_MODEL", "  ")
    assert resolve_anthropic_model("") == DEFAULT_ANTHROPIC_MODEL
