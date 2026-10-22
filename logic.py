"""Логика бота: какая сейчас пара, какая неделя, разбор команд администратора.

Здесь нет ничего про Telegram — поэтому эту часть легко проверять тестами
(см. test_logic.py).
"""

import math
from datetime import date, datetime, time, timedelta

WEEKDAY_NAMES = {
    1: "Понедельник", 2: "Вторник", 3: "Среда",
    4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье",
}

DAY_ALIASES = {
    "пн": 1, "понедельник": 1,
    "вт": 2, "вторник": 2,
    "ср": 3, "среда": 3,
    "чт": 4, "четверг": 4,
    "пт": 5, "пятница": 5,
    "сб": 6, "суббота": 6,
}

# Тип недели: 0 — пара каждую неделю, 1 — только по числителю, 2 — только по знаменателю
WEEK_EVERY, WEEK_NUMERATOR, WEEK_DENOMINATOR = 0, 1, 2
WEEK_NAMES = {0: "каждую неделю", 1: "числитель", 2: "знаменатель"}
WEEK_ALIASES = {
    "каждая": 0, "все": 0, "0": 0,
    "числитель": 1, "чис": 1, "1": 1,
    "знаменатель": 2, "знам": 2, "2": 2,
}


# ---------- Разбор ввода ----------

def normalize_group(text: str) -> str:
    """'  тс   9-26-3 ' -> 'ТС 9-26-3' (чтобы одна группа не записалась по-разному)."""
    return " ".join(text.split()).upper()


def parse_time(text: str) -> time:
    """'8:30' или '08:30' -> time(8, 30). Если формат неверный — ValueError."""
    return datetime.strptime(text.strip(), "%H:%M").time()


def parse_day(text: str) -> int:
    t = text.strip().lower()
    if t.isdigit() and 1 <= int(t) <= 6:
        return int(t)
    if t in DAY_ALIASES:
        return DAY_ALIASES[t]
    raise ValueError(f"не понял день недели «{text.strip()}» (пиши пн, вт, ср, чт, пт, сб)")


def parse_week(text: str) -> int:
    t = text.strip().lower()
    if t == "":
        return WEEK_EVERY
    if t in WEEK_ALIASES:
        return WEEK_ALIASES[t]
    raise ValueError(f"не понял тип недели «{text.strip()}» (пиши каждая, числитель или знаменатель)")


def _empty_to_none(text: str):
    text = text.strip()
    return None if text in ("", "-") else text


def parse_lesson_line(line: str) -> dict:
    """Разбирает строку вида
    'ТС 9-26-3; пн; 1; Математика; 214; Иванова И.И.; каждая'
    Обязательны первые 4 поля. Кабинет и преподавателя можно пропустить или поставить '-'.
    """
    parts = [p.strip() for p in line.split(";")]
    if len(parts) < 4:
        raise ValueError("нужно минимум 4 поля: группа; день; пара; предмет")
    if len(parts) > 7:
        raise ValueError("слишком много полей (максимум 7)")
    parts += [""] * (7 - len(parts))
    group, day, pair, subject, room, teacher, week = parts

    if not group:
        raise ValueError("не указана группа")
    if not subject:
        raise ValueError("не указан предмет")
    if not pair.isdigit() or int(pair) < 1:
        raise ValueError(f"номер пары должен быть числом, а не «{pair}»")

    return {
        "group_name": normalize_group(group),
        "weekday": parse_day(day),
        "pair_num": int(pair),
        "subject": subject,
        "room": _empty_to_none(room),
        "teacher": _empty_to_none(teacher),
        "week_type": parse_week(week),
    }


# ---------- Неделя и звонки ----------

def week_type_for(day: date, semester_start: date) -> int:
    """Числитель (1) или знаменатель (2) для указанной даты."""
    first_monday = semester_start - timedelta(days=semester_start.weekday())
    weeks_passed = (day - first_monday).days // 7
    return WEEK_NUMERATOR if weeks_passed % 2 == 0 else WEEK_DENOMINATOR


def bells_from_rows(rows) -> dict:
    """Строки из таблицы bells -> {номер_пары: (начало1, конец1, начало2, конец2)}."""
    return {
        r["pair_num"]: tuple(parse_time(r[k]) for k in ("start1", "end1", "start2", "end2"))
        for r in rows
    }


# ---------- Форматирование ----------

