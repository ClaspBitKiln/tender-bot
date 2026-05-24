#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Test script for Tender Bot"""
import asyncio
import os
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv

load_dotenv()

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

async def run_tests():
    print("=" * 50)
    print("[TEST] Tender Bot - Test")
    print("=" * 50)

    # 1. Env vars
    print("\n[1] Env vars:")
    print(f"   TELEGRAM_BOT_TOKEN: {'OK' if TG_TOKEN else 'MISSING'}")
    print(f"   GROQ_API_KEY: {'OK' if GROQ_KEY else 'MISSING'}")

    # 2. zakupki.gov.ru RSS
    print(f"\n[2] zakupki.gov.ru RSS:")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html",
                params={"searchString": "металл", "morphology": "on", "fz44": "on", "recordsPerPage": "_5"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                items = root.findall(".//item")
                if items:
                    title = items[0].findtext("title", "")[:70]
                    print(f"   OK - found {len(items)} tenders")
                    print(f"   Example: {title}")
                else:
                    print(f"   WARNING: empty response")
            else:
                print(f"   ERROR: HTTP {r.status_code}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # 3. Groq AI
    print(f"\n[3] Groq AI:")
    if not GROQ_KEY:
        print("   MISSING key")
    else:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.1-8b-instant",
                        "messages": [{"role": "user", "content": "Скажи 'тест пройден'"}],
                        "max_tokens": 10
                    },
                    timeout=10
                )
                if r.status_code == 200:
                    reply = r.json()["choices"][0]["message"]["content"]
                    print(f"   OK - reply: {reply.strip()}")
                else:
                    print(f"   ERROR {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"   ERROR: {e}")

    # 4. Telegram
    print(f"\n[4] Telegram:")
    if not TG_TOKEN:
        print("   MISSING token")
    else:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://api.telegram.org/bot{TG_TOKEN}/getMe",
                    timeout=10
                )
                if r.status_code == 200:
                    username = r.json()["result"]["username"]
                    print(f"   OK - bot: @{username}")
                else:
                    print(f"   ERROR {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"   ERROR: {e}")

    print("\n" + "=" * 50)
    print("DONE")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(run_tests())
