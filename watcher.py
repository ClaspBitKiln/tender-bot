#!/usr/bin/env python3
"""MetalWatch: independent low-cost monitor for public Russian procurement notices."""
from __future__ import annotations

import asyncio
import csv
import html
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import httpx

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
EIS_RSS = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html"
USER_AGENT = "MetalWatch/0.1 (public procurement monitor)"
DEFAULT_TERMS = [
    "металлопрокат", "поставка труб", "трубы стальные", "нержавеющая труба",
    "нержавеющий лист", "металлические изделия", "металлопродукция",
    "сварочная проволока", "поковки", "13ХФА", "ХН60ВТ", "лист стальной",
    "труба бесшовная", "труба электросварная", "круг стальной",
]
POSITIVE_TERMS = [
    "металл", "прокат", "труб", "сталь", "нержав", "поков", "проволок",
    "фланц", "отвод", "тройник", "арматур", "лист", "круг", "шестигран",
    "13хфа", "хн60вт", "12х18н10т", "09г2с",
]
BAD_TERMS = [
    "ремонт кровли", "питание", "медицин", "мебель", "уборка",
    "поставка продуктов", "канцеляр", "аренда автомобиля",
]
MONEY_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[\s\u00a0\u202f]\d{3})+(?:[,.]\d{1,2})?|\d{6,}(?:[,.]\d{1,2})?)\s*(?:руб(?:лей|ля|\.)?|₽)",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(?<!\d)(\d{2})[.](\d{2})[.](\d{4})(?:\s*(?:г\.?\s*)?(?:,|в)?\s*(\d{1,2}):(\d{2}))?", re.IGNORECASE)
DEADLINE_LABELS = (
    "окончание срока подачи заявок", "окончание подачи заявок",
    "дата окончания подачи заявок", "срок подачи заявок", "подача заявок до",
    "прием заявок до", "приём заявок до", "окончание приема заявок",
    "окончание приёма заявок", "дата и время окончания",
)
BUYER_LABELS = ("наименование заказчика", "заказчик:", "организация-заказчик", "заказчик закупки")
ID_RE = re.compile(r"(?:regNumber=|purchaseNumber=|noticeNumber=)(\d{10,25})", re.IGNORECASE)


@dataclass
class Tender:
    tender_id: str
    title: str
    url: str
    customer: str = ""
    region: str = ""
    price_rub: int | None = None
    deadline: str = ""
    published: str = ""
    source: str = "ЕИС RSS"
    matched_terms: str = ""
    fetched_at: str = ""

    @property
    def deadline_dt(self) -> datetime | None:
        if not self.deadline:
            return None
        try:
            dt = datetime.fromisoformat(self.deadline)
            return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
        except ValueError:
            return None


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<\s*br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[\t\r ]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    return value.strip()


def parse_money(text: str) -> int | None:
    values: list[int] = []
    for match in MONEY_RE.finditer(text):
        number = re.sub(r"[\s\u00a0\u202f]", "", match.group(1)).replace(",", ".")
        try:
            values.append(int(float(number)))
        except ValueError:
            continue
    return max(values) if values else None


def parse_date_value(text: str) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    day, month, year, hour, minute = m.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 23), int(minute or 59), tzinfo=TZ)
    except ValueError:
        return None


def find_deadline(description: str) -> datetime | None:
    low = description.lower()
    candidates: list[tuple[int, datetime]] = []
    for label in DEADLINE_LABELS:
        pos = 0
        while True:
            pos = low.find(label, pos)
            if pos < 0:
                break
            dt = parse_date_value(description[pos:pos + 240])
            if dt:
                candidates.append((pos, dt))
                break
            pos += len(label)
    if candidates:
        return sorted(candidates, key=lambda x: x[0])[0][1]
    for m in re.finditer(r"(?:заявк\w*|подач\w*|при[её]м\w*).{0,50}", description, re.I):
        dt = parse_date_value(m.group(0))
        if dt:
            return dt
    return None


def find_customer(description: str) -> str:
    low = description.lower()
    for label in BUYER_LABELS:
        pos = low.find(label)
        if pos >= 0:
            tail = description[pos + len(label):pos + len(label) + 180]
            value = re.split(r"\n|;|\||\s{3,}", tail, maxsplit=1)[0].strip(" :-\t")
            if value:
                return value[:140]
    return ""


def extract_id(url: str, title: str) -> str:
    m = ID_RE.search(url)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{10,25})(?!\d)", title)
    return m.group(1) if m else url.rstrip("/")


