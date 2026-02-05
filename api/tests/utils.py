import hashlib
import hmac
import json
import time
from urllib.parse import urlencode


def make_init_data(bot_token: str, user_id: int = 123) -> str:
    user = json.dumps({"id": user_id, "first_name": "Test"})
    auth_date = str(int(time.time()))
    data = {"user": user, "auth_date": auth_date, "query_id": "q"}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_value = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    data["hash"] = hash_value
    return urlencode(data)
