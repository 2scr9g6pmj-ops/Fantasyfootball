import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import PlatformCredential
from app.services.yahoo_auth import YahooNotConnected, yahoo_access_token
from app.services.yahoo_client import YahooAPIError, YahooClient
from app.services.yahoo_sync_service import YahooSyncService

router = APIRouter(prefix="/api/platforms/yahoo", tags=["Yahoo"])


def _configured():
    settings = get_settings()
    if not all([settings.yahoo_client_id, settings.yahoo_client_secret, settings.app_encryption_key]):
        raise HTTPException(503, "Yahoo is not configured. Add YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET, and APP_ENCRYPTION_KEY to .env.")
    try: Fernet(settings.app_encryption_key.encode())
    except Exception as exc: raise HTTPException(503, "APP_ENCRYPTION_KEY is not a valid Fernet key") from exc
    return settings


@router.get("/status")
def status(db: Session = Depends(get_db)):
    settings = get_settings(); credential = db.get(PlatformCredential, "yahoo")
    return {"configured": bool(settings.yahoo_client_id and settings.yahoo_client_secret and settings.app_encryption_key), "connected": credential is not None, "account_id": credential.account_id if credential else None, "expires_at": credential.expires_at if credential else None}


@router.get("/connect")
def connect():
    settings = _configured()
    state = URLSafeTimedSerializer(settings.app_encryption_key, salt="yahoo-oauth").dumps({"platform": "yahoo"})
    params = {"client_id": settings.yahoo_client_id, "redirect_uri": settings.effective_yahoo_redirect_uri, "response_type": "code", "state": state}
    return RedirectResponse("https://api.login.yahoo.com/oauth2/request_auth?" + urlencode(params))


@router.get("/callback")
async def callback(code: str, state: str = Query(...), db: Session = Depends(get_db)):
    settings = _configured()
    try: URLSafeTimedSerializer(settings.app_encryption_key, salt="yahoo-oauth").loads(state, max_age=600)
    except BadSignature as exc: raise HTTPException(400, "Invalid or expired OAuth state") from exc
    basic = base64.b64encode(f"{settings.yahoo_client_id}:{settings.yahoo_client_secret}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://api.login.yahoo.com/oauth2/get_token", headers={"Authorization": f"Basic {basic}"}, data={"grant_type": "authorization_code", "redirect_uri": settings.effective_yahoo_redirect_uri, "code": code})
            if response.is_error:
                raise HTTPException(502, "Yahoo rejected the authorization code. Please connect Yahoo again.")
            token = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not reach Yahoo. Please try connecting again.") from exc
    cipher = Fernet(settings.app_encryption_key.encode())
    credential = db.get(PlatformCredential, "yahoo") or PlatformCredential(platform="yahoo", access_token_encrypted="")
    credential.access_token_encrypted = cipher.encrypt(token["access_token"].encode()).decode()
    if token.get("refresh_token"): credential.refresh_token_encrypted = cipher.encrypt(token["refresh_token"].encode()).decode()
    credential.account_id = token.get("xoauth_yahoo_guid") or credential.account_id
    credential.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=int(token.get("expires_in", 3600)))
    db.add(credential); db.commit()
    return RedirectResponse("/?yahoo=connected")


@router.post("/sync")
async def sync(db: Session = Depends(get_db)):
    try:
        token = await yahoo_access_token(db)
        client = YahooClient(token)
        try:
            return await YahooSyncService(db, client).sync()
        finally:
            await client.close()
    except YahooNotConnected as exc:
        raise HTTPException(401, str(exc)) from exc
    except YahooAPIError as exc:
        raise HTTPException(403, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not reach Yahoo Fantasy Sports") from exc
