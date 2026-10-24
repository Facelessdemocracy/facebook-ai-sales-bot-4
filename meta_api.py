import os
import requests

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v23.0")
BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

def _send(psid, token, message):
    if not token:
        raise RuntimeError("Missing Page access token")
    response = requests.post(
        f"{BASE}/me/messages",
        params={"access_token": token},
        json={"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": message},
        timeout=20
    )
    response.raise_for_status()
    return response.json()

def send_text(psid, text, token):
    return _send(psid, token, {"text": str(text)[:2000]})

def send_image(psid, image_url, token):
    if not image_url:
        return None
    return _send(psid, token, {
        "attachment": {
            "type": "image",
            "payload": {"url": image_url, "is_reusable": True}
        }
    })

def send_typing_on(psid, token):
    if not token:
        return None
    response = requests.post(
        f"{BASE}/me/messages",
        params={"access_token": token},
        json={"recipient": {"id": psid}, "sender_action": "typing_on"},
        timeout=10
    )
    response.raise_for_status()
    return response.json()
