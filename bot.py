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


def _find(name):
    try:
        files = os.listdir(".")
    except Exception:
        files = []
    for f in files:
        low = f.lower()
        if name in low and low.endswith((".png", ".jpg", ".jpeg")):
            return f
    return None


PAGE1_IMG = _find("page1")
PAGE2_IMG = _find("page2")

logging.info(f"Files in cwd: {os.listdir('.')}")
logging.info(f"Page1: {PAGE1_IMG}, Page2: {PAGE2_IMG}")

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

PAGE_W, PAGE_H = A4   # 210 × 297 мм

# =============================================================
# РЕЖИМ КАЛИБРОВКИ:
#   True  = рисовать сетку и красные крестики (для настройки)
#   False = рабочий режим — стирать и писать значения
# =============================================================
CALIBRATE = True


# ============ КООРДИНАТЫ ПОЛЕЙ (мм от левого-верхнего угла A4) ============
# (x, y_верх, ширина, высота)
FIELDS = {
    "policy_no":       (36.0,  53.5,  45, 5),
    "proposer_code":   (47.0,  60.0,  40, 5),
    "proposer_name":   (47.0,  66.5,  55, 5),
    "address":         (24.0,  73.0,  50, 5),
    "phone":           (28.0, 100.0,  45, 5),
    "email":           (24.0, 107.0,  55, 5),
    "proposal_date":   (43.0, 113.5,  40, 5),
    "inception_date":  (68.0, 120.0,  40, 5),
    "receipt_no":      (39.0, 133.5,  40, 5),
    "receipt_date":    (39.0, 140.5,  40, 5),
    "service_tax":     (36.0, 147.0,  40, 5),
    "prev_policy_no":  (152.0, 53.5,  45, 5),
    "scheme":          (61.0, 160.5,  60, 5),
    "plan":            (53.0, 168.0,  60, 5),
    "limit":           (57.0, 176.0,  60, 5),
    "period":          (69.0, 153.5,  60, 5),
    "insured_name":    (16.0, 191.5,  60, 5),
    "sex":             (105.0, 191.5, 20, 5),
    "dob":             (150.0, 191.5, 30, 5),
    "id_card":         (192.0, 191.5, 20, 5),
    "barcode_wipe":    (108.0, 108.0, 90, 30),
}

FONT_SIZE = 8.5
FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


# ============ GENERATOR ============
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
        "period": f"{fmt_date(pf)} TO {fmt_date(pt)}",
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


# ============ PARSER ============
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


# ============ PDF BUILDER ============
def _coords(key):
    x_mm, y_mm, w_mm, h_mm = FIELDS[key]
    x = x_mm * mm
    y = PAGE_H - (y_mm + h_mm) * mm
    return x, y, w_mm * mm, h_mm * mm


def _draw_grid(c):
    """Миллиметровая сетка: толстые линии каждые 10 мм, тонкие каждые 5 мм."""
    for x in range(0, 211, 5):
        if x % 10 == 0:
            c.setStrokeColor(colors.HexColor("#F4A0A0"))
            c.setLineWidth(0.4)
        else:
            c.setStrokeColor(colors.HexColor("#F8D0D0"))
            c.setLineWidth(0.15)
        c.line(x * mm, 0, x * mm, PAGE_H)
    for y in range(0, 298, 5):
        if y % 10 == 0:
            c.setStrokeColor(colors.HexColor("#F4A0A0"))
            c.setLineWidth(0.4)
        else:
            c.setStrokeColor(colors.HexColor("#F8D0D0"))
            c.setLineWidth(0.15)
        c.line(0, PAGE_H - y * mm, PAGE_W, PAGE_H - y * mm)
    c.setFillColor(colors.HexColor("#C00000"))
    c.setFont("Helvetica-Bold", 5)
    for x in range(0, 211, 10):
        c.drawString(x * mm + 0.5, PAGE_H - 3 * mm, str(x))
    for y in range(0, 298, 10):
        c.drawString(0.5 * mm, PAGE_H - y * mm - 1.5 * mm, str(y))


