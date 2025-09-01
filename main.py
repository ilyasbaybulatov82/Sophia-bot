import os
import re
import asyncio
import logging
import httpx
import aiosqlite
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    LabeledPrice,
)
from aiogram.filters import CommandStart, Command
from aiogram.filters import Command

@dp.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong")

# ========= ENV =========
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "3"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Бесплатный период (дней) — для отображения и расчёта «сколько осталось»
FREE_TRIAL_DAYS = int(os.getenv("FREE_TRIAL_DAYS", "7"))

# Stars-параметры (когда включишь Stars в BotFather)
# В оплате через Stars цены указываются в XTR. Пока используем заглушку: выставим произвольные значения.
PRICE_MONTH_STARS = int(os.getenv("PRICE_MONTH_STARS", "1000"))  # заглушка под 1000 ₽
PRICE_WEEK_STARS  = int(os.getenv("PRICE_WEEK_STARS", "300"))   # заглушка под 300 ₽
PREMIUM_DAYS_MONTH = int(os.getenv("PREMIUM_DAYS_MONTH", "30"))
PREMIUM_DAYS_WEEK  = int(os.getenv("PREMIUM_DAYS_WEEK", "7"))

# Контекст диалога — сколько последних пар реплик подмешивать
HISTORY_MAX_TURNS = int(os.getenv("HISTORY_MAX_TURNS", "8"))

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ========= Память «ожидаемого ввода» для редактирования профиля =========
# user_id -> "name" | "age" | "interests"
PENDING_EDIT: Dict[int, str] = {}

# ========= SYSTEM PROMPT (EN) =========
SYSTEM_PROMPT = """
You are **Sophia**, a virtual friend for warm, supportive, and natural conversations.
Your role is to listen, support, and gently help people see things in a more positive light while staying natural,
respectful, and safe. You are not a therapist, and you must not provide medical, psychiatric, legal, or financial advice.
You are a caring, smart, and friendly conversational partner.

## Goals
1) Create the feeling of a real, trusted conversation: listen actively, reflect emotions, and ask gentle clarifying questions.
2) Maintain realistic optimism (avoid “toxic positivity”). Help the user find small, encouraging perspectives or steps.
3) Adapt to the user’s profile (name, age, interests, personal notes) and mirror their tone and style.

## Style and tone
- Warm, natural, conversational. Speak like a good, empathetic friend.
- A hint of playful warmth/flirt is allowed but only lightly, respectfully, and only if the user’s tone invites it.
  Always respect boundaries; if any discomfort is detected, immediately dial it back to neutral warmth.
- Emojis: rare, light, and natural (0–2 in a short message) only when they feel organic.
- Mirror the user’s language, tone, and emotional state.
- Keep answers concise: 1–3 short paragraphs; no lectures.
- Address the user by name if known; adapt formality to age/cultural context.

## Personalization
- Use stored profile/context: refer to the user’s name, mention their interests, never contradict known facts.
- Adjust complexity and references to the user’s age, role, and personality.
- If gender or preferred address form is unclear, stay neutral.

## Conversation flow
(a) Reflect the feeling → (b) Clarify the request → (c) Offer 1–2 supportive questions or perspectives
→ (d) Suggest a small step → (e) Check in on how it feels.
- Never overload with too many questions.
- Keep the dialogue flowing naturally; small talk is welcome if the user seeks it.

## Must NOT do
- No diagnoses; no medical/psychiatric, legal, or financial instructions.
- No harmful advice or instructions that could cause damage to the user, others, or yourself.
- No explicit content with minors, hate speech, violence, illegal activity, weapons, drugs, etc.
- Do not engage with provocations or trolling.
- Do not promise “healing” or “guaranteed outcomes.”

## Handling provocations & unsafe requests
- Set a soft boundary and redirect to helpful topics.
  Example: “I understand your curiosity, but I don’t discuss that. Maybe we can talk about what’s really on your mind instead?”
- If asked for harmful instructions — refuse politely and redirect to safe, neutral support.

## Risk signals (self-harm, suicide, violence)
- Stay calm, caring, and empathetic. Emphasize the user is not alone.
- Gently encourage seeking urgent help from local emergency services, hotlines, or trusted people.
- Avoid discussing harmful methods in detail.

## Format of replies
- Keep answers short and natural: 1–3 paragraphs max.
- If suggesting something, offer only one small practical step (1–3 minutes).
- End with one clear question to continue the flow unless the user asked for no questions.

## Use profile and context
Below is a short summary of the user’s profile/context (if available). Do not repeat it verbatim; just use it naturally.

[User Profile]: {Name, Age, Interests, About if available}
[Recent conversation history]: {last few turns}

## Language rule
Always reply only in the language the user writes in. Mirror the user’s language; do not switch languages unless asked.

Remember: your purpose is to be a supportive, natural, empathetic friend who makes the user feel understood and a bit stronger.
"""

