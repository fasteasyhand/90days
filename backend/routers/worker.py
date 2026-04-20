import os
from datetime import datetime
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db, User, ReportRequest, PaymentRequest, LineLinkCode, SessionLocal
from ..dependencies import require_worker
from ..services.claude_service import extract_from_documents, assess_old_report
from ..services.storage_service import save_upload
from ..services.line_service import _push

router = APIRouter(prefix="/worker", tags=["worker"])
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))




@router.post("/dashboard")
async def worker_dashboard_post():
    """ChillPay POST มาที่ return URL — redirect ไป dashboard"""
    return RedirectResponse(url="/worker/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def worker_dashboard(request: Request, user: User = Depends(require_worker), db: Session = Depends(get_db)):
    requests = (
        db.query(ReportRequest)
        .filter(ReportRequest.worker_id == user.id)
        .order_by(ReportRequest.created_at.desc())
        .all()
    )
    return templates.TemplateResponse("worker_dashboard.html", {
        "request": request, "user": user, "reports": requests, "now": datetime.now()
    })


@router.get("/new-report", response_class=HTMLResponse)
def new_report_page(request: Request, user: User = Depends(require_worker)):
    return templates.TemplateResponse("new_report.html", {"request": request, "user": user})


@router.post("/api/new-report")
async def create_report(
    passport_file: UploadFile = File(...),
    visa_file: UploadFile = File(...),
    old_report_file: UploadFile = File(None),
    street: str = Form(...),
    tambol: str = Form(...),
    amphur: str = Form(...),
    province: str = Form(...),
    phone: str = Form(...),
    auth_type: str = Form("self"),   # self | consent | authorized
    user: User = Depends(require_worker),
    db: Session = Depends(get_db),
):
    async def _save(f: UploadFile, name: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        ext = os.path.splitext(f.filename or "")[1] or ".jpg"
        data = await f.read()
        return save_upload(data, str(user.id), f"{name}_{timestamp}{ext}")

    passport_path = await _save(passport_file, "passport")
    visa_path     = await _save(visa_file, "visa")
    has_old = old_report_file and old_report_file.filename
    old_path = await _save(old_report_file, "old_report") if has_old else None

    # Claude ดึงข้อมูลจากเอกสาร
    extracted = await extract_from_documents(passport_path, visa_path)

    # บันทึกประเภทการมอบอำนาจ
    _auth_labels = {"self": "ยื่นให้ตัวเอง", "consent": "ยื่นแทน (เจ้าของยินยอม)", "authorized": "ยื่นแทน (มีสิทธิ์โดยชอบธรรม)"}
    extracted["auth_type"] = auth_type
    extracted["auth_label"] = _auth_labels.get(auth_type, auth_type)

    mailing_address = {"street": street, "tambol": tambol, "amphur": amphur, "province": province, "phone": phone}

    if has_old:
        # มีใบเดิม → AI ประเมินทันที (synchronous) — คืน {"amount": 300|800, "due_date": datetime|None}
        assessed = await assess_old_report(old_path)
        amount = float(assessed["amount"])
        old_due_date = assessed.get("due_date")
    else:
        # ไม่มีใบเดิม = หาย → 800 ทันที
        amount = 800.0
        old_due_date = None

    report = ReportRequest(
        worker_id=user.id,
        case_type="normal" if has_old else "urgent",
        status="pending_payment",
        passport_file=passport_path,
        visa_file=visa_path,
        old_report_file=old_path,
        mailing_address=mailing_address,
        extracted_data=extracted,
        amount_charged=amount,
        next_report_date_extracted=old_due_date,  # วันจากใบเดิม — staff จะ overwrite ด้วยวันจาก receipt
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return JSONResponse({
        "report_id": report.id,
        "amount": int(amount),
        "has_old_report": bool(has_old),
    })


@router.post("/api/line-link-code")
def generate_line_link_code(user: User = Depends(require_worker), db: Session = Depends(get_db)):
    """ออกโค้ดสำหรับเชื่อม LINE — อายุ 10 นาที ใช้ได้ครั้งเดียว"""
    import random, string
    from datetime import timedelta

    # ยกเลิกโค้ดเก่าของ user นี้ที่ยังไม่ได้ใช้
    db.query(LineLinkCode).filter(
        LineLinkCode.user_id == user.id,
        LineLinkCode.is_used == False,
    ).delete()
    db.commit()

    code = "".join(random.choices(string.digits, k=6))
    expires = datetime.utcnow() + timedelta(minutes=10)
    db.add(LineLinkCode(user_id=user.id, code=code, expires_at=expires))
    db.commit()
    return {"code": code, "expires_minutes": 10}


@router.get("/status/{report_id}", response_class=HTMLResponse)
def report_status(request: Request, report_id: int, user: User = Depends(require_worker), db: Session = Depends(get_db)):
    report = db.query(ReportRequest).filter(
        ReportRequest.id == report_id,
        ReportRequest.worker_id == user.id
    ).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    return templates.TemplateResponse("status.html", {"request": request, "user": user, "report": report})


@router.get("/payment/{report_id}", response_class=HTMLResponse)
def payment_page(request: Request, report_id: int, user: User = Depends(require_worker), db: Session = Depends(get_db)):
    report = db.query(ReportRequest).filter(
        ReportRequest.id == report_id,
        ReportRequest.worker_id == user.id
    ).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    payment = db.query(PaymentRequest).filter(
        PaymentRequest.report_request_id == report_id
    ).order_by(PaymentRequest.created_at.desc()).first()
    return templates.TemplateResponse("payment.html", {
        "request": request, "user": user, "report": report, "payment": payment
    })
