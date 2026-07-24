import math
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_pipeline import ScoringPipline
from app.routers.auth import get_current_user, router as auth_router, ALGORITHM, SECRET_KEY
from data.database import Base, engine, get_db
from data.model import Account, Essay, Student, Teacher

app = FastAPI()
app.include_router(auth_router)

Base.metadata.create_all(bind=engine)

pipeline = ScoringPipline()

user_dependency = Annotated[dict, Depends(get_current_user)]
db_dependency = Annotated[Session, Depends(get_db)]


def get_teacher(db: Session, account_id: int) -> Teacher:
    teacher = db.execute(
        select(Teacher).where(Teacher.account_id == account_id)
    ).scalars().first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Giáo viên không tồn tại")
    return teacher


def count_by_status(db: Session, teacher_id: int, status: str) -> int:
    return db.execute(
        select(func.count()).select_from(Essay).where(
            Essay.teacher_id == teacher_id,
            Essay.status == status,
        )
    ).scalar() or 0


# ── Page routes ──────────────────────────────────────────────────────────────

@app.get("/api/me")
async def get_me(user: user_dependency, db: db_dependency):
    teacher = get_teacher(db, user["user_id"])
    return {"name": teacher.name, "id": teacher.id}


@app.get("/login")
def sign_in():
    return FileResponse("templates/login.html")


@app.get("/dashboard")
async def access_dashboard():
    return FileResponse("templates/dashboard.html")


@app.get("/detail/{essay_id}")
async def access_detail():
    return FileResponse("templates/detail.html")


# ── Request / Response models ────────────────────────────────────────────────

class ScoringRequest(BaseModel):
    id: int
    prompt: str
    essay: str


class AllScoringRequest(BaseModel):
    ids: list[int]
    prompts: list[str]
    essays: list[str]


class GradeRequest(BaseModel):
    content: float
    language: float
    organization: float
    comment: str | None = None


class EssaySummary(BaseModel):
    id: int
    student_id: int
    student_name: str
    status: str
    total: float | None
    content: float | None
    language: float | None
    organization: float | None

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list[EssaySummary]
    total: int
    page: int
    per_page: int
    total_pages: int


class DashboardStats(BaseModel):
    total: int
    unscored: int
    ai_scored: int
    reviewed: int
    avg_score: float | None


class DashboardResponse(BaseModel):
    stats: DashboardStats
    essays: PaginatedResponse


class StudentInfo(BaseModel):
    id: int
    name: str


class EssayDetail(BaseModel):
    id: int
    prompt: str
    essay: str
    content: float | None
    language: float | None
    organization: float | None
    total: float | None
    comment: str | None
    status: str
    student: StudentInfo
    has_pdf: bool


# ── Dashboard API ────────────────────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(
    user: user_dependency,
    db: db_dependency,
    status: str = Query("all", pattern=r"^(all|unscored|ai_scored|reviewed|scored)$"),
    search: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=5, le=100),
):
    teacher = get_teacher(db, user["user_id"])

    total_all = db.execute(
        select(func.count()).select_from(Essay).where(Essay.teacher_id == teacher.id)
    ).scalar()

    unscored_count = count_by_status(db, teacher.id, "unscored")
    ai_scored_count = count_by_status(db, teacher.id, "ai_scored")
    reviewed_count = count_by_status(db, teacher.id, "reviewed")

    avg_score = db.execute(
        select(func.avg(Essay.total)).where(
            Essay.teacher_id == teacher.id,
            Essay.status != "unscored",
        )
    ).scalar()

    base_query = select(Essay).where(Essay.teacher_id == teacher.id)

    if status == "unscored":
        base_query = base_query.where(Essay.status == "unscored")
    elif status == "ai_scored":
        base_query = base_query.where(Essay.status == "ai_scored")
    elif status == "reviewed":
        base_query = base_query.where(Essay.status == "reviewed")
    elif status == "scored":
        base_query = base_query.where(Essay.status != "unscored")

    if search:
        base_query = base_query.join(Student, Essay.student_id == Student.id)
        if search.isdigit():
            base_query = base_query.where(Student.id == int(search))
        else:
            base_query = base_query.where(Student.name.ilike(f"%{search}%"))

    count_query = select(func.count()).select_from(base_query.subquery())
    filtered_total = db.execute(count_query).scalar()

    total_pages = max(1, math.ceil(filtered_total / per_page))
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    rows = db.execute(
        base_query
        .join(Student, Essay.student_id == Student.id)
        .add_columns(Student.name)
        .order_by(Essay.id)
        .offset(offset)
        .limit(per_page)
    ).all()

    items = [
        EssaySummary(
            id=row[0].id,
            student_id=row[0].student_id,
            student_name=row[1],
            status=row[0].status,
            total=row[0].total,
            content=row[0].content,
            language=row[0].language,
            organization=row[0].organization,
        )
        for row in rows
    ]

    return DashboardResponse(
        stats=DashboardStats(
            total=total_all,
            unscored=unscored_count,
            ai_scored=ai_scored_count,
            reviewed=reviewed_count,
            avg_score=round(avg_score, 2) if avg_score else None,
        ),
        essays=PaginatedResponse(
            items=items,
            total=filtered_total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
        ),
    )


