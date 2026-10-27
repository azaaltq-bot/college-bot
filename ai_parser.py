"""Распознавание расписания по картинке с помощью ИИ.

Основной ИИ — Nemotron 3 Ultra (переменная AI_API_KEY, любой сервис с OpenAI-совместимым API).
Он читает ТЕКСТ: расписание и звонки можно прислать текстом в любом виде.
Для ФОТО нужна модель с «глазами»: AI_VISION_MODEL (тот же сервис) либо запасные Claude / DeepSeek.

ИИ возвращает данные в формате JSON, а потом ответ проверяется в logic.lessons_from_ai()
и logic.bells_from_ai().
"""

import asyncio
import base64
import json
import re

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
  «числитель»/«знаменатель» (или выделена зелёным/синим), то верхняя часть (числитель, зелёная) —
  "зелёная", нижняя (знаменатель, синяя) — "синяя".
- room и teacher: если на изображении их не видно, пиши пустую строку "".
- group: название группы так, как оно написано на изображении. Если группа не указана, пиши "{group_hint}".
- bells: заполняй, только если на изображении есть время звонков со ВСЕМИ четырьмя значениями
  (начало и конец первой половины пары, начало и конец второй). Иначе пустой список [].
- Ничего не выдумывай. Пустая ячейка значит, что пары нет."""

TEXT_PROMPT = """Ты помогаешь заполнить базу расписания техникума. Ниже текст расписания занятий —
он мог быть скопирован откуда угодно, формат может быть любым (таблица, список, одной строкой).
Извлеки ВСЕ пары и верни ТОЛЬКО JSON, без пояснений и без markdown, строго в таком формате:
{"lessons": [{"group": "ТС 9-26-3", "day": "пн", "pair": 1, "subject": "Математика", "room": "214", "teacher": "Иванова И.И.", "week": "каждая"}],
 "bells": []}

Правила:
- day: одно из пн, вт, ср, чт, пт, сб.
- pair: номер пары числом.
- week: "каждая", если пара идёт каждую неделю; "зелёная" — только по зелёной неделе (числитель);
  "синяя" — только по синей неделе (знаменатель).
- room и teacher: если в тексте их нет, пиши пустую строку "".
- group: название группы так, как оно написано в тексте. Если группа не указана, пиши "{group_hint}".
- Ничего не выдумывай. Если не уверен — пропусти запись.

ТЕКСТ РАСПИСАНИЯ:
"""

BELLS_PROMPT = """Ты помогаешь заполнить расписание звонков техникума. {source}
Извлеки время пар и верни ТОЛЬКО JSON, без пояснений и без markdown, строго в таком формате:
{"bells": [{"pair": 1, "start1": "08:30", "end1": "09:15", "start2": "09:20", "end2": "10:05"}],
 "lunches": [{"start": "11:50", "end": "12:20"}]}

Правила:
- Время в формате ЧЧ:ММ (24 часа). Точки и двоеточия исправляй: 8.30 → 08:30.
- Пара в техникуме состоит из двух половин. Если указаны обе (например «08:30–09:15, 09:20–10:05»),
  заполни start1, end1, start2, end2.
- Если указано только начало и конец всей пары (например «1 пара 08:30–10:05»), верни вместо четырёх
  значений два: {"pair": 1, "start": "08:30", "end": "10:05"} — бот сам разделит пару пополам.
- lunches — только то, что названо обедом или большой переменой на обед. Обычные короткие перемены
  между парами в lunches не клади.
