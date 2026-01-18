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
    text = message.get("text", "")

    if text == "/start":
        await send_telegram_message(
            chat_id,
            "Привет! Бот запущен и работает. Потом здесь будет выбор туров.",
        )

    # тут позже добавим другие команды
    return {"ok": True}
