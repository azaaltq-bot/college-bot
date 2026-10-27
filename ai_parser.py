"""Распознавание расписания по картинке с помощью ИИ.

Поддерживаются два варианта (выбирается автоматически по тому, какой ключ задан):
  1. Claude (Anthropic)  — переменная ANTHROPIC_API_KEY  (основной вариант)
  2. DeepSeek            — переменная DEEPSEEK_API_KEY   (запасной)

ИИ возвращает пары в формате JSON, а потом ответ проверяется в logic.lessons_from_ai().
"""

import asyncio
import base64
import json

import aiohttp

import config

PROMPT = """Ты помогаешь заполнить базу расписания техникума. На изображении расписание занятий.
Извлеки ВСЕ пары и верни ТОЛЬКО JSON, без пояснений и без markdown, строго в таком формате:
{"lessons": [{"group": "ТС 9-26-3", "day": "пн", "pair": 1, "subject": "Математика", "room": "214", "teacher": "Иванова И.И.", "week": "каждая"}],
 "bells": [{"pair": 1, "start1": "08:30", "end1": "09:15", "start2": "09:20", "end2": "10:05"}]}

Правила:
- day: одно из пн, вт, ср, чт, пт, сб.
- pair: номер пары числом.
- week: "каждая", если пара идёт каждую неделю. Если ячейка разделена на две части или подписана
  «числитель»/«знаменатель», то верхняя часть — "числитель", нижняя — "знаменатель".
- room и teacher: если на изображении их не видно, пиши пустую строку "".
- group: название группы так, как оно написано на изображении. Если группа не указана, пиши "{group_hint}".
- bells: заполняй, только если на изображении есть время звонков со ВСЕМИ четырьмя значениями
  (начало и конец первой половины пары, начало и конец второй). Иначе пустой список [].
- Ничего не выдумывай. Пустая ячейка значит, что пары нет."""

# Claude принимает только эти форматы картинок
CLAUDE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# Лимит Claude API — 10 МБ на картинку в base64 (это примерно 7,5 МБ исходного файла)
CLAUDE_MAX_BASE64_BYTES = 10 * 1024 * 1024


class AIError(Exception):
    """Ошибка, понятная человеку: её текст бот покажет админу."""


def provider_name() -> str:
    """Какой ИИ сейчас используется: 'claude', 'deepseek' или '' (ключей нет)."""
    if config.ANTHROPIC_API_KEY:
        return "claude"
    if config.DEEPSEEK_API_KEY:
        return "deepseek"
    return ""


