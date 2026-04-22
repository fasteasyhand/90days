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


@router.post("/api/delete-report")
def delete_report(
    report_id: int = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """แอดมินลบรายการงาน (สำหรับลบข้อมูล demo/test)"""
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        return JSONResponse({"error": "ไม่พบรายการ"}, status_code=404)
    # ลบ PaymentRequest ที่ผูกกันด้วย (ถ้ามี)
    db.query(PaymentRequest).filter(PaymentRequest.report_request_id == report_id).delete()
    db.delete(report)
    db.commit()
    return {"message": f"ลบรายการ #{report_id} สำเร็จ"}


@router.post("/api/delete-report-range")
def delete_report_range(
    id_from: int = Form(...),
    id_to: int = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """ลบ report หลายตัวตามช่วง id (inclusive ทั้งสองฝั่ง)"""
    ids = [r.id for r in db.query(ReportRequest).filter(
        ReportRequest.id >= id_from, ReportRequest.id <= id_to
    ).all()]
    if not ids:
        return {"message": "ไม่พบรายการในช่วงนั้น", "deleted": 0}
    db.query(PaymentRequest).filter(PaymentRequest.report_request_id.in_(ids)).delete(synchronize_session=False)
    db.query(ReportRequest).filter(ReportRequest.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"message": f"ลบ {len(ids)} รายการ (#{id_from}–#{id_to})", "deleted": len(ids), "ids": ids}


@router.post("/api/delete-demo-reports")
def delete_demo_reports(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """ลบข้อมูลเดโมทั้งหมด (id > 1000)"""
    demo_ids = [r.id for r in db.query(ReportRequest).filter(ReportRequest.id > 1000).all()]
    if not demo_ids:
        return {"message": "ไม่มีข้อมูลเดโมให้ลบ", "deleted": 0}
    db.query(PaymentRequest).filter(PaymentRequest.report_request_id.in_(demo_ids)).delete(synchronize_session=False)
    db.query(ReportRequest).filter(ReportRequest.id.in_(demo_ids)).delete(synchronize_session=False)
    db.commit()
    return {"message": f"ลบข้อมูลเดโม {len(demo_ids)} รายการ", "deleted": len(demo_ids), "ids": demo_ids}


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
