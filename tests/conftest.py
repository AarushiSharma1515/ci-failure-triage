import pytest

from app import main
from app.real_logs import RealLogSet


@pytest.fixture(autouse=True)
def no_real_logs(monkeypatch):
    """API tests must not depend on the logs collected on this machine (data/raw is never
    committed): by default the app sees no labelled real logs. Tests that need some build
    their own and set main._real_logs."""
    monkeypatch.setattr(main, "_real_logs", RealLogSet())
