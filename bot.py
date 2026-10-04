import asyncio
import datetime
import hashlib
import io
import logging
import os
import random
import re
import secrets

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from dateutil.relativedelta import relativedelta
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# ============ CONFIG ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _find(*names):
    try:
        files = os.listdir(".")
    except Exception:
        files = []
    for f in files:
        low = f.lower()
        if not low.endswith((".png", ".jpg", ".jpeg")):
            continue
        for n in names:
            if n in low:
                return f
    return None


HEAD1_IMG   = _find("head1", "header1")
HEAD2_IMG   = _find("head2", "header2")
BARCODE_IMG = _find("barcode")
DOCTOR_IMG  = _find("doctor")

logging.info(f"Files in cwd: {os.listdir('.')}")
logging.info(f"Head1: {HEAD1_IMG}, Head2: {HEAD2_IMG}, Barcode: {BARCODE_IMG}, Doctor: {DOCTOR_IMG}")

PLANS = {
    "BASIC":    {"limit": "125,000 GBP", "tax": "330 GBP"},
    "STANDART": {"limit": "295,000 GBP", "tax": "780 GBP"},
    "PRIME":    {"limit": "575,000 GBP", "tax": "1,285 GBP"},
}
SCHEMES = ["1 ADULT", "2 ADULTS", "1 ADULT + 1 CHILD", "FAMILY"]
OPERATOR_NAMES = [
    "Kannan S", "Rajesh M", "Arun K", "Suresh P", "Vijay R",
    "Deepak N", "Anand V", "Prakash T", "Senthil K", "Ramesh B",
    "Karthik J", "Manoj D", "Ganesh L", "Praveen H", "Naveen C",
]

PAGE_W, PAGE_H = A4
MARGIN = 12 * mm

AXA_BLUE   = colors.HexColor("#0F1FA8")
TEXT_BLACK = colors.black

# Высота шапки A4 с сохранением пропорций картинки ~13:1
HEADER_H = 27 * mm


# ============ ГЕНЕРАТОРЫ ============
def gen_policy_no():
    return f"P/{random.randint(700000000,799999999)}/{random.randint(10000,99999)}/{random.randint(1,9)}"

def gen_prev_policy_no(p):
    return f"P/{p.split('/')[1]}/{random.randint(10000,99999)}/{random.randint(1,9)}"

def gen_proposer_code():
    return str(random.randint(100000000, 999999999))

def gen_receipt_no():
    return str(random.randint(1000000000, 9999999999))

def gen_operator():
    n = random.choice(OPERATOR_NAMES)
    return n, n.replace(" ", ".")

def gen_serial():
    return f"{secrets.token_hex(24)}\n{secrets.token_hex(24)}"

def gen_csd():
    return f"CSD/{random.randint(1000,9999)}/{random.randint(1000,9999)}"

def calc_period(d):
    return d, d + relativedelta(months=6)

def parse_date(s):
    return datetime.datetime.strptime(s.strip(), "%d.%m.%Y").date()

def fmt_date(d):
    return d.strftime("%d.%m.%Y")


def build_data(user):
    pn = gen_policy_no()
    inception = parse_date(user["ДАТА НАЧАЛА"])
    pf, pt = calc_period(inception)
    plan = user["ПЛАН"].upper()
    pd = PLANS[plan]
    op_n, op_c = gen_operator()
    return {
        "policy_no": pn,
        "prev_policy_no": gen_prev_policy_no(pn),
        "proposer_code": gen_proposer_code(),
        "proposer_name": user["ФИО"],
        "address": user["АДРЕС"],
        "phone": user["ТЕЛЕФОН"],
        "email": user["EMAIL"],
        "proposal_date": user["ДАТА ПРОПОЗАЛА"],
        "inception_date": user["ДАТА НАЧАЛА"],
        "receipt_no": gen_receipt_no(),
        "receipt_date": user["ДАТА ЧЕКА"],
        "service_tax": pd["tax"],
        "period_from": fmt_date(pf),
        "period_to": fmt_date(pt),
        "scheme": user["СХЕМА"],
        "plan": plan,
        "limit": pd["limit"],
        "insured_name": user["ФИО ЗАСТРАХОВАННОГО"],
        "sex": user["ПОЛ"].upper(),
        "dob": user["ДАТА РОЖДЕНИЯ"],
        "id_card": user["ID КАРТЫ"],
        "operator_name": op_n,
        "operator_cn": op_c,
        "serial": gen_serial(),
        "csd": gen_csd(),
    }


