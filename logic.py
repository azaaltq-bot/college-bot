"""Логика бота: курс по группе, какая сейчас пара, какая неделя, разбор команд администратора.

Здесь нет ничего про Telegram — поэтому эту часть легко проверять тестами (см. test_logic.py).
Все тексты для студентов оформлены в HTML-разметке Telegram (<b>, <i>, <code>).
"""

import html
import math
import re
from datetime import date, datetime, time, timedelta

import specialties

WEEKDAY_NAMES = {
    1: "Понедельник", 2: "Вторник", 3: "Среда",
    4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье",
}
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")

DAY_ALIASES = {
    "пн": 1, "понедельник": 1,
    "вт": 2, "вторник": 2,
    "ср": 3, "среда": 3,
    "чт": 4, "четверг": 4,
    "пт": 5, "пятница": 5,
    "сб": 6, "суббота": 6,
}

# Тип недели: 0 — пара каждую неделю, 1 — зелёное расписание (бывший числитель),
# 2 — синее расписание (бывший знаменатель)
WEEK_EVERY, WEEK_NUMERATOR, WEEK_DENOMINATOR = 0, 1, 2
WEEK_NAMES = {0: "каждую неделю", 1: "🟢 зелёное расписание", 2: "🔵 синее расписание"}
WEEK_ALIASES = {
    "каждая": 0, "все": 0, "0": 0,
    "зелёное": 1, "зеленое": 1, "зелёная": 1, "зеленая": 1, "зелёный": 1, "зеленый": 1,
    "зел": 1, "зелен": 1, "🟢": 1,
    "числитель": 1, "чис": 1, "1": 1,                      # старые названия тоже понимаем
    "синее": 2, "синяя": 2, "синий": 2, "син": 2, "🔵": 2,
    "знаменатель": 2, "знам": 2, "2": 2,
}

SEP = "━━━━━━━━━━━━━━"
NUM_EMOJI = {i: f"{i}\ufe0f\u20e3" for i in range(1, 10)}
NUM_EMOJI[10] = "🔟"


def esc(value) -> str:
    """Экранирует <, > и & — иначе Telegram примет их за HTML-теги."""
    return html.escape(str(value), quote=False)


def pair_icon(number: int) -> str:
    return NUM_EMOJI.get(number, f"{number}.")


def date_ru(day: date, with_year: bool = False) -> str:
    text = f"{WEEKDAY_NAMES[day.isoweekday()]}, {day.day} {MONTHS_GEN[day.month - 1]}"
    return f"{text} {day.year}" if with_year else text


# ---------- Группа, курс, специальность ----------

GROUP_RE = re.compile(r"(?<!\d)(\d{1,2})\s*-\s*(\d{2})(?!\d)")


# Латинские буквы, похожие на русские (на случай неверной раскладки)
LATIN_TO_CYR = str.maketrans("ABCEHKMOPTXYabcehkmoptxy", "АВСЕНКМОРТХУАВСЕНКМОРТХУ")

KNOWN_PREFIXES = set(specialties.PREFIXES)


def set_known_prefixes(prefixes) -> None:
    """Список известных кодов групп (из specialties.py + из базы)."""
    KNOWN_PREFIXES.clear()
    KNOWN_PREFIXES.update(p.upper() for p in prefixes if p)


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fix_prefix(letters: str):
    """Исправляет опечатки в буквах группы. Возвращает (код, статус):
    'ok' — уже верно, 'fixed' — исправили, 'unknown' — не нашли похожий,
    'ambiguous' — подходит несколько кодов (лучше переспросить)."""
    letters = letters.translate(LATIN_TO_CYR).upper().replace("Ё", "Е")
    if letters in KNOWN_PREFIXES:
        return letters, "ok"
    # без учёта Е/Ё, Й/И
    flat = {p: p.replace("Ё", "Е").replace("Й", "И") for p in KNOWN_PREFIXES}
    key = letters.replace("Й", "И")
    for p, f in flat.items():
        if f == key:
            return p, "fixed"
    max_dist = 1 if len(letters) <= 4 else 2
    scored = sorted((_edit_distance(key, f), p) for p, f in flat.items())
    if scored and scored[0][0] <= max_dist:
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            return letters, "ambiguous"
        return scored[0][1], "fixed"
    return letters, "unknown"


