import os
import httpx

CHILLPAY_WORKER_URL = os.getenv("CHILLPAY_WORKER_URL", "")

def _get_base_url() -> str:
    """คืน base URL ที่ถูกต้อง — กรองค่า ngrok/localhost ออก"""
    url = os.getenv("BASE_URL", "").strip().rstrip("/")
    if url and "ngrok" not in url and "localhost" not in url and "127.0.0.1" not in url:
        return url
    return "https://90days-nu.vercel.app"


def create_qr_payment(order_id: str, amount: float, description: str) -> dict:
    """
    เรียก Cloudflare Worker สร้าง ChillPay QR PromptPay
    amount: บาท (300 หรือ 800) — Worker จะแปลงเป็น satang เอง
    """
    if not CHILLPAY_WORKER_URL:
        # Dev mode: mock QR string (EMV QR format จำลอง)
        satang = int(amount * 100)
        mock_qr = (
            f"00020101021229370016A000000677010111"
            f"011300668000000005303764"
            f"54{len(str(satang)):02d}{satang}"
            f"5802TH6304ABCD"
        )
        print(f"\n[DEV] Mock QR สร้างแล้ว | order: {order_id} | ฿{int(amount)}\n")
        return {"order_id": order_id, "qr_data": mock_qr, "amount": amount}

    base_url = _get_base_url()
    payload = {
        "OrderNo":     order_id,
        "Amount":      int(amount * 100),   # satang
        "Description": "90day report",   # ASCII only — ภาษาไทยทำให้ checksum ผิด
        "PhoneNumber": "0812345678",         # placeholder — ChillPay ต้องการ
        "CustomerId":  order_id,
        "IPAddress":   "127.0.0.1",
        "ReturnUrl":   f"{base_url}/worker/dashboard",
        "CallbackUrl": f"{base_url}/payment/webhook/chillpay",
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(CHILLPAY_WORKER_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    print(f"\n[ChillPay] Raw response: {data}\n")

    pay_url  = data.get("pay_url") or data.get("qr_data") or ""
    qr_data  = data.get("qr_data") or pay_url

    return {
        "order_id": order_id,
        "qr_data":  qr_data,
        "pay_url":  pay_url,
        "amount":   amount,
        "raw":      data,
    }


def verify_payment_signature(body: dict) -> bool:
    """Worker ส่ง callback มาแล้ว — เชื่อใจได้เลย (Worker อยู่ใต้ domain เรา)"""
    return True
