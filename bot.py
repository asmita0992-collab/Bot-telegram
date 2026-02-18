"""
Bot de Telegram - Relatos eróticos multi-categoría
- Scraping de sexosintabues30.com
- Publicación en Telegraph
- Índice por categoría con botones colapsables
- Persistencia en MongoDB Atlas
"""

import asyncio
import logging
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from bs4 import BeautifulSoup
from telegraph import Telegraph
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest
from pymongo import MongoClient

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]
MONGO_URI        = os.environ["MONGO_URI"]
TELEGRAPH_AUTHOR = os.getenv("TELEGRAPH_AUTHOR", "Mi Canal")
INTERVAL_HOURS   = int(os.getenv("INTERVAL_HOURS", "12"))
MAX_PAGES        = 10
MAX_CONTENT_SIZE = 30000

CATEGORIES = {
    "gays":                  {"name": "🏳️‍🌈 Gays",                "url": "https://sexosintabues30.com/category/relatos-eroticos/gays/"},
    "dominacion":            {"name": "⛓️ Dominación",             "url": "https://sexosintabues30.com/category/relatos-eroticos/dominacion-hombres/"},
    "fantasias":             {"name": "💭 Fantasías y Parodias",   "url": "https://sexosintabues30.com/category/relatos-eroticos/fantasias-parodias/"},
    "incestos":              {"name": "👨‍👩‍👧 Incestos",               "url": "https://sexosintabues30.com/category/relatos-eroticos/incestos-en-familia/"},
    "heterosexual":          {"name": "👫 Heterosexual",           "url": "https://sexosintabues30.com/category/relatos-eroticos/heterosexual/"},
    "travestis":             {"name": "⚧️ Travestis/Transexuales", "url": "https://sexosintabues30.com/category/relatos-eroticos/travestis-transexuales/"},
    "zoofilia":              {"name": "🐾 Zoofilia",               "url": "https://sexosintabues30.com/category/relatos-eroticos/zoofilia-hombre/"},
}