# ========= HELPERS =========
def profile_to_text(p: dict | None) -> str:
    if not p:
        return "Профиль неизвестен."
    parts = []
    if p.get("name"):
        parts.append(f"Имя: {p['name']}")
    if p.get("age"):
        parts.append(f"Возраст: {p['age']}")
    if p.get("interests"):
        parts.append(f"Интересы: {p['interests']}")
    if p.get("about"):
        parts.append(f"О себе: {p['about']}")
    return " | ".join(parts) if parts else "Профиль неизвестен."

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💜 Профиль"), KeyboardButton(text="💎 Подписка")],
            [KeyboardButton(text="📝 Изменить данные")],
            [KeyboardButton(text="🔄 Сбросить диалог"), KeyboardButton(text="❌ Забыть всё")],
        ],
        resize_keyboard=True
    )

def subscription_panel_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оформить/Продлить", callback_data="sub_buy")],
        [InlineKeyboardButton(text="Посмотреть тарифы", callback_data="sub_pricing")],
    ])

def subscription_choose_plan():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Месяц — 1000 ₽", callback_data="sub_buy_month")],
        [InlineKeyboardButton(text="Неделя — 300 ₽", callback_data="sub_buy_week")],
    ])

# ========= DB =========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # пользователи (для даты первого визита — free trial)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)
        # лимиты
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                cnt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
        """)
        # премиум (+ план)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS premium (
                user_id INTEGER PRIMARY KEY,
                premium_until TEXT NOT NULL,
                plan TEXT
            )
        """)
        # профиль
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                age INTEGER,
                interests TEXT,
                about TEXT,
                updated_at TEXT
            )
        """)
        # история диалога
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dialog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,         -- 'user' | 'assistant'
                content TEXT NOT NULL,
                ts TEXT NOT NULL
            )
        """)
        # на случай старой схемы без планов — добавим колонку план, если нет
        try:
            await db.execute("ALTER TABLE premium ADD COLUMN plan TEXT")
        except Exception:
            pass

        await db.commit()

async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
                    (user_id, datetime.now().isoformat())
                )
                await db.commit()

async def get_user_created_at(user_id: int) -> Optional[datetime]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT created_at FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row[0]) if row else None

def today_str() -> str:
    return datetime.now().date().isoformat()

# ---- лимиты
async def get_count(user_id: int) -> int:
    day = today_str()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT cnt FROM usage WHERE user_id=? AND day=?", (user_id, day)) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0

async def inc_count(user_id: int, delta: int = 1) -> None:
    day = today_str()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE usage SET cnt = cnt + ? WHERE user_id=? AND day=?", (delta, user_id, day))
        if cur.rowcount == 0:
            await db.execute("INSERT INTO usage (user_id, day, cnt) VALUES (?, ?, ?)", (user_id, day, delta))
        await db.commit()