# ============ ПАРСЕР ============
REQUIRED_KEYS = [
    "ФИО", "АДРЕС", "ТЕЛЕФОН", "EMAIL",
    "ДАТА ПРОПОЗАЛА", "ДАТА НАЧАЛА", "ДАТА ЧЕКА",
    "ФИО ЗАСТРАХОВАННОГО", "ДАТА РОЖДЕНИЯ", "ПОЛ",
    "ID КАРТЫ", "ПЛАН", "СХЕМА",
]
ALIASES = {
    "ФИО": ["фио", "имя"],
    "АДРЕС": ["адрес", "address"],
    "ТЕЛЕФОН": ["телефон", "тел", "phone"],
    "EMAIL": ["email", "почта"],
    "ДАТА ПРОПОЗАЛА": ["дата пропозала", "дата пропозал"],
    "ДАТА НАЧАЛА": ["дата начала", "inception"],
    "ДАТА ЧЕКА": ["дата чека", "дата квитанции"],
    "ФИО ЗАСТРАХОВАННОГО": ["фио застрахованного", "застрахованный"],
    "ДАТА РОЖДЕНИЯ": ["дата рождения", "др", "dob"],
    "ПОЛ": ["пол", "sex", "gender"],
    "ID КАРТЫ": ["id карты", "id", "карта"],
    "ПЛАН": ["план", "plan"],
    "СХЕМА": ["схема", "scheme"],
}

def _norm(s):
    return re.sub(r"\s+", " ", s.strip().lower()).replace("ё", "е")

def _key_lookup(raw):
    k = _norm(raw)
    for c, alts in ALIASES.items():
        if k == _norm(c) or k in [_norm(a) for a in alts]:
            return c
    return None

def parse_input(text):
    r = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        c = _key_lookup(k)
        if c:
            r[c] = v.strip()
    missing = [k for k in REQUIRED_KEYS if k not in r or not r[k]]
    if missing:
        raise ValueError(f"Не хватает полей: {', '.join(missing)}")
    _validate(r)
    return r

def _validate(d):
    date_re = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
    for k in ["ДАТА ПРОПОЗАЛА", "ДАТА НАЧАЛА", "ДАТА ЧЕКА", "ДАТА РОЖДЕНИЯ"]:
        if not date_re.match(d[k]):
            raise ValueError(f"{k}: формат дд.мм.гггг")
        try:
            datetime.datetime.strptime(d[k], "%d.%m.%Y")
        except ValueError:
            raise ValueError(f"{k}: некорректная дата")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["EMAIL"]):
        raise ValueError("EMAIL: некорректный")
    if not re.match(r"^\+?\d[\d\s\-]{8,}$", d["ТЕЛЕФОН"]):
        raise ValueError("ТЕЛЕФОН: некорректный")
    if not re.match(r"^[A-Za-z]{2}\d{6,8}$", d["ID КАРТЫ"]):
        raise ValueError("ID КАРТЫ: PE#######")
    if d["ПОЛ"].upper() not in ("MALE", "FEMALE"):
        raise ValueError("ПОЛ: MALE / FEMALE")
    if d["ПЛАН"].upper() not in ("BASIC", "STANDART", "PRIME"):
        raise ValueError("ПЛАН: BASIC / STANDART / PRIME")
    if d["СХЕМА"].upper() not in ("1 ADULT", "2 ADULTS", "1 ADULT + 1 CHILD", "FAMILY"):
        raise ValueError("СХЕМА: 1 ADULT / 2 ADULTS / 1 ADULT + 1 CHILD / FAMILY")