_SPLIT_RE = re.compile(r"^\s*([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\s]*?)\s*(\d.*)$")


def autocorrect_group(text: str):
    """'Тэс 9-26-1,2' -> ('ТС 9-26-1,2', 'fixed'). Правим ТОЛЬКО буквы, цифры не трогаем.
    Статусы: ok / fixed / unknown / ambiguous / bad_format (нет цифр группы)."""
    clean = " ".join(text.replace("–", "-").replace("—", "-").split())
    m = _SPLIT_RE.match(clean)
    if not m:
        return clean.upper(), "bad_format"
    letters = "".join(m.group(1).split())          # «ИТ Д» -> «ИТД»
    rest = m.group(2).strip()
    prefix, status = fix_prefix(letters)
    return f"{prefix} {rest}", status


def normalize_group(text: str) -> str:
    """'  тэс   9-26-3 ' -> 'ТС 9-26-3' (чтобы одна группа не записалась по-разному)."""
    group, status = autocorrect_group(text)
    return group


def group_prefix(group: str) -> str:
    """'ТС 9-26-3,4' -> 'ТС' (буквы в начале — по ним определяем специальность)."""
    m = re.match(r"\s*([A-Za-zА-Яа-яЁё]+)", group)
    return m.group(1).upper() if m else ""


def admission_year(group: str):
    """'ТС 9-26-3' -> 2026. В названии группы «9-26» число 26 — год поступления."""
    m = GROUP_RE.search(group)
    return 2000 + int(m.group(2)) if m else None


def study_year_start(today: date) -> int:
    """Учебный год начинается 1 сентября: в октябре 2026 это 2026, в мае 2027 — тоже 2026."""
    return today.year if today.month >= 9 else today.year - 1


def course_of_group(group: str, today: date):
    """Курс по группе: поступил в 2026 → в 2026/27 учебном году это 1 курс, в 2027/28 — 2-й.
    Если определить нельзя (нет года в названии, ещё не начал учиться, уже выпустился) — None."""
    year = admission_year(group)
    if year is None:
        return None
    course = study_year_start(today) - year + 1
    return course if 1 <= course <= 4 else None


def course_info(group: str, today: date):
    """(курс или None, пояснение для настроек)."""
    year = admission_year(group)
    if year is None:
        return None, "в названии группы не нашёл год поступления (например, 9-26)"
    course = study_year_start(today) - year + 1
    if course < 1:
        return None, f"учёба с {year} года ещё не началась"
    if course > 4:
        return None, f"поступление в {year} — проверь группу"
    return course, ""


# ---------- Разбор ввода ----------

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
    raise ValueError(f"не понял тип недели «{text.strip()}» (пиши: каждая, зелёная или синяя)")


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


def parse_bell_line(line: str) -> tuple:
    """'1 08:30 09:15 09:20 10:05' -> (1, '08:30', '09:15', '09:20', '10:05')."""
    parts = line.replace(";", " ").replace(",", " ").split()
    if len(parts) != 5:
        raise ValueError("нужно 5 значений: пара, начало 1-й половины, конец 1-й, начало 2-й, конец 2-й")
    if not parts[0].isdigit() or int(parts[0]) < 1:
        raise ValueError(f"номер пары должен быть числом, а не «{parts[0]}»")
    times = []
    for p in parts[1:]:
        try:
            times.append(parse_time(p))
        except ValueError:
            raise ValueError(f"время «{p}» не в формате ЧЧ:ММ")
    if not (times[0] < times[1] <= times[2] < times[3]):
        raise ValueError("время должно идти по порядку: начало1 < конец1 ≤ начало2 < конец2")
    return (int(parts[0]), *(t.strftime("%H:%M") for t in times))


def parse_lunch_line(line: str) -> tuple:
    """'11:50 12:20' или '11:50-12:20' -> ('11:50', '12:20')."""
    parts = re.sub(r"[-–—;]", " ", line).split()
    if len(parts) != 2:
        raise ValueError("нужно 2 значения: начало и конец обеда, например 11:50 12:20")
    times = []
    for p in parts:
        try:
            times.append(parse_time(p))
        except ValueError:
            raise ValueError(f"время «{p}» не в формате ЧЧ:ММ")
    if not times[0] < times[1]:
        raise ValueError("обед должен заканчиваться позже, чем начинается")
    return tuple(t.strftime("%H:%M") for t in times)