# ── Essay detail API ─────────────────────────────────────────────────────────

@app.get("/api/essays/{essay_id}", response_model=EssayDetail)
async def get_essay_detail(
    essay_id: int,
    user: user_dependency,
    db: db_dependency,
):
    teacher = get_teacher(db, user["user_id"])

    row = db.execute(
        select(Essay, Student.name)
        .join(Student, Essay.student_id == Student.id)
        .where(Essay.id == essay_id, Essay.teacher_id == teacher.id)
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Bài luận không tồn tại")

    essay = row[0]
    student_name = row[1]

    return EssayDetail(
        id=essay.id,
        prompt=essay.prompt,
        essay=essay.essay,
        content=essay.content,
        language=essay.language,
        organization=essay.organization,
        total=essay.total,
        comment=essay.comment,
        status=essay.status,
        student=StudentInfo(id=essay.student_id, name=student_name),
        has_pdf=essay.pdf is not None,
    )


@app.get("/api/essays/{essay_id}/pdf")
async def get_essay_pdf(
    essay_id: int,
    db: db_dependency,
    token: str = Query(None),
):
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        account_id = payload.get("id")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not account_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    teacher = get_teacher(db, account_id)

    essay = db.execute(
        select(Essay).where(Essay.id == essay_id, Essay.teacher_id == teacher.id)
    ).scalars().first()

    if not essay:
        raise HTTPException(status_code=404, detail="Bài luận không tồn tại")
    if not essay.pdf:
        raise HTTPException(status_code=404, detail="Không có file PDF")

    return Response(
        content=essay.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=essay_{essay_id}.pdf"},
    )


@app.post("/api/essays/{essay_id}/grade")
async def save_essay_grade(
    essay_id: int,
    grade: GradeRequest,
    user: user_dependency,
    db: db_dependency,
):
    teacher = get_teacher(db, user["user_id"])

    essay = db.execute(
        select(Essay).where(Essay.id == essay_id, Essay.teacher_id == teacher.id)
    ).scalars().first()

    if not essay:
        raise HTTPException(status_code=404, detail="Bài luận không tồn tại")

    essay.content = grade.content
    essay.language = grade.language
    essay.organization = grade.organization
    essay.total = grade.content + grade.language + grade.organization
    essay.comment = grade.comment
    essay.status = "reviewed"

    db.commit()

    return {"status": "success", "message": "Đã lưu điểm thành công"}


# ── AI Scoring endpoints ─────────────────────────────────────────────────────

@app.post("/score_one", status_code=201)
async def score_one_essay(
    user: user_dependency,
    scoring_request: ScoringRequest,
    db: db_dependency,
):
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication Failed")

    content, language, organization = pipeline.predict(
        scoring_request.prompt, scoring_request.essay
    )

    essay = db.execute(
        select(Essay).where(Essay.id == scoring_request.id)
    ).scalars().first()

    if not essay:
        raise HTTPException(status_code=404, detail="Bài luận không tồn tại")

    essay.content = round(float(content), 2)
    essay.language = round(float(language), 2)
    essay.organization = round(float(organization), 2)
    essay.total = essay.content + essay.language + essay.organization
    essay.status = "ai_scored"

    db.commit()

    return {
        "status": "success",
        "message": "Scored and saved successfully",
        "scores": {
            "content": essay.content,
            "language": essay.language,
            "organization": essay.organization,
            "total": essay.total,
        },
    }


@app.post("/score_all")
async def score_all_essays(
    user: user_dependency,
    all_scoring_request: AllScoringRequest,
    db: db_dependency,
):
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication Failed")

    scored_count = 0
    error_ids = []

    for essay_id, prompt, essay_text in zip(
        all_scoring_request.ids,
        all_scoring_request.prompts,
        all_scoring_request.essays,
    ):
        try:
            content, language, organization = pipeline.predict(prompt, essay_text)

            essay = db.execute(
                select(Essay).where(Essay.id == essay_id)
            ).scalars().first()

            if essay:
                essay.content = round(float(content), 2)
                essay.language = round(float(language), 2)
                essay.organization = round(float(organization), 2)
                essay.total = essay.content + essay.language + essay.organization
                essay.status = "ai_scored"
                scored_count += 1
            else:
                error_ids.append(essay_id)
        except Exception:
            error_ids.append(essay_id)

    db.commit()

    return {
        "status": "success",
        "message": f"Scored {scored_count} essays successfully",
        "scored_count": scored_count,
        "error_ids": error_ids,
    }