- Если обеда нет, lunches — пустой список [].
- Ничего не выдумывай. Если время нечитаемо — пропусти эту пару.
"""

BELLS_TEXT_SOURCE = "Ниже текст расписания звонков (формат может быть любым).\n\nТЕКСТ ЗВОНКОВ:\n"
BELLS_IMAGE_SOURCE = "На изображении расписание звонков."

# Claude принимает только эти форматы картинок
CLAUDE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# Лимит Claude API — 10 МБ на картинку в base64 (это примерно 7,5 МБ исходного файла)
CLAUDE_MAX_BASE64_BYTES = 10 * 1024 * 1024


class AIError(Exception):
    """Ошибка, понятная человеку: её текст бот покажет админу."""


def provider_name() -> str:
    """Какой ИИ разбирает ТЕКСТ: 'custom' (Nemotron), 'claude', 'deepseek' или '' (ключей нет)."""
    if config.AI_API_KEY:
        return "custom"
    if config.ANTHROPIC_API_KEY:
        return "claude"
    if config.DEEPSEEK_API_KEY:
        return "deepseek"
    return ""


def vision_provider() -> str:
    """Какой ИИ умеет читать ФОТО ('' — никакой)."""
    if config.AI_API_KEY and config.AI_VISION_MODEL:
        return "custom"
    if config.ANTHROPIC_API_KEY:
        return "claude"
    if config.DEEPSEEK_API_KEY:
        return "deepseek"
    return ""


NO_VISION_MESSAGE = (
    "📷 Модель Nemotron 3 Ultra читает только текст, картинки она не видит.\n"
    "Пришли расписание текстом: скопируй его как есть (формат любой), ИИ сам его разберёт."
)


def extract_json(text: str) -> dict:
    """Достаёт JSON из ответа модели, даже если она обернула его в ```json ... ```."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)   # «рассуждения» модели
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


# ---------- Nemotron и другие сервисы с OpenAI-совместимым API ----------

def resolve_base_url() -> str:
    """Адрес сервиса ИИ: из AI_BASE_URL или по началу ключа."""
    if config.AI_BASE_URL:
        return config.AI_BASE_URL.rstrip("/")
    key = config.AI_API_KEY
    if key.startswith("nvapi-"):
        return "https://integrate.api.nvidia.com/v1"
    if key.startswith("sk-or-"):
        return "https://openrouter.ai/api/v1"
    raise AIError("Не знаю адрес сервиса ИИ. Добавь в Railway переменную AI_BASE_URL "
                  "(адрес API, который указан в документации сервиса, обычно заканчивается на /v1).")


def explain_custom_error(status: int, body: str, model: str) -> str:
    if status == 401:
        return "Сервис ИИ не принял ключ. Проверь переменную AI_API_KEY в Railway."
    if status in (402, 403):
        return "Сервис ИИ отказал в доступе: возможно, закончился бесплатный лимит или ключ не подходит к модели."
    if status == 404:
        return (f"Модель «{model}» не найдена у сервиса. Открой каталог моделей своего сервиса, "
                "скопируй точное название Nemotron 3 Ultra и вставь его в переменную AI_MODEL в Railway.")
    if status == 429:
        return "Слишком много запросов к ИИ (бесплатный лимит). Попробуй через минуту."
    if status >= 500:
        return "Сервис ИИ сейчас перегружен. Попробуй через пару минут."
    short = body.strip().replace("\n", " ")[:300]
    return f"ИИ отклонил запрос (код {status}): {short}"


async def _chat_openai(base_url: str, key: str, model: str, content, explain, max_tokens: int = 8000) -> str:
    """Один запрос в формате OpenAI chat/completions. Возвращает текст ответа."""
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": content}]}
    status, body = await _post(base_url.rstrip("/") + "/chat/completions", payload,
                               {"Authorization": f"Bearer {key}"})
    if status != 200:
        raise AIError(explain(status, body, model))
    try:
        data = json.loads(body)
        choice = data["choices"][0]
        answer = choice["message"].get("content")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        raise AIError("ИИ прислал непонятный ответ. Попробуй ещё раз.")
    if choice.get("finish_reason") == "length":
        raise AIError("Ответ ИИ получился слишком длинным. Пришли данные частями (например, по одной группе).")
    if isinstance(answer, list):   # некоторые сервисы отдают ответ списком блоков
        answer = "".join(block.get("text", "") for block in answer if isinstance(block, dict))
    if not answer or not answer.strip():
        raise AIError("ИИ вернул пустой ответ. Попробуй ещё раз.")
    return answer


