import os

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine, text
import httpx

# ---------- БАЗА ДАННЫХ ----------

DATABASE_URL = os.environ.get("DATABASE_URL")

engine = None
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# ---------- TELEGRAM БОТ ----------

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
GUIDES_CHAT_ID = os.environ.get("GUIDES_CHAT_ID")  # строка, приведём к int ниже

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


app = FastAPI()


@app.on_event("startup")
async def on_startup():
    """
    При старте приложения регистрируем вебхук в Telegram.
    """
    if BOT_TOKEN and WEBHOOK_URL:
        async with httpx.AsyncClient() as client:
            await client.get(
                f"{TELEGRAM_API_URL}/setWebhook",
                params={"url": WEBHOOK_URL},
            )


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Adler backend working"}


@app.get("/db-check")
def db_check():
    """
    Проверка соединения с базой Neon.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
        return {"db_ok": bool(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")


async def send_telegram_message(chat_id: int, text: str):
    """
    Отправить сообщение в Telegram.
    """
    if not BOT_TOKEN:
        return

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def notify_guides(text: str):
    """
    Отправить сообщение в группу гидов.
    """
    if not BOT_TOKEN or not GUIDES_CHAT_ID:
        return

    try:
        guides_chat_id = int(GUIDES_CHAT_ID)
    except ValueError:
        return

    await send_telegram_message(guides_chat_id, text)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Обработчик вебхука Telegram.
    """
    update = await request.json()

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "") or ""
    from_user = message.get("from", {})

    username = from_user.get("username")
    first_name = from_user.get("first_name", "")
    last_name = from_user.get("last_name", "")
    full_name = (first_name + " " + last_name).strip()

    # Команда /start
    if text == "/start":
        await send_telegram_message(
            chat_id,
            "Привет! Бот запущен и работает. Потом здесь будет выбор туров.",
        )
        return {"ok": True}

    # Тестовая команда для заявки
    if text == "/testbooking":
        # Сообщение для группы гидов
        guides_text = (
            "🧪 Тестовая заявка\n"
            f"От: {full_name or 'Без имени'}"
            f"{' (@' + username + ')' if username else ''}\n"
            f"chat_id: {chat_id}\n"
            "\nЭто просто тест, настоящей брони нет."
        )

        await notify_guides(guides_text)

        # Ответ клиенту
        await send_telegram_message(
            chat_id,
            "Тестовая заявка отправлена в группу гидов.\n"
            "Проверь свою группу гидов — там должно появиться сообщение.",
        )
        return {"ok": True}

    # На всё остальное можно отвечать молчанием или текстом
    # await send_telegram_message(chat_id, "Неизвестная команда. Напишите /start или /testbooking.")
    return {"ok": True}
