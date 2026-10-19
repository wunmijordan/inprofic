import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from .security import decrypt_access_token


class MetaWhatsAppError(RuntimeError):
    pass


def graph_request(path, *, method="GET", token="", params=None, body=None, timeout=12):
    version = settings.META_WHATSAPP_GRAPH_VERSION
    url = f"https://graph.facebook.com/{version}/{path.lstrip('/')}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode() or "{}")
    except (HTTPError, URLError, TimeoutError) as exc:
        detail = "Meta request failed."
        if isinstance(exc, HTTPError):
            try:
                error = json.loads(exc.read().decode()).get("error", {})
                detail = str(error.get("message") or detail)
            except (ValueError, AttributeError):
                pass
        raise MetaWhatsAppError(detail[:500]) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise MetaWhatsAppError("Meta returned an unreadable response.") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise MetaWhatsAppError(str(payload["error"].get("message") or "Meta request failed.")[:500])
    return payload


def exchange_signup_code(code):
    if not settings.META_WHATSAPP_APP_ID or not settings.META_WHATSAPP_APP_SECRET:
        raise MetaWhatsAppError("Meta Embedded Signup is not available on this installation.")
    return graph_request(
        "oauth/access_token",
        params={
            "client_id": settings.META_WHATSAPP_APP_ID,
            "client_secret": settings.META_WHATSAPP_APP_SECRET,
            "code": code,
        },
    )


def validate_phone_number(phone_number_id, token):
    return graph_request(
        phone_number_id,
        token=token,
        params={"fields": "id,display_phone_number,verified_name,quality_rating"},
    )


def send_text(connection, recipient, body):
    token = decrypt_access_token(connection.encrypted_access_token)
    return graph_request(
        f"{connection.phone_number_id}/messages",
        method="POST",
        token=token,
        body={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": body[:4096]},
        },
    )
