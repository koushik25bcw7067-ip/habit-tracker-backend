from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse, RefreshRequest, UserResponse
from app.services.auth_service import register, authenticate
from app.utils.security import create_access_token, create_refresh_token, decode_token
router=APIRouter(prefix="/auth",tags=["Authentication"])

@router.post("/register",response_model=UserResponse,status_code=201)
def register_user(data:RegisterRequest,db:Session=Depends(get_db)):
    return register(db,data.username,data.email,data.password)

@router.post("/login",response_model=TokenResponse)
def login(data:LoginRequest,db:Session=Depends(get_db)):
    u=authenticate(db,data.email,data.password)
    return TokenResponse(access_token=create_access_token(u.id),refresh_token=create_refresh_token(u.id))

@router.post("/refresh",response_model=TokenResponse)
def refresh(data:RefreshRequest):
    p=decode_token(data.refresh_token,"refresh")
    uid=int(p["sub"])
    return TokenResponse(access_token=create_access_token(uid),refresh_token=create_refresh_token(uid))

@router.post("/logout")
def logout():
    return {"message":"Logout acknowledged. Delete tokens on the client; access tokens expire automatically."}
