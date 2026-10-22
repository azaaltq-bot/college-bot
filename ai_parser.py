"""Распознавание расписания по картинке через DeepSeek.

Картинка отправляется в DeepSeek, а он возвращает пары в формате JSON.
Сам ответ потом проверяется в logic.lessons_from_ai().
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


class AIError(Exception):
    """Ошибка, понятная человеку: её текст бот покажет админу."""


def _explain_http_error(status: int, body: str) -> str:
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
            f"Если речь о модели — проверь DEEPSEEK_MODEL в config.py.")


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


async def recognize_schedule(image: bytes, mime: str, group_hint=None) -> dict:
    """Отправляет картинку в DeepSeek и возвращает распознанный JSON."""
    if not config.DEEPSEEK_API_KEY:
        raise AIError("Не задан ключ DeepSeek (переменная DEEPSEEK_API_KEY).")
    image_b64 = base64.b64encode(image).decode()
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "temperature": 0,  # без «фантазии»: нам нужна точность
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

    try:
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                body = await response.text()
                if response.status != 200:
                    raise AIError(_explain_http_error(response.status, body))
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise AIError("Не удалось связаться с DeepSeek. Проверь интернет и попробуй ещё раз.")

    try:
        answer = json.loads(body)["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise AIError("DeepSeek прислал непонятный ответ. Попробуй ещё раз.")
    return extract_json(answer)