# ============ PDF ============
def _text(c, x, y, txt, size=8.5, bold=False, color=TEXT_BLACK):
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.setFillColor(color)
    c.drawString(x, y, txt)

def _text_label(c, x, y, label, value, size=8.5):
    c.setFont("Helvetica-Bold", size)
    c.setFillColor(TEXT_BLACK)
    c.drawString(x, y, label)
    w = c.stringWidth(label, "Helvetica-Bold", size)
    c.setFont("Helvetica", size)
    c.drawString(x + w + 2, y, value)

def _hline(c, x1, y, x2, width=0.5):
    c.setStrokeColor(AXA_BLUE)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)

def _vline(c, x, y1, y2, width=0.5):
    c.setStrokeColor(AXA_BLUE)
    c.setLineWidth(width)
    c.line(x, y1, x, y2)

def _rect(c, x, y, w, h, width=0.5):
    c.setStrokeColor(AXA_BLUE)
    c.setLineWidth(width)
    c.rect(x, y, w, h)


def build_page1(c, data):
    # ===== Шапка (head1) =====
    header_y = PAGE_H - HEADER_H
    if HEAD1_IMG:
        c.drawImage(ImageReader(HEAD1_IMG), 0, header_y,
                    width=PAGE_W, height=HEADER_H,
                    preserveAspectRatio=False, mask="auto")

    table_top = header_y - 4 * mm
    mid_x = MARGIN + (PAGE_W - 2 * MARGIN) * 0.5
    right_x = PAGE_W - MARGIN
    row_h = 6.5 * mm

    table_h = row_h * 3 + 22 * mm + row_h * 8
    table_bottom = table_top - table_h

    _rect(c, MARGIN, table_bottom, PAGE_W - 2 * MARGIN, table_h)
    _vline(c, mid_x, table_bottom, table_top)

    y = table_top - row_h
    for label, value in [
        ("Policy No.:", data["policy_no"]),
        ("Proposer's Code:", data["proposer_code"]),
        ("Proposer's Name:", data["proposer_name"]),
    ]:
        _text_label(c, MARGIN + 2 * mm, y + 2.2 * mm, label, value)
        _hline(c, MARGIN, y, mid_x)
        y -= row_h

    _text_label(c, MARGIN + 2 * mm, y + 2.2 * mm, "Address:", data["address"])
    y -= 22 * mm
    _hline(c, MARGIN, y, mid_x)

    for label, value in [
        ("Phone No.:", data["phone"]),
        ("E-mail id:", data["email"]),
        ("Proposal date:", data["proposal_date"]),
        ("Date of Inception of first policy:", data["inception_date"]),
        ("Renewal Year:", "NEW"),
        ("Receipt No.:", data["receipt_no"]),
        ("Receipt Date:", data["receipt_date"]),
        ("Service Tax:", data["service_tax"]),
    ]:
        _text_label(c, MARGIN + 2 * mm, y + 2.2 * mm, label, value)
        _hline(c, MARGIN, y, mid_x)
        y -= row_h

    # Правая колонка
    yr = table_top - row_h
    _text_label(c, mid_x + 2 * mm, yr + 2.2 * mm, "Previous Policy No.:", data["prev_policy_no"])
    _hline(c, mid_x, yr, right_x)
    yr -= row_h

    _text_label(c, mid_x + 2 * mm, yr + 2.2 * mm, "E-mail id:", "info@axahealth.co.uk")
    _hline(c, mid_x, yr, right_x)
    yr -= row_h

    _text_label(c, mid_x + 2 * mm, yr + 2.2 * mm, "Issuing Office Name:", "Online Business")
    _hline(c, mid_x, yr, right_x)
    yr -= row_h

    _text(c, mid_x + 2 * mm, yr + 2.2 * mm, "Address:", bold=True)
    yr -= 4 * mm
    for ln in ["20 Gracechurch Street,", "London,", "United Kingdom,", "EC3V 0BG"]:
        _text(c, mid_x + 2 * mm, yr, ln)
        yr -= 4 * mm
    _hline(c, mid_x, yr, right_x)

    # ===== Barcode =====
    bc_x = mid_x + 6 * mm
    bc_y = table_bottom + 6 * mm
    bc_w = right_x - mid_x - 12 * mm
    bc_h = 28 * mm
    if BARCODE_IMG:
        c.drawImage(ImageReader(BARCODE_IMG), bc_x, bc_y,
                    width=bc_w, height=bc_h,
                    preserveAspectRatio=True, anchor="c", mask="auto")
    else:
        text = data["policy_no"].replace("/", "")
        digest = hashlib.sha256(text.encode()).digest()
        bits = [1,1,0,1,0,0,1,1,0]
        for ch in text:
            v = ord(ch)
            for i in range(5, -1, -1):
                b = (v >> i) & 1
                bits.append(b); bits.append(b ^ 1)
        for byte in digest[:4]:
            for i in range(7, -1, -1):
                bits.append((byte >> i) & 1)
        bits.extend([1,1,0,0,1,0,1,1,1,0,1,1])
        module_w = bc_w / len(bits)
        c.setFillColor(colors.black)
        cur = bc_x
        for b in bits:
            if b:
                c.rect(cur, bc_y, module_w, bc_h, fill=1, stroke=0)
            cur += module_w

    # ===== PERIOD =====
    period_y = table_bottom - 6 * mm
    _rect(c, MARGIN, period_y, PAGE_W - 2 * MARGIN, 7 * mm)
    _text_label(c, MARGIN + 2 * mm, period_y + 2.4 * mm,
                "PERIOD OF INSURANCE FROM:",
                f"{data['period_from']} TO {data['period_to']}")

    # ===== SCHEME / PLAN / LIMIT =====
    sp_top = period_y - 8 * mm
    sp_h = 21 * mm
    _rect(c, MARGIN, sp_top - sp_h, PAGE_W - 2 * MARGIN, sp_h)
    line_h = sp_h / 3
    for i, (lbl, val) in enumerate([
        ("SCHEME - DESCRIPTION:", data["scheme"]),
        ("PLAN - DESCRIPTION:", data["plan"]),
        ("LIMIT OF COVERAGE:", data["limit"]),
    ]):
        yy = sp_top - (i + 1) * line_h
        if i > 0:
            _hline(c, MARGIN, yy + line_h, PAGE_W - MARGIN)
        _text_label(c, MARGIN + 2 * mm, yy + 3 * mm, lbl, val, size=9)

    # ===== NAME OF INSURED =====
    ins_top = sp_top - sp_h - 5 * mm
    ins_h = 13 * mm
    _rect(c, MARGIN, ins_top - ins_h, PAGE_W - 2 * MARGIN, ins_h)
    _hline(c, MARGIN, ins_top - 6.5 * mm, PAGE_W - MARGIN)

    col_w = (PAGE_W - 2 * MARGIN) / 4
    for i in [1, 2, 3]:
        _vline(c, MARGIN + i * col_w, ins_top - ins_h, ins_top)

    headers = ["Name of the Insured", "Sex", "Date of Birth", "ID Card No."]
    values = [data["insured_name"], data["sex"], data["dob"], data["id_card"]]
    for i, (h, v) in enumerate(zip(headers, values)):
        cx = MARGIN + i * col_w + col_w / 2
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(TEXT_BLACK)
        c.drawCentredString(cx, ins_top - 4.5 * mm, h)
        c.setFont("Helvetica", 8.5)
        c.drawCentredString(cx, ins_top - 10 * mm, v)

    # ===== Юр.текст =====
    legal_y = ins_top - ins_h - 5 * mm
    legal_lines = [
        "Warranted that in case of dishonour of premium cheque(s), the Company shall not be liable under the policy and the policy shall be",
        "void ab initio (from inception).",
        "THE INSURANCE UNDER THIS POLICY IS SUBJECT TO CONDITIONS, CLAUSES, WARRANTIES, EXCLUSIONS ETC., ATTACHED.",
        "IMPORTANT: IN THE EVENT OF HOSPITALIZATION OF INSURED PERSON, INTIMATION SHOULD BE GIVEN TO THE COMPANY",
        "IMMEDIATELY, HOWEVER, WITHIN 24 HRS FROM THE TIME OF ADMISSION.",
        "In the event of the policy being withdrawn in future, intimation about the withdrawal will be sent 3 months prior to the date when",
        "renewal falls due. The insured will have the option of migrating to any other similar health insurance policy offered by the Company",
        "at the relevant time.",
        "Continuity of benefits for waiting period and bonus, if any and if applicable, will be given provided the insured had been renewing",
        "the policy without any break (or renewing within the grace period offered).",
    ]
    for ln in legal_lines:
        _text(c, MARGIN + 2 * mm, legal_y, ln, size=7.8)
        legal_y -= 3.8 * mm

    # ===== Footer =====
    footer_y = legal_y - 3 * mm
    _hline(c, MARGIN, footer_y, PAGE_W - MARGIN)

    _text(c, MARGIN + 2 * mm, footer_y - 4 * mm, "Entered By", size=6.5)
    _text(c, MARGIN + 2 * mm, footer_y - 7.5 * mm, "STAR PORTAL", size=6.5)
    _text(c, MARGIN + 2 * mm, footer_y - 11 * mm, "IRDA Regn. No 129", size=6.5)
    _text(c, MARGIN + 2 * mm, footer_y - 14.5 * mm, "Corporate Identity Number U66010TN2005PLC056649", size=6.5)
    _text(c, MARGIN + 2 * mm, footer_y - 20 * mm, data["operator_name"], size=6.5)
    _text(c, MARGIN + 2 * mm, footer_y - 23.5 * mm, f"CN={data['operator_cn']}", size=6.5)
    _text(c, MARGIN + 2 * mm, footer_y - 27 * mm, "SERIAL_NUMBER=" + data["serial"].split("\n")[0], size=6)
    _text(c, MARGIN + 2 * mm, footer_y - 30.5 * mm, data["serial"].split("\n")[1], size=6)

    _text(c, mid_x + 2 * mm, footer_y - 4 * mm, "This is an electronically", size=6.5)
    _text(c, mid_x + 2 * mm, footer_y - 7.5 * mm, "generated document", size=6.5)
    _text(c, mid_x + 2 * mm, footer_y - 11 * mm, "(Policy Schedule).", size=6.5)
    _text(c, mid_x + 2 * mm, footer_y - 14.5 * mm, "Consolidated stamp", size=6.5)
    _text(c, mid_x + 2 * mm, footer_y - 18 * mm, "paid vide certificate.", size=6.5)
    _text(c, mid_x + 2 * mm, footer_y - 23 * mm, "No:" + data["csd"], size=6.5)

    _text(c, right_x - 30 * mm, footer_y - 7 * mm, "Authorised signature", size=6.5)
    _hline(c, right_x - 28 * mm, footer_y - 16 * mm, right_x - 4 * mm)


