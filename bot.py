"""
Bot de Telegram que scrape relatos y los publica en Telegraph automáticamente.
"""

import asyncio
import logging
import json
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from telegraph import Telegraph
from telegram.ext import Application, CommandHandler, ContextTypes

# ─────────────────────────────────────────────
# CONFIGURACIÓN — se leen desde variables de entorno
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]
TELEGRAPH_AUTHOR = os.getenv("TELEGRAPH_AUTHOR", "Mi Canal")
BASE_URL         = "https://sexosintabues30.com/category/relatos-eroticos/gays/"
INTERVAL_HOURS   = int(os.getenv("INTERVAL_HOURS", "6"))
PUBLISHED_FILE   = "published.json"
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

# Palabras que indican que el enlace NO es un relato
SKIP_TITLES = {
    "leer más", "leer mas", "comentarios", "comentario",
    "0 comentarios", "1 comentario", "sin comentarios",
}


# ══════════════════════════════════════════════
# PERSISTENCIA
# ══════════════════════════════════════════════

def load_published() -> set:
    if os.path.exists(PUBLISHED_FILE):
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_published(published: set):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(published), f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════
# SCRAPING
# ══════════════════════════════════════════════

def get_story_links(page_url: str) -> list:
    """Obtiene solo los enlaces reales a relatos (no menús ni botones)."""
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error al acceder a la lista: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stories = []
    seen_urls = set()
    domain = BASE_URL.split("/")[2]

    # Buscamos solo enlaces dentro de títulos de artículos
    for tag in soup.select("h1 a, h2 a, h3 a, .entry-title a, .post-title a"):
        href = a_href = tag.get("href", "").strip()
        title = tag.get_text(strip=True)

        # Filtros de limpieza
        if not href or not title:
            continue
        if domain not in href:
            continue
        if href in seen_urls:
            continue
        if len(title) < 8:  # títulos muy cortos son basura (autores, números)
            continue
        if title.lower() in SKIP_TITLES:
            continue
        if re.match(r"^\d+\s+comentario", title.lower()):
            continue
        # Evitar URLs de categorías (no son relatos individuales)
        if "/category/" in href:
            continue

        seen_urls.add(href)
        stories.append({"title": title, "url": href})

    logger.info(f"Encontrados {len(stories)} relatos válidos.")
    return stories


def clean_html_for_telegraph(html: str) -> str:
    """Convierte HTML a formato compatible con Telegraph (sin divs)."""
    soup = BeautifulSoup(html, "html.parser")

    # Eliminar elementos no deseados
    for tag in soup.select("script, style, .sharedaddy, .jp-relatedposts, ins, iframe, form, nav"):
        tag.decompose()

    # Convertir divs a párrafos
    for div in soup.find_all("div"):
        div.name = "p"

    # Convertir span a texto plano
    for span in soup.find_all("span"):
        span.unwrap()

    # Limpiar atributos innecesarios (Telegraph solo permite ciertos)
    allowed_tags = {"p", "br", "strong", "em", "b", "i", "a", "ul", "ol", "li",
                    "h3", "h4", "blockquote", "figure", "figcaption", "img"}
    for tag in soup.find_all(True):
        if tag.name not in allowed_tags:
            tag.unwrap()
        else:
            # Solo conservar atributo href en <a> y src en <img>
            attrs = {}
            if tag.name == "a" and tag.get("href"):
                attrs["href"] = tag["href"]
            if tag.name == "img" and tag.get("src"):
                attrs["src"] = tag["src"]
            tag.attrs = attrs

    return str(soup)


def get_story_content(story_url: str) -> str:
    try:
        resp = requests.get(story_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error al descargar relato {story_url}: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    for selector in [".entry-content", ".post-content", "article .content", "article"]:
        content = soup.select_one(selector)
        if content:
            return clean_html_for_telegraph(str(content))

    return "<p>No se pudo extraer el contenido.</p>"


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
    published = load_published()
    stories = get_story_links(BASE_URL)
    new_count = 0

    for story in stories:
        url = story["url"]
        title = story["title"]

        if url in published:
            continue

        logger.info(f"Nuevo relato: {title}")
        content = get_story_content(url)
        if not content:
            continue

        try:
            telegraph_url = publish_to_telegraph(title, content)
            published.add(url)
            save_published(published)
            new_count += 1

            message = (
                f"📖 *{title}*\n\n"
                f"🔗 [Leer en Telegraph]({telegraph_url})\n\n"
                f"_Publicado el {datetime.now().strftime('%d/%m/%Y %H:%M')}_"
            )
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=message,
                parse_mode="Markdown",
            )
            logger.info(f"Publicado: {telegraph_url}")
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
        "📌 Comandos:\n"
        "• /check — revisar ahora\n"
        "• /status — relatos publicados",
        parse_mode="Markdown",
    )


async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando ahora...")
    await check_and_publish(context)


async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    published = load_published()
    await update.message.reply_text(
        f"📊 Relatos publicados: *{len(published)}*",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))

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