# ---------- Неделя, звонки, обеды ----------

def week_type_for(day: date, semester_start: date) -> int:
    """Зелёная (1) или синяя (2) неделя для указанной даты."""
    first_monday = semester_start - timedelta(days=semester_start.weekday())
    weeks_passed = (day - first_monday).days // 7
    return WEEK_NUMERATOR if weeks_passed % 2 == 0 else WEEK_DENOMINATOR


def bells_from_rows(rows) -> dict:
    """Строки из таблицы bells -> {номер_пары: (начало1, конец1, начало2, конец2)}."""
    return {
        r["pair_num"]: tuple(parse_time(r[k]) for k in ("start1", "end1", "start2", "end2"))
        for r in rows
    }


def lunches_from_rows(rows) -> list:
    """Строки из таблицы lunches -> [(начало, конец), ...] по порядку."""
    return sorted((parse_time(r["start"]), parse_time(r["end"])) for r in rows)


def week_explain_text(today: date, semester_start: date) -> str:
    current = week_type_for(today, semester_start)
    other = WEEK_DENOMINATOR if current == WEEK_NUMERATOR else WEEK_NUMERATOR
    next_monday = today + timedelta(days=7 - today.weekday())
    return (
        "❓ <b>Зелёное и синее расписание</b>\n"
        f"{SEP}\n"
        "Некоторые пары проходят не каждую неделю, а через одну. Поэтому недели делятся на два "
        "вида, которые чередуются:\n\n"
        "🟢 <b>Зелёное расписание</b> — первая неделя (раньше: числитель)\n"
        "🔵 <b>Синее расписание</b> — вторая неделя (раньше: знаменатель)\n"
        "и дальше по кругу: зелёная, синяя, зелёная, синяя…\n\n"
        "В расписании это выглядит так: в одной клетке две пары — <b>верхняя идёт по зелёной неделе</b>, "
        "<b>нижняя по синей</b>. Пары без пометки идут каждую неделю.\n\n"
        "Неделя меняется каждый понедельник.\n"
        f"{SEP}\n"
        f"📍 Сейчас: <b>{WEEK_NAMES[current]}</b>\n"
        f"➡️ С {next_monday.day} {MONTHS_GEN[next_monday.month - 1]}: <b>{WEEK_NAMES[other]}</b>"
    )


def settings_text(user, today: date, semester_start: date, specialty_name=None) -> str:
    group = user["group_name"]
    course, note = course_info(group, today)
    prefix = group_prefix(group)
    if specialty_name:
        spec = esc(specialty_name)
    elif prefix:
        spec = f"{esc(prefix)} <i>(полное название админ ещё не добавил)</i>"
    else:
        spec = "не определена"
    course_line = f"{course} курс" if course else "не определён"
    return (
        "⚙️ <b>Настройки</b>\n"
        f"{SEP}\n"
        f"👤 <b>ФИО:</b> {esc(user['fio'])}\n"
        f"🎓 <b>Специальность:</b> {spec}\n"
        f"👥 <b>Группа:</b> {esc(group)}\n"
        f"🔢 <b>Курс:</b> {course_line}\n"
        + (f"      <i>{esc(note)}</i>\n" if note and not course else "") +
        f"{SEP}\n"
        f"📅 <b>Сегодня:</b> {date_ru(today, with_year=True)}\n"
        f"🔁 <b>Неделя:</b> {WEEK_NAMES[week_type_for(today, semester_start)]}"
    )


def bells_text(course, bell_rows, lunch_rows) -> str:
    """Звонки и обеды курса — для студентов и для проверки админом."""
    head = f"🔔 <b>Звонки — {course} курс</b>" if course else "🔔 <b>Звонки</b>"
    if not bell_rows:
        return f"{head}\n{SEP}\nПока не заданы. Сообщи администратору."
    lines = [head, SEP]
    for r in bell_rows:
        lines.append(f"{pair_icon(r['pair_num'])} {r['start1']}–{r['end1']}  ·  {r['start2']}–{r['end2']}")
    lines.append(SEP)
    if lunch_rows:
        for r in lunch_rows:
            lines.append(f"🍽 Обед {r['start']}–{r['end']}")
    else:
        lines.append("🍽 Обед: не задан")
    return "\n".join(lines)


