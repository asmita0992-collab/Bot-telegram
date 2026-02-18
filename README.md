# 📖 Bot de Relatos — Telegram + Telegraph

Bot que scrape relatos de un sitio web y los publica automáticamente en Telegraph,
enviando una notificación a tu canal/grupo de Telegram.

---

## ⚙️ Configuración rápida

### 1. Instala las dependencias

```bash
pip install -r requirements.txt
```

### 2. Obtén tus credenciales

#### Token del bot
1. Abre Telegram y busca **@BotFather**
2. Envía `/newbot` y sigue las instrucciones
3. Copia el token que te da (formato: `123456789:ABCdef...`)

#### Chat ID
- **Canal**: copia el `@username` del canal (ej: `@mi_canal`)
  o usa un bot como @userinfobot para obtener el ID numérico.
- **Grupo o usuario**: usa @userinfobot para ver tu ID numérico.

### 3. Edita `bot.py`

Abre `bot.py` y rellena esta sección al inicio del archivo:

```python
TELEGRAM_TOKEN   = "123456789:ABCdef..."   # ← tu token
CHAT_ID          = "@mi_canal"             # ← tu canal/grupo
TELEGRAPH_AUTHOR = "Mi Canal"             # ← nombre del autor
INTERVAL_HOURS   = 6                      # ← cada cuántas horas revisar
```

### 4. Ejecuta el bot

```bash
python bot.py
```

---

## 🤖 Comandos disponibles

| Comando   | Descripción                              |
|-----------|------------------------------------------|
| `/start`  | Muestra información del bot              |
| `/check`  | Fuerza una revisión inmediata del sitio  |
| `/status` | Muestra cuántos relatos se han publicado |

---

## 📁 Archivos

| Archivo          | Descripción                                      |
|------------------|--------------------------------------------------|
| `bot.py`         | Código principal del bot                         |
| `requirements.txt` | Dependencias de Python                         |
| `published.json` | Se crea automáticamente; guarda las URLs ya publicadas |

---

## 🚀 Ejecutar en segundo plano (Linux/VPS)

```bash
# Con nohup
nohup python bot.py &

# O con screen
screen -S relatos_bot
python bot.py
# Ctrl+A, D para desconectar sin cerrar
```

---

## 🔧 Ajustar el scraper

Si el bot no extrae bien el contenido, abre `bot.py` y busca la función
`get_story_links()`. Puedes cambiar el selector CSS:

```python
# Ejemplo: si los títulos están en <h2 class="title">
for a in soup.select("h2.title a"):
```

Para identificar el selector correcto, abre la página en Chrome,
clic derecho en el título → **Inspeccionar** y mira la estructura HTML.
