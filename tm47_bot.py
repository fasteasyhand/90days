"""TM47 Auto-fill Bot — port จาก tm47_auto_v65.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ดึงข้อมูลจาก database แทน Google Sheet

Usage:
  py tm47_bot.py                  # process ทุก job ที่ status='ready_to_submit'
  py tm47_bot.py --id 42          # process เฉพาะ report id 42
  py tm47_bot.py --ids 42 43 44   # process หลาย id
  py tm47_bot.py --dry-run        # โชว์รายการ ไม่รัน bot

Requirements:
  py -m pip install seleniumbase pyautogui sqlalchemy psycopg2-binary python-dotenv

Env:
  DATABASE_URL  — ต้องชี้ไป Supabase Postgres เดียวกับเว็บ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import os
import sys
import time
import math
import random
import argparse
import datetime
from typing import Optional

# ─── Load .env ก่อน import database ───
try:
    from dotenv import load_dotenv
    # override=True เพื่อให้ค่าใน .env ชนะ system env var
    # (Windows อาจมี DATABASE_URL ตัวเก่าฝังอยู่)
    load_dotenv(override=True)
except ImportError:
    pass

import pyautogui
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─── Project DB ───
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.database import SessionLocal, ReportRequest  # noqa: E402

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0

# ==========================================
# CONFIG
# ==========================================
TM47_LOGIN_URL = "https://tm47.immigration.go.th/tm47/#/login"

NATIONALITY_MAP = {
    "KHM": "KHM", "LAO": "LAO", "MMR": "MMR",
}

STEP_DELAY = 3  # 3 วินาที/ขั้น


# ==========================================
# DB helpers (แทน Google Sheet)
# ==========================================
def load_reports(ids: Optional[list[int]] = None) -> list[ReportRequest]:
    """
    ids=None → ดึง job ทั้งหมดที่ status='ready_to_submit'
    ids=[...] → ดึงเฉพาะ id ที่ระบุ (ไม่สนสถานะ)
    """
    db = SessionLocal()
    try:
        q = db.query(ReportRequest)
        if ids:
            q = q.filter(ReportRequest.id.in_(ids))
        else:
            q = q.filter(ReportRequest.status == "ready_to_submit")
        reports = q.order_by(ReportRequest.id.asc()).all()
        # detach จาก session (อ่านอย่างเดียว)
        for r in reports:
            db.expunge(r)
        return reports
    finally:
        db.close()


def mark_submitted(report_id: int):
    """บันทึกสถานะเมื่อยื่นสำเร็จ: status='submitted_online' + tm47_submitted_at=now"""
    db = SessionLocal()
    try:
        r = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
        if not r:
            return
        r.status = "submitted_online"
        r.tm47_submitted_at = datetime.datetime.utcnow()
        if not r.data_confirmed_at:
            r.data_confirmed_at = datetime.datetime.utcnow()
        db.commit()
        print(f"   ✍️  DB: report {report_id} → submitted_online")
    finally:
        db.close()


def report_to_person(r: ReportRequest) -> dict:
    """แปลง ReportRequest → dict เหมือน row ใน Google Sheet"""
    return {
        "report_id":   r.id,
        "passport_no": (r.passport_no or "").strip(),
        "nationality": (r.nationality or "").strip(),
        "surname":     (r.surname or "").strip(),
        "given_name":  (r.given_name or "").strip(),
        "middle_name": (r.middle_name or "").strip(),
        "gender":      (r.gender or "M").strip(),
        "dob_day":     r.dob_day or 1,
        "dob_month":   r.dob_month or 1,
        "dob_year":    r.dob_year or 1990,
        "arrival_date": (r.arrival_date or "").strip(),
        "visa_expire":  (r.visa_expire or "").strip(),
        "address_no":  (r.address_no or "").strip(),
        "road":        (r.road or "").strip(),
        "province":    (r.province or "").strip(),
        "city":        (r.city or "").strip(),
        "district":    (r.district or "").strip(),
        "email":       (r.tm47_email or "").strip(),
        "password":    (r.tm47_password or "").strip(),
    }


# ==========================================
# Tab helpers
# ==========================================
def active(sb):
    return sb.driver.execute_script("return document.activeElement;")


def active_id(sb):
    return sb.driver.execute_script("return document.activeElement.id;")


def tab(sb, n=1, delay=0.4):
    for _ in range(n):
        active(sb).send_keys(Keys.TAB)
        time.sleep(delay)


def type_active(sb, text, delay=0.07):
    el = active(sb)
    for ch in str(text):
        el.send_keys(ch)
        time.sleep(delay)


# ==========================================
# Human-like bezier curve  ← ไม่แตะ
# ==========================================
def human_move_and_click(target_x, target_y):
    start_x, start_y = pyautogui.position()
    cp_x = random.randint(min(start_x, target_x) - 50, max(start_x, target_x) + 50)
    cp_y = random.randint(min(start_y, target_y) - 80, max(start_y, target_y) + 80)
    dist  = math.hypot(target_x - start_x, target_y - start_y)
    steps = max(20, int(dist / 8))
    for i in range(steps + 1):
        t = i / steps
        x = int((1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t ** 2 * target_x)
        y = int((1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t ** 2 * target_y)
        pyautogui.moveTo(x, y)
        speed = 0.5 - abs(t - 0.5)
        time.sleep(random.uniform(0.005, 0.015) * (1 - speed * 0.8))
    time.sleep(random.uniform(0.08, 0.18))
    pyautogui.click(target_x, target_y)


# ==========================================
# Cloudflare  ← ไม่แตะ
# ==========================================
def click_cf_checkbox(sb):
    try:
        iframes = sb.driver.find_elements(
            By.CSS_SELECTOR,
            "iframe[src*='challenges.cloudflare.com'],"
            "iframe[src*='cloudflare.com/cdn-cgi/challenge']"
        )
        if not iframes:
            iframes = sb.driver.find_elements(By.CSS_SELECTOR, "iframe[title*='Widget']")
        if not iframes:
            sb.uc_gui_handle_captcha()
            return
        iframe   = iframes[0]
        rect     = iframe.rect
        scroll_x = sb.driver.execute_script("return window.scrollX;")
        scroll_y = sb.driver.execute_script("return window.scrollY;")
        screen_x = int(rect["x"] - scroll_x + 20 + random.randint(-2, 2))
        screen_y = int(rect["y"] - scroll_y + rect["height"] / 2 + random.randint(-2, 2))
        human_move_and_click(screen_x, screen_y)
    except Exception as e:
        print(f"   ⚠️  click_cf_checkbox: {e}")
        try:
            sb.uc_gui_handle_captcha()
        except Exception:
            pass


def wait_cloudflare(sb, target_url):
    src = sb.driver.page_source
    if "Verify you are human" not in src and "Just a moment" not in src:
        return True
    print("   🔒 ตรวจพบ Cloudflare กดผ่านอัตโนมัติ...")
    for attempt in range(1, 5):
        try:
            sb.uc_open_with_reconnect(target_url, reconnect_time=8)
            time.sleep(random.uniform(2.5, 4.0))
            src2 = sb.driver.page_source
            if "Verify you are human" not in src2 and "Just a moment" not in src2:
                print("   ✅ ผ่าน Cloudflare (reconnect)")
                return True
            time.sleep(random.uniform(0.5, 1.2))
            click_cf_checkbox(sb)
            time.sleep(random.uniform(3.0, 5.0))
            src3 = sb.driver.page_source
            if "Verify you are human" not in src3 and "Just a moment" not in src3:
                print(f"   ✅ ผ่าน Cloudflare (human click รอบ {attempt})")
                return True
            time.sleep(random.uniform(8.0, 12.0))
        except Exception as e:
            print(f"   ⚠️  CF attempt {attempt}: {e}")
            time.sleep(3)
    print("   ❌ กดผ่านอัตโนมัติไม่ได้ รอให้กดเอง (สูงสุด 10 นาที)...")
    for _ in range(300):
        src2 = sb.driver.page_source
        if "Verify you are human" not in src2 and "Just a moment" not in src2:
            print("   ✅ ผ่าน Cloudflare (กดเอง)")
            return True
        time.sleep(2)
    return False


# ==========================================
# Login  ← ไม่แตะ
# ==========================================
def login(sb, email, password):
    print(f"\n🔐 Login: {email}")
    wait = WebDriverWait(sb.driver, 30)

    sb.uc_open_with_reconnect(TM47_LOGIN_URL, reconnect_time=4)
    # 🔒 บังคับขนาด+ตำแหน่งหน้าต่างให้คงที่ เพื่อให้ turnstile click coordinate แม่นยำ
    # (Chrome auto-update หรือ DPI scaling อาจทำให้หน้าต่างเปลี่ยนขนาด)
    try:
        sb.driver.set_window_rect(x=0, y=0, width=1280, height=900)
    except Exception as e:
        print(f"   ⚠️  set_window_rect failed: {e}")
    time.sleep(3)

    if "Verify you are human" in sb.driver.page_source or "Just a moment" in sb.driver.page_source:
        ok = wait_cloudflare(sb, TM47_LOGIN_URL)
        if not ok:
            return False
        time.sleep(2)

    print("   🔒 รอ Cloudflare Turnstile โหลด...")
    time.sleep(random.uniform(1.5, 2.5))
    for attempt in range(1, 6):
        src = sb.driver.page_source
        if "Verify you are human" not in src:
            print("   ✅ ผ่าน Turnstile แล้ว")
            break
        print(f"   🖱️  ติ๊ก Turnstile (attempt {attempt}/5)...")
        click_cf_checkbox(sb)
        time.sleep(random.uniform(3.0, 5.0))
        try:
            WebDriverWait(sb.driver, 8).until(
                EC.presence_of_element_located((By.ID, "mat-input-0"))
            )
        except Exception:
            pass
    else:
        print("   ❌ กดผ่านอัตโนมัติไม่ได้ กรุณาติ๊กด้วยมือ")
        input("   >>> ติ๊กเสร็จแล้วกด Enter...")

    print("   🔒 ด่าน 2 - หา app-turnstile checkbox...")
    time.sleep(random.uniform(1.5, 2.5))

    def click_turnstile_d2():
        selectors = ["app-turnstile", "cf-turnstile", "[class*='turnstile']", "div[id*='cf-chl']"]
        el = None
        for sel in selectors:
            try:
                els = sb.driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    el = els[0]
                    print(f"   found: {sel}")
                    break
            except Exception:
                continue
        if el is None:
            click_cf_checkbox(sb)
            return
        js = ("var r = arguments[0].getBoundingClientRect();"
              "return {left:r.left, top:r.top, width:r.width, height:r.height};")
        bnd = sb.driver.execute_script(js, el)
        win_x    = sb.driver.execute_script("return window.screenX;")
        win_y    = sb.driver.execute_script("return window.screenY;")
        chrome_h = sb.driver.execute_script("return window.outerHeight - window.innerHeight;")
        dpr      = sb.driver.execute_script("return window.devicePixelRatio || 1;")
        cb_ratio = 0.343
        # CSS pixel → physical pixel (ชดเชย Windows DPI scaling 125%/150%)
        screen_x = int((win_x + bnd["left"] + bnd["width"] * cb_ratio) * dpr + random.randint(-3, 3))
        screen_y = int((win_y + chrome_h + bnd["top"] + bnd["height"] / 2) * dpr + random.randint(-3, 3))
        print(f"   click turnstile at ({screen_x}, {screen_y})  dpr={dpr}")
        human_move_and_click(screen_x, screen_y)

    def turnstile_done():
        try:
            val = sb.driver.execute_script(
                "var el = document.querySelector('input[name=cf-turnstile-response]');"
                "return el ? el.value : '';"
            )
            return bool(val and len(val) > 10)
        except Exception:
            return False

    if not turnstile_done():
        for attempt in range(1, 11):
            click_turnstile_d2()
            time.sleep(random.uniform(3.0, 5.0))
            if turnstile_done():
                print(f"   d2 passed (attempt {attempt})")
                break
            print(f"   d2 not passed yet ({attempt}/10)")
        else:
            print("   d2 auto failed - please click manually")
            input("   >>> click done, press Enter...")
    else:
        print("   d2 already passed")

    wait.until(EC.presence_of_element_located((By.ID, "mat-input-0")))
    email_el = sb.driver.find_element(By.ID, "mat-input-0")
    ActionChains(sb.driver).move_to_element(email_el).click().perform()
    time.sleep(0.3)
    for ch in email:
        email_el.send_keys(ch)
        time.sleep(0.05)
    pw_el = sb.driver.find_element(By.ID, "mat-input-1")
    ActionChains(sb.driver).move_to_element(pw_el).click().perform()
    time.sleep(0.3)
    for ch in password:
        pw_el.send_keys(ch)
        time.sleep(0.05)
    time.sleep(0.5)
    sb.driver.find_element(By.CSS_SELECTOR, "button.btn-submit").click()

    for _ in range(30):
        if "#/home" in sb.driver.current_url:
            print("   ✅ Login สำเร็จ")
            time.sleep(1.5)
            try:
                sb.driver.find_element(By.XPATH, "//button[normalize-space()='Close']").click()
                time.sleep(1)
            except Exception:
                pass
            return True
        time.sleep(1)

    print(f"   ❌ Login ล้มเหลว")
    return False


# ==========================================
# เปิดฟอร์ม
# ==========================================
def click_new_application(sb):
    time.sleep(2)
    for _ in range(5):
        try:
            sb.driver.find_element(By.XPATH, "//button[normalize-space()='Close']").click()
            time.sleep(1)
        except Exception:
            break
    sb.driver.get("https://tm47.immigration.go.th/tm47/#/requestfrm/add")
    time.sleep(3)
    WebDriverWait(sb.driver, 30).until(
        EC.presence_of_element_located((By.ID, "mat-input-2"))
    )
    time.sleep(1)
    print("   ✅ เปิดฟอร์ม TM47 สำเร็จ")


# ==========================================
# กรอก Passport + Nationality + Search
# ==========================================
def fill_passport_and_search(sb, person):
    wait = WebDriverWait(sb.driver, 15)

    print(f"   📝 Passport: {person['passport_no']}")
    passport_el = sb.driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='passportNo']")
    passport_el.click()
    time.sleep(0.3)
    type_active(sb, person["passport_no"])
    print(f"   ✅ active={active_id(sb)}")

    raw_nat  = str(person.get("nationality", "")).strip().upper()
    nat_code = NATIONALITY_MAP.get(raw_nat, raw_nat[:3])
    print(f"   📝 Nationality: {nat_code}")
    tab(sb, 1)
    print(f"   ✅ active={active_id(sb)}")
    type_active(sb, nat_code.lower())
    time.sleep(2.0)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "mat-option")))
        print("   ✅ dropdown ขึ้น")
    except Exception:
        print("   ❌ dropdown ไม่ขึ้น")
    time.sleep(0.3)
    active(sb).send_keys(Keys.ARROW_DOWN)
    time.sleep(0.3)
    active(sb).send_keys(Keys.RETURN)
    time.sleep(0.5)

    print("   🔍 Tab ไป Search แล้วกด Enter")
    tab(sb, 1)
    print(f"   ✅ active={active_id(sb)}")
    active(sb).send_keys(Keys.RETURN)
    time.sleep(3)

    surname_val = sb.driver.find_element(By.ID, "mat-input-3").get_attribute("value")
    if surname_val:
        print(f"   ⚠️  พบประวัติเดิม — หยุด")
        return False
    print(f"   ℹ️  ไม่พบประวัติ — กรอกใหม่")
    return True


# ==========================================
# v65 helpers: DOB + Location
# ==========================================
def select_mat_option(sb, wait, mat_select_el, value):
    mat_select_el.click()
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "mat-option")))
    time.sleep(0.5)
    for opt in sb.driver.find_elements(By.CSS_SELECTOR, "mat-option"):
        if opt.text.strip() == str(value):
            opt.click()
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "mat-option")))
            time.sleep(0.3)
            return True
    opts = sb.driver.find_elements(By.CSS_SELECTOR, "mat-option")
    if opts:
        print(f"   ⚠️  ไม่พบ '{value}' ใน options — fallback option แรก")
        opts[0].click()
        wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "mat-option")))
        time.sleep(0.3)
    return False


def fill_autocomplete_by_typing(sb, wait, input_index, value):
    """Fallback: พิมเต็มชื่อลง input แล้วหา mat-option ที่ match แล้วคลิกโดยตรง"""
    value = str(value).strip()
    print(f"   ⚠️  Fallback: พิม '{value}' ลง input[{input_index}]")
    try:
        inputs = sb.driver.find_elements(By.CSS_SELECTOR, "input")
        el = inputs[input_index]
        el.click()
        time.sleep(0.3)
        # เคลียร์ค่าเก่าก่อน
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
        time.sleep(0.2)
        # พิมเต็มชื่อ
        for ch in value:
            el.send_keys(ch)
            time.sleep(0.04)
        time.sleep(0.8)
        # หาตัวเลือกที่ match case-insensitive → คลิก
        target = value.lower()
        options = sb.driver.find_elements(By.CSS_SELECTOR, "mat-option")
        for opt in options:
            if opt.text.strip().lower() == target:
                opt.click(); time.sleep(0.5); return True
        for opt in options:
            if opt.text.strip().lower().startswith(target):
                opt.click(); time.sleep(0.5); return True
        # สุดท้ายจริงๆ: ARROW_DOWN + RETURN
        if options:
            el.send_keys(Keys.ARROW_DOWN); time.sleep(0.2)
            el.send_keys(Keys.RETURN); time.sleep(0.5)
            return True
    except Exception as e:
        print(f"   ❌ fallback error: {e}")
    return False


def click_search_icon_and_select(sb, wait, icon_index, value):
    icons = sb.driver.find_elements(By.CSS_SELECTOR, "mat-icon.cursor-icon")
    icons[icon_index].click()
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "mat-option")))
    time.sleep(0.5)
    target = str(value).strip().lower()
    # 1) exact case-insensitive
    for opt in sb.driver.find_elements(By.CSS_SELECTOR, "mat-option"):
        if opt.text.strip().lower() == target:
            opt.click()
            time.sleep(0.5)
            return True
    # 2) startswith case-insensitive (เผื่อมี suffix แปลก ๆ)
    for opt in sb.driver.find_elements(By.CSS_SELECTOR, "mat-option"):
        if opt.text.strip().lower().startswith(target):
            opt.click()
            time.sleep(0.5)
            return True
    print(f"   ⚠️  ไม่พบ '{value}' ใน dropdown — กด Escape")
    try:
        sb.driver.find_elements(By.CSS_SELECTOR, "input")[11 + (icon_index - 1) * 2].send_keys(Keys.ESCAPE)
    except IndexError:
        active(sb).send_keys(Keys.ESCAPE)
    return False


# ==========================================
# กรอก Personal Information
# ==========================================
def fill_personal_info(sb, person):
    wait = WebDriverWait(sb.driver, 15)

    print("   📝 Surname")
    tab(sb, 1)
    time.sleep(STEP_DELAY)
    print(f"   ✅ active={active_id(sb)}")
    type_active(sb, person["surname"])
    time.sleep(1)

    print("   📝 Given Name")
    tab(sb, 1)
    time.sleep(1)
    print(f"   ✅ active={active_id(sb)}")
    type_active(sb, person["given_name"])
    time.sleep(1)

    print("   📝 Middle Name")
    tab(sb, 2)
    time.sleep(1)
    print(f"   ✅ active={active_id(sb)}")
    if person.get("middle_name"):
        type_active(sb, person["middle_name"])
    time.sleep(1)

    print("   📝 Gender")
    tab(sb, 1)
    time.sleep(2)
    is_female = str(person["gender"]).strip().upper() in ["F", "FEMALE"]
    gender_text = "Female" if is_female else "Male"
    gender_select = sb.driver.find_element(By.CSS_SELECTOR, "mat-select[formcontrolname='gender']")
    gender_select.click()
    time.sleep(STEP_DELAY)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "mat-option")))
    time.sleep(1.0)
    options = sb.driver.find_elements(By.CSS_SELECTOR, "mat-option")
    target = None
    for opt in options:
        if opt.text.strip().lower() == gender_text.lower():
            target = opt
            break
    if target:
        target.click()
        print(f"   ✅ เลือก {gender_text} เสร็จ")
    else:
        print(f"   ⚠️  ไม่พบ '{gender_text}' — fallback option แรก")
        options[0].click()
    time.sleep(STEP_DELAY)
    wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "mat-option")))
    time.sleep(0.5)
    tab(sb, 1)
    time.sleep(2)

    # DOB
    print("   📝 DOB")
    all_selects = sb.driver.find_elements(By.CSS_SELECTOR, "mat-select")
    dob_day   = str(int(person["dob_day"]))
    dob_month = str(int(person["dob_month"]))
    dob_year  = str(int(person["dob_year"]))

    print(f"   📝 DOB DD = {dob_day}")
    select_mat_option(sb, wait, all_selects[1], dob_day)
    print(f"   ✅ DD เสร็จ")

    print(f"   📝 DOB MM = {dob_month}")
    all_selects = sb.driver.find_elements(By.CSS_SELECTOR, "mat-select")
    select_mat_option(sb, wait, all_selects[2], dob_month)
    print(f"   ✅ MM เสร็จ")

    print(f"   📝 DOB YYYY = {dob_year}")
    all_selects = sb.driver.find_elements(By.CSS_SELECTOR, "mat-select")
    select_mat_option(sb, wait, all_selects[3], dob_year)
    print(f"   ✅ YYYY เสร็จ")
    time.sleep(1)

    # Arrival Date
    tab(sb, 1)
    time.sleep(2)
    print("   📝 Arrival Date")
    arr = str(person["arrival_date"]).replace("/", "")
    type_active(sb, arr)
    time.sleep(1)

    tab(sb, 2)
    time.sleep(1)
    print("   📝 Visa Expire")
    visa = str(person["visa_expire"]).replace("/", "")
    type_active(sb, visa)
    time.sleep(1)

    tab(sb, 3)
    time.sleep(1)
    print(f"   ✅ Personal Information เสร็จ — focus ที่ Address No")


# ==========================================
# กรอก Address Information
# ==========================================
def fill_address_info(sb, person):
    wait = WebDriverWait(sb.driver, 15)

    print("   📝 Address No.")
    print(f"   ✅ active={active_id(sb)}")
    if person.get("address_no"):
        type_active(sb, person["address_no"])
    time.sleep(1)

    print("   📝 Soi/Road")
    tab(sb, 1)
    time.sleep(1)
    print(f"   ✅ active={active_id(sb)}")
    if person.get("road"):
        type_active(sb, person["road"])
    time.sleep(1)

    tab(sb, 1)
    time.sleep(STEP_DELAY)

    print(f"   📝 Province: {person['province']}")
    ok = click_search_icon_and_select(sb, wait, 1, person["province"])
    if not ok:
        fill_autocomplete_by_typing(sb, wait, 11, person["province"])
    time.sleep(STEP_DELAY)
    print(f"   ✅ Province เสร็จ")

    print(f"   📝 City: {person['city']}")
    time.sleep(1.0)
    ok = click_search_icon_and_select(sb, wait, 2, person["city"])
    if not ok:
        fill_autocomplete_by_typing(sb, wait, 13, person["city"])
    time.sleep(STEP_DELAY)
    print(f"   ✅ City เสร็จ")

    print(f"   📝 District: {person['district']}")
    time.sleep(0.5)
    ok = click_search_icon_and_select(sb, wait, 3, person["district"])
    if not ok:
        fill_autocomplete_by_typing(sb, wait, 15, person["district"])
    time.sleep(1)
    print(f"   ✅ District เสร็จ")

    print("   📝 Use Login Information")
    tab(sb, 3)
    time.sleep(1)
    print(f"   ✅ active={active_id(sb)}")
    active(sb).send_keys(Keys.SPACE)
    time.sleep(1)

    print("   ✅ Address Information เสร็จ")


# ==========================================
# Tick Terms + Submit
# ==========================================
def tick_terms_and_submit(sb, person, auto_submit=False):
    wait = WebDriverWait(sb.driver, 15)

    print("   📝 I acknowledge Terms")
    tab(sb, 4)
    time.sleep(1)
    print(f"   ✅ active={active_id(sb)}")
    active(sb).send_keys(Keys.SPACE)
    time.sleep(1)
    tab(sb, 1)
    time.sleep(1)
    print(f"   ✅ focus อยู่ที่ Submit")

    print(f"\n📋 Report #{person['report_id']} / Passport {person['passport_no']} — กรอกครบแล้ว")

    if auto_submit:
        print("   🤖 auto-submit: กด Submit อัตโนมัติ")
    else:
        print("   'yes' = Submit | 'skip' = ข้าม | 'quit' = หยุด")
        confirm = input("   >>> ").strip().lower()
        if confirm == "quit":
            return "quit"
        if confirm != "yes":
            return "skip"

    print("   🙋 บอทติ๊ก Terms แล้ว — รอให้ผู้ใช้กด Submit เอง (timeout 5 นาที)")

    # รอจน modal "Would you like to submit data?" โผล่ = ผู้ใช้กด Submit แล้ว
    try:
        WebDriverWait(sb.driver, 300).until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[normalize-space()='Confirm']"))
        )
        print("   ✅ ผู้ใช้กด Submit แล้ว — รอต่อให้กด Confirm")
    except Exception:
        print("   ⏱  รอ 5 นาทีแล้วผู้ใช้ยังไม่กด Submit — ยกเลิก")
        return "skip"

    # รอจน modal หาย = ผู้ใช้กด Confirm เรียบร้อย
    try:
        WebDriverWait(sb.driver, 180).until(
            EC.invisibility_of_element_located((By.XPATH,
                "//button[normalize-space()='Confirm']"))
        )
        print("   ✅ ผู้ใช้กด Confirm แล้ว")
    except Exception:
        print("   ⏱  รอ 3 นาทีแล้วผู้ใช้ยังไม่กด Confirm — ยกเลิก")
        return "skip"

    print("   ⏳ รอเว็บ ตม. ประมวลผล 10 วิ...")
    time.sleep(10)
    print("   ✅ Submit + Confirm สำเร็จ!")
    return "submitted"


# ==========================================
# MAIN
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="TM47 Auto-fill Bot (DB-driven)")
    parser.add_argument("--id", type=int, help="process เฉพาะ report id นี้")
    parser.add_argument("--ids", type=int, nargs="+", help="process หลาย report id")
    parser.add_argument("--dry-run", action="store_true", help="โชว์รายการที่จะทำ ไม่เปิด browser")
    parser.add_argument("--auto-submit", action="store_true", help="กด Submit อัตโนมัติ (ไม่ถาม yes/skip/quit)")
    args = parser.parse_args()

    ids = None
    if args.id:
        ids = [args.id]
    elif args.ids:
        ids = args.ids

    print("🚀 TM47 Bot (DB-driven, based on v65)")
    reports = load_reports(ids)
    print(f"📋 พบ {len(reports)} report")

    if not reports:
        print("✅ ไม่มีงาน")
        return

    # ตรวจ tm47_email/password ของแต่ละงาน
    missing_creds = [r for r in reports if not ((r.tm47_email or "").strip() and (r.tm47_password or "").strip())]
    if missing_creds:
        print("⚠️  มี report ที่ไม่มี tm47_email/password — ข้าม:")
        for r in missing_creds:
            print(f"   - #{r.id}  passport={r.passport_no}")
        reports = [r for r in reports if r not in missing_creds]

    if args.dry_run:
        print("\n[DRY RUN] รายการที่จะทำ:")
        for r in reports:
            p = report_to_person(r)
            print(f"  #{p['report_id']}  {p['passport_no']}  {p['surname']} {p['given_name']}  login={p['email']}")
        return

    # จัดกลุ่มตาม email (login ครั้งเดียวต่อ 1 บัญชี)
    from collections import defaultdict
    groups = defaultdict(list)
    for r in reports:
        p = report_to_person(r)
        groups[(p["email"], p["password"])].append(p)

    with SB(uc=True, headed=True) as sb:
        for (email, password), persons in groups.items():
            print(f"\n{'#' * 55}")
            print(f"# LOGIN {email}  —  {len(persons)} รายการ")
            print(f"{'#' * 55}")

            ok = login(sb, email, password)
            if not ok:
                print(f"❌ Login ล้มเหลวสำหรับ {email} — ข้ามกลุ่มนี้")
                continue

            for i, person in enumerate(persons):
                print(f"\n{'=' * 55}")
                print(f"[{i + 1}/{len(persons)}] Report #{person['report_id']}  Passport {person['passport_no']}")

                try:
                    click_new_application(sb)
                    has_no_history = fill_passport_and_search(sb, person)

                    if not has_no_history:
                        print(f"   ⚠️  พบประวัติเดิม — ไม่เปลี่ยนสถานะ DB")
                        try:
                            sb.driver.find_element(
                                By.XPATH, "//button[normalize-space()='Close']"
                            ).click()
                        except Exception:
                            pass
                        continue

                    fill_personal_info(sb, person)
                    fill_address_info(sb, person)

                    result = tick_terms_and_submit(sb, person, auto_submit=args.auto_submit)

                    if result == "quit":
                        print("   ⏸  หยุดตามคำสั่ง quit")
                        return

                    if result == "submitted":
                        mark_submitted(person["report_id"])
                    else:
                        print(f"   ⏭  skip — ไม่เปลี่ยนสถานะ DB")
                        try:
                            sb.driver.find_element(
                                By.XPATH, "//button[normalize-space()='Close']"
                            ).click()
                        except Exception:
                            pass

                except Exception as e:
                    print(f"   ❌ ผิดพลาด: {e}")
                    try:
                        sb.driver.find_element(
                            By.XPATH, "//button[normalize-space()='Close']"
                        ).click()
                    except Exception:
                        pass

                time.sleep(2)

    print("\n✅ เสร็จสิ้นทุกรายการ")


if __name__ == "__main__":
    main()