def format_duration(delta: timedelta) -> str:
    """timedelta -> '12 мин' или '1 ч 50 мин'. Округляем вверх, чтобы не было '0 мин'."""
    minutes = math.ceil(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def lesson_title(lesson) -> str:
    text = lesson["subject"]
    if lesson["room"]:
        text += f", каб. {lesson['room']}"
    if lesson["teacher"]:
        text += f" ({lesson['teacher']})"
    return text


# ---------- Главное: какая сейчас пара ----------

def current_pair_text(now: datetime, lessons, bells: dict) -> str:
    """Возвращает ответ для кнопки «Текущая пара».

    now     — текущее время (уже в часовом поясе техникума)
    lessons — пары группы на СЕГОДНЯ (уже с учётом числителя/знаменателя)
    bells   — расписание звонков из bells_from_rows()
    """
    if not lessons:
        return "Сегодня у твоей группы пар нет 🎉"

    def at(t: time) -> datetime:
        return datetime.combine(now.date(), t, tzinfo=now.tzinfo)

    # Оставляем только пары, для которых задано время звонков, и сортируем по номеру
    items = []
    for lesson in sorted(lessons, key=lambda l: l["pair_num"]):
        bell = bells.get(lesson["pair_num"])
        if bell is not None:
            items.append((lesson, *(at(t) for t in bell)))

    if not items:
        return "Для твоих пар ещё не задано расписание звонков. Сообщи администратору."

    for index, (lesson, start1, end1, start2, end2) in enumerate(items):
        head = f"{lesson['pair_num']} пара: {lesson_title(lesson)}"

        if now < start1:
            intro = "Пары ещё не начались." if index == 0 else "Сейчас перерыв."
            return (f"{intro}\n"
                    f"Следующая — {head}\n"
                    f"Начнётся через {format_duration(start1 - now)} (в {start1:%H:%M}).")

        if start1 <= now < end1:
            return (f"Сейчас идёт {head}\n"
                    f"Первая половина. До её конца {format_duration(end1 - now)}.")

        if end1 <= now < start2:
            return (f"Перемена между половинами пары.\n"
                    f"{head}\n"
                    f"До начала второй половины {format_duration(start2 - now)}.")

        if start2 <= now < end2:
            return (f"Сейчас идёт {head}\n"
                    f"Вторая половина. До конца пары {format_duration(end2 - now)}.")

    return "На сегодня пары закончились. Отдыхай! 😌"


def today_schedule_text(now: datetime, lessons, bells: dict, week_type: int) -> str:
    day_name = WEEKDAY_NAMES[now.isoweekday()]
    header = f"📅 {day_name}, {WEEK_NAMES[week_type]}"
    if not lessons:
        return f"{header}\nПар нет 🎉"

    lines = [header]
    for lesson in sorted(lessons, key=lambda l: l["pair_num"]):
        bell = bells.get(lesson["pair_num"])
        when = f"{bell[0]:%H:%M}–{bell[3]:%H:%M}" if bell else "время не задано"
        lines.append(f"{lesson['pair_num']}. {when} — {lesson_title(lesson)}")
    return "\n".join(lines)


# ---------- Проверка того, что распознал ИИ ----------

def lessons_from_ai(raw: dict, group_hint=None):
    """Проверяет ответ ИИ и переводит его в формат базы.

    Возвращает (lessons, bells, problems):
      lessons  — список словарей для db.add_lesson
      bells    — список [номер, начало1, конец1, начало2, конец2] для db.set_bell
      problems — что пришлось пропустить и почему (покажем админу)
    ИИ может ошибиться, поэтому каждую запись проверяем теми же правилами,
    что и ручной ввод через /add_lesson.
    """
    lessons, bells, problems = [], [], []
    seen = set()

    for item in raw.get("lessons") or []:
        if not isinstance(item, dict):
            continue
        group = str(item.get("group") or "").strip() or (group_hint or "")
        subject = str(item.get("subject") or "").strip()
        where = f"{item.get('day', '?')}, {item.get('pair', '?')} пара, {subject or 'без названия'}"
        try:
            if not group:
                raise ValueError("не видно группу — подпиши фото названием группы")
            if not subject:
                raise ValueError("нет названия предмета")
            pair = int(str(item.get("pair", "")).strip())
            if pair < 1 or pair > 10:
                raise ValueError("странный номер пары")
            lesson = {
                "group_name": normalize_group(group),
                "weekday": parse_day(str(item.get("day", ""))),
                "pair_num": pair,
                "subject": subject,
                "room": _empty_to_none(str(item.get("room") or "")),
                "teacher": _empty_to_none(str(item.get("teacher") or "")),
                "week_type": parse_week(str(item.get("week") or "")),
            }
        except ValueError as error:
            problems.append(f"{where}: {error}")
            continue

        key = (lesson["group_name"], lesson["weekday"], lesson["pair_num"],
               lesson["week_type"], lesson["subject"].lower())
        if key not in seen:  # ИИ иногда повторяет одну и ту же пару
            seen.add(key)
            lessons.append(lesson)

    for item in raw.get("bells") or []:
        if not isinstance(item, dict):
            continue
        try:
            pair = int(str(item.get("pair", "")).strip())
            times = [parse_time(str(item.get(k, ""))) for k in ("start1", "end1", "start2", "end2")]
            if not (times[0] < times[1] <= times[2] < times[3]):
                raise ValueError("время идёт не по порядку")
        except ValueError as error:
            problems.append(f"звонок {item.get('pair', '?')} пары: {error}")
            continue
        bells.append([pair] + [t.strftime("%H:%M") for t in times])

    return lessons, bells, problems


def ai_preview_text(lessons, bells, problems) -> str:
    """Текст, который админ видит перед сохранением."""
    lines = [f"🤖 ИИ распознал пар: {len(lessons)}. Проверь, всё ли верно:"]

    for group in sorted({l["group_name"] for l in lessons}):
        lines.append(f"\n👥 {group}")
        group_lessons = sorted((l for l in lessons if l["group_name"] == group),
                               key=lambda l: (l["weekday"], l["pair_num"], l["week_type"]))
        current_day = None
        for l in group_lessons:
            if l["weekday"] != current_day:
                current_day = l["weekday"]
                lines.append(f"{WEEKDAY_NAMES[current_day]}:")
            week = "" if l["week_type"] == WEEK_EVERY else f" [{WEEK_NAMES[l['week_type']]}]"
            lines.append(f"  {l['pair_num']}. {lesson_title(l)}{week}")

    if bells:
        lines.append("\n🔔 Звонки:")
        for b in bells:
            lines.append(f"  {b[0]} пара: {b[1]}–{b[2]} | {b[3]}–{b[4]}")

    if problems:
        lines.append("\n⚠️ Пропущено (не удалось разобрать):")
        lines.extend(f"  • {p}" for p in problems)

    return "\n".join(lines)
