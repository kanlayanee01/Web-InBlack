from types import SimpleNamespace

import pytest

from app.api import routes
from app.problem import Problem


@pytest.mark.parametrize("env", ["development", "production", "staging", "TEST", ""])
async def test_reset_is_404_unless_app_env_is_exactly_test(monkeypatch, env):
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(app_env=env))
    with pytest.raises(Problem) as caught:
        await routes.test_reset(None)  # the guard runs before the request is touched
    assert caught.value.status == 404
