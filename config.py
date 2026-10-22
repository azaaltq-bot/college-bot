"""Настройки бота.

Секретные ключи НЕ хранятся в коде: бот берёт их из переменных окружения.
На Railway они задаются во вкладке Variables:
  BOT_TOKEN         — токен от @BotFather
  DEEPSEEK_API_KEY  — ключ DeepSeek
  ADMIN_IDS         — Telegram ID админов через запятую (необязательно)
  DB_PATH           — где хранить базу, например /data/college_bot.db
"""

import os
from datetime import date

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Только эти пользователи могут выполнять админ-команды и загружать расписание по фото
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "1431708118").replace(" ", "").split(",") if x}

# Файл базы данных. На Railway обязательно укажи путь внутри Volume (см. README),
# иначе база будет стираться при каждом перезапуске.
DB_PATH = os.getenv("DB_PATH", "college_bot.db")

# Часовой пояс техникума. Чита = UTC+9.
TIMEZONE = "Asia/Chita"

# Дата начала семестра. Неделя, в которую попадает эта дата, считается ЧИСЛИТЕЛЕМ,
# следующая — знаменателем, и так далее по очереди.
# Во втором семестре поменяй дату (например, на начало января).
SEMESTER_START = date(2026, 9, 1)

# ---------- ИИ для распознавания расписания по фото ----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# Модель DeepSeek, которая умеет читать картинки (экспериментальная).
# Если DeepSeek её переименует — поменяй здесь или задай переменную DEEPSEEK_MODEL.
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash-vision-exp")
