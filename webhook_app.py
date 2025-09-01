import os
import logging
from fastapi import FastAPI, Request, Response
from aiogram.types import Update

from main import bot, dp, init_db  # твой текущий main.py

app = FastAPI()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
PUBLIC_URL = os.getenv("PUBLIC_URL")  # можно не задавать

@app.on_event("startup")
async def on_startup():
    await init_db()
    me = await bot.get_me()
    logging.info("RUNNING AS @%s (id=%s)", me.username, me.id)
    # Вебхук можно ставить вручную через setWebhook, поэтому PUBLIC_URL не обязателен
    if PUBLIC_URL:
        await bot.set_webhook(f"{PUBLIC_URL}/webhook/{WEBHOOK_SECRET}")

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        return Response(status_code=403)

    data = await request.json()

    # Диагностика: ответим напрямую без aiogram
    try:
        msg = data.get("message") or data.get("edited_message")
        if msg and "text" in msg and msg["text"].strip() == "/hookping":
            await bot.send_message(msg["chat"]["id"], "webhook alive ✅")
            return {"ok": True}
    except Exception as e:
        logging.exception("Direct send from webhook failed: %s", e)

    # Обычная передача апдейта в aiogram
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
