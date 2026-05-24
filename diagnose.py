#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Full diagnostic for tender-bot"""
import asyncio
import os
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv

load_dotenv()

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

KEYWORDS = ["металлопрокат", "строительство", "труба стальная"]


async def test_zakupki(keyword: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html",
                params={"searchString": keyword, "morphology": "on", "fz44": "on", "recordsPerPage": "_5"},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                timeout=15,
                follow_redirects=True
            )
            if r.status_code == 200:
                content = r.content
                if len(content) < 100:
                    return False, f"Empty response ({len(content)} bytes)"
                try:
                    root = ET.fromstring(content)
                    items = root.findall(".//item")
                    if items:
                        title = items[0].findtext("title", "")[:60]
                        return True, f"Found {len(items)} tenders. First: {title}"
                    else:
                        return False, f"XML parsed OK but 0 items (HTTP {r.status_code})"
                except ET.ParseError as e:
                    return False, f"XML parse error: {e} | Response: {r.text[:200]}"
            else:
                return False, f"HTTP {r.status_code} | {r.text[:100]}"
    except Exception as e:
        return False, f"Exception: {type(e).__name__}: {e}"


async def test_groq():
    if not GROQ_KEY:
        return False, "No GROQ_KEY"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5
                },
                timeout=10
            )
            if r.status_code == 200:
                reply = r.json()["choices"][0]["message"]["content"]
                return True, reply.strip()
            return False, f"HTTP {r.status_code}: {r.text[:100]}"
    except Exception as e:
        return False, str(e)


async def test_telegram():
    if not TG_TOKEN:
        return False, "No token"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getMe",
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()["result"]
                return True, f"@{data['username']}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def test_webhook_status():
    """Check webhook status"""
    if not TG_TOKEN:
        return False, "No token"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getWebhookInfo",
                timeout=10
            )
            if r.status_code == 200:
                info = r.json().get("result", {})
                url = info.get("url", "")
                pending = info.get("pending_update_count", 0)
                last_error = info.get("last_error_message", "")
                if url:
                    return True, f"Active: {url[:60]} | pending={pending} | err={last_error or 'none'}"
                return False, f"No webhook set | pending={pending}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def run_all():
    print("=" * 55)
    print("TENDER BOT - FULL DIAGNOSTIC")
    print("=" * 55)

    # Telegram
    ok, msg = await test_telegram()
    print(f"\n[1] Telegram bot: {'OK' if ok else 'FAIL'} - {msg}")

    # Webhook
    ok, msg = await test_webhook_status()
    print(f"[2] Webhook:      {'OK' if ok else 'FAIL'} - {msg}")

    # Groq
    ok, msg = await test_groq()
    print(f"[3] Groq AI:      {'OK' if ok else 'FAIL'} - {msg}")

    # zakupki.gov.ru - test multiple keywords
    print(f"\n[4] zakupki.gov.ru RSS:")
    for kw in KEYWORDS:
        ok, msg = await test_zakupki(kw)
        status = "OK  " if ok else "FAIL"
        print(f"    [{status}] '{kw}': {msg}")

    print("\n" + "=" * 55)
    print("DIAGNOSIS COMPLETE")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(run_all())
