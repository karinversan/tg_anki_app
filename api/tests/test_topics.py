import pytest

from app.core import config
from tests.utils import make_init_data


@pytest.mark.asyncio
async def test_topic_permissions(client):
    config.settings.bot_token = "test-bot-token"

    init_data_user1 = make_init_data(config.settings.bot_token, user_id=1)
    init_data_user2 = make_init_data(config.settings.bot_token, user_id=2)

    resp1 = await client.post("/auth/telegram", json={"init_data": init_data_user1})
    token1 = resp1.json()["access_token"]
    resp2 = await client.post("/auth/telegram", json={"init_data": init_data_user2})
    token2 = resp2.json()["access_token"]

    create = await client.post("/topics/", json={"title": "Biology"}, headers={"Authorization": f"Bearer {token1}"})
    topic_id = create.json()["id"]

    delete = await client.delete(f"/topics/{topic_id}", headers={"Authorization": f"Bearer {token2}"})
    assert delete.status_code == 404
