import os
import base64
import re
from datetime import datetime
from typing import Optional
import anthropic

_api_key = os.getenv("CLAUDE_API_KEY", "")
_claude_enabled = bool(_api_key and not _api_key.startswith("sk-ant-..."))
client = anthropic.Anthropic(api_key=_api_key) if _claude_enabled else None


def _encode_image(path_or_url: str) -> tuple[str, str]:
    """รองรับทั้ง local path และ Cloudinary URL"""
    clean = path_or_url.split("?")[0].split("#")[0]
    ext = os.path.splitext(clean)[1].lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}
    media_type = media_map.get(ext, "image/jpeg")

    if path_or_url.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(path_or_url, timeout=30) as resp:
            raw = resp.read()
    else:
        with open(path_or_url, "rb") as f:
            raw = f.read()

    data = base64.standard_b64encode(raw).decode("utf-8")
    return data, media_type


async def extract_from_documents(passport_path: str, visa_path: str) -> dict:
    """Extract ชื่อ-นามสกุลจากพาสปอร์ต"""
    if not _claude_enabled:
        print("\n[DEV] Claude API ไม่ได้ตั้งค่า — ใช้ข้อมูลจำลอง\n")
        return {"full_name": "DEV MOCK NAME"}

    passport_data, passport_media = _encode_image(passport_path)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Look at this passport image.\n"
                        "Extract the full name exactly as printed (surname and given name).\n"
                        "Return ONLY valid JSON, nothing else:\n"
                        '{"full_name": "SURNAME GIVEN NAME"}'
                    ),
                },
                {"type": "image", "source": {"type": "base64", "media_type": passport_media, "data": passport_data}},
            ],
        }],
    )

    text = response.content[0].text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"```$", "", text).strip()

    import json
    try:
        return json.loads(text)
    except Exception:
        return {"full_name": text.strip('"{}').replace('"full_name":', '').strip().strip('"')}


async def extract_full_tm47_data(passport_path: str, visa_path: str) -> dict:
    """
    Extract ข้อมูลทั้งหมดจาก passport + visa สำหรับกรอก TM47
    คืน dict ที่มีครบ 11 fields
    """
    if not _claude_enabled:
        print("\n[DEV] Claude API ไม่ได้ตั้งค่า — ใช้ข้อมูลจำลอง TM47\n")
        return {
            "passport_no": "A12345678", "nationality": "MMR",
            "surname": "MOCK", "given_name": "WORKER", "middle_name": "",
            "gender": "M", "dob_day": 1, "dob_month": 1, "dob_year": 1990,
            "arrival_date": "01/01/2024", "visa_expire": "01/01/2025",
        }

    passport_data, passport_media = _encode_image(passport_path)
    visa_data, visa_media = _encode_image(visa_path)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Extract information from these passport and visa/arrival card images for Thai TM47 form.\n"
                        "Return ONLY valid JSON with these exact fields:\n"
                        "{\n"
                        '  "passport_no": "passport number",\n'
                        '  "nationality": "3-letter code e.g. MMR/KHM/LAO",\n'
                        '  "surname": "SURNAME IN CAPS",\n'
                        '  "given_name": "GIVEN NAME IN CAPS",\n'
                        '  "middle_name": "MIDDLE NAME or empty string",\n'
                        '  "gender": "M or F",\n'
                        '  "dob_day": 1,\n'
                        '  "dob_month": 1,\n'
                        '  "dob_year": 1990,\n'
                        '  "arrival_date": "DD/MM/YYYY",\n'
                        '  "visa_expire": "DD/MM/YYYY"\n'
                        "}\n"
                        "For arrival_date: entry date to Thailand — look for the date stamp in the CENTER or BOTTOM-MIDDLE of the visa/arrival stamp frame (this is when the stamp was applied).\n"
                        "For visa_expire: the 'permitted to stay until' / 'APPLICATION OF STAY IS PERMITTED UP TO' date — this appears in the TOP-RIGHT corner of the visa stamp frame, NOT the center/bottom date. The top-right date is always LATER than the center date. If you see two dates and one is in the top-right and another in the center, visa_expire = top-right date, arrival_date = center date.\n"
                        "Both dates must be in DD/MM/YYYY format. Convert month names (e.g. 'FEB' → 02, 'JUL' → 07). Years are Gregorian (e.g. 2027, not 2570).\n"
                        "Return only valid JSON, no explanation."
                    ),
                },
                {"type": "image", "source": {"type": "base64", "media_type": passport_media, "data": passport_data}},
                {"type": "image", "source": {"type": "base64", "media_type": visa_media, "data": visa_data}},
            ],
        }],
    )

    text = response.content[0].text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"```$", "", text).strip()

    import json
    try:
        return json.loads(text)
    except Exception:
        return {}