def parse_rss(xml_bytes: bytes, keyword: str, now: datetime | None = None) -> list[Tender]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    now = now or datetime.now(TZ)
    tenders: list[Tender] = []
    nodes = root.findall(".//item")
    if not nodes:
        nodes = [node for node in root.iter() if node.tag.lower().endswith("entry")]
    for item in nodes:
        title = clean_text(item.findtext("title"))
        url = clean_text(item.findtext("link"))
        if not url:
            link_node = item.find("link")
            if link_node is not None:
                url = link_node.attrib.get("href", "")
        desc = clean_text(item.findtext("description") or item.findtext("summary") or item.findtext("content"))
        if not title or not url:
            continue
        full_text = f"{title}\n{desc}"
        lowered = full_text.lower()
        if any(term in lowered for term in BAD_TERMS):
            continue
        positives = [term for term in POSITIVE_TERMS if term in lowered]
        if not positives:
            continue
        deadline = find_deadline(desc)
        pub_raw = clean_text(item.findtext("pubDate") or item.findtext("published"))
        pub_dt = parse_date_value(pub_raw)
        tenders.append(Tender(
            tender_id=extract_id(url, title), title=title[:350], url=url,
            customer=find_customer(desc), price_rub=parse_money(desc),
            deadline=deadline.isoformat() if deadline else "",
            published=pub_dt.isoformat() if pub_dt else pub_raw[:80],
            matched_terms=", ".join(sorted(set(positives))), fetched_at=now.isoformat(),
        ))
    unique: dict[str, Tender] = {}
    for tender in tenders:
        unique[tender.tender_id] = tender
    return list(unique.values())


def get_terms() -> list[str]:
    raw = os.getenv("SEARCH_TERMS", "")
    return [x.strip() for x in raw.split(";") if x.strip()] if raw.strip() else DEFAULT_TERMS


async def fetch_keyword(client: httpx.AsyncClient, keyword: str) -> list[Tender]:
    try:
        response = await client.get(EIS_RSS, params={
            "searchString": keyword, "morphology": "on",
            "fz44": "on", "fz223": "on", "recordsPerPage": "_50",
        }, timeout=25)
        response.raise_for_status()
        return parse_rss(response.content, keyword)
    except httpx.HTTPError as exc:
        print(f"[WARN] EIS request failed for {keyword!r}: {exc}", file=sys.stderr)
        return []


