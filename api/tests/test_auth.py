import pytest

from app.core.security import verify_telegram_init_data
from tests.utils import make_init_data


def test_verify_init_data_valid():
    token = "test-bot-token"
    init_data = make_init_data(token, user_id=42)
    result = verify_telegram_init_data(init_data, token)
    assert result["user"]["id"] == 42


def test_verify_init_data_invalid():
    token = "test-bot-token"
    init_data = make_init_data(token, user_id=1).replace("hash=", "hash=bad")
    with pytest.raises(ValueError):
        verify_telegram_init_data(init_data, token)
