import os
import re
import hmac
import hashlib
import base64
import json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from ..database import SessionLocal, User, LineLinkCode
from ..services.line_service import _reply

router = APIRouter(prefix="/webhook", tags=["line"])

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")


def _verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET or CHANNEL_SECRET.strip() in ("", "..."):
        return True  # Dev mode
    digest = hmac.new(
        CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


@router.post("/line")
async def line_webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    if not _verify_signature(body, signature):
        raise HTTPException(400, "Invalid signature")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    for event in data.get("events", []):
        event_type = event.get("type")
        source = event.get("source", {})
        line_user_id = source.get("userId", "")
        reply_token = event.get("replyToken", "")

        if event_type == "follow":
            _reply(reply_token,
                "สวัสดีครับ! บริการรายงานตัว 90 วัน 🇹🇭\n\n"
                "วิธีเชื่อมบัญชี:\n"
                "1. สมัครสมาชิกที่เว็บของเรา\n"
                "2. ระบบจะออกโค้ด 6 หลักให้\n"
                "3. ส่งโค้ด 6 หลักนั้นมาที่นี่เลยครับ"
            )

        elif event_type == "message":
            msg = event.get("message", {})
            if msg.get("type") == "text":
                text = msg.get("text", "").strip()
                if re.match(r"^\d{6}$", text):
                    _handle_link_code(line_user_id, text, reply_token)
                else:
                    _reply(reply_token,
                        "ส่งโค้ด 6 หลักจากเว็บมาที่นี่ได้เลยครับ 👆\n\n"
                        "ยังไม่มีบัญชี? สมัครได้ที่เว็บของเราครับ"
                    )

    return JSONResponse({"message": "ok"})


def _handle_link_code(line_user_id: str, code: str, reply_token: str):
    from datetime import datetime
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()

        # ตรวจสอบโค้ด
        link = db.query(LineLinkCode).filter(
            LineLinkCode.code == code,
            LineLinkCode.is_used == False,
            LineLinkCode.expires_at > now,
        ).first()

        if not link:
            _reply(reply_token, "โค้ดไม่ถูกต้องหรือหมดอายุแล้วครับ\nกรุณาสมัครใหม่หรือติดต่อแอดมิน")
            return

        user = db.query(User).filter(User.id == link.user_id).first()
        if not user:
            _reply(reply_token, "ไม่พบบัญชีผู้ใช้ครับ")
            return

        # 1 เบอร์ต่อ 1 LINE — ถ้าผูกแล้วห้ามเปลี่ยน
        if user.line_user_id:
            _reply(reply_token, "บัญชีนี้เชื่อม LINE แล้วครับ\nหากมีปัญหากรุณาติดต่อแอดมิน")
            return

        # LINE ID นี้ถูกใช้กับเบอร์อื่นอยู่แล้วหรือไม่
        conflict = db.query(User).filter(
            User.line_user_id == line_user_id,
            User.id != user.id
        ).first()
        if conflict:
            _reply(reply_token, "LINE นี้ผูกกับบัญชีอื่นอยู่แล้วครับ\nกรุณาติดต่อแอดมิน")
            return

        user.line_user_id = line_user_id
        link.is_used = True
        db.commit()

        _reply(reply_token,
            f"✅ เชื่อมบัญชีสำเร็จแล้วครับ!\n"
            f"เบอร์: {user.phone}\n"
            f"ตอนนี้รับ OTP ผ่าน LINE ได้เลยครับ"
        )
    finally:
        db.close()
