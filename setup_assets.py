"""
รัน script นี้ครั้งเดียวเพื่อ setup assets:
  python setup_assets.py

จะ copy ตม.47 PDF จาก Desktop ไปที่ assets/tm47_template.pdf
และสร้าง fonts/ folder สำหรับ TH Sarabun
"""
import os
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets", "fonts")
os.makedirs(ASSETS_DIR, exist_ok=True)

# Copy TM47 template
src = r"C:\Users\Administrator\Desktop\ตม 47 แบบฟอร์มรายงานตัว 90 วัน.pdf"
dst = os.path.join(BASE_DIR, "assets", "tm47_template.pdf")
if os.path.exists(src) and not os.path.exists(dst):
    shutil.copy2(src, dst)
    print(f"✓ Copied TM47 template → {dst}")
else:
    print(f"{'✓ Already exists' if os.path.exists(dst) else '✗ Source not found'}: {dst}")

# Font note
font_path = os.path.join(ASSETS_DIR, "THSarabun.ttf")
if not os.path.exists(font_path):
    print(f"\n⚠  ไม่พบ Thai font ที่ {font_path}")
    print("   ดาวน์โหลด TH Sarabun จาก f0nt.com แล้ววางไว้ที่ assets/fonts/THSarabun.ttf")
    print("   (ถ้าไม่มีจะใช้ Helvetica แทน แต่ภาษาไทยอาจแสดงไม่ได้)")
else:
    print(f"✓ Font found: {font_path}")

print("\nSetup complete!")