SKIP_TITLES = {
    "leer más", "leer mas", "comentarios", "comentario",
    "0 comentarios", "1 comentario", "sin comentarios",
}

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ══════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def start_health_server():
    server = HTTPServer(("0.0.0.0", 8000), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health check en puerto 8000.")


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
    return get_db().published.find_one({"url": url}) is not None

def mark_published(url: str, title: str, telegraph_url: str, pub_date: str, category: str):
    get_db().published.update_one(
        {"url": url},
        {"$set": {
            "url": url, "title": title, "telegraph_url": telegraph_url,
            "pub_date": pub_date, "category": category, "date": datetime.now()
        }},
        upsert=True,
    )

def get_published_by_category(category: str, limit: int = 0) -> list:
    cursor = get_db().published.find(
        {"category": category},
        {"_id": 0, "title": 1, "telegraph_url": 1, "pub_date": 1}
    ).sort("date", -1)
    if limit:
        cursor = cursor.limit(limit)
    return list(reversed(list(cursor)))

def count_published() -> int:
    return get_db().published.count_documents({})

def count_by_category(category: str) -> int:
    return get_db().published.count_documents({"category": category})

def get_index_message_id() -> int | None:
    doc = get_db().config.find_one({"key": "index_message_id"})
    return doc["value"] if doc else None

def set_index_message_id(message_id: int):
    get_db().config.update_one(
        {"key": "index_message_id"},
        {"$set": {"key": "index_message_id", "value": message_id}},
        upsert=True,
    )


# ══════════════════════════════════════════════
# ÍNDICE CON BOTONES COLAPSABLES
# ══════════════════════════════════════════════

def build_index_summary() -> tuple[str, InlineKeyboardMarkup]:
    """Mensaje principal del índice con botones por categoría."""
    lines = ["📚 <b>Índice de Relatos</b>\n"]
    buttons = []

    for cat_id, cat in CATEGORIES.items():
        count = count_by_category(cat_id)
        lines.append(f"{cat['name']}: <b>{count}</b> relatos")
        buttons.append([InlineKeyboardButton(
            f"{cat['name']} ({count})",
            callback_data=f"cat_{cat_id}"
        )])

    total = count_published()
    lines.append(f"\n<i>Total: {total} relatos</i>")

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def update_index(bot):
    """Actualiza o crea el mensaje índice principal."""
    try:
        text, keyboard = build_index_summary()
        message_id = get_index_message_id()

        if message_id:
            try:
                await bot.edit_message_text(
                    chat_id=CHAT_ID,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                logger.info("Índice actualizado.")
                return
            except BadRequest as e:
                logger.warning(f"No se pudo editar índice: {e}. Creando nuevo.")

        msg = await bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        set_index_message_id(msg.message_id)
        logger.info(f"Índice creado: message_id={msg.message_id}")

    except Exception as e:
        logger.error(f"Error actualizando índice: {e}")


async def callback_category(update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los últimos 25 relatos de la categoría."""
    query = update.callback_query
    await query.answer()

    cat_id = query.data.replace("cat_", "")
    cat = CATEGORIES.get(cat_id)
    if not cat:
        return

    total = count_by_category(cat_id)
    stories = get_published_by_category(cat_id, limit=25)

    if not stories:
        await query.message.reply_text(
            f"{cat['name']}\n\n<i>No hay relatos publicados aún.</i>",
            parse_mode="HTML",
        )
        return

    lines = [f"<b>{cat['name']}</b>"]
    if total > 25:
        lines.append(f"<i>Últimos 25 de {total} relatos</i>\n")
    else:
        lines.append(f"<i>{total} relatos</i>\n")

    for i, story in enumerate(stories, 1):
        title = story.get("title", "Sin título")
        url = story.get("telegraph_url", "")
        pub_date = story.get("pub_date", "")
        date_str = f" <i>({pub_date})</i>" if pub_date else ""
        if url:
            lines.append(f'{i}. <a href="{url}">{title}</a>{date_str}')
        else:
            lines.append(f"{i}. {title}{date_str}")

    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4090] + "\n..."

    await query.message.reply_text(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ══════════════════════════════════════════════
# SCRAPING
# ══════════════════════════════════════════════

def get_story_links_from_page(page_url: str, domain: str) -> list:
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error accediendo {page_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stories = []
    seen_urls = set()

    for tag in soup.select("h1 a, h2 a, h3 a, .entry-title a, .post-title a"):
        href = tag.get("href", "").strip()
        title = tag.get_text(strip=True)
        if not href or not title: continue
        if domain not in href: continue
        if href in seen_urls: continue
        if len(title) < 8: continue
        if title.lower() in SKIP_TITLES: continue
        if re.match(r"^\d+\s+comentario", title.lower()): continue
        if "/category/" in href or "/page/" in href: continue
        seen_urls.add(href)
        stories.append({"title": title, "url": href})

    return stories


def get_all_story_links(base_url: str) -> list:
    domain = base_url.split("/")[2]
    all_stories = []
    seen_urls = set()

    for page_num in range(1, MAX_PAGES + 1):
        page_url = base_url if page_num == 1 else f"{base_url}page/{page_num}/"
        logger.info(f"  Página {page_num}: {page_url}")
        stories = get_story_links_from_page(page_url, domain)
        if not stories:
            logger.info(f"  Página {page_num} vacía. Deteniendo.")
            break
        for s in stories:
            if s["url"] not in seen_urls:
                seen_urls.add(s["url"])
                all_stories.append(s)
        import time; time.sleep(1)

    return all_stories


def extract_pub_date(soup: BeautifulSoup) -> str:
    time_tag = soup.find("time")
    if time_tag:
        dt = time_tag.get("datetime", "")
        if dt:
            try:
                return datetime.fromisoformat(dt[:10]).strftime("%d/%m/%Y")
            except Exception:
                pass
        text = time_tag.get_text(strip=True)
        if text: return text
    for selector in [".entry-date", ".post-date", ".published", ".date"]:
        el = soup.select_one(selector)
        if el: return el.get_text(strip=True)
    return ""


def clean_html_for_telegraph(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script, style, .sharedaddy, .jp-relatedposts, ins, iframe, form, nav"):
        tag.decompose()
    for div in soup.find_all("div"): div.name = "p"
    for span in soup.find_all("span"): span.unwrap()
    allowed = {"p", "br", "strong", "em", "b", "i", "a", "ul", "ol", "li", "h3", "h4", "blockquote", "figure", "figcaption", "img"}
    for tag in soup.find_all(True):
        if tag.name not in allowed:
            tag.unwrap()
        else:
            attrs = {}
            if tag.name == "a" and tag.get("href"): attrs["href"] = tag["href"]
            if tag.name == "img" and tag.get("src"): attrs["src"] = tag["src"]
            tag.attrs = attrs
    return str(soup).encode("utf-8").decode("utf-8")


def get_story_content(story_url: str) -> tuple:
    """Retorna (html_content, pub_date, real_title)"""
    try:
        resp = requests.get(story_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        logger.error(f"Error descargando {story_url}: {e}")
        return "", "", ""
    soup = BeautifulSoup(resp.text, "html.parser")
    pub_date = extract_pub_date(soup)

    # Obtener título real de la página
    real_title = ""
    for selector in [".entry-title", "h1.post-title", "h1", "title"]:
        el = soup.select_one(selector)
        if el:
            real_title = el.get_text(strip=True)
            # Limpiar sufijos típicos de WordPress como " – Nombre del Sitio"
            for sep in [" – ", " | ", " - "]:
                if sep in real_title:
                    real_title = real_title.split(sep)[0].strip()
            if real_title:
                break

    for selector in [".entry-content", ".post-content", "article .content", "article"]:
        content = soup.select_one(selector)
        if content:
            return clean_html_for_telegraph(str(content)), pub_date, real_title
    return "<p>No se pudo extraer el contenido.</p>", pub_date, real_title


# ══════════════════════════════════════════════
# TELEGRAPH
# ══════════════════════════════════════════════

_telegraph = None

def get_telegraph() -> Telegraph:
    global _telegraph
    if _telegraph is None:
        _telegraph = Telegraph()
        _telegraph.create_account(short_name=TELEGRAPH_AUTHOR)
        logger.info("Cuenta Telegraph creada.")
    return _telegraph


def publish_to_telegraph(title: str, html_content: str) -> list:
    tph = get_telegraph()

    if len(html_content) <= MAX_CONTENT_SIZE:
        r = tph.create_page(title=title, html_content=html_content, author_name=TELEGRAPH_AUTHOR)
        return [f"https://telegra.ph/{r['path']}"]

    # Dividir por párrafos
    soup = BeautifulSoup(html_content, "html.parser")
    paragraphs = soup.find_all(["p", "h3", "h4", "blockquote"])

    if len(paragraphs) < 2:
        r = tph.create_page(title=title, html_content=html_content[:MAX_CONTENT_SIZE], author_name=TELEGRAPH_AUTHOR)
        return [f"https://telegra.ph/{r['path']}"]

    mid = len(paragraphs) // 2
    part1_html = "".join(str(p) for p in paragraphs[:mid])
    part2_html = "".join(str(p) for p in paragraphs[mid:])

    r1 = tph.create_page(
        title=f"{title} – Parte 1",
        html_content=part1_html + "<p><em>Continúa en Parte 2...</em></p>",
        author_name=TELEGRAPH_AUTHOR,
    )
    url1 = f"https://telegra.ph/{r1['path']}"

    r2 = tph.create_page(
        title=f"{title} – Parte 2",
        html_content=f'<p><em><a href="{url1}">← Parte 1</a></em></p>' + part2_html,
        author_name=TELEGRAPH_AUTHOR,
    )
    url2 = f"https://telegra.ph/{r2['path']}"

    return [url1, url2]


# ══════════════════════════════════════════════
# LÓGICA PRINCIPAL
# ══════════════════════════════════════════════

async def check_and_publish(context: ContextTypes.DEFAULT_TYPE):
    total_new = 0

    for cat_id, cat in CATEGORIES.items():
        logger.info(f"Revisando categoría: {cat['name']}")
        stories = get_all_story_links(cat["url"])
        logger.info(f"  Encontrados: {len(stories)} relatos")
        new_count = 0

        for story in stories:
            url = story["url"]
            title = story["title"]

            if is_published(url):
                continue

            logger.info(f"  Nuevo: {title}")
            content, pub_date, real_title = get_story_content(url)
            if not content:
                continue

            # Usar el título real de la página si está disponible
            if real_title:
                logger.info(f"  Título original: {real_title}")
                title = real_title

            try:
                urls = publish_to_telegraph(title, content)
                telegraph_url = urls[0]
                mark_published(url, title, telegraph_url, pub_date, cat_id)
                new_count += 1
                total_new += 1

                date_line = f"📅 <i>{pub_date}</i>\n\n" if pub_date else ""
                cat_line = f"📂 <i>{cat['name']}</i>\n\n"

                if len(urls) == 1:
                    links = f'🔗 <a href="{urls[0]}">Leer en Telegraph</a>'
                else:
                    links = f'🔗 <a href="{urls[0]}">Parte 1</a> | <a href="{urls[1]}">Parte 2</a>'

                message = f"📖 <b>{title}</b>\n\n{cat_line}{date_line}{links}"
                await context.bot.send_message(
                    chat_id=CHAT_ID, text=message, parse_mode="HTML",
                )
                logger.info(f"  Publicado: {telegraph_url}")
                await update_index(context.bot)
                await asyncio.sleep(3)

            except Exception as e:
                error_str = str(e)
                logger.error(f"  Error publicando '{title}': {error_str}")
                # Si Telegraph pide esperar, respetar el tiempo y detener el ciclo
                if "FLOOD_WAIT" in error_str:
                    try:
                        wait_seconds = int(error_str.split("FLOOD_WAIT_")[1].split()[0])
                    except Exception:
                        wait_seconds = 60
                    logger.warning(f"  Telegraph flood wait: {wait_seconds}s. Pausando ciclo.")
                    await asyncio.sleep(min(wait_seconds, 3600))
                    break  # salir del loop de esta categoría y continuar en el siguiente ciclo

        logger.info(f"  {new_count} nuevos en {cat['name']}")

    logger.info(f"Revisión completada. Total nuevos: {total_new}")


# ══════════════════════════════════════════════
# COMANDOS
# ══════════════════════════════════════════════

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    cats = "\n".join(f"  • {c['name']}" for c in CATEGORIES.values())
    await update.message.reply_text(
        f"👋 Bot activo.\n\n"
        f"⏰ Reviso cada <b>{INTERVAL_HOURS} horas</b>\n"
        f"📄 Hasta <b>{MAX_PAGES} páginas</b> por categoría\n\n"
        f"📂 Categorías:\n{cats}\n\n"
        f"📌 Comandos:\n"
        f"• /check — revisar ahora\n"
        f"• /status — estadísticas\n"
        f"• /indice — mostrar índice\n"
        f"• /fix_titles — corregir títulos",
        parse_mode="HTML",
    )

async def cmd_check(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando todas las categorías...")
    await check_and_publish(context)

async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📊 <b>Relatos publicados</b>\n"]
    for cat_id, cat in CATEGORIES.items():
        count = count_by_category(cat_id)
        lines.append(f"{cat['name']}: <b>{count}</b>")
    lines.append(f"\n<b>Total: {count_published()}</b>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_indice(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 Actualizando índice...")
    await update_index(context.bot)




async def cmd_fix_titles(update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    stories = list(db.published.find({}, {"_id": 1, "url": 1, "title": 1}))
    total = len(stories)
    updated = 0
    failed = 0
    skipped = 0

    await update.message.reply_text(
        "<b>Actualizando títulos de " + str(total) + " relatos...</b>\n<i>Esto puede tardar varios minutos.</i>",
        parse_mode="HTML",
    )

    for i, story in enumerate(stories, 1):
        url = story.get("url", "")
        old_title = story.get("title", "")
        if not url:
            skipped += 1
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            real_title = ""
            for selector in [".entry-title", "h1.post-title", "h1", "title"]:
                el = soup.select_one(selector)
                if el:
                    real_title = el.get_text(strip=True)
                    for sep in [" \u2013 ", " | ", " - "]:
                        if sep in real_title:
                            real_title = real_title.split(sep)[0].strip()
                    if real_title:
                        break

            if real_title and real_title != old_title:
                db.published.update_one(
                    {"_id": story["_id"]},
                    {"$set": {"title": real_title}}
                )
                updated += 1
                logger.info("[" + str(i) + "/" + str(total) + "] '" + old_title + "' -> '" + real_title + "'")
            else:
                skipped += 1

            if i % 20 == 0:
                await update.message.reply_text(
                    "Progreso: " + str(i) + "/" + str(total) + " revisados, " + str(updated) + " actualizados...",
                )

            await asyncio.sleep(1)

        except Exception as e:
            logger.error("Error actualizando titulo de " + url + ": " + str(e))
            failed += 1

    await update.message.reply_text(
        "<b>Listo.</b>\nActualizados: <b>" + str(updated) + "</b>\nSin cambios: <b>" + str(skipped) + "</b>\nErrores: <b>" + str(failed) + "</b>",
        parse_mode="HTML",
    )

    if updated > 0:
        await update_index(context.bot)


# ══════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════

def main():
    start_health_server()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("indice", cmd_indice))
    app.add_handler(CommandHandler("fix_titles", cmd_fix_titles))
    app.add_handler(CallbackQueryHandler(callback_category, pattern="^cat_"))

    app.job_queue.run_repeating(
        check_and_publish,
        interval=INTERVAL_HOURS * 3600,
        first=10,
    )

    logger.info(f"Bot iniciado. {len(CATEGORIES)} categorías. Revisando cada {INTERVAL_HOURS}h.")

    import time
    from telegram.error import Conflict, NetworkError

    max_retries = 10
    for attempt in range(max_retries):
        try:
            app.run_polling(drop_pending_updates=True)
            break
        except Conflict:
            wait = 15 * (attempt + 1)
            logger.warning(f"Conflict: otra instancia activa. Esperando {wait}s (intento {attempt+1}/{max_retries})...")
            time.sleep(wait)
        except NetworkError as e:
            logger.warning(f"NetworkError: {e}. Reintentando en 10s...")
            time.sleep(10)
        except Exception as e:
            logger.error(f"Error inesperado: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