# ---------- Форматирование ----------

def fmt_hms(delta: timedelta) -> str:
    """timedelta -> '01:05:09' (часы:минуты:секунды). Секунды округляем вверх, чтобы не было 00:00:00 раньше срока."""
    total = max(0, math.ceil(delta.total_seconds()))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def progress_bar(done: float, total: float, width: int = 12) -> str:
    share = 0 if total <= 0 else min(max(done / total, 0), 1)
    filled = round(share * width)
    return f"{'▰' * filled}{'▱' * (width - filled)} {round(share * 100)}%"


def lesson_title(lesson) -> str:
    """Короткая строка 'Предмет, каб. 214 (Иванова И.И.)' (уже экранирована под HTML)."""
    text = esc(lesson["subject"])
    if lesson["room"]:
        text += f", каб. {esc(lesson['room'])}"
    if lesson["teacher"]:
        text += f" ({esc(lesson['teacher'])})"
    return text


def lesson_details(lesson) -> str:
    parts = []
    if lesson["room"]:
        parts.append(f"🚪 каб. {esc(lesson['room'])}")
    if lesson["teacher"]:
        parts.append(f"👨‍🏫 {esc(lesson['teacher'])}")
    return "  ·  ".join(parts)


# ---------- Главное: какая сейчас пара ----------

def pair_state(now: datetime, lessons, bells: dict, lunches=()) -> dict:
    """Определяет, что происходит прямо сейчас. Возвращает словарь с ключом kind:
    no_lessons, no_bells, before, gap, lunch, half1, between, half2, done.

    now     — текущее время (уже в часовом поясе техникума)
    lessons — пары группы на СЕГОДНЯ (уже с учётом зелёной/синей недели)
    bells   — звонки курса из bells_from_rows()
    lunches — обеды курса из lunches_from_rows()
    """
    if not lessons:
        return {"kind": "no_lessons"}

    def at(t: time) -> datetime:
        return datetime.combine(now.date(), t, tzinfo=now.tzinfo)

    items = []
    for lesson in sorted(lessons, key=lambda l: l["pair_num"]):
        bell = bells.get(lesson["pair_num"])
        if bell is not None:
            s1, e1, s2, e2 = (at(t) for t in bell)
            items.append({"lesson": lesson, "s1": s1, "e1": e1, "s2": s2, "e2": e2})
    if not items:
        return {"kind": "no_bells"}

    lunch_ranges = [(at(s), at(e)) for s, e in lunches]

    for index, item in enumerate(items):
        following = items[index + 1] if index + 1 < len(items) else None
        if now < item["s1"]:
            if index > 0:
                for lunch_start, lunch_end in lunch_ranges:
                    if lunch_start <= now < lunch_end:
                        return {"kind": "lunch", "next": item,
                                "lunch_start": lunch_start, "lunch_end": lunch_end}
            return {"kind": "before" if index == 0 else "gap", "next": item}
        if now < item["e1"]:
            return {"kind": "half1", "cur": item, "next": following}
        if now < item["s2"]:
            return {"kind": "between", "cur": item, "next": following}
        if now < item["e2"]:
            return {"kind": "half2", "cur": item, "next": following}
    return {"kind": "done"}


LIVE_KINDS = {"before", "gap", "lunch", "half1", "between", "half2"}


def _pair_block(item) -> list:
    lesson = item["lesson"]
    lines = [f"📚 <b>{esc(lesson['subject'])}</b>"]
    details = lesson_details(lesson)
    if details:
        lines.append(details)
    lines.append(f"🕐 {item['s1']:%H:%M} – {item['e2']:%H:%M}")
    return lines


def _next_line(item) -> str:
    if item is None:
        return "🏁 Это последняя пара на сегодня"
    lesson = item["lesson"]
    return (f"➡️ Дальше: {lesson['pair_num']} пара — <b>{esc(lesson['subject'])}</b> "
            f"(в {item['s1']:%H:%M})")


