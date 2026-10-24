"""require_api_key(): None and "" (docker-compose's API_KEY="" when the host var is unset) must both
disable auth; only a real non-empty API_KEY should require a matching X-API-Key header."""
import pytest

from app import main
from app.routes.dependencies import require_api_key


@pytest.fixture(autouse=True)
def _restore_api_key():
    original = main.settings.api_key
    yield
    object.__setattr__(main.settings, "api_key", original)


@pytest.mark.parametrize("disabled_value", [None, ""])
def test_auth_disabled_when_api_key_is_none_or_empty(disabled_value):
    object.__setattr__(main.settings, "api_key", disabled_value)
    require_api_key(x_api_key=None)  # must not raise


def test_auth_rejects_missing_header_when_api_key_is_set():
    object.__setattr__(main.settings, "api_key", "secret123")
    with pytest.raises(Exception) as exc_info:
        require_api_key(x_api_key=None)
    assert exc_info.value.status_code == 401


def test_auth_rejects_wrong_key():
    object.__setattr__(main.settings, "api_key", "secret123")
    with pytest.raises(Exception) as exc_info:
        require_api_key(x_api_key="wrong")
    assert exc_info.value.status_code == 401


def test_auth_accepts_matching_key():
    object.__setattr__(main.settings, "api_key", "secret123")
    require_api_key(x_api_key="secret123")  # must not raise