# ---- премиум
async def has_premium(user_id: int) -> bool:
    now = datetime.now()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT premium_until FROM premium WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return False
            until = datetime.fromisoformat(row[0])
            return now < until

async def get_premium_info(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT premium_until, plan FROM premium WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {"until": datetime.fromisoformat(row[0]), "plan": row[1]}

async def grant_premium(user_id: int, days: int, plan: str):
    until = datetime.now() + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO premium (user_id, premium_until, plan) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                premium_until=excluded.premium_until,
                plan=excluded.plan
        """, (user_id, until.isoformat(), plan))
        await db.commit()

# ---- профиль
async def get_profile(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name, age, interests, about, updated_at FROM profile WHERE user_id=?",
                              (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {"name": row[0], "age": row[1], "interests": row[2], "about": row[3], "updated_at": row[4]}

async def set_profile(user_id: int, name=None, age=None, interests=None, about=None):
    prof = await get_profile(user_id) or {}
    name = prof.get("name") if name is None else name
    age = prof.get("age") if age is None else age
    interests = prof.get("interests") if interests is None else interests
    about = prof.get("about") if about is None else about
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO profile (user_id, name, age, interests, about, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                age=excluded.age,
                interests=excluded.interests,
                about=excluded.about,
                updated_at=excluded.updated_at
        """, (user_id, name, age, interests, about, now))
        await db.commit()

async def forget_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM profile WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM premium WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM usage WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM dialog WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        await db.commit()

# ---- диалог
async def add_dialog(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO dialog (user_id, role, content, ts) VALUES (?, ?, ?, ?)",
                         (user_id, role, content, datetime.now().isoformat()))
        await db.execute("""
            DELETE FROM dialog
            WHERE id NOT IN (
                SELECT id FROM dialog WHERE user_id=? ORDER BY id DESC LIMIT ?
            ) AND user_id=?
        """, (user_id, HISTORY_MAX_TURNS * 2, user_id))
        await db.commit()

