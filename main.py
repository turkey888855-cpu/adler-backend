import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response
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
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")        # токен для админ-панели

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


# ---------- FASTAPI + РУЧНОЙ CORS ----------

app = FastAPI()


# Обработчик preflight-запросов OPTIONS для любых путей
@app.options("/{full_path:path}")
async def options_handler(full_path: str, request: Request):
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )


# Middleware: добавляем CORS-заголовки ко всем ответам
@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response


# ---------- АВТОРИЗАЦИЯ АДМИНА ----------

def require_admin(request: Request):
    """
    Простая авторизация по заголовку X-Admin-Token.
    """
    token = request.headers.get("X-Admin-Token")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


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


# --- модели для админки ---

class TourCreate(BaseModel):
    title: str
    type: str
    description: Optional[str] = None
    price_from: Optional[float] = None
    duration_hours: Optional[int] = None
    is_active: bool = True


class TourUpdate(BaseModel):
    title: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    price_from: Optional[float] = None
    duration_hours: Optional[int] = None
    is_active: Optional[bool] = None


class BookingOut(BaseModel):
    id: int
    tour_id: int
    tour_title: str
    client_name: str
    client_phone: str
    people_count: int
    date_time: datetime
    comment: Optional[str] = None
    status: str


class BookingUpdate(BaseModel):
    status: Optional[str] = None


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


# ---------- API ДЛЯ ТУРОВ И ЗАЯВОК (публичная часть) ----------

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

    try:
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

    except HTTPException:
        raise
    except Exception as e:
        # ВРЕМЕННО: логируем и отдаём текст ошибки наружу, чтобы видеть проблему
        print("DB error in create_booking:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))

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


# ---------- АДМИН: ТУРЫ ----------

@app.get("/admin/tours", response_model=List[TourOut])
def admin_list_tours(admin=Depends(require_admin)):
    """
    Список всех туров (включая неактивные).
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT id, title, type, description, price_from, duration_hours
                FROM tours
                ORDER BY id
                """
            )
        )
        tours = [dict(row._mapping) for row in result]
    return tours


@app.post("/admin/tours", response_model=TourOut)
def admin_create_tour(data: TourCreate, admin=Depends(require_admin)):
    """
    Создать новый тур.
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO tours (title, type, description, price_from, duration_hours, is_active)
                VALUES (:title, :type, :description, :price_from, :duration_hours, :is_active)
                RETURNING id, title, type, description, price_from, duration_hours
                """
            ),
            data.model_dump(),
        )
        row = result.mappings().first()
    return dict(row)


@app.patch("/admin/tours/{tour_id}", response_model=TourOut)
def admin_update_tour(tour_id: int, data: TourUpdate, admin=Depends(require_admin)):
    """
    Обновить тур (название, описание, цену, длительность, активность).
    """
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL 
