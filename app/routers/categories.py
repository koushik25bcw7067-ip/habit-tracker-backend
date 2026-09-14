from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.models.category import Category
from app.routers.users import current_user

router = APIRouter(prefix="/categories", tags=["Categories"])

class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)

@router.get("")
def list_categories(user=Depends(current_user), db: Session=Depends(get_db)):
    return db.scalars(select(Category).where((Category.user_id.is_(None)) | (Category.user_id == user.id)).order_by(Category.name)).all()

@router.post("", status_code=201)
def create_category(data: CategoryIn, user=Depends(current_user), db: Session=Depends(get_db)):
    exists = db.scalar(select(Category).where(Category.user_id == user.id, Category.name.ilike(data.name)))
    if exists:
        raise HTTPException(409, "Category already exists")
    category = Category(user_id=user.id, name=data.name.strip(), is_default=False)
    db.add(category); db.commit(); db.refresh(category)
    return category

@router.delete("/{category_id}")
def delete_category(category_id: int, user=Depends(current_user), db: Session=Depends(get_db)):
    category = db.scalar(select(Category).where(Category.id == category_id, Category.user_id == user.id))
    if not category:
        raise HTTPException(404, "Custom category not found")
    db.delete(category); db.commit()
    return {"message": "Category deleted"}
