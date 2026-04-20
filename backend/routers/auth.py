import os
import random
import string
import hashlib
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db, User, OTPLog, LineLinkCode
from ..dependencies import create_access_token
from ..services.line_service import send_otp_via_line


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{key.hex()}"


def _verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key = hashed.split(":")
        new_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return secrets.compare_digest(new_key.hex(), key)
    except Exception:
        return False

router = APIRouter(tags=["auth"])
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/api/auth/check-role")
def check_role(phone: str, db: Session = Depends(get_db)):
    """Login page ใช้เช็คว่า user เป็น worker หรือ staff/admin"""
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(404, "ไม่พบเบอร์นี้ในระบบ")
    return {"role": user.role, "has_password": bool(user.password_hash)}


@router.post("/api/auth/login-password")
def login_password(
    phone: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Staff/Admin/Worker(demo) login ด้วย password"""
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(400, "ไม่พบบัญชีนี้ในระบบ")
    if not user.password_hash:
        raise HTTPException(400, "ยังไม่ได้ตั้งรหัสผ่าน กรุณาติดต่อแอดมิน")
    if not _verify_password(password, user.password_hash):
        raise HTTPException(400, "รหัสผ่านไม่ถูกต้อง")

    token = create_access_token(user.id, user.role)
    response = JSONResponse({"message": "เข้าสู่ระบบสำเร็จ", "role": user.role})
    response.set_cookie("access_token", token, httponly=True, max_age=604800, samesite="lax")
    return response


@router.post("/api/auth/send-otp")
def send_otp(phone: str = Form(...), db: Session = Depends(get_db)):
    """ส่ง OTP ผ่าน Line OA — ถ้าไม่มี LINE token จะแสดงใน console (dev mode)"""
    import os
    _line_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    dev_mode = not _line_token or _line_token.strip() in ("", "...")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="ไม่พบเบอร์นี้ในระบบ กรุณาลงทะเบียนก่อน")
    if not dev_mode and not user.line_user_id:
        raise HTTPException(status_code=400, detail="บัญชีนี้ยังไม่ได้เชื่อม LINE กรุณาติดต่อแอดมินเพื่อขอโค้ดเชื่อม LINE ใหม่")

    otp = _generate_otp()
    expires = datetime.utcnow() + timedelta(minutes=5)
    db.add(OTPLog(phone=phone, otp_code=otp, expires_at=expires))
    db.commit()

    if dev_mode:
        # DEV MODE — print OTP to console
        print(f"\n{'='*40}\n🔑 DEV OTP  |  {phone}  |  {otp}\n{'='*40}\n")
        return {"message": f"[DEV] OTP คือ {otp} (ดูใน CMD)"}

    send_otp_via_line(user.line_user_id, otp)
    return {"message": "ส่ง OTP ทาง Line แล้ว"}


@router.post("/api/auth/register")
def register(phone: str = Form(...), db: Session = Depends(get_db)):
    """สร้างบัญชีใหม่ — ออกโค้ดเชื่อม LINE ทันทีหลังสมัคร"""
    import random, string
    from datetime import timedelta

    existing = db.query(User).filter(User.phone == phone).first()
    if existing:
        raise HTTPException(status_code=400, detail="เบอร์นี้มีในระบบแล้ว")

    user = User(phone=phone, role="worker", is_verified=False)
    db.add(user)
    db.flush()  # ได้ user.id ก่อน commit

    code = "".join(random.choices(string.digits, k=6))
    expires = datetime.utcnow() + timedelta(minutes=10)
    db.add(LineLinkCode(user_id=user.id, code=code, expires_at=expires))
    db.commit()

    return {
        "message": "ลงทะเบียนสำเร็จ",
        "line_link_code": code,
        "expires_minutes": 10,
    }


@router.post("/api/auth/verify-otp")
def verify_otp(phone: str = Form(...), otp: str = Form(...), db: Session = Depends(get_db)):
    now = datetime.utcnow()
    log = (
        db.query(OTPLog)
        .filter(
            OTPLog.phone == phone,
            OTPLog.otp_code == otp,
            OTPLog.is_used == False,
            OTPLog.expires_at > now,
        )
        .order_by(OTPLog.created_at.desc())
        .first()
    )
    if not log:
        raise HTTPException(status_code=400, detail="OTP ไม่ถูกต้องหรือหมดอายุแล้ว")

    log.is_used = True
    user = db.query(User).filter(User.phone == phone).first()
    user.is_verified = True
    db.commit()

    token = create_access_token(user.id, user.role)
    response = JSONResponse({"message": "เข้าสู่ระบบสำเร็จ", "role": user.role})
    response.set_cookie("access_token", token, httponly=True, max_age=604800, samesite="lax")
    return response


@router.post("/logout")
@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@router.get("/setup-admin")
def setup_admin(phone: str, password: str, secret: str, db: Session = Depends(get_db)):
    """
    One-time admin setup — ใช้ได้เฉพาะตอนที่ยังไม่มี admin ในระบบ
    ลบ endpoint นี้หลังใช้งาน
    """
    import os
    if secret != os.getenv("CRON_SECRET", ""):
        raise HTTPException(403, "Invalid secret")

    existing_admin = db.query(User).filter(User.role == "admin").first()
    if existing_admin:
        raise HTTPException(400, "Admin already exists")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(phone=phone, role="admin", is_verified=True)
        db.add(user)
        db.flush()
    else:
        user.role = "admin"
        user.is_verified = True

    user.password_hash = _hash_password(password)
    db.commit()
    return {"message": f"Admin created: {phone}"}