def build_page2(c, data):
    # ===== Шапка (head2) =====
    header_y = PAGE_H - HEADER_H
    if HEAD2_IMG:
        c.drawImage(ImageReader(HEAD2_IMG), 0, header_y,
                    width=PAGE_W, height=HEADER_H,
                    preserveAspectRatio=False, mask="auto")

    # ===== 3 блока Basic / Standard / Prime =====
    blocks = [
        ("Basic", "£125,000", [
            "Coverage Scope: Covers accidental death and permanent disability",
        ]),
        ("Standard", "£295,000", [
            "Coverage Scope: Extends coverage to include work-related injuries and illnesses",
            "Additional Benefits: Includes rehabilitation benefits for work-related injuries",
        ]),
        ("Prime", "£575,000", [
            "Comprehensive coverage for medical expenses, including hospitalization, dental care,",
            "and mental health services",
            "Additional Benefits: Offers coverage for pre-existing conditions and worldwide emergency",
            "medical assistance",
        ]),
    ]

    y = header_y - 8 * mm
    for title, limit, lines in blocks:
        c.setFillColor(AXA_BLUE)
        c.rect(MARGIN, y - 8 * mm, PAGE_W - 2 * MARGIN, 8 * mm, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(MARGIN + 4 * mm, y - 6 * mm, title)

        y -= 12 * mm
        _text(c, MARGIN + 4 * mm, y, f"Coverage up to {limit}", size=10, bold=True)
        y -= 6 * mm
        for ln in lines:
            _text(c, MARGIN + 4 * mm, y, ln, size=9)
            y -= 5 * mm
        y -= 8 * mm

    # ===== Низ: врач =====
    bottom_h = 80 * mm
    if DOCTOR_IMG:
        c.drawImage(ImageReader(DOCTOR_IMG), 0, 0,
                    width=PAGE_W, height=bottom_h,
                    preserveAspectRatio=False, mask="auto")
    else:
        c.setFillColor(colors.HexColor("#E8E9F2"))
        c.rect(0, 0, PAGE_W, bottom_h, fill=1, stroke=0)
        c.setFillColor(AXA_BLUE)
        c.setFont("Helvetica-Bold", 32)
        c.drawString(MARGIN + 4 * mm, bottom_h - 25 * mm, "From")
        c.setFont("Helvetica-Bold", 48)
        c.drawString(MARGIN + 4 * mm, bottom_h - 45 * mm, "£235")
        c.setFont("Helvetica-Bold", 20)
        c.drawString(MARGIN + 4 * mm, bottom_h - 58 * mm, "Medical Plans")
        c.drawString(MARGIN + 4 * mm, bottom_h - 66 * mm, "For Your Family.")


def build_pdf(data):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(""); c.setAuthor(""); c.setSubject("")
    c.setCreator(""); c.setProducer("")

    build_page1(c, data)
    c.showPage()
    build_page2(c, data)
    c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()


# ============ BOT ============
bot = Bot(BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "Привет. Кидай данные одним сообщением, столбиком:\n\n"
        "ФИО: SAMIEV MAKHAMADSADYK\n"
        "АДРЕС: KYRGYZ REPUBLIC\n"
        "ТЕЛЕФОН: +7 964 589 31 55\n"
        "EMAIL: mahamadsamiev590@gmail.com\n"
        "ДАТА ПРОПОЗАЛА: 16.09.2026\n"
        "ДАТА НАЧАЛА: 16.09.2026\n"
        "ДАТА ЧЕКА: 16.09.2026\n"
        "ФИО ЗАСТРАХОВАННОГО: SAMIEV MAKHAMADSADYK\n"
        "ДАТА РОЖДЕНИЯ: 15.02.2006\n"
        "ПОЛ: MALE\n"
        "ID КАРТЫ: PE1649351\n"
        "ПЛАН: STANDART\n"
        "СХЕМА: 1 ADULT"
    )


@dp.message(F.text)
async def handle_input(m: Message):
    try:
        user = parse_input(m.text)
        data = build_data(user)
        pdf_bytes = build_pdf(data)
        fname = f"P{data['policy_no'].replace('/', '')}.pdf"
        await m.answer_document(BufferedInputFile(pdf_bytes, filename=fname))
    except ValueError as e:
        await m.answer(f"Ошибка: {e}")
    except Exception as e:
        logging.exception("render failed")
        await m.answer(f"Внутренняя ошибка: {e}")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    logging.info("Starting bot...")
    logging.info(f"Head1: {HEAD1_IMG}, Head2: {HEAD2_IMG}, Barcode: {BARCODE_IMG}, Doctor: {DOCTOR_IMG}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
