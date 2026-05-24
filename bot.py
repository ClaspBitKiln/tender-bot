#!/usr/bin/env python3
"""
Tender Bot - Поиск тендеров по ключевым словам
Telegram: @TenderRussia_bot
"""

import os
import asyncio
import xml.etree.ElementTree as ET
import httpx
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.types import Message

load_dotenv()

app = FastAPI(title="Tender Bot")

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
WEBHOOK_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

bot = Bot(token=TG_TOKEN) if TG_TOKEN else None
dp  = Dispatcher() if bot else None

# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "Tender Bot"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/search/{keyword}")
async def search(keyword: str):
    return await search_tenders(keyword)

# ── Tender Search ──────────────────────────────────────────────────────────────

async def search_tenders(keyword: str, limit: int = 5):
    """Search tenders via zakupki.gov.ru RSS feed"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html",
                params={
                    "searchString": keyword,
                    "morphology": "on",
                    "pageNumber": "1",
                    "recordsPerPage": "_10",
                    "fz44": "on",
                    "fz223": "on",
                    "af": "on",
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if r.status_code != 200:
                return {"error": f"HTTP {r.status_code}", "items": []}

            root = ET.fromstring(r.content)
            items = []
            for item in root.findall(".//item")[:limit]:
                title = item.findtext("title", "").strip()
                link  = item.findtext("link", "").strip()
                desc  = item.findtext("description", "").strip()
                pub   = item.findtext("pubDate", "")[:16]

                # Extract price from description
                price = None
                import re
                m = re.search(r'([\d\s]+[\.,]\d{2})\s*руб', desc)
                if m:
                    price = m.group(1).strip()

                items.append({
                    "name": title[:120],
                    "link": link,
                    "price": price,
                    "date": pub,
                })
            return {"items": items, "keyword": keyword}
    except Exception as e:
        return {"error": str(e), "items": []}


async def ai_summary(keyword: str, items: list) -> str:
    """Get AI summary of found tenders"""
    if not GROQ_KEY or not items:
        return ""
    try:
        tenders_text = "\n".join([
            f"- {item['name']}" + (f" ({item['price']} руб.)" if item.get('price') else "")
            for item in items[:5]
        ])
        prompt = f"""Ты эксперт по госзакупкам России. Кратко проанализируй найденные тендеры по теме "{keyword}" (2-3 предложения):
- Какой средний уровень цен?
- Какие типы заказчиков?
- Стоит ли участвовать?

Тендеры:
{tenders_text}

Напиши только анализ на русском, без заголовков."""

        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.3
                },
                timeout=8
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"AI error: {e}")
    return ""

# ── Telegram ───────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "🔔 *Бот для поиска тендеров на zakupki.gov.ru*\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "Просто напишите ключевое слово:\n\n"
    "  `металлопрокат`\n"
    "  `строительство дороги`\n"
    "  `поставка труб`\n"
    "  `охрана объектов`\n\n"
    "Бот найдёт свежие тендеры и даст AI-анализ 🤖\n"
    "━━━━━━━━━━━━━━━━━━"
)


@dp.message() if dp else lambda x: None
async def handle_message(message: Message):
    text_in = message.text.strip() if message.text else ""

    if text_in in ["/start", "/help", ""]:
        await message.answer(HELP_TEXT, parse_mode="Markdown")
        return

    keyword = text_in
    await message.answer(f"🔍 Ищу тендеры: *{keyword}*...", parse_mode="Markdown")

    data = await search_tenders(keyword)

    if data.get("error"):
        await message.answer(
            f"❌ Ошибка доступа к zakupki.gov.ru\n\n"
            f"`{data['error']}`\n\n"
            f"Попробуйте позже.",
            parse_mode="Markdown"
        )
        return

    items = data.get("items", [])
    if not items:
        await message.answer(
            f"🔍 По запросу *{keyword}* тендеры не найдены.\n\n"
            f"Попробуйте другое слово.",
            parse_mode="Markdown"
        )
        return

    # Format results
    t = f"📋 *Тендеры: {keyword}*\n━━━━━━━━━━━━━━━━━━\n"
    for i, item in enumerate(items, 1):
        t += f"\n*{i}. {item['name'][:80]}*\n"
        if item.get("price"):
            t += f"   💰 {item['price']} руб.\n"
        if item.get("date"):
            t += f"   📅 {item['date']}\n"
        if item.get("link"):
            t += f"   🔗 [Открыть]({item['link']})\n"

    # AI summary
    ai_text = await ai_summary(keyword, items)
    if ai_text:
        t += f"\n━━━━━━━━━━━━━━━━━━\n🤖 *AI-анализ:*\n{ai_text}"

    t += "\n━━━━━━━━━━━━━━━━━━"
    await message.answer(t, parse_mode="Markdown", disable_web_page_preview=True)


# ── Webhook ────────────────────────────────────────────────────────────────────

from aiogram.types import Update

WEBHOOK_PATH = f"/webhook/{TG_TOKEN}"

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update(**data)
    await dp.feed_webhook_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    if not bot:
        return
    if WEBHOOK_HOST:
        webhook_url = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url, drop_pending_updates=True)
        print(f"Webhook: {webhook_url}")
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        print("WARNING: RAILWAY_PUBLIC_DOMAIN not set, webhook not configured")

@app.on_event("shutdown")
async def on_shutdown():
    if bot:
        await bot.delete_webhook()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"Starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