def _text_prompt(kind: str, text: str, group_hint=None) -> str:
    if kind == "bells":
        return BELLS_PROMPT.replace("{source}", BELLS_TEXT_SOURCE) + text
    return TEXT_PROMPT.replace("{group_hint}", group_hint or "") + text


def _image_prompt(kind: str, group_hint=None) -> str:
    if kind == "bells":
        return BELLS_PROMPT.replace("{source}", BELLS_IMAGE_SOURCE)
    return PROMPT.replace("{group_hint}", group_hint or "")


async def analyze_text(kind: str, text: str, group_hint=None) -> dict:
    """Разбирает текст расписания (kind='schedule') или звонков (kind='bells') -> JSON от ИИ."""
    text = (text or "").strip()
    if not text:
        raise AIError("Пустой текст. Пришли расписание словами или скопированным текстом.")
    if len(text) > 30000:
        raise AIError("Текст слишком длинный. Пришли его частями (например, по одной группе).")
    prompt = _text_prompt(kind, text, group_hint)
    provider = provider_name()
    if provider == "custom":
        answer = await _chat_openai(resolve_base_url(), config.AI_API_KEY, config.AI_MODEL,
                                    prompt, explain_custom_error)
    elif provider == "claude":
        payload = {"model": config.CLAUDE_MODEL, "max_tokens": 8000, "temperature": 0,
                   "messages": [{"role": "user", "content": prompt}]}
        headers = {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        status, body = await _post(config.ANTHROPIC_BASE_URL.rstrip("/") + "/v1/messages", payload, headers)
        if status != 200:
            raise AIError(explain_claude_error(status, body))
        try:
            answer = "".join(b.get("text", "") for b in json.loads(body)["content"] if b.get("type") == "text")
        except (json.JSONDecodeError, KeyError, TypeError):
            raise AIError("Claude прислал непонятный ответ. Попробуй ещё раз.")
    elif provider == "deepseek":
        answer = await _chat_openai(config.DEEPSEEK_BASE_URL, config.DEEPSEEK_API_KEY, config.DEEPSEEK_MODEL,
                                    prompt, lambda s, b, m: _explain_deepseek_error(s, b))
    else:
        raise AIError("Не задан ключ ИИ. Добавь в Railway переменную AI_API_KEY.")
    return extract_json(answer)


async def recognize_image(kind: str, image: bytes, mime: str, group_hint=None) -> dict:
    """Читает ФОТО расписания (kind='schedule') или звонков (kind='bells')."""
    provider = vision_provider()
    if not provider:
        raise AIError(NO_VISION_MESSAGE)
    prompt = _image_prompt(kind, group_hint)
    if provider == "custom":
        image_b64 = base64.b64encode(image).decode()
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}]
        answer = await _chat_openai(resolve_base_url(), config.AI_API_KEY, config.AI_VISION_MODEL,
                                    content, explain_custom_error)
        return extract_json(answer)
    if provider == "claude":
        payload, headers = build_claude_request(image, mime, None)
        payload["messages"][0]["content"][1]["text"] = prompt
        status, body = await _post(config.ANTHROPIC_BASE_URL.rstrip("/") + "/v1/messages", payload, headers)
        if status != 200:
            raise AIError(explain_claude_error(status, body))
        try:
            data = json.loads(body)
            answer = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (json.JSONDecodeError, KeyError, TypeError):
            raise AIError("Claude прислал непонятный ответ. Попробуй ещё раз.")
        return extract_json(answer)
    image_b64 = base64.b64encode(image).decode()
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}", "detail": "high"}}]
    answer = await _chat_openai(config.DEEPSEEK_BASE_URL, config.DEEPSEEK_API_KEY, config.DEEPSEEK_MODEL,
                                content, lambda s, b, m: _explain_deepseek_error(s, b))
    return extract_json(answer)


# ---------- Главная функция ----------

async def recognize_schedule(image: bytes, mime: str, group_hint=None) -> dict:
    """Отправляет фото расписания в ИИ и возвращает распознанный JSON."""
    return await recognize_image("schedule", image, mime, group_hint)
