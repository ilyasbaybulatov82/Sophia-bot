import os
from fastapi import FastAPI, Request
from aiogram.types import Update

# импортируй свои объекты из текущего main.py (ничего в нём менять не нужно)
from main import bot, dp, init_db

app = FastAPI()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
PUBLIC_URL = os.getenv("PUBLIC_URL")  # Render URL, добавишь в переменные

@app.on_event("startup")
async def on_startup():
    await init_db()
    if PUBLIC_URL:
        await bot.set_webhook(f"{PUBLIC_URL}/webhook/{WEBHOOK_SECRET}")

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        return {"ok": False}
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}
