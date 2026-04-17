import os
import httpx
from datetime import datetime
from typing import Optional

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_API = "https://api.line.me/v2/bot/message"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _push(line_user_id: str, text: str):
    if not line_user_id or not CHANNEL_ACCESS_TOKEN:
        return
    payload = {
        "to": line_user_id,
        "messages": [{"type": "text", "text": text}],
    }
    with httpx.Client(timeout=10) as client:
        client.post(f"{LINE_API}/push", json=payload, headers=_headers())


def _reply(reply_token: str, text: str):
    if not CHANNEL_ACCESS_TOKEN:
        return
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    with httpx.Client(timeout=10) as client:
        client.post(f"{LINE_API}/reply", json=payload, headers=_headers())


def send_otp_via_line(line_user_id: str, otp: str):
    _push(line_user_id, f"รหัส OTP ของคุณคือ: {otp}\n(หมดอายุใน 5 นาที)")


def send_completion_notification(worker, next_date: Optional[datetime]):
    if not worker.line_user_id:
        return
    if next_date:
        date_str = next_date.strftime("%d/%m/%Y")
        msg = (
            f"เสร็จแล้วครับ! ส่งไปรษณีย์รายงานตัวให้คุณแล้ว\n"
            f"รอบรายงานตัวครั้งต่อไป: {date_str}\n"
            f"ระบบจะแจ้งเตือนคุณล่วงหน้า 15 วันครับ"
        )
    else:
        msg = "เสร็จแล้วครับ! ส่งไปรษณีย์รายงานตัวให้คุณแล้ว"
    _push(worker.line_user_id, msg)


def send_reminder_notification(line_user_id: str, next_date: datetime):
    date_str = next_date.strftime("%d/%m/%Y")
    msg = (
        f"แจ้งเตือน: ครบ 90 วันในอีก 15 วัน\n"
        f"วันครบกำหนดรายงานตัว: {date_str}\n\n"
        f"กรุณายื่นรายงานตัวใหม่ที่เว็บของเราเพื่อดำเนินการล่วงหน้าครับ"
    )
    _push(line_user_id, msg)


def schedule_reminder(worker, next_date: datetime):
    pass  # handled by scheduler.py
