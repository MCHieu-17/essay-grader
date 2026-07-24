import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
# 2. Tạo Engine kết nối
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
# 3. Tạo SessionLocal (mỗi instance của class này sẽ là một phiên làm việc với DB)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# 4. Tạo Base class để các models ở file models.py kế thừa
Base = declarative_base()
# Hàm tiện ích để đóng/mở session an toàn
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()