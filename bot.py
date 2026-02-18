"""
Bot de Telegram que scrape relatos y los publica en Telegraph automáticamente.
"""

import asyncio
import logging
import json
import os
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from telegraph import Telegraph
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────
# CONFIGURACIÓN  ←  edita estos valores
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = "TU_BOT_TOKEN_AQUI"          # @BotFather
CHAT_ID          = "TU_CHAT_ID_AQUI"            # ID del canal/grupo/usuario
TELEGRAPH_AUTHOR = "Mi Canal"                    # Nombre del autor en Telegraph
BASE_URL         = "https://sexosintabues30.com/relatos-eroticos/dominacion-hombres/"
INTERVAL_HOURS   = 6                             # Cada cuántas horas revisa el sitio
PUBLISHED_FILE   = "published.json"             # Archivo para recordar lo ya publicado
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

def get_story_links(page_url: str) -> list[dict]:
    """Obtiene lista de relatos (título + URL) desde la página principal."""
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error al acceder a la lista: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stories = []

    # Selector genérico; ajusta si el sitio usa otra estructura
    for a in soup.select("article a, h2 a, h3 a, .entry-title a"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if href and title and BASE_URL.split("/")[2] in href:
            if {"title": title, "url": href} not in stories:
                stories.append({"title": title, "url": href})

    logger.info(f"Encontrados {len(stories)} relatos en la página.")
    return stories


def get_story_content(story_url: str) -> str:
    """Descarga y limpia el contenido HTML de un relato."""
    try:
        resp = requests.get(story_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error al descargar relato {story_url}: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Intentamos varios selectores comunes para el contenido
    for selector in [".entry-content", ".post-content", "article .content", "article"]:
        content = soup.select_one(selector)
        if content:
            # Limpiamos scripts, estilos y anuncios
            for tag in content.select("script, style, .sharedaddy, .jp-relatedposts, ins"):
                tag.decompose()
            return str(content)

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
    """Publica el relato en Telegraph y devuelve la URL."""
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

async def check_and_publish(bot: Bot):
    """Revisa el sitio, publica relatos nuevos y notifica en Telegram."""
    logger.info("Iniciando revisión del sitio...")
    published = load_published()
    stories = get_story_links(BASE_URL)

    new_count = 0
    for story in stories:
        url = story["url"]
        title = story["title"]

        if url in published:
            continue  # ya fue publicado

        logger.info(f"Nuevo relato encontrado: {title}")
        content = get_story_content(url)
        if not content:
            continue

        try:
            telegraph_url = publish_to_telegraph(title, content)
            published.add(url)
            save_published(published)
            new_count += 1

            # Notificar al canal
            message = (
                f"📖 *{title}*\n\n"
                f"🔗 [Leer en Telegraph]({telegraph_url})\n\n"
                f"_Publicado automáticamente el {datetime.now().strftime('%d/%m/%Y %H:%M')}_"
            )
            await bot.send_message(
                chat_id=CHAT_ID,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
            logger.info(f"Publicado: {telegraph_url}")

            # Pausa para no saturar las APIs
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
        "📌 Comandos disponibles:\n"
        "• /check — forzar revisión ahora\n"
        "• /status — ver cuántos relatos hay publicados",
        parse_mode="Markdown",
    )


async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando el sitio ahora mismo...")
    await check_and_publish(context.bot)


async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    published = load_published()
    await update.message.reply_text(
        f"📊 Relatos publicados hasta ahora: *{len(published)}*",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))

    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_and_publish,
        trigger="interval",
        hours=INTERVAL_HOURS,
        args=[app.bot],
        next_run_time=datetime.now(),  # primera ejecución inmediata
    )
    scheduler.start()

    logger.info(f"Bot iniciado. Revisando cada {INTERVAL_HOURS} horas.")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
