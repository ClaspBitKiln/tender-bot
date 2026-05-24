#!/usr/bin/env python3
"""
TenderPoisk Bot - поиск тендеров на zakupki.gov.ru
"""
import os
import asyncio
import re
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart

load_dotenv()

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

bot = Bot(token=TG_TOKEN)
dp = Dispatcher()


async def search_tenders(keyword: str, limit: int = 5):
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
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/rss+xml,application/xml,text/xml,*/*",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                timeout=15,
                follow_redirects=True
            )
            if r.status_code != 200:
                return {"error": f"HTTP {r.status_code}", "items": []}
            if len(r.content) < 50:
                return {"error": "Empty response", "items": []}
            root = ET.fromstring(r.content)
            items = []
            for item in root.findall(".//item")[:limit]:
                title = item.findtext("title", "").strip()
                link  = item.findtext("link", "").strip()
                desc  = item.findtext("description", "").strip()
                pub   = item.findtext("pubDate", "")[:16]
                price = None
                m = re.search(r"([\d\s]+[.,]\d{2})\s*руб", desc)
                if m:
                    price = m.group(1).strip()
                items.append({"name": title[:120], "link": link, "price": price, "date": pub})
            return {"items": items}
    except Exception as e:
        return {"error": str(e), "items": []}


async def ai_summary(keyword: str, items: list) -> str:
    if not GROQ_KEY or not items:
        return ""
    try:
        tenders_text = "\n".join([
            f"- {i['name']}" + (f" ({i['price']} руб.)" if i.get("price") else "")
            for i in items[:5]
        ])
        prompt = (
            f"Ты эксперт по госзакупкам России. Кратко (2-3 предложения) проанализируй "
            f"тендеры по теме \"{keyword}\": какой уровень цен, кто заказчики, стоит ли участвовать?\n\n"
            f"Тендеры:\n{tenders_text}\n\nТолько анализ на русском, без заголовков."
        )
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
                timeout=10
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"AI error: {e}")
    return ""


HELP_TEXT = (
    "*Бот поиска тендеров zakupki.gov.ru*\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "Напишите ключевое слово для поиска:\n\n"
    "  `металлопрокат`\n"
    "  `строительство дороги`\n"
    "  `поставка труб`\n"
    "  `охрана объектов`\n\n"
    "Получите список свежих тендеров + AI-анализ"
)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(HELP_TEXT, parse_mode="Markdown")


@dp.message()
async def handle_message(message: Message):
    keyword = (message.text or "").strip()
    if not keyword:
        return

    await message.answer(f"Ищу тендеры: *{keyword}*...", parse_mode="Markdown")

    data = await search_tenders(keyword)

    if data.get("error"):
        await message.answer(
            f"Ошибка доступа к zakupki.gov.ru:\n`{data['error']}`\n\nПопробуйте позже.",
            parse_mode="Markdown"
        )
        return

    items = data.get("items", [])
    if not items:
        await message.answer(
            f"По запросу *{keyword}* тендеры не найдены.\nПопробуйте другое слово.",
            parse_mode="Markdown"
        )
        return

    t = f"*Тендеры: {keyword}*\n━━━━━━━━━━━━━━━━━━\n"
    for i, item in enumerate(items, 1):
        t += f"\n*{i}. {item['name'][:80]}*\n"
        if item.get("price"):
            t += f"   Цена: {item['price']} руб.\n"
        if item.get("date"):
            t += f"   Дата: {item['date']}\n"
        if item.get("link"):
            t += f"   [Открыть тендер]({item['link']})\n"

    ai_text = await ai_summary(keyword, items)
    if ai_text:
        t += f"\n━━━━━━━━━━━━━━━━━━\n*AI-анализ:*\n{ai_text}"

    t += "\n━━━━━━━━━━━━━━━━━━"
    await message.answer(t, parse_mode="Markdown", disable_web_page_preview=True)


async def main():
    print("TenderPoisk Bot started")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
