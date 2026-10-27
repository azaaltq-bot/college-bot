"""Настройки бота.

Секретные ключи НЕ хранятся в коде: бот берёт их из переменных окружения.
На Railway они задаются во вкладке Variables:
  BOT_TOKEN          — токен от @BotFather
  ANTHROPIC_API_KEY  — ключ Claude (для распознавания расписания по фото)
  DB_PATH            — где хранить базу, например /data/college_bot.db
  ADMIN_IDS          — Telegram ID админов через запятую (необязательно)
  CLAUDE_MODEL       — какую модель Claude использовать (необязательно)
"""

import os
from datetime import date

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Только эти пользователи могут выполнять админ-команды и загружать расписание по фото
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

# Файл базы данных. На Railway обязательно укажи путь внутри Volume (см. README),
# иначе база будет стираться при каждом перезапуске.
DB_PATH = os.getenv("DB_PATH", "college_bot.db")

# Часовой пояс техникума. Чита = UTC+9.
TIMEZONE = "Asia/Chita"

# Дата начала семестра. Неделя, в которую попадает эта дата, считается ЧИСЛИТЕЛЕМ,
# следующая — знаменателем, и так далее по очереди.
# Во втором семестре поменяй дату (например, на начало января).
SEMESTER_START = date(2026, 9, 1)

# ---------- «Живая» кнопка «Текущая пара» ----------
# Как часто обновлять сообщение с таймером (в секундах). 1 = каждую секунду.
# Если Telegram начнёт ругаться на частые правки (ошибка 429), поставь 2.
LIVE_INTERVAL = float(os.getenv("LIVE_INTERVAL", "1"))
# Сколько времени таймер обновляется после нажатия, потом останавливается (экономим нагрузку).
LIVE_MAX_SECONDS = int(os.getenv("LIVE_MAX_SECONDS", str(3 * 60 * 60)))

# ---------- ИИ для распознавания расписания по фото ----------
# Основной вариант — Claude. Если ANTHROPIC_API_KEY задан, используется он.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
# claude-sonnet-5-5 — хороший баланс точности и цены.
# Дешевле: claude-haiku-5-5. Точнее, но дороже: claude-opus-5-5.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5").strip()

# Запасной вариант — DeepSeek (используется, только если ключа Claude нет).
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash-vision-exp")