def current_pair(now: datetime, lessons, bells: dict, lunches=()):
    """Ответ для кнопки «Текущая пара»: (текст, нужно_ли_обновлять_каждую_секунду)."""
    state = pair_state(now, lessons, bells, lunches)
    kind = state["kind"]
    clock = f"🕒 Чита · <code>{now:%H:%M:%S}</code>"

    if kind == "no_lessons":
        return "Сегодня у твоей группы пар нет 🎉", False
    if kind == "no_bells":
        return ("🔔 Для твоего курса ещё не задано расписание звонков. "
                "Сообщи администратору."), False
    if kind == "done":
        return f"🎉 <b>На сегодня пары закончились.</b>\nОтдыхай! 😌\n\n{clock}", False

    if kind in ("before", "gap"):
        item = state["next"]
        title = "🌅 <b>Пары ещё не начались.</b>" if kind == "before" else "☕ <b>Сейчас перерыв.</b>"
        lines = [title, SEP, f"Следующая — {item['lesson']['pair_num']} пара:", *_pair_block(item), "",
                 f"⏳ До начала: <code>{fmt_hms(item['s1'] - now)}</code>", "", clock]
        return "\n".join(lines), True

    if kind == "lunch":
        item = state["next"]
        lines = ["🍽 <b>Сейчас обед</b>", SEP,
                 f"🕐 {state['lunch_start']:%H:%M} – {state['lunch_end']:%H:%M}",
                 f"⏳ До конца обеда: <code>{fmt_hms(state['lunch_end'] - now)}</code>", "",
                 _next_line(item), "", clock]
        return "\n".join(lines), True

    item = state["cur"]
    number = item["lesson"]["pair_num"]
    bar = progress_bar((now - item["s1"]).total_seconds(), (item["e2"] - item["s1"]).total_seconds())
    lines = [f"📍 <b>Сейчас идёт {number} пара</b>", SEP, *_pair_block(item), "", bar]
    if kind == "half1":
        lines += ["🔹 <b>Первая половина</b>",
                  f"⏳ До конца половины: <code>{fmt_hms(item['e1'] - now)}</code>",
                  f"🏁 До конца пары: <code>{fmt_hms(item['e2'] - now)}</code>"]
    elif kind == "between":
        lines += ["☕ <b>Перемена между половинами</b>",
                  f"⏳ До начала второй половины: <code>{fmt_hms(item['s2'] - now)}</code>",
                  f"🏁 До конца пары: <code>{fmt_hms(item['e2'] - now)}</code>"]
    else:
        lines += ["🔸 <b>Вторая половина</b>",
                  f"🏁 До конца пары: <code>{fmt_hms(item['e2'] - now)}</code>"]
    lines += ["", _next_line(state["next"]), "", clock]
    return "\n".join(lines), True


def current_pair_text(now: datetime, lessons, bells: dict, lunches=()) -> str:
    return current_pair(now, lessons, bells, lunches)[0]


def today_schedule_text(now: datetime, lessons, bells: dict, week_type: int, lunches=()) -> str:
    header = [f"📅 <b>{date_ru(now.date())}</b>",
              f"🔁 Неделя: <b>{WEEK_NAMES[week_type]}</b>", SEP]
    if not lessons:
        return "\n".join(header + ["Пар нет 🎉"])

    def at(t: time) -> datetime:
        return datetime.combine(now.date(), t, tzinfo=now.tzinfo)

    pending = sorted(lunches)
    lines = list(header)
    previous = 0
    last_end = None
    for lesson in sorted(lessons, key=lambda l: l["pair_num"]):
        number = lesson["pair_num"]
        bell = bells.get(number)

        # «окно»: пропущены номера пар между двумя занятиями
        if previous and number - previous > 1:
            for gap in range(previous + 1, number):
                lines.append(f"🪟 {pair_icon(gap)} <i>окно</i>")

        # обед ставим перед первой парой, которая начинается позже его начала
        while pending and bell and pending[0][0] <= bell[0]:
            start, end = pending.pop(0)
            lines.append(f"🍽 <i>Обед {start:%H:%M}–{end:%H:%M}</i>")

        if bell:
            start, end = at(bell[0]), at(bell[3])
            icon = "✅" if now >= end else ("▶️" if now >= start else "⏳")
            when = f"🕐 {start:%H:%M}–{end:%H:%M}"
            last_end = bell[3]
        else:
            icon, when = "▫️", "🕐 время не задано"
        lines.append(f"{icon} {pair_icon(number)} <b>{esc(lesson['subject'])}</b>")
        details = lesson_details(lesson)
        lines.append(f"      {when}" + (f"  ·  {details}" if details else ""))
        if lesson["week_type"] != WEEK_EVERY:
            lines.append(f"      🔁 <i>{WEEK_NAMES[lesson['week_type']]}</i>")
        previous = number

    lines.append(SEP)
    footer = f"Пар сегодня: {len(lessons)}"
    if last_end:
        footer += f"  ·  занятия до {last_end:%H:%M}"
    lines.append(footer)
    return "\n".join(lines)