async def assess_old_report(old_report_path: str) -> dict:
    """
    อ่านวันนัดจากใบรายงานตัวเดิม (ตม.47)
    หาวันที่ใต้ข้อความ 'PLEASE NOTIFY YOUR ADDRESS AGAIN ON'

    กฎ: รายงานได้ช่วง [due_date - 15 วัน] ถึง [due_date + 7 วัน]
      - ยังอยู่ในช่วง → 300 บาท
      - เกิน due_date + 7 วันแล้ว → 800 บาท (เกินกำหนด)
      - หาวันไม่เจอ → 800 บาท

    Returns: {"amount": 300|800, "due_date": datetime|None}
    """
    from datetime import timedelta

    if not _claude_enabled:
        print("\n[DEV] Claude API ไม่ได้ตั้งค่า — ใช้วันจำลอง -5 วัน (ยังอยู่ในกำหนด)\n")
        mock_due = datetime.utcnow() - timedelta(days=5)   # เกินมา 5 วัน → ยังอยู่ใน grace 7 วัน → 300
        return {"amount": 300, "due_date": mock_due}

    data, media_type = _encode_image(old_report_path)
    today = datetime.utcnow()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=32,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "This is a Thai TM.47 immigration form.\n"
                        "Find the date under or after 'PLEASE NOTIFY YOUR ADDRESS AGAIN ON'.\n"
                        "Reply with ONLY this line (nothing else):\n"
                        "DATE: DD/MM/YYYY\n"
                        "If you cannot find the date, reply: DATE: unknown"
                    ),
                },
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
            ],
        }],
    )

    text = response.content[0].text.strip()

    # Parse วัน
    due_date = None
    date_match = re.search(r"DATE:\s*(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if date_match:
        day, month, year = date_match.groups()
        try:
            due_date = datetime(int(year), int(month), int(day))
        except ValueError:
            pass

    # ── Business logic (Python ทำเอง — แม่นยำกว่าให้ AI คิด) ──
    # ช่วงที่รายงานได้: [due_date - 15 วัน] ถึง [due_date + 7 วัน]
    # เกิน due_date + 7 วัน = เกินกำหนด = 800
    if due_date is None:
        amount = 800  # หาวันไม่เจอ = เก็บ 800
    elif today > due_date + timedelta(days=7):
        amount = 800  # เกิน grace period 7 วัน
    else:
        amount = 300  # อยู่ในช่วงปกติ (ก่อนกำหนด / ก่อนครบ grace)

    return {"amount": amount, "due_date": due_date}


async def extract_next_report_date(receipt_path: str) -> Optional[datetime]:
    """Extract วันครบกำหนดถัดไปจากรูปใบ ตม.47 ที่ ตม. ประทับตราคืน"""
    if not _claude_enabled:
        print("\n[DEV] Claude API ไม่ได้ตั้งค่า — ใช้วันครบกำหนดจำลอง (+90 วัน)\n")
        from datetime import timedelta
        return datetime.utcnow() + timedelta(days=90)

    data, media_type = _encode_image(receipt_path)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "This is a Thai TM.47 immigration form receipt (ใบรับแจ้ง).\n"
                            "Find the date stamped by the immigration officer in the 'FOR OFFICIAL USE ONLY' section.\n"
                            "This is the date the alien must report next (PLEASE NOTIFY YOUR ADDRESS AGAIN ON ...).\n"
                            "Return ONLY the date in format DD/MM/YYYY (Christian Era). "
                            "If you cannot find it, return null.\n"
                            "Example: 15/07/2025"
                        ),
                    },
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                ],
            }
        ],
    )

    text = response.content[0].text.strip()
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        day, month, year = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            return None
    return None
