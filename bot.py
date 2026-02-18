"""
Bot de Telegram que scrape relatos y los publica en Telegraph automáticamente.
Usa MongoDB Atlas para persistencia entre reinicios.
Mantiene un mensaje índice actualizado con todos los relatos publicados.
"""

import asyncio
import logging
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from telegraph import Telegraph
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import BadRequest
from pymongo import MongoClient

# ─────────────────────────────────────────────
# CONFIGURACIÓN — se leen desde variables de entorno
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]
MONGO_URI        = os.environ["MONGO_URI"]
TELEGRAPH_AUTHOR = os.getenv("TELEGRAPH_AUTHOR", "Mi Canal")
BASE_URL         = "https://sexosintabues30.com/category/relatos-eroticos/gays/"
INTERVAL_HOURS   = int(os.getenv("INTERVAL_HOURS", "12"))
MAX_PAGES        = 10
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

SKIP_TITLES = {
    "leer más", "leer mas", "comentarios", "comentario",
    "0 comentarios", "1 comentario", "sin comentarios",
}


# ══════════════════════════════════════════════
# MONGODB
# ══════════════════════════════════════════════

_db = None

def get_db():
    global _db
    if _db is None:
        client = MongoClient(MONGO_URI)
        _db = client["relatos_bot"]
    return _db

def is_published(url: str) -> bool:
    db = get_db()
    return db.published.find_one({"url": url}) is not None

def mark_published(url: str, title: str, telegraph_url: str, pub_date: str):
    db = get_db()
    db.published.update_one(
        {"url": url},
        {"$set": {
            "url": url,
            "title": title,
            "telegraph_url": telegraph_url,
            "pub_date": pub_date,
            "date": datetime.now()
        }},
        upsert=True,
    )

def count_published() -> int:
    db = get_db()
    return db.published.count_documents({})

def get_all_published() -> list:
    db = get_db()
    return list(db.published.find({}, {"_id": 0, "title": 1, "telegraph_url": 1, "pub_date": 1}).sort("date", 1))

def get_index_message_id() -> int | None:
    db = get_db()
    doc = db.config.find_one({"key": "index_message_id"})
    return doc["value"] if doc else None

def set_index_message_id(message_id: int):
    db = get_db()
    db.config.update_one(
        {"key": "index_message_id"},
        {"$set": {"key": "index_message_id", "value": message_id}},
        upsert=True,
    )


# ══════════════════════════════════════════════
# ÍNDICE
# ══════════════════════════════════════════════

def build_index_text() -> str:
    stories = get_all_published()
    if not stories:
        return "📚 *Índice de Relatos*\n\n_Aún no hay relatos publicados._"

    lines = ["📚 *Índice de Relatos*\n"]
    for i, story in enumerate(stories, 1):
        title = story["title"]
        url = story["telegraph_url"]
        pub_date = story.get("pub_date", "")
        date_str = f" _({pub_date})_" if pub_date else ""
        lines.append(f"{i}\\. [{title}]({url}){date_str}")

    lines.append(f"\n_Total: {len(stories)} relatos_")
    return "\n".join(lines)


async def update_index(bot):
    text = build_index_text()
    message_id = get_index_message_id()

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=CHAT_ID,
                message_id=message_id,
                text=text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
            logger.info("Índice actualizado.")
            return
        except BadRequest as e:
            logger.warning(f"No se pudo editar el índice: {e}. Creando uno nuevo.")

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )
    set_index_message_id(msg.message_id)
    logger.info(f"Índice creado con message_id={msg.message_id}")


# ══════════════════════════════════════════════
# SCRAPING
# ══════════════════════════════════════════════

