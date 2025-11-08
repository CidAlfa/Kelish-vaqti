import datetime
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import warnings

# подавляем лишние предупреждения
warnings.filterwarnings("ignore", category=UserWarning)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8013930431:AAH7pPMdsTnmO-IFpmpkZ71pjGobztLeYHE"
SERVICE_FILE = "nomadic-bedrock-477215-t4-0170caaa7c78.json"
SPREADSHEET_NAME = "Kelish vaqti"

# Часовой пояс Узбекистана (UTC+5)
UZB_TZ = datetime.timezone(datetime.timedelta(hours=5))

# ========== GOOGLE SHEETS ==========
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_file(SERVICE_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open(SPREADSHEET_NAME).sheet1

# ========== TELEGRAM ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# список сотрудников (username)
USERS = []


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_record(username, date_str):
    """Находит запись по username и дате, возвращает (индекс строки, данные)"""
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):  # первая строка — заголовки
        clean_row = {k.strip(): v for k, v in row.items()}
        if clean_row.get("Username") == username and clean_row.get("Дата") == date_str:
            return i, clean_row
    return None, None


def add_row(username, name, date_str, time_str, status, file_id="-", reason="-"):
    next_row = len(sheet.get_all_values()) + 1
    sheet.append_row(
        [next_row - 1, username, name, date_str, time_str, status, file_id, reason]
    )


# ===== ОБРАБОТКА ВИДЕО =====
@dp.message(F.content_type.in_({"video", "video_note"}))
async def handle_video(message: Message):
    username = message.from_user.username or str(message.from_user.id)
    name = message.from_user.first_name
    video = message.video or message.video_note
    file_id = video.file_id

    now = datetime.datetime.now(UZB_TZ)
    date_str = now.strftime("%d.%m.%Y")
    time_str = now.strftime("%H:%M")

    row_idx, record = get_record(username, date_str)

    # если уже есть "Опоздал" без видео — обновляем строку
    if record and record.get("Статус") == "Опоздал" and (
        record.get("Файл видео") == "-" or not record.get("Файл видео")
    ):
        sheet.update_cell(row_idx, 5, time_str)  # Время
        sheet.update_cell(row_idx, 7, file_id)   # Файл видео
        await message.answer(
            f"🎥 Видео принято. Приход после опоздания зарегистрирован в {time_str}."
        )
        return

    # если уже есть запись "Пришёл" или "Не пришёл"
    if record:
        await message.answer("✅ Сегодня ты уже зарегистрирован.")
        return

    # иначе создаём новую запись как "Пришёл"
    add_row(username, name, date_str, time_str, "Пришёл", file_id)
    await message.answer(f"🎉 Приход зарегистрирован в {time_str}.")

    if username not in USERS:
        USERS.append(username)


# ===== /BE_LATE =====
@dp.message(F.text.startswith("/be_late"))
async def be_late(message: Message):
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

    if username not in USERS:
        USERS.append(username)


# ===== /DONT_COME =====
@dp.message(F.text.startswith("/dont_come"))
async def dont_come(message: Message):
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

    if username not in USERS:
        USERS.append(username)


# ===== НАПОМИНАНИЯ =====
async def remind_users():
    now = datetime.datetime.now(UZB_TZ)
    weekday = now.weekday()  # 0 = Пн ... 6 = Вс
    if weekday == 6:
        return  # воскресенье — не беспокоим

    date_str = now.strftime("%d.%m.%Y")
    records = sheet.get_all_records()
    marked_users = {r["Username"] for r in records if r["Дата"] == date_str}

    for username in USERS:
        if username not in marked_users:
            try:
                await bot.send_message(
                    username,
                    "⏰ Не забудь отправить видео или написать /be_late или /dont_come с причиной до 10:30!",
                )
            except Exception as e:
                print(f"⚠️ Не смог отправить {username}: {e}")


# ===== ПОСЛЕ 10:30 — ОТМЕТКА 'БЕЗ ПРИЧИНЫ' =====
async def mark_absent_users():
    now = datetime.datetime.now(UZB_TZ)
    weekday = now.weekday()
    if weekday == 6:
        return

    date_str = now.strftime("%d.%m.%Y")
    records = sheet.get_all_records()
    marked_users = {r["Username"] for r in records if r["Дата"] == date_str}

    for username in USERS:
        if username not in marked_users:
            add_row(username, username, date_str, "-", "Не пришёл", "-", "Без причины")
            try:
                await bot.send_message(
                    username,
                    "🚫 Ты не отметил приход и не написал причину. Отмечено как 'Без причины'.",
                )
            except:
                pass


# ===== /START =====
@dp.message(F.text == "/start")
async def start_cmd(message: Message):
    username = message.from_user.username or str(message.from_user.id)
    if username not in USERS:
        USERS.append(username)
    await message.answer(
        "👋 Привет! Этот бот отмечает приход на работу.\n\n"
        "📹 Отправь видео, когда придёшь.\n"
        "⚠️ Если опаздываешь — напиши /be_late причина.\n"
        "🚫 Если не придёшь — /dont_come причина.\n"
        "⏰ Напоминания идут до 10:30 (воскресенье — выходной)."
    )


# ===== MAIN =====
async def main():
    print("✅ Бот запущен (учёт + опоздания + причины + напоминания).")

    # каждые 10 минут с 9:00 до 10:30 (можно поменять на 21:55-22:10 для теста)
    scheduler.add_job(
        remind_users,
        CronTrigger(hour="9", minute="0-30/10", timezone="Asia/Tashkent"),
    )

    # после 10:30 отмечаем "Без причины"
    scheduler.add_job(
        mark_absent_users, CronTrigger(hour=10, minute=30, timezone="Asia/Tashkent")
    )

    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
