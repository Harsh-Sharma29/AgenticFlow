from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import bcrypt

from backend.app.services import storage
from backend.app.api.dependencies import create_access_token

router = APIRouter()

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    name: str

@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest):
    # Hash password
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(req.password.encode('utf-8'), salt).decode('utf-8')
    
    try:
        user_id = await storage.create_user(
            email=req.email.lower(),
            password_hash=hashed_password,
            name=req.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    # Generate token
    token = create_access_token(data={"sub": user_id})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user_id,
        name=req.name
    )

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    user = await storage.get_user_by_email(req.email.lower())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    if not bcrypt.checkpw(req.password.encode('utf-8'), user["password_hash"].encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    user_id = user["id"]
    token = create_access_token(data={"sub": user_id})
    
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user_id,
        name=user["name"] or ""
    )
