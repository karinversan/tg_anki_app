import pytest

from app.core import config
from tests.utils import make_init_data


@pytest.mark.asyncio
async def test_auth_me_returns_admin_flag(client):
    config.settings.bot_token = "test-bot-token"
    config.settings.admin_telegram_ids = "101"

    init_data = make_init_data(config.settings.bot_token, user_id=101)
    auth = await client.post("/auth/telegram", json={"init_data": init_data})
    token = auth.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    payload = me.json()
    assert payload["telegram_id"] == 101
    assert payload["is_admin"] is True


@pytest.mark.asyncio
async def test_admin_metrics_report_requires_admin(client, tmp_path):
    config.settings.bot_token = "test-bot-token"
    config.settings.storage_path = str(tmp_path)
    config.settings.admin_telegram_ids = "101"

    admin_auth = await client.post(
        "/auth/telegram",
        json={"init_data": make_init_data(config.settings.bot_token, user_id=101)},
    )
    admin_token = admin_auth.json()["access_token"]

    user_auth = await client.post(
        "/auth/telegram",
        json={"init_data": make_init_data(config.settings.bot_token, user_id=202)},
    )
    user_token = user_auth.json()["access_token"]

    denied = await client.post(
        "/admin/metrics/report",
        json={"limit": 5},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert denied.status_code == 403

    report = await client.post(
        "/admin/metrics/report",
        json={"limit": 5},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert report.status_code == 200
    payload = report.json()
    assert payload["jobs_analyzed"] == 0
    assert payload["report_id"]

    download_json = await client.get(
        f"/admin/metrics/report/{payload['report_id']}/download/json",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert download_json.status_code == 200
