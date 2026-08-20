from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.app.config import get_settings

security = HTTPBearer(auto_error=False)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    settings = get_settings()
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=7)
    to_encode.update({"exp": expire})
    secret_key = getattr(settings, "JWT_SECRET", "super-secret-key-12345")
    encoded_jwt = jwt.encode(to_encode, secret_key, algorithm="HS256")
    return encoded_jwt

async def get_current_user_id(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> str:
    """
    Returns the user_id from the JWT token.
    If no token is provided, or token is invalid, returns the X-Guest-ID header or 'guest'.
    """
    guest_id = request.headers.get("X-Guest-ID", "guest")
    
    if not credentials:
        return guest_id
    
    token = credentials.credentials
    settings = get_settings()
    secret_key = getattr(settings, "JWT_SECRET", "super-secret-key-12345")
    
    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        user_id: str = payload.get("sub")
        if user_id is None:
            return guest_id
        return user_id
    except JWTError:
        return guest_id
