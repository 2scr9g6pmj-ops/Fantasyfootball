import base64
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PlatformCredential


class YahooNotConnected(RuntimeError):
    pass


async def yahoo_access_token(db: Session) -> str:
    settings = get_settings()
    credential = db.get(PlatformCredential, "yahoo")
    if not credential:
        raise YahooNotConnected("Yahoo is not connected")
    cipher = Fernet(settings.app_encryption_key.encode())
    if credential.expires_at and credential.expires_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=60):
        return cipher.decrypt(credential.access_token_encrypted.encode()).decode()
    if not credential.refresh_token_encrypted:
        raise YahooNotConnected("Yahoo authorization expired; reconnect Yahoo")

    refresh_token = cipher.decrypt(credential.refresh_token_encrypted.encode()).decode()
    basic = base64.b64encode(f"{settings.yahoo_client_id}:{settings.yahoo_client_secret}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.login.yahoo.com/oauth2/get_token",
                headers={"Authorization": f"Basic {basic}"},
                data={"grant_type": "refresh_token", "redirect_uri": settings.effective_yahoo_redirect_uri, "refresh_token": refresh_token},
            )
            response.raise_for_status()
            token = response.json()
    except httpx.HTTPError as exc:
        raise YahooNotConnected("Yahoo token refresh failed; reconnect Yahoo") from exc

    credential.access_token_encrypted = cipher.encrypt(token["access_token"].encode()).decode()
    if token.get("refresh_token"):
        credential.refresh_token_encrypted = cipher.encrypt(token["refresh_token"].encode()).decode()
    credential.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=int(token.get("expires_in", 3600)))
    db.add(credential)
    db.commit()
    return token["access_token"]