def group_schedule_text(group: str, lessons) -> str:
    """Расписание группы целиком — для админа (с номерами записей для /del_lesson)."""
    if not lessons:
        return f"У группы {esc(group)} пока нет пар."
    lines = [f"📋 <b>Расписание {esc(group)}</b>"]
    current_day = None
    for l in lessons:
        if l["weekday"] != current_day:
            current_day = l["weekday"]
            lines.append(f"\n<b>{WEEKDAY_NAMES[current_day]}</b>")
        week = "" if l["week_type"] == 0 else f" [{WEEK_NAMES[l['week_type']]}]"
        lines.append(f"  #{l['id']} · {l['pair_num']} пара — {lesson_title(l)}{week}")
    return "\n".join(lines)


# ---------- Проверка ответа ИИ ----------

def lessons_from_ai(raw: dict, group_hint=None, course=None, today=None):
    """Проверяет JSON от ИИ. Возвращает (пары, звонки, проблемы).

    Хорошие записи попадают в пары, плохие — в «проблемы» (их покажем админу).
    Если указан course, группы других курсов отбрасываются.
    """
    today = today or date.today()
    hint = normalize_group(group_hint) if group_hint else ""
    lessons, bells, problems = [], {}, []
    seen = set()

    raw_lessons = raw.get("lessons") if isinstance(raw, dict) else None
    for number, item in enumerate(raw_lessons if isinstance(raw_lessons, list) else [], start=1):
        if not isinstance(item, dict):
            problems.append(f"запись {number}: непонятный формат")
            continue
        subject = str(item.get("subject") or "").strip()
        label = f"{item.get('day') or '?'}, {item.get('pair') or '?'} пара, «{subject or 'без названия'}»"
        try:
            group = normalize_group(str(item.get("group") or "")) or hint
            if not group:
                raise ValueError("не указана группа")
            if not subject:
                raise ValueError("нет названия предмета")
            pair = str(item.get("pair") or "").strip()
            if not pair.isdigit() or int(pair) < 1:
                raise ValueError(f"номер пары «{pair}»")
            weekday = parse_day(str(item.get("day") or ""))
            week_type = parse_week(str(item.get("week") or ""))
        except ValueError as error:
            problems.append(f"{label}: {error}")
            continue

        if course:
            group_course = course_of_group(group, today)
            if group_course and group_course != course:
                problems.append(f"{label}: группа {group} — это {group_course} курс, а загрузка идёт для {course} курса")
                continue

        key = (group, weekday, int(pair), week_type, subject.lower())
        if key in seen:  # одна и та же пара дважды — оставляем одну
            continue
        seen.add(key)
        lessons.append({
            "group_name": group, "weekday": weekday, "pair_num": int(pair), "subject": subject,
            "room": _empty_to_none(str(item.get("room") or "")),
            "teacher": _empty_to_none(str(item.get("teacher") or "")),
            "week_type": week_type,
        })

    bell_list, bell_problems = bells_from_ai(raw)
    problems += bell_problems
    return lessons, bell_list, problems


