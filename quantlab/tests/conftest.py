import pytest

from quantlab.api import security
from quantlab.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every test gets its own data and reports folders, never the real ones."""
    monkeypatch.setenv("QUANTLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUANTLAB_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("QUANTLAB_API_TOKEN", "test-token")
    monkeypatch.setenv("QUANTLAB_SCHEDULE_ENABLED", "false")
    get_settings.cache_clear()
    security.api_token.cache_clear()
    yield
    get_settings.cache_clear()
    security.api_token.cache_clear()
