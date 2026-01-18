import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
import httpx

# ---------- БАЗА ДАННЫХ ----------

DATABASE_URL = os.environ.get("DATABASE_URL")

engine = None
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# ---------- TELEGRAM-БОТ И НАСТРОЙКИ ----------

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
GUIDES_CHAT_ID = os.environ.get("GUIDES_CHAT_ID")  # строка, приведём к int
WEBAPP_URL = os.environ.get("WEBAPP_URL")          # URL WebApp (GitHub Pages)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


# ---------- FASTAPI + CORS ----------

app = FastAPI()

# Разрешаем запросы только с GitHub Pages (твой домен)
origins = [
    "https://turkey888855-cpu.github.io",
    "https://turkey888855-cpu.github.io/",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,       # куки не нужны
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- СОБЫТИЯ ПРИ СТАРТЕ ПРИЛОЖЕНИЯ ----------

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


# ---------- ПРОСТЫЕ СИСТЕМНЫЕ ЭНДПОИНТЫ ----------

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


# ---------- МОДЕЛИ ДЛЯ API ----------

class TourOut(BaseModel):
    id: int
    title: str
    type: str
    description: Optional[str] = None
    price_from: Optional[float] = None
    duration_hours: Optional[int] = None


class BookingCreate(BaseModel):
    tour_id: int
    date_time: datetime
    people_count: int
    client_name: str
    client_phone: str
    comment: Optional[str] = None
    telegram_user_id: Optional[int] = None
    telegram_username: Optional[str] = None


# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ TELEGRAM ----------

async def send_telegram_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
):
    """
    Отправить сообщение в Telegram.
    """
    if not BOT_TOKEN:
        return

    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json=payload,
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


# ---------- API ДЛЯ ТУРОВ И ЗАЯВОК ----------

@app.get("/api/tours", response_model=List[TourOut])
def list_tours():
    """
    Вернуть список активных туров.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT id, title, type, description, price_from, duration_hours
                FROM tours
                WHERE is_active = TRUE
                ORDER BY id
                """
            )
        )
        tours = [dict(row._mapping) for row in result]
    return tours


@app.post("/api/bookings")
async def create_booking(payload: BookingCreate):
    """
    Создать заявку на тур и отправить уведомление в группу гидов.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    # Проверим, что тур существует и активен
    with engine.begin() as conn:
        tour_row = conn.execute(
            text(
                """
                SELECT id, title
                FROM tours
                WHERE id = :tour_id AND is_active = TRUE
                """
            ),
            {"tour_id": payload.tour_id},
        ).mappings().first()

        if not tour_row:
            raise HTTPException(status_code=400, detail="Invalid tour_id")

        # Вставляем заявку
        result = conn.execute(
            text(
                """
                INSERT INTO bookings (
                    tour_id,
                    telegram_user_id,
                    telegram_username,
                    client_name,
                    client_phone,
                    people_count,
                    date_time,
                    comment,
                    status
                ) VALUES (
                    :tour_id,
                    :telegram_user_id,
                    :telegram_username,
                    :client_name,
                    :client_phone,
                    :people_count,
                    :date_time,
                    :comment,
                    'new'
                )
                RETURNING id
                """
            ),
            {
                "tour_id": payload.tour_id,
                "telegram_user_id": payload.telegram_user_id,
                "telegram_username": payload.telegram_username,
                "client_name": payload.client_name,
                "client_phone": payload.client_phone,
                "people_count": payload.people_count,
                "date_time": payload.date_time,
                "comment": payload.comment,
            },
        )
        booking_id = result.scalar()

    # Формируем текст для группы гидов
    tour_title = tour_row["title"]
    username_part = (
        f" (@{payload.telegram_username})" if payload.telegram_username else ""
    )

    guides_text = (
        f"🆕 Новая заявка #{booking_id}\n"
        f"Тур: {tour_title}\n"
        f"Дата/время: {payload.date_time}\n"
        f"Кол-во человек: {payload.people_count}\n"
        f"Клиент: {payload.client_name}{username_part}\n"
        f"Телефон: {payload.client_phone}\n"
        f"Комментарий: {payload.comment or '-'}"
    )

    await notify_guides(guides_text)

    return {"ok": True, "booking_id": booking_id}


# ---------- WEBHOOK TELEGRAM ----------

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

    # ----- /start -----
    if text == "/start":
        if WEBAPP_URL:
            keyboard = {
                "keyboard": [
                    [
                        {
                            "text": "Открыть каталог туров",
                            "web_app": {"url": WEBAPP_URL},
                        }
                    ]
                ],
                "resize_keyboard": True,
                "one_time_keyboard": False,
            }
            await send_telegram_message(
                chat_id,
                "Привет! Нажмите кнопку ниже, чтобы открыть каталог туров.",
                reply_markup=keyboard,
            )
        else:
            await send_telegram_message(
                chat_id,
                "Привет! WebApp ещё не настроен.",
            )
        return {"ok": True}

    # ----- /testbooking (тестовая заявка в группу) -----
    if text == "/testbooking":
        guides_text = (
            "🧪 Тестовая заявка\n"
            f"От: {full_name or 'Без имени'}"
            f"{' (@' + username + ')' if username else ''}\n"
            f"chat_id: {chat_id}\n"
            "\nЭто просто тест, настоящей брони нет."
        )

        await notify_guides(guides_text)

        await send_telegram_message(
            chat_id,
            "Тестовая заявка отправлена в группу гидов.\n"
            "Проверьте группу — там должно появиться сообщение.",
        )
        return {"ok": True}

    return {"ok": True}
