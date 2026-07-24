import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import get_db
from data.model import Account


load_dotenv()

router = APIRouter(prefix="/auth")

oauth2_bearer = OAuth2PasswordBearer(tokenUrl="auth/token")
password_hasher = PasswordHash.recommended()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
db_dependency = Annotated[Session, Depends(get_db)]
token_dependency = Annotated[str, Depends(oauth2_bearer)]
form_data_dependency = Annotated[OAuth2PasswordRequestForm, Depends()]

class Token(BaseModel):
    access_token: str
    token_type: str 

def create_access_token(username: str, user_id: int, expires_delta: timedelta):
    """
    Tạo chuỗi jwt từ thông tin người dùng
    Args:
        username (str): tên đăng nhập 
        user_id (int): id của người dùng
    Returns:
        jwt (str): Chuỗi jwt
    """
    jwt_info = {
        "username": username, 
        "id": user_id, 
        "exp": datetime.now(timezone.utc) + expires_delta}
    return jwt.encode(jwt_info, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: token_dependency):
    """
    Phân tích chuỗi JWT để lấy thông tin người dùng
    Args:
        token (str): toàn bộ chuỗi jwt
    Returns:
        dict: chứa "username", "user_id"
    Raises:
        jwt.PyJWTError: Nếu token không hợp lệ hoặc đã hết hạn.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("username")
        user_id: int = payload.get("id")

        if user_id is None or username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Could not validate user.")
        return {
            "username": username,
            "user_id": user_id
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired."
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate user."
        )

def authenticate_user(username: str, password: str, db: db_dependency):
    """
    Xác thực đăng nhập
    Args:
        username (str): tên đăng nhập
        password (str): mật khẩu
        db (Session): con trỏ đến csdl
    Returns:
        user | Literal[False]: Trả về đối tượng user nếu đúng, ngược lại trả về False
    """
    statement = select(Account).where(Account.username == username)
    user = db.execute(statement).scalars().first()
    if user is None:
        return False
    if not password_hasher.verify(password, user.hashed_password):
        return False
    return user 

@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: form_data_dependency,
                                 db: db_dependency):

    """
    Xác thực đăng nhập và trả về jwt
    Args:
        form_data: Mẫu đăng nhập chuẩn OAuth2
        db: con trỏ đến db
    Returns:
        dict: access token và token type
    Raise:
        404: Nếu thông tin đăng nhập sai
    """
    user = authenticate_user(form_data.username, form_data.password, db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incorrect account or password",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return {
        "access_token": create_access_token(user.username, user.id, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)),
        "token_type": "bearer"
    }