async def fetch_all() -> list[Tender]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,text/xml,*/*"}
    async with httpx.AsyncClient(headers=headers, limits=httpx.Limits(max_connections=4), follow_redirects=True) as client:
        semaphore = asyncio.Semaphore(3)
        async def limited(term: str) -> list[Tender]:
            async with semaphore:
                result = await fetch_keyword(client, term)
                await asyncio.sleep(0.25)
                return result
        batches = await asyncio.gather(*(limited(term) for term in get_terms()))
    unique: dict[str, Tender] = {}
    for batch in batches:
        for tender in batch:
            old = unique.get(tender.tender_id)
            if old is None:
                unique[tender.tender_id] = tender
            else:
                old.matched_terms = ", ".join(sorted(set(filter(None, (old.matched_terms + ", " + tender.matched_terms).split(", ")))))
                if not old.price_rub and tender.price_rub:
                    old.price_rub = tender.price_rub
                if not old.deadline and tender.deadline:
                    old.deadline = tender.deadline
                if not old.customer and tender.customer:
                    old.customer = tender.customer
    return list(unique.values())


def configure_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS notices (
        tender_id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
        first_seen_at TEXT NOT NULL, notified_at TEXT)""")
    conn.commit()
    return conn


def passes_filters(t: Tender, now: datetime, min_price: int, min_days_left: int) -> bool:
    if t.price_rub is None or t.price_rub < min_price:
        return False
    deadline = t.deadline_dt
    return bool(deadline and deadline >= now + timedelta(days=min_days_left))


def load_unseen(conn: sqlite3.Connection, tenders: Iterable[Tender]) -> list[Tender]:
    unseen = []
    for tender in tenders:
        row = conn.execute("SELECT notified_at FROM notices WHERE tender_id = ?", (tender.tender_id,)).fetchone()
        if row is None or row[0] is None:
            unseen.append(tender)
    return unseen


def mark_notified(conn: sqlite3.Connection, tenders: Iterable[Tender], now: datetime) -> None:
    for t in tenders:
        conn.execute("""INSERT INTO notices(tender_id,title,url,first_seen_at,notified_at)
            VALUES(?,?,?,?,?) ON CONFLICT(tender_id) DO UPDATE SET
            title=excluded.title,url=excluded.url,notified_at=excluded.notified_at""",
            (t.tender_id, t.title, t.url, now.isoformat(), now.isoformat()))
    conn.commit()


def export_csv(tenders: list[Tender], out_dir: Path, now: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"metal-tenders-{now.strftime('%Y%m%d-%H%M')}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(Tender.__dataclass_fields__.keys()))
        writer.writeheader()
        writer.writerows(asdict(t) for t in tenders)
    return path


def format_notice(t: Tender) -> str:
    deadline = t.deadline_dt
    deadline_text = deadline.strftime("%d.%m.%Y %H:%M МСК") if deadline else "не определён"
    price_text = f"{t.price_rub:,.0f} ₽".replace(",", " ") if t.price_rub is not None else "цена не указана"
    customer_text = f"\nЗаказчик: {t.customer}" if t.customer else ""
    return f"• {t.title}\nЦена: {price_text}\nДедлайн: {deadline_text}{customer_text}\nДокументы: {t.url}\nСовпадения: {t.matched_terms}"


async def send_telegram(messages: list[str]) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[INFO] Telegram is not configured; no messages sent.")
        return False
    async with httpx.AsyncClient(timeout=20) as client:
        for body in messages:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": body, "disable_web_page_preview": True},
            )
            if response.status_code >= 300 or not response.json().get("ok"):
                print(f"[ERROR] Telegram send failed: {response.status_code} {response.text[:300]}", file=sys.stderr)
                return False
    return True


def chunk_digest(tenders: list[Tender], now: datetime, max_items: int = 12) -> list[str]:
    heading = (
        f"METALWATCH · новые тендеры\nПроверено: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"Фильтр: от 1 млн ₽; дедлайн не менее 3 суток; публичный RSS ЕИС\n"
        f"Новых подходящих закупок: {min(len(tenders), max_items)}\n\n"
    )
    chunks, current = [], heading
    for tender in tenders[:max_items]:
        block = format_notice(tender) + "\n\n"
        if len(current) + len(block) > 3800 and current != heading:
            chunks.append(current.rstrip())
            current = heading
        current += block
    if current.strip():
        chunks.append(current.rstrip())
    if len(tenders) > max_items:
        chunks.append(f"MetalWatch: ещё {len(tenders) - max_items} закупок осталось в очереди и будет отправлено в следующем отчёте.")
    return chunks


async def main() -> int:
    now = datetime.now(TZ)
    min_price = int(os.getenv("MIN_PRICE_RUB", "1000000"))
    min_days_left = int(os.getenv("MIN_DAYS_LEFT", "3"))
    db_path = Path(os.getenv("DB_PATH", "data/metalwatch.sqlite3"))
    output_dir = Path(os.getenv("OUTPUT_DIR", "reports"))
    dry_run = "--dry-run" in sys.argv

    print(f"[INFO] MetalWatch started at {now.isoformat()}")
    print(f"[INFO] Search terms: {len(get_terms())}; minimum price: {min_price:,} RUB; minimum days left: {min_days_left}")
    fetched = await fetch_all()
    selected = [t for t in fetched if passes_filters(t, now, min_price, min_days_left)]
    selected.sort(key=lambda t: (t.price_rub or 0, t.deadline or ""), reverse=True)
    csv_path = export_csv(selected, output_dir, now)
    print(f"[INFO] Fetched {len(fetched)} unique notices; {len(selected)} passed filters. CSV: {csv_path}")

    conn = configure_db(db_path)
    try:
        unseen = load_unseen(conn, selected)
        unseen.sort(key=lambda t: (t.price_rub or 0, t.deadline or ""), reverse=True)
        print(f"[INFO] {len(unseen)} unnotified notices.")
        for t in unseen[:20]:
            print(f"- {(t.price_rub or 0):,} RUB | {t.deadline or 'no deadline'} | {t.title} | {t.url}")
        if dry_run or not unseen:
            return 0
        # Limit each scheduled digest to 12 notices so important leads are not buried.
        batch = unseen[:12]
        sent = await send_telegram(chunk_digest(batch, now, max_items=12))
        if sent:
            mark_notified(conn, batch, now)
            print("[INFO] Digest delivered; notified items saved.")
        else:
            print("[WARN] Digest not delivered; items will be retried next run.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