def extract_json(text: str) -> dict:
    """Достаёт JSON из ответа модели, даже если она обернула его в ```json ... ```."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AIError("ИИ ответил не в том формате. Попробуй отправить картинку ещё раз.")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise AIError("ИИ ответил не в том формате. Попробуй отправить картинку ещё раз.")
    if not isinstance(data, dict):
        raise AIError("ИИ ответил не в том формате. Попробуй отправить картинку ещё раз.")
    return data


async def _post(url: str, payload: dict, headers: dict):
    """Отправляет запрос и возвращает (код ответа, текст ответа)."""
    try:
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                return response.status, await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise AIError("Не удалось связаться с ИИ. Проверь интернет и попробуй ещё раз.")


# ---------- Claude (Anthropic) ----------

def build_claude_request(image: bytes, mime: str, group_hint=None):
    """Собирает запрос к Claude. Вынесено отдельно, чтобы это можно было проверить тестом."""
    if mime not in CLAUDE_IMAGE_TYPES:
        raise AIError("Claude понимает только JPG, PNG, GIF и WEBP. Пришли картинку в одном из этих форматов.")
    image_b64 = base64.b64encode(image).decode()
    if len(image_b64) > CLAUDE_MAX_BASE64_BYTES:
        raise AIError("Картинка слишком большая (больше ~7 МБ). Сделай скриншот или сожми фото.")

    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": 8000,  # расписание на неделю может быть длинным
        "temperature": 0,    # без «фантазии»: нам нужна точность
        "messages": [{
            "role": "user",
            "content": [
                # Документация Claude советует ставить картинку перед текстом
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime, "data": image_b64}},
                {"type": "text", "text": PROMPT.replace("{group_hint}", group_hint or "")},
            ],
        }],
    }
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    return payload, headers


def explain_claude_error(status: int, body: str) -> str:
    try:
        message = json.loads(body).get("error", {}).get("message", "")
    except (json.JSONDecodeError, AttributeError):
        message = ""
    low = message.lower()

    if status == 401:
        return "Claude не принял API-ключ. Проверь переменную ANTHROPIC_API_KEY в Railway."
    if "credit balance" in low or status == 402:
        return "На балансе Anthropic закончились деньги. Пополни его в Claude Console (раздел Billing)."
    if status == 403:
        return f"Anthropic отказал в доступе (403). {message}".strip()
    if status == 404:
        return (f"Модель «{config.CLAUDE_MODEL}» не найдена. Проверь переменную CLAUDE_MODEL "
                f"(например claude-sonnet-5-5).")
    if status == 429:
        return "Слишком много запросов к Claude. Попробуй через минуту."
    if status >= 500:
        return "Серверы Claude сейчас перегружены. Попробуй через пару минут."
    short = (message or body).strip().replace("\n", " ")[:300]
    return f"Claude отклонил запрос (код {status}): {short}"


async def _recognize_claude(image: bytes, mime: str, group_hint=None) -> dict:
    payload, headers = build_claude_request(image, mime, group_hint)
    status, body = await _post(config.ANTHROPIC_BASE_URL.rstrip("/") + "/v1/messages", payload, headers)
    if status != 200:
        raise AIError(explain_claude_error(status, body))
    try:
        data = json.loads(body)
        answer = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
    except (json.JSONDecodeError, KeyError, TypeError):
        raise AIError("Claude прислал непонятный ответ. Попробуй ещё раз.")
    if data.get("stop_reason") == "max_tokens":
        raise AIError("Расписание слишком большое для одного раза. Пришли его частями (например, по одной группе).")
    return extract_json(answer)


# ---------- DeepSeek (запасной вариант) ----------

def _explain_deepseek_error(status: int, body: str) -> str:
    if status == 401:
        return "DeepSeek не принял API-ключ. Проверь переменную DEEPSEEK_API_KEY."
    if status == 402:
        return "На балансе DeepSeek нет денег. Пополни его на platform.deepseek.com."
    if status == 429:
        return "Слишком много запросов к DeepSeek. Попробуй через минуту."
    if status >= 500:
        return "Сервер DeepSeek сейчас не отвечает. Попробуй позже."
    short = body.strip().replace("\n", " ")[:300]
    return (f"DeepSeek отклонил запрос (код {status}): {short}\n"
            f"Если речь о модели — проверь переменную DEEPSEEK_MODEL.")


async def _recognize_deepseek(image: bytes, mime: str, group_hint=None) -> dict:
    image_b64 = base64.b64encode(image).decode()
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.replace("{group_hint}", group_hint or "")},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{image_b64}", "detail": "high"}},
            ],
        }],
    }
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}
    url = config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    status, body = await _post(url, payload, headers)
    if status != 200:
        raise AIError(_explain_deepseek_error(status, body))
    try:
        answer = json.loads(body)["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise AIError("DeepSeek прислал непонятный ответ. Попробуй ещё раз.")
    return extract_json(answer)


# ---------- Главная функция ----------

async def recognize_schedule(image: bytes, mime: str, group_hint=None) -> dict:
    """Отправляет картинку в ИИ и возвращает распознанный JSON."""
    provider = provider_name()
    if provider == "claude":
        return await _recognize_claude(image, mime, group_hint)
    if provider == "deepseek":
        return await _recognize_deepseek(image, mime, group_hint)
    raise AIError("Не задан ключ ИИ. Добавь в Railway переменную ANTHROPIC_API_KEY.")