def _mid_time(start: time, end: time) -> time:
    """Середина между началом и концом (для пар, где указано только начало и конец всей пары)."""
    total = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    m = start.hour * 60 + start.minute + total // 2
    return time(m // 60, m % 60)


def bells_from_ai(raw):
    """Звонки из ответа ИИ -> (список [пара, s1, e1, s2, e2], проблемы).

    ИИ может прислать 4 времени (две половины пары) или только начало и конец всей пары —
    тогда пара делится пополам без перерыва."""
    bells, problems = {}, []
    raw_bells = raw.get("bells") if isinstance(raw, dict) else None
    for item in raw_bells if isinstance(raw_bells, list) else []:
        try:
            pair = str(item.get("pair") or "").strip()
            if not pair.isdigit() or int(pair) < 1:
                raise ValueError
            if item.get("start1") and item.get("end2"):
                times = [parse_time(str(item.get(k) or "")) for k in ("start1", "end1", "start2", "end2")]
            else:
                start, end = parse_time(str(item.get("start") or "")), parse_time(str(item.get("end") or ""))
                if not start < end:
                    raise ValueError
                mid = _mid_time(start, end)
                times = [start, mid, mid, end]
            if not (times[0] < times[1] <= times[2] < times[3]):
                raise ValueError
        except (ValueError, AttributeError):
            pair_label = item.get("pair", "?") if isinstance(item, dict) else "?"
            problems.append(f"звонок {pair_label} пары: время нечитаемо или идёт не по порядку")
            continue
        bells[int(pair)] = [int(pair), *(t.strftime("%H:%M") for t in times)]
    return [bells[k] for k in sorted(bells)], problems


def lunches_from_ai(raw):
    """Обеды из ответа ИИ -> (список (начало, конец), проблемы)."""
    lunches, problems = [], []
    raw_l = raw.get("lunches") if isinstance(raw, dict) else None
    for item in raw_l if isinstance(raw_l, list) else []:
        try:
            start = parse_time(str(item.get("start") or ""))
            end = parse_time(str(item.get("end") or ""))
            if not start < end:
                raise ValueError
        except (ValueError, AttributeError):
            problems.append("обед: время нечитаемо или идёт не по порядку")
            continue
        pair = (start.strftime("%H:%M"), end.strftime("%H:%M"))
        if pair not in lunches:
            lunches.append(pair)
    return lunches, problems


def ai_bells_preview_text(course, bells, lunches, problems) -> str:
    """Что ИИ понял про звонки — показываем админу перед сохранением."""
    lines = [f"🤖 <b>ИИ разобрал звонки — {course} курс</b>", SEP]
    for number, s1, e1, s2, e2 in bells:
        lines.append(f"  {number} пара: {s1}–{e1} | {s2}–{e2}")
    if not bells:
        lines.append("  звонков не нашёл")
    if lunches:
        lines.append("\n🍽 <b>Обеды</b>")
        lines += [f"  {s}–{e}" for s, e in lunches]
    if problems:
        lines.append(f"\n⚠️ <b>Пропущено ({len(problems)}):</b>")
        lines += [f"• {esc(p)}" for p in problems]
    lines.append("\nПроверь время и выбери действие 👇")
    return "\n".join(lines)



def ai_preview_text(lessons, bells, problems) -> str:
    """Что распознал ИИ — показываем админу перед сохранением."""
    lines = [f"🤖 <b>ИИ распознал:</b> пар — {len(lessons)}, звонков — {len(bells)}", SEP]
    by_group = {}
    for l in lessons:
        by_group.setdefault(l["group_name"], []).append(l)
    for group, items in sorted(by_group.items()):
        lines.append(f"\n👥 <b>{esc(group)}</b>")
        day = None
        for l in sorted(items, key=lambda x: (x["weekday"], x["pair_num"], x["week_type"])):
            if l["weekday"] != day:
                day = l["weekday"]
                lines.append(f"<b>{WEEKDAY_NAMES[day]}</b>")
            week = "" if l["week_type"] == 0 else f" [{WEEK_NAMES[l['week_type']]}]"
            lines.append(f"  {l['pair_num']}. {lesson_title(l)}{week}")
    if bells:
        lines.append("\n🔔 <b>Звонки</b>")
        for number, s1, e1, s2, e2 in bells:
            lines.append(f"  {number} пара: {s1}–{e1} | {s2}–{e2}")
    if problems:
        lines.append(f"\n⚠️ <b>Пропущено ({len(problems)}):</b>")
        lines += [f"• {esc(p)}" for p in problems]
    lines.append("\nПроверь и выбери действие 👇")
    return "\n".join(lines)
