import datetime
import asyncio
import os
import json
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv("8013930431:AAH7pPMdsTnmO-IFpmpkZ71pjGobztLeYHE")  # теперь из Railway
SPREADSHEET_NAME = "Kelish vaqti"
UZB_TZ = datetime.timezone(datetime.timedelta(hours=5))

# ===== GOOGLE CREDENTIALS ИЗ PERENV =====
GOOGLE_CREDS = os.getenv("GOOGLE_CREDENTIALS")

if not GOOGLE_CREDS:
    raise Exception("❌ GOOGLE_CREDENTIALS отсутствует в Railway Variables!")

creds_dict = json.loads(GOOGLE_CREDS)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open(SPREADSHEET_NAME).sheet1

# ===== TELEGRAM =====
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# Было: USERS = [] → Делаем словарь username -> chat_id
USERS: dict[str, int] = {}

# ===== УТИЛИТЫ =====
def get_record(username: str, date_str: str):
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):  # 1-я строка — заголовки
        clean_row = {k.strip(): v for k, v in row.items()}
        if clean_row.get("Username") == username and clean_row.get("Дата") == date_str:
            return i, clean_row
    return None, None


def add_row(username, name, date_str, time_str, status, file_id="-", reason="-"):
    next_row = len(sheet.get_all_values()) + 1
    sheet.append_row([next_row - 1, username, name, date_str, time_str, status, file_id, reason])


def _remember_user(message: Message):
    username = message.from_user.username or str(message.from_user.id)
    chat_id = message.from_user.id
    if username not in USERS or USERS[username] != chat_id:
        USERS[username] = chat_id
        print(f"📌 Saved user: {username} -> {chat_id}")


# ===== ОБРАБОТКА ВИДЕО =====
@dp.message(F.content_type.in_({"video", "video_note"}))
async def handle_video(message: Message):
    _remember_user(message)

    username = message.from_user.username or str(message.from_user.id)
    name = message.from_user.first_name
    video = message.video or message.video_note
    file_id = video.file_id

    now = datetime.datetime.now(UZB_TZ)
    date_str = now.strftime("%d.%m.%Y")
    time_str = now.strftime("%H:%M")

    row_idx, record = get_record(username, date_str)

    # Уже отмечен опоздавшим без видео → дополняем
    if record and record.get("Статус") == "Опоздал" and (record.get("Файл видео") in ("", None, "-")):
        sheet.update_cell(row_idx, 5, time_str)  # Время
        sheet.update_cell(row_idx, 7, file_id)   # Файл видео
        await message.answer(f"🎥 Видео принято. Приход после опоздания зарегистрирован в {time_str}.")
        return

    # Уже есть запись (Пришёл/Не пришёл/и т.п.)
    if record:
        await message.answer("✅ Сегодня ты уже зарегистрирован.")
        return

    # Новая запись — Пришёл
    add_row(username, name, date_str, time_str, "Пришёл", file_id)
    await message.answer(f"🎉 Приход зарегистрирован в {time_str}.")


# ===== /be_late =====
@dp.message(F.text.startswith("/be_late"))
async def be_late(message: Message):
    _remember_user(message)

    username = message.from_user.username or str(message.from_user.id)
    name = message.from_user.first_name
    reason = message.text.replace("/be_late", "").strip() or "Без причины"

    now = datetime.datetime.now(UZB_TZ)
    date_str = now.strftime("%d.%m.%Y")

    row_idx, record = get_record(username, date_str)
    if record:
        await message.answer("📅 Сегодня ты уже зарегистрирован.")
        return

    add_row(username, name, date_str, "-", "Опоздал", "-", reason)
    await message.answer(f"⚠️ Опоздание отмечено. Причина: {reason}")


# ===== /dont_come =====
@dp.message(F.text.startswith("/dont_come"))
async def dont_come(message: Message):
    _remember_user(message)

    username = message.from_user.username or str(message.from_user.id)
    name = message.from_user.first_name
    reason = message.text.replace("/dont_come", "").strip() or "Без причины"

    now = datetime.datetime.now(UZB_TZ)
    date_str = now.strftime("%d.%m.%Y")

    row_idx, record = get_record(username, date_str)
    if record:
        await message.answer("📅 Сегодня ты уже зарегистрирован.")
        return

    add_row(username, name, date_str, "-", "Не пришёл", "-", reason)
    await message.answer(f"🚫 Отсутствие отмечено. Причина: {reason}")


# ===== НАПОМИНАНИЯ =====
async def remind_users():
    now = datetime.datetime.now(UZB_TZ)
    weekday = now.weekday()
    if weekday == 6:
        print("🕘 Sunday: skip reminders")
        return

    date_str = now.strftime("%d.%m.%Y")
    records = sheet.get_all_records()
    marked_users = {r.get("Username") for r in records if r.get("Дата") == date_str}

    print(f"⏰ Remind run at {now.strftime('%H:%M')}, users saved: {len(USERS)}")
    for username, chat_id in USERS.items():
        if username not in marked_users:
            try:
                await bot.send_message(
                    chat_id,
                    "⏰ Илонтирув: 10:30 гача видео юборинг ёки /be_late /dont_come сабаб билан.",
                )
                print(f"✔ reminded {username} ({chat_id})")
            except Exception as e:
                print(f"⚠️ remind failed {username} ({chat_id}): {e}")


# ===== ПОСЛЕ 10:30 — 'БЕЗ ПРИЧИНЫ' =====
async def mark_absent_users():
    now = datetime.datetime.now(UZB_TZ)
    weekday = now.weekday()
    if weekday == 6:
        print("🕥 Sunday: skip marking")
        return

    date_str = now.strftime("%d.%m.%Y")
    records = sheet.get_all_records()
    marked_users = {r.get("Username") for r in records if r.get("Дата") == date_str}

    for username, chat_id in USERS.items():
        if username not in marked_users:
            add_row(username, username, date_str, "-", "Не пришёл", "-", "Без причины")
            try:
                await bot.send_message(
                    chat_id,
                    "🚫 Бугун келиш қайд этилмади ва сабаб ҳам берилмади. 'Без причины' деб белгиланди.",
                )
            except:
                pass
            print(f"✍ marked absent: {username}")


# ===== /start =====
@dp.message(F.text == "/start")
async def start_cmd(message: Message):
    _remember_user(message)
    await message.answer(
        "👋 Привет! Этот бот отмечает приход на работу.\n\n"
        "📹 Отправь видео, когда придёшь.\n"
        "⚠️ Если опаздываешь — напиши /be_late причина.\n"
        "🚫 Если не придёшь — /dont_come причина.\n"
        "⏰ Напоминания идут до 10:30 (воскресенье — выходной)."
    )


# ===== /test_remind =====
@dp.message(F.text == "/test_remind")
async def test_remind(message: Message):
    _remember_user(message)
    await remind_users()
    await message.answer("✅ Напоминание отправлено всем, кто ещё не отметился сегодня.")


# ===== MAIN =====
async def main():
    print("✅ Бот запущен (учёт + опоздания + причины + напоминания).")

    scheduler.add_job(
        remind_users,
        CronTrigger(hour="9", minute="0-30/10", timezone="Asia/Tashkent"),
    )
    scheduler.add_job(
        mark_absent_users,
        CronTrigger(hour=10, minute=30, timezone="Asia/Tashkent"),
    )

    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