def _stamp(c, key, value, size=FONT_SIZE, bold=False):
    x, y, w, h = _coords(key)
    if CALIBRATE:
        cx = x + w / 2
        cy = y + h / 2
        c.setStrokeColor(colors.red)
        c.setLineWidth(1.4)
        c.line(cx - 2 * mm, cy, cx + 2 * mm, cy)
        c.line(cx, cy - 2 * mm, cx, cy + 2 * mm)
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 6)
        c.drawString(x, y + h + 0.6 * mm, key)
    else:
        c.setFillColor(colors.white)
        c.rect(x, y, w, h, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.setFont(FONT_BOLD if bold else FONT_NAME, size)
        c.drawString(x + 0.4 * mm, y + h * 0.30, value)


def _draw_barcode(c, data):
    x, y, w, h = _coords("barcode_wipe")
    if CALIBRATE:
        cx = x + w / 2
        cy = y + h / 2
        c.setStrokeColor(colors.red)
        c.setLineWidth(1.4)
        c.line(cx - 3 * mm, cy, cx + 3 * mm, cy)
        c.line(cx, cy - 3 * mm, cx, cy + 3 * mm)
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 6)
        c.drawString(x, y + h + 0.6 * mm, "BARCODE")
        return

    # Рабочий режим: стираем + рисуем свой штрих-код
    c.setFillColor(colors.white)
    c.rect(x, y, w, h, fill=1, stroke=0)

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

    bx = x + 3 * mm
    by = y + 3 * mm
    bw = w - 6 * mm
    bh = h - 6 * mm
    module_w = bw / len(bits)
    c.setFillColor(colors.black)
    cur = bx
    for b in bits:
        if b:
            c.rect(cur, by, module_w, bh, fill=1, stroke=0)
        cur += module_w


def build_pdf(data):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(""); c.setAuthor(""); c.setSubject("")
    c.setCreator(""); c.setProducer("")

    # -------- PAGE 1 --------
    if PAGE1_IMG:
        logging.info(f"Drawing background from {PAGE1_IMG}")
        c.drawImage(ImageReader(PAGE1_IMG), 0, 0, width=PAGE_W, height=PAGE_H)
    else:
        logging.warning("PAGE1_IMG not found, drawing on white")

    if CALIBRATE:
        _draw_grid(c)

    _stamp(c, "policy_no",      data["policy_no"], bold=True)
    _stamp(c, "proposer_code",  data["proposer_code"])
    _stamp(c, "proposer_name",  data["proposer_name"])
    _stamp(c, "address",        data["address"])
    _stamp(c, "phone",          data["phone"])
    _stamp(c, "email",          data["email"])
    _stamp(c, "proposal_date",  data["proposal_date"])
    _stamp(c, "inception_date", data["inception_date"])
    _stamp(c, "receipt_no",     data["receipt_no"])
    _stamp(c, "receipt_date",   data["receipt_date"])
    _stamp(c, "service_tax",    data["service_tax"])
    _stamp(c, "prev_policy_no", data["prev_policy_no"])
    _stamp(c, "scheme",         data["scheme"])
    _stamp(c, "plan",           data["plan"])
    _stamp(c, "limit",          data["limit"])
    _stamp(c, "period",         data["period"])
    _stamp(c, "insured_name",   data["insured_name"])
    _stamp(c, "sex",            data["sex"])
    _stamp(c, "dob",            data["dob"])
    _stamp(c, "id_card",        data["id_card"])

    _draw_barcode(c, data)
    c.showPage()

    # -------- PAGE 2 --------
    if PAGE2_IMG:
        logging.info(f"Drawing page2 from {PAGE2_IMG}")
        c.drawImage(ImageReader(PAGE2_IMG), 0, 0, width=PAGE_W, height=PAGE_H)
    else:
        logging.warning("PAGE2_IMG not found")
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
    logging.info(f"Page1: {PAGE1_IMG}, Page2: {PAGE2_IMG}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
