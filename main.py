import os, asyncio, re, xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

async def search(keyword):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("https://zakupki.gov.ru/epz/order/extendedsearch/rss.html",
                params={"searchString": keyword, "morphology": "on", "fz44": "on", "fz223": "on", "recordsPerPage": "_10"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True)
            if r.status_code != 200 or len(r.content) < 50:
                return []
            root = ET.fromstring(r.content)
            items = []
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                desc = item.findtext("description", "")
                price = None
                m = re.search(r"([\d\s]+[.,]\d{2})\s*руб", desc)
                if m: price = m.group(1).strip()
                items.append({"name": title[:100], "link": link, "price": price})
            return items
    except:
        return None

async def groq_ai(keyword, items):
    if not GROQ_KEY or not items: return ""
    try:
        text = "\n".join([f"- {i['name']}" + (f" ({i['price']} руб.)" if i.get("price") else "") for i in items])
        async with httpx.AsyncClient() as c:
            r = await c.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.1-8b-instant",
                      "messages": [{"role": "user", "content": f"Кратко (2-3 предложения) оцени тендеры по теме '{keyword}' — цены, заказчики, стоит участвовать?\n{text}"}],
                      "max_tokens": 150, "temperature": 0.3}, timeout=10)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
    except: pass
    return ""

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Бот поиска тендеров zakupki.gov.ru*\n\nНапишите ключевое слово:\n`металлопрокат`\n`строительство`\n`поставка труб`",
        parse_mode="Markdown")

async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kw = (update.message.text or "").strip()
    if not kw: return
    await update.message.reply_text(f"Ищу: *{kw}*...", parse_mode="Markdown")
    items = await search(kw)
    if items is None:
        await update.message.reply_text("Ошибка доступа к zakupki.gov.ru. Попробуйте позже.")
        return
    if not items:
        await update.message.reply_text(f"По запросу *{kw}* ничего не найдено.", parse_mode="Markdown")
        return
    t = f"*Тендеры: {kw}*\n{'─'*20}\n"
    for i, item in enumerate(items, 1):
        t += f"\n*{i}. {item['name']}*\n"
        if item.get("price"): t += f"   Цена: {item['price']} руб.\n"
        if item.get("link"): t += f"   [Открыть]({item['link']})\n"
    ai = await groq_ai(kw, items)
    if ai: t += f"\n{'─'*20}\n*AI-анализ:*\n{ai}"
    await update.message.reply_text(t, parse_mode="Markdown", disable_web_page_preview=True)

async def error_handler(update, context):
    import traceback
    err = context.error
    print(f"Error: {err}")
    traceback.print_exc()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.add_error_handler(error_handler)

if __name__ == "__main__":
    import time
    print("Bot waiting 10s for old instance to stop...")
    time.sleep(10)
    print("Bot started")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
    )