def get_story_links_from_page(page_url: str, domain: str) -> list:
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error al acceder a {page_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stories = []
    seen_urls = set()

    for tag in soup.select("h1 a, h2 a, h3 a, .entry-title a, .post-title a"):
        href = tag.get("href", "").strip()
        title = tag.get_text(strip=True)

        if not href or not title:
            continue
        if domain not in href:
            continue
        if href in seen_urls:
            continue
        if len(title) < 8:
            continue
        if title.lower() in SKIP_TITLES:
            continue
        if re.match(r"^\d+\s+comentario", title.lower()):
            continue
        if "/category/" in href or "/page/" in href:
            continue

        seen_urls.add(href)
        stories.append({"title": title, "url": href})

    return stories


def get_all_story_links() -> list:
    domain = BASE_URL.split("/")[2]
    all_stories = []
    seen_urls = set()

    for page_num in range(1, MAX_PAGES + 1):
        page_url = BASE_URL if page_num == 1 else f"{BASE_URL}page/{page_num}/"
        logger.info(f"Revisando página {page_num}: {page_url}")
        stories = get_story_links_from_page(page_url, domain)

        if not stories:
            logger.info(f"Página {page_num} vacía. Deteniendo.")
            break

        for story in stories:
            if story["url"] not in seen_urls:
                seen_urls.add(story["url"])
                all_stories.append(story)

        import time; time.sleep(1)

    logger.info(f"Total relatos encontrados: {len(all_stories)}")
    return all_stories


def extract_pub_date(soup: BeautifulSoup) -> str:
    """Extrae la fecha de publicación original del relato."""
    # Intentar con etiqueta <time>
    time_tag = soup.find("time")
    if time_tag:
        # Primero intentar el atributo datetime
        dt = time_tag.get("datetime", "")
        if dt:
            try:
                d = datetime.fromisoformat(dt[:10])
                return d.strftime("%d/%m/%Y")
            except Exception:
                pass
        # Si no, usar el texto visible
        text = time_tag.get_text(strip=True)
        if text:
            return text

    # Intentar con clases comunes de WordPress
    for selector in [".entry-date", ".post-date", ".published", ".date", "span.date"]:
        el = soup.select_one(selector)
        if el:
            return el.get_text(strip=True)

    return ""


def clean_html_for_telegraph(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.select("script, style, .sharedaddy, .jp-relatedposts, ins, iframe, form, nav"):
        tag.decompose()

    for div in soup.find_all("div"):
        div.name = "p"

    for span in soup.find_all("span"):
        span.unwrap()

    allowed_tags = {"p", "br", "strong", "em", "b", "i", "a", "ul", "ol", "li",
                    "h3", "h4", "blockquote", "figure", "figcaption", "img"}
    for tag in soup.find_all(True):
        if tag.name not in allowed_tags:
            tag.unwrap()
        else:
            attrs = {}
            if tag.name == "a" and tag.get("href"):
                attrs["href"] = tag["href"]
            if tag.name == "img" and tag.get("src"):
                attrs["src"] = tag["src"]
            tag.attrs = attrs

    return str(soup).encode("utf-8").decode("utf-8")


def get_story_content(story_url: str) -> tuple[str, str]:
    """Retorna (html_content, pub_date)."""
    try:
        resp = requests.get(story_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        logger.error(f"Error al descargar relato {story_url}: {e}")
        return "", ""

    soup = BeautifulSoup(resp.text, "html.parser")
    pub_date = extract_pub_date(soup)

    for selector in [".entry-content", ".post-content", "article .content", "article"]:
        content = soup.select_one(selector)
        if content:
            return clean_html_for_telegraph(str(content)), pub_date

    return "<p>No se pudo extraer el contenido.</p>", pub_date


# ══════════════════════════════════════════════
# TELEGRAPH
# ══════════════════════════════════════════════

_telegraph = None

def get_telegraph() -> Telegraph:
    global _telegraph
    if _telegraph is None:
        _telegraph = Telegraph()
        _telegraph.create_account(short_name=TELEGRAPH_AUTHOR)
        logger.info("Cuenta de Telegraph creada.")
    return _telegraph


def publish_to_telegraph(title: str, html_content: str) -> str:
    tph = get_telegraph()
    response = tph.create_page(
        title=title,
        html_content=html_content,
        author_name=TELEGRAPH_AUTHOR,
    )
    return f"https://telegra.ph/{response['path']}"


# ══════════════════════════════════════════════
# LÓGICA PRINCIPAL
# ══════════════════════════════════════════════

async def check_and_publish(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Iniciando revisión del sitio...")
    stories = get_all_story_links()
    new_count = 0

    for story in stories:
        url = story["url"]
        title = story["title"]

        if is_published(url):
            continue

        logger.info(f"Nuevo relato: {title}")
        content, pub_date = get_story_content(url)
        if not content:
            continue

        try:
            telegraph_url = publish_to_telegraph(title, content)
            mark_published(url, title, telegraph_url, pub_date)
            new_count += 1

            date_line = f"📅 _{pub_date}_\n\n" if pub_date else ""
            message = (
                f"📖 *{title}*\n\n"
                f"{date_line}"
                f"🔗 [Leer en Telegraph]({telegraph_url})"
            )
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=message,
                parse_mode="Markdown",
            )
            logger.info(f"Publicado: {telegraph_url}")

            await update_index(context.bot)
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Error publicando '{title}': {e}")

    logger.info(f"Revisión completada. {new_count} relatos nuevos publicados.")


# ══════════════════════════════════════════════
# COMANDOS DEL BOT
# ══════════════════════════════════════════════

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bot activo.\n\n"
        f"⏰ Reviso nuevos relatos cada *{INTERVAL_HOURS} horas*.\n"
        f"📄 Reviso hasta *{MAX_PAGES} páginas* por ciclo.\n"
        "📌 Comandos:\n"
        "• /check — revisar ahora\n"
        "• /status — relatos publicados\n"
        "• /indice — actualizar índice",
        parse_mode="Markdown",
    )


async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando ahora...")
    await check_and_publish(context)


async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    total = count_published()
    await update.message.reply_text(
        f"📊 Relatos publicados: *{total}*",
        parse_mode="Markdown",
    )


async def cmd_indice(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 Actualizando índice...")
    await update_index(context.bot)


# ══════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("indice", cmd_indice))

    job_queue = app.job_queue
    job_queue.run_repeating(
        check_and_publish,
        interval=INTERVAL_HOURS * 3600,
        first=10,
    )

    logger.info(f"Bot iniciado. Revisando cada {INTERVAL_HOURS} horas.")
    app.run_polling()


if __name__ == "__main__":
    main()
