import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class WhatsAppTokenError(ValueError):
    pass


def _fernet():
    secret = (getattr(settings, "WHATSAPP_TOKEN_ENCRYPTION_KEY", "") or "").encode()
    if len(secret) < 32:
        raise WhatsAppTokenError("Configure a dedicated WhatsApp token-encryption secret with at least 32 characters.")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_access_token(token):
    if not token:
        raise WhatsAppTokenError("Meta did not return an access token.")
    return _fernet().encrypt(token.encode()).decode()


def decrypt_access_token(value):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise WhatsAppTokenError("The saved WhatsApp credential cannot be decrypted.") from exc