async def get_history_messages(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT role, content FROM dialog
            WHERE user_id=?
            ORDER BY id ASC
        """, (user_id,)) as cur:
            return await cur.fetchall()

# ========= PASSIVE PROFILE EXTRACTION =========
RE_NAME = re.compile(r"\b(меня зовут|зови меня|я\s*—|я\s*-)\s*([A-Za-zА-Яа-яЁё\-]+)\b", re.IGNORECASE)
RE_AGE = re.compile(r"\bмне\s+(\d{1,3})\b", re.IGNORECASE)
RE_INTERESTS = re.compile(r"\b(я люблю|нравится|интересуюсь|мои интересы[:\-]?)\s+(.+)", re.IGNORECASE)

async def try_extract_and_save_profile(user_id: int, text: str):
    prof = await get_profile(user_id) or {}
    updated = False

    m = RE_NAME.search(text)
    if m:
        name = m.group(2).strip().capitalize()
        if not prof.get("name") or prof.get("name") != name:
            await set_profile(user_id, name=name)
            updated = True

    m = RE_AGE.search(text)
    if m:
        age = int(m.group(1))
        if 5 <= age <= 120 and prof.get("age") != age:
            await set_profile(user_id, age=age)
            updated = True

    m = RE_INTERESTS.search(text)
    if m:
        interests = m.group(2).strip()[:300]
        if not prof.get("interests") or prof.get("interests") != interests:
            await set_profile(user_id, interests=interests)
            updated = True

    return updated

# ========= AI CALL =========
async def ask_deepseek(messages: list[dict]) -> str:
    url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    payload = {"model": DEEPSEEK_MODEL, "messages": messages}
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()

# ========= UI PIECES =========
def buy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 Оформить/Продлить", callback_data="sub_buy")],
        [InlineKeyboardButton(text="Посмотреть тарифы", callback_data="sub_pricing")],
    ])

# ========= COMMANDS & MENU =========
@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user.id)

    premium = await has_premium(m.from_user.id)
    status = "Безлимит активен 💎" if premium else f"Бесплатно: {DAILY_FREE_LIMIT} сообщений/день"
    p = await get_profile(m.from_user.id)
    name = p.get("name") if p else None
    hello = f"Привет, {name}! 💜" if name else "Привет! 💜"

    await m.answer(
        f"{hello}\n{status}\n\n"
        "Я — Sophia, твоя виртуальная подруга.\n"
        "Можешь просто написать мне или выбрать действие в меню ниже 👇",
        reply_markup=main_menu()
    )

@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Команды: /profile /reset /forget\n"
        "Но удобнее пользоваться меню под строкой ввода 😊",
        reply_markup=main_menu()
    )

@dp.message(Command("profile"))
async def cmd_profile(m: Message):
    await ensure_user(m.from_user.id)
    p = await get_profile(m.from_user.id)
    text = profile_to_text(p)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить имя", callback_data="edit_name"),
         InlineKeyboardButton(text="Изменить возраст", callback_data="edit_age")],
        [InlineKeyboardButton(text="Изменить интересы", callback_data="edit_interests")]
    ])
    await m.answer("Твой профиль:\n" + text, reply_markup=kb)

@dp.message(Command("reset"))
async def cmd_reset(m: Message):
    await ensure_user(m.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM dialog WHERE user_id=?", (m.from_user.id,))
        await db.commit()
    await m.answer("Историю диалога очистила.", reply_markup=main_menu())

@dp.message(Command("forget"))
async def cmd_forget(m: Message):
    await forget_user(m.from_user.id)
    await m.answer("Я всё забыла: профиль, историю и лимиты. Можем начать заново.", reply_markup=main_menu())

# ========= MENU (ReplyKeyboard) =========
@dp.message(F.text == "💜 Профиль")
async def menu_profile(m: Message):
    await cmd_profile(m)

@dp.message(F.text == "📝 Изменить данные")
async def menu_edit(m: Message):
    await m.answer(
        "Выбери, что изменить 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Имя", callback_data="edit_name")],
            [InlineKeyboardButton(text="Возраст", callback_data="edit_age")],
            [InlineKeyboardButton(text="Интересы", callback_data="edit_interests")],
        ])
    )

@dp.message(F.text == "💎 Подписка")
async def menu_subscription(m: Message):
    await ensure_user(m.from_user.id)

    # Статус премиума
    info = await get_premium_info(m.from_user.id)
    if info and info["until"] > datetime.now():
        plan = "Месячная" if (info["plan"] or "").lower() == "month" else "Недельная" if (info["plan"] or "").lower() == "week" else "Премиум"
        premium_line = f"Статус: 💎 Премиум активен ({plan}), до {info['until'].strftime('%d.%m.%Y')}."
    else:
        premium_line = "Статус: Премиума нет."

    # Бесплатный период
    created = await get_user_created_at(m.from_user.id)
    if created:
        days_used = (datetime.now().date() - created.date()).days
        days_left = max(FREE_TRIAL_DAYS - days_used, 0)
    else:
        days_used = 0
        days_left = FREE_TRIAL_DAYS

    trial_line = f"Бесплатный период: {FREE_TRIAL_DAYS} дней, осталось: {days_left}."

    # Текущий доступ по дням (если есть премиум — это понятнее отдельной строкой)
    await m.answer(
        f"{premium_line}\n{trial_line}\n\n"
        "Выбери действие ниже:",
        reply_markup=subscription_panel_main()
    )

@dp.message(F.text == "🔄 Сбросить диалог")
async def menu_reset(m: Message):
    await cmd_reset(m)

@dp.message(F.text == "❌ Забыть всё")
async def menu_forget(m: Message):
    await cmd_forget(m)

# ========= INLINE EDIT CALLBACKS (без слэш-команд) =========
@dp.callback_query(F.data == "edit_name")
async def cb_edit_name(c: CallbackQuery):
    PENDING_EDIT[c.from_user.id] = "name"
    await c.message.answer("Окей, напиши новое имя одним сообщением 🙂")
    await c.answer()

@dp.callback_query(F.data == "edit_age")
async def cb_edit_age(c: CallbackQuery):
    PENDING_EDIT[c.from_user.id] = "age"
    await c.message.answer("Хорошо, напиши возраст числом (например, 25).")
    await c.answer()

@dp.callback_query(F.data == "edit_interests")
async def cb_edit_interests(c: CallbackQuery):
    PENDING_EDIT[c.from_user.id] = "interests"
    await c.message.answer("Отлично! Напиши через запятую твои интересы (например: бег, музыка, кино).")
    await c.answer()

# ========= SUBSCRIPTION CALLBACKS =========
@dp.callback_query(F.data == "sub_pricing")
async def cb_sub_pricing(c: CallbackQuery):
    text = (
        "Тарифы Sophia:\n"
        "• Месяц — 1000 ₽ (безлимит на 30 дней)\n"
        "• Неделя — 300 ₽ (безлимит на 7 дней)\n\n"
        "Оплата будет проходить через Telegram Stars.\n"
        "Пока Stars не включены — покупка может быть временно недоступна."
    )
    await c.message.answer(text, reply_markup=subscription_choose_plan())
    await c.answer()

@dp.callback_query(F.data == "sub_buy")
async def cb_sub_buy(c: CallbackQuery):
    await c.message.answer("Выбери тариф:", reply_markup=subscription_choose_plan())
    await c.answer()

@dp.callback_query(F.data == "sub_buy_month")
async def cb_sub_buy_month(c: CallbackQuery):
    await c.answer()
    title = "Sophia · Месяц безлимита"
    description = "Безлимитные сообщения с Sophia на 30 дней. Оплата в Telegram Stars."
    payload = f"premium_month_{c.from_user.id}_{int(datetime.now().timestamp())}"
    prices = [LabeledPrice(label="Месяц — 1000 ₽", amount=PRICE_MONTH_STARS)]
    try:
        await bot.send_invoice(
            chat_id=c.message.chat.id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",   # для Stars — пусто
            currency="XTR",
            prices=prices,
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False
        )
    except Exception:
        await c.message.answer(
            "Похоже, Telegram Stars ещё не включены для этого бота. "
            "Как включатся — оплата заработает. 💜"
        )

@dp.callback_query(F.data == "sub_buy_week")
async def cb_sub_buy_week(c: CallbackQuery):
    await c.answer()
    title = "Sophia · Неделя безлимита"
    description = "Безлимитные сообщения с Sophia на 7 дней. Оплата в Telegram Stars."
    payload = f"premium_week_{c.from_user.id}_{int(datetime.now().timestamp())}"
    prices = [LabeledPrice(label="Неделя — 300 ₽", amount=PRICE_WEEK_STARS)]
    try:
        await bot.send_invoice(
            chat_id=c.message.chat.id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",   # для Stars — пусто
            currency="XTR",
            prices=prices,
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False
        )
    except Exception:
        await c.message.answer(
            "Похоже, Telegram Stars ещё не включены для этого бота. "
            "Как включатся — оплата заработает. 💜"
        )

# ========= PAYMENTS HOOKS =========
@dp.pre_checkout_query()
async def on_pre_checkout(pre: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre.id, ok=True)

@dp.message(F.successful_payment)
async def on_success_payment(m: Message):
    sp = m.successful_payment
    if sp.currency == "XTR":
        # Определим план из payload
        plan = "month" if "premium_month_" in sp.invoice_payload else "week"
        days = PREMIUM_DAYS_MONTH if plan == "month" else PREMIUM_DAYS_WEEK
        await grant_premium(m.from_user.id, days, plan)
        until = datetime.now() + timedelta(days=days)
        await m.answer(
            f"Спасибо! 💎 Премиум ({'Месячная' if plan=='month' else 'Недельная'}) активирован до {until.strftime('%d.%m.%Y')}.\n"
            f"Теперь можно общаться без ограничений 💜",
            reply_markup=main_menu()
        )
    else:
        await m.answer("Оплата получена. Если это был премиум — он скоро активируется.", reply_markup=main_menu())

# ========= TEXT MESSAGE =========
@dp.message(F.text)
async def on_text(m: Message):
    await ensure_user(m.from_user.id)

    user_text = (m.text or "").strip()
    if not user_text:
        return await m.answer("Напиши текстом, пожалуйста 🙂", reply_markup=main_menu())

    user_id = m.from_user.id

    # 1) Если ждём ответ для редактирования профиля — обрабатываем и выходим (не считаем в лимит)
    if user_id in PENDING_EDIT:
        field = PENDING_EDIT.pop(user_id)
        if field == "name":
            name = user_text.strip()
            if not re.match(r"^[A-Za-zА-Яа-яЁё\-\s]{1,40}$", name):
                return await m.answer("Имя выглядит странно. Попробуй без цифр и спецсимволов (до 40 символов).")
            await set_profile(user_id, name=name)
            return await m.answer(f"Запомнила. Буду звать тебя: {name} 💜", reply_markup=main_menu())

        if field == "age":
            if not user_text.isdigit():
                return await m.answer("Возраст должен быть числом. Например: 25")
            age = int(user_text)
            if not (5 <= age <= 120):
                return await m.answer("Возраст должен быть от 5 до 120.")
            await set_profile(user_id, age=age)
            return await m.answer(f"Запомнила возраст: {age}", reply_markup=main_menu())

        if field == "interests":
            interests = user_text[:300]
            await set_profile(user_id, interests=interests)
            return await m.answer("Интересы обновила 💜", reply_markup=main_menu())

    # 2) Пассивное извлечение профиля из обычной речи (опционально)
    await try_extract_and_save_profile(user_id, user_text)

    # 3) Лимит (если нет премиума)
    if not await has_premium(user_id):
        used = await get_count(user_id)
        if used >= DAILY_FREE_LIMIT:
            return await m.answer(
                "Похоже, бесплатный лимит сообщений на сегодня исчерпан 💜\n\n"
                "Чтобы продолжить без ограничений — открой раздел «💎 Подписка» или нажми ниже:",
                reply_markup=buy_keyboard()
            )

    # 4) Контекст: system + профиль + краткая история + текущее сообщение
    profile = await get_profile(user_id)
    profile_text = profile_to_text(profile)
    history_rows = await get_history_messages(user_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": f"[User Profile] {profile_text}"})
    for role, content in history_rows[-HISTORY_MAX_TURNS * 2:]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_text})

    try:
        await bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass

    try:
        reply = await ask_deepseek(messages)
    except httpx.HTTPStatusError as e:
        logging.exception("DeepSeek HTTP error: %s", e)
        return await m.answer("Не получается ответить (ошибка сервера). Попробуем ещё раз?", reply_markup=main_menu())
    except Exception as e:
        logging.exception("DeepSeek error: %s", e)
        return await m.answer("У меня затык. Давай попробуем ещё раз через минуту.", reply_markup=main_menu())

    await m.answer(reply, reply_markup=main_menu())

    # 5) Сохраняем диалог
    await add_dialog(user_id, "user", user_text)
    await add_dialog(user_id, "assistant", reply)

    # 6) Инкремент лимита, если нет премиума
    if not await has_premium(user_id):
        used = await get_count(user_id)
        await inc_count(user_id, 1)
        remaining = DAILY_FREE_LIMIT - (used + 1)
        if remaining in (2, 1):
            await m.answer(f"Осталось бесплатных сообщений: {remaining}")

# ========= RUN =========
async def main():
    await init_db()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
