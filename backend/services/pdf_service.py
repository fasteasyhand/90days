"""
Fill TM.47 form using reportlab overlay + pypdf merge.
Coordinates are in PDF points (72 pts = 1 inch), origin = bottom-left of A4 (595 x 842 pts).
⚠️  Run once and visually check output — calibrate X/Y constants below if needed.
"""
import os
from datetime import datetime
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, os.getenv("UPLOAD_DIR", "uploads"))
TEMPLATE_PDF = os.path.join(BASE_DIR, "assets", "tm47_template.pdf")

# ---- Field coordinates (x, y) on A4 page ----
# Calibrate these by running fill_tm47_form() and checking the PDF output
FIELDS = {
    "written_at":       (355, 770),   # เขียนที่
    "date_day":         (340, 748),   # วันที่
    "date_month":       (390, 748),   # เดือน
    "date_year":        (460, 748),   # พ.ศ.
    "full_name":        (165, 697),   # ชื่อ-นามสกุล
    "nationality":      (125, 665),   # สัญชาติ
    "entry_day":        (152, 630),   # วันที่เดินทางเข้า
    "entry_month":      (210, 630),   # เดือน
    "entry_year":       (310, 630),   # พ.ศ.
    "entry_by":         (430, 630),   # พาหนะ
    "passport_no":      (130, 598),   # เลขพาสปอร์ต
    "arrival_card_no":  (365, 598),   # บัตรขาเข้า
    "address_street":   (95,  555),   # ซอย/ถนน
    "tambol":           (270, 533),   # ตำบล
    "amphur":           (415, 533),   # อำเภอ
    "province":         (115, 511),   # จังหวัด
    "phone":            (345, 511),   # โทรศัพท์
}

# Visa type checkbox positions
VISA_TOURIST_X, VISA_TOURIST_Y = 368, 665
VISA_NON_IMM_X, VISA_NON_IMM_Y = 368, 653


def _register_thai_font():
    """ลอง register Sarabun / TH Sarabun ถ้ามี ไม่งั้นใช้ Helvetica"""
    font_candidates = [
        ("THSarabun", os.path.join(BASE_DIR, "assets", "fonts", "THSarabun.ttf")),
        ("Sarabun", os.path.join(BASE_DIR, "assets", "fonts", "Sarabun-Regular.ttf")),
    ]
    for name, path in font_candidates:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name, path))
            return name
    return "Helvetica"


def _create_overlay(extracted: dict, mailing: dict, today: datetime) -> BytesIO:
    font_name = _register_thai_font()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont(font_name, 11)

    def draw(field: str, text: str):
        if field in FIELDS and text:
            x, y = FIELDS[field]
            c.drawString(x, y, str(text))

    # Today's date
    draw("written_at", "Bangkok")
    draw("date_day",   today.strftime("%d"))
    draw("date_month", today.strftime("%B"))
    draw("date_year",  str(today.year + 543))  # CE → BE

    # From extracted_data
    draw("full_name",       extracted.get("full_name", ""))
    draw("nationality",     extracted.get("nationality", ""))
    draw("entry_day",       extracted.get("entry_date_day", ""))
    draw("entry_month",     extracted.get("entry_date_month", ""))
    draw("entry_year",      extracted.get("entry_date_year", ""))
    draw("entry_by",        extracted.get("entry_by", ""))
    draw("passport_no",     extracted.get("passport_no", ""))
    draw("arrival_card_no", extracted.get("arrival_card_no", ""))

    # Visa type checkbox
    visa_type = extracted.get("visa_type", "").upper()
    if "NON" in visa_type:
        c.drawString(VISA_NON_IMM_X, VISA_NON_IMM_Y, "✓")
    else:
        c.drawString(VISA_TOURIST_X, VISA_TOURIST_Y, "✓")

    # From mailing_address
    draw("address_street", mailing.get("street", ""))
    draw("tambol",         mailing.get("tambol", ""))
    draw("amphur",         mailing.get("amphur", ""))
    draw("province",       mailing.get("province", ""))
    draw("phone",          mailing.get("phone", ""))

    c.save()
    buf.seek(0)
    return buf


def fill_tm47_form(report, worker) -> str:
    """
    กรอก ตม.47 PDF แล้ว save ใน uploads/
    Returns path ของ PDF ที่กรอกแล้ว
    """
    extracted = report.extracted_data or {}
    mailing = report.mailing_address or {}
    today = datetime.now()

    overlay_buf = _create_overlay(extracted, mailing, today)

    writer = PdfWriter()

    if os.path.exists(TEMPLATE_PDF):
        # Merge overlay onto template
        template = PdfReader(TEMPLATE_PDF)
        overlay = PdfReader(overlay_buf)
        page = template.pages[0]
        page.merge_page(overlay.pages[0])
        writer.add_page(page)
    else:
        # ไม่มี template — ใช้ overlay ล้วนๆ (text only, no background)
        overlay = PdfReader(overlay_buf)
        writer.add_page(overlay.pages[0])

    out_dir = os.path.join(UPLOAD_DIR, str(report.worker_id), "forms")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"TM47_{report.id}_{today.strftime('%Y%m%d%H%M%S')}.pdf")

    with open(out_path, "wb") as f:
        writer.write(f)

    return out_path
