from sqlalchemy import Column, Integer, String, Text, LargeBinary, Boolean, ForeignKey, REAL

try:
    from data.database import Base
except ImportError:
    from database import Base

class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    username = Column(String)
    hashed_password = Column(String)

class Teacher(Base):
    __tablename__ = 'teachers'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    account_id = Column(Integer, ForeignKey('accounts.id'))

class Student(Base):
    __tablename__ = 'students'
    id = Column(Integer, primary_key=True)
    name = Column(String)

class Essay(Base):
    __tablename__ = 'essays'
    id = Column(Integer, primary_key=True)
    prompt = Column(Text)
    essay = Column(Text)
    pdf = Column(LargeBinary) # Tương đương bytea/blob
    content = Column(REAL)
    language = Column(REAL)
    organization =Column(REAL)
    total = Column(REAL)
    comment = Column(Text, nullable=True, default=None)
    status = Column(String, default="unscored")
    student_id = Column(Integer, ForeignKey('students.id'))
    teacher_id = Column(Integer, ForeignKey('teachers.id'))
