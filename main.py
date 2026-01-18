import os

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

# Читаем строку подключения из переменных окружения
DATABASE_URL = os.environ.get("DATABASE_URL")

engine = None
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

app = FastAPI()


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Adler backend working"}


@app.get("/db-check")
def db_check():
    if engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
        return {"db_ok": bool(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
