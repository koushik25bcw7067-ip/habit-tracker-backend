from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.user import UserUpdate
from app.utils.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
router=APIRouter(prefix="/users",tags=["Users"])
bearer=HTTPBearer()

def current_user(db=Depends(get_db), creds:HTTPAuthorizationCredentials=Depends(bearer)):
    uid=int(decode_token(creds.credentials)["sub"])
    u=db.get(User,uid)
    if not u or not u.is_active: raise HTTPException(401,"User not found")
    return u

@router.get("/me",response_model=UserResponse)
def me(user=Depends(current_user)): return user

@router.put("/me",response_model=UserResponse)
def update_me(data:UserUpdate,user=Depends(current_user),db:Session=Depends(get_db)):
    if data.email and db.scalar(select(User).where(User.email==data.email.lower(),User.id!=user.id)): raise HTTPException(409,"Email already registered")
    if data.username and db.scalar(select(User).where(User.username==data.username,User.id!=user.id)): raise HTTPException(409,"Username already taken")
    for k,v in data.model_dump(exclude_unset=True).items(): setattr(user,k,v.lower() if k=="email" else v)
    db.commit();db.refresh(user);return user

@router.delete("/me")
def delete_me(user=Depends(current_user),db:Session=Depends(get_db)):
    db.delete(user);db.commit();return {"message":"Account deleted"}
