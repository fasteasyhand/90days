import os
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db, User, ReportRequest, PaymentRequest, LineLinkCode
from ..dependencies import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    stats = {
        "total_workers": db.query(User).filter(User.role == "worker").count(),
        "total_reports": db.query(ReportRequest).count(),
        "pending": db.query(ReportRequest).filter(ReportRequest.status == "pending_payment").count(),
        "processing": db.query(ReportRequest).filter(ReportRequest.status == "processing").count(),
        "mailing": db.query(ReportRequest).filter(ReportRequest.status == "mailing").count(),
        "completed": db.query(ReportRequest).filter(ReportRequest.status == "completed").count(),
        "revenue": db.query(func.sum(PaymentRequest.amount)).filter(PaymentRequest.status == "paid").scalar() or 0,
    }
    recent_reports = (
        db.query(ReportRequest)
        .join(User, ReportRequest.worker_id == User.id)
        .order_by(ReportRequest.created_at.desc())
        .limit(30)
        .all()
    )
    users = db.query(User).order_by(User.created_at.desc()).limit(50).all()
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, "user": user, "stats": stats,
        "recent_reports": recent_reports, "users": users,
    })


@router.post("/api/set-role")
def set_user_role(
    target_user_id: int = Form(...),
    role: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if role not in ("worker", "staff", "admin"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)
    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)
    target.role = role
    db.commit()
    return {"message": f"อัพเดท role สำเร็จ: {target.phone} → {role}"}


@router.post("/api/gen-line-code")
def gen_line_code(
    target_user_id: int = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """แอดมินออกโค้ดเชื่อม LINE ให้ user คนไหนก็ได้"""
    import random, string
    from datetime import datetime, timedelta

    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        return JSONResponse({"error": "ไม่พบ user"}, status_code=404)

    # ยกเลิกโค้ดเก่า
    db.query(LineLinkCode).filter(
        LineLinkCode.user_id == target.id,
        LineLinkCode.is_used == False,
    ).delete()

    code = "".join(random.choices(string.digits, k=6))
    expires = datetime.utcnow() + timedelta(minutes=30)
    db.add(LineLinkCode(user_id=target.id, code=code, expires_at=expires))
    db.commit()
    return {"code": code, "phone": target.phone, "expires_minutes": 30}


@router.post("/api/set-password")
def set_password(
    target_user_id: int = Form(...),
    password: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """แอดมินตั้ง password ให้ staff/admin"""
    from ..routers.auth import _hash_password
    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        return JSONResponse({"error": "ไม่พบ user"}, status_code=404)
    if target.role not in ("staff", "admin"):
        return JSONResponse({"error": "ตั้ง password ได้เฉพาะ staff/admin"}, status_code=400)
    target.password_hash = _hash_password(password)
    db.commit()
    return {"message": f"ตั้งรหัสผ่านสำเร็จ: {target.phone}"}
