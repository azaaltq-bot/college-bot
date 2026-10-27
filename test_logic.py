"""Проверка логики без Telegram. Запуск: python test_logic.py"""

import os
import sqlite3
import tempfile
from datetime import date, datetime

import logic

bells = logic.bells_from_rows([
    {"pair_num": 1, "start1": "08:30", "end1": "09:15", "start2": "09:20", "end2": "10:05"},
    {"pair_num": 2, "start1": "10:15", "end1": "11:00", "start2": "11:05", "end2": "11:50"},
    {"pair_num": 3, "start1": "12:20", "end1": "13:05", "start2": "13:10", "end2": "13:55"},
])
lunches = logic.lunches_from_rows([{"start": "11:50", "end": "12:20"}])

# У группы 1 и 3 пара, на 2-й «окно»
lessons = [
    {"pair_num": 3, "subject": "Физика", "room": "305", "teacher": None, "week_type": 2},
    {"pair_num": 1, "subject": "Математика", "room": "214", "teacher": "Иванова И.И.", "week_type": 0},
]


def at(hh_mm: str, sec: int = 0) -> datetime:
    h, m = map(int, hh_mm.split(":"))
    return datetime(2026, 10, 27, h, m, sec)


# ---------- Текущая пара ----------
cases = [
    ("08:00", 0, ["Пары ещё не начались", "Математика", "00:30:00"], True),
    ("08:40", 0, ["Первая половина", "00:35:00", "До конца пары: <code>01:25:00"], True),
    ("09:17", 0, ["Перемена между половинами", "00:03:00"], True),
    ("09:30", 30, ["Вторая половина", "00:34:30"], True),
    ("10:30", 0, ["перерыв", "Физика", "01:50:00"], True),      # окно на месте 2-й пары
    ("12:00", 0, ["Сейчас обед", "00:20:00", "Физика"], True),  # обед 11:50–12:20
    ("13:55", 0, ["закончились"], False),
]
for moment, sec, expected, live in cases:
    text, is_live = logic.current_pair(at(moment, sec), lessons, bells, lunches)
    for piece in expected:
        assert piece in text, f"{moment}: ожидал «{piece}», а бот ответил:\n{text}"
    assert is_live == live, f"{moment}: live={is_live}"
    print(f"✅ {moment}:{sec:02d} → {text.splitlines()[0]}")

# Секундный таймер: через секунду текст меняется
t1 = logic.current_pair_text(at("08:40", 0), lessons, bells, lunches)
t2 = logic.current_pair_text(at("08:40", 1), lessons, bells, lunches)
assert t1 != t2 and "00:34:59" in t2
print("✅ таймер идёт по секундам")

assert "пар нет" in logic.current_pair_text(at("10:00"), [], bells)
assert "звонков" in logic.current_pair_text(at("10:00"), lessons, {})
print("✅ день без пар / нет звонков")

# ---------- Расписание на сегодня ----------
text = logic.today_schedule_text(at("10:30"), lessons, bells, 2, lunches)
for piece in ("Вторник, 27 октября", "синее расписание", "✅ 1️⃣", "⏳ 3️⃣", "окно", "Обед 11:50–12:20"):
    assert piece in text, f"нет «{piece}» в:\n{text}"
print("✅ расписание на сегодня")
print(text)

# ---------- Курс по группе ----------
oct26, may27, aug26 = date(2026, 10, 27), date(2027, 5, 10), date(2026, 8, 20)
assert logic.course_of_group("ТС 9-26-3,4", oct26) == 1       # поступил в 2026 → 1 курс
assert logic.course_of_group("ТС 9-25-1", oct26) == 2         # 25 → 2 курс
assert logic.course_of_group("ТС 9-24-1", oct26) == 3
assert logic.course_of_group("ТС 9-23-1", oct26) == 4
assert logic.course_of_group("ТС 9-22-1", oct26) is None      # уже выпустились
assert logic.course_of_group("ТС 9-26-3", may27) == 1         # весной всё ещё тот же учебный год
assert logic.course_of_group("ТС 9-26-3", date(2027, 9, 1)) == 2   # с 1 сентября — 2 курс
assert logic.course_of_group("ТС 9-26-3", aug26) is None      # учёба ещё не началась
assert logic.course_of_group("ТС 11-25-2", oct26) == 2
assert logic.course_of_group("без года", oct26) is None
assert logic.group_prefix("тс 9-26-3,4") == "ТС"
print("✅ автоопределение курса")

# ---------- Зелёное / синее расписание ----------
start = date(2026, 9, 1)
assert logic.week_type_for(date(2026, 8, 31), start) == 1
assert logic.week_type_for(date(2026, 9, 6), start) == 1
assert logic.week_type_for(date(2026, 9, 7), start) == 2
assert logic.week_type_for(date(2026, 9, 14), start) == 1
assert "Зелёное и синее расписание" in logic.week_explain_text(oct26, start)
assert "числител" not in logic.WEEK_NAMES[1].lower() and "знаменател" not in logic.WEEK_NAMES[2].lower()
assert logic.parse_week("Зелёная") == 1 and logic.parse_week("синее") == 2
assert logic.parse_week("числитель") == 1 and logic.parse_week("знаменатель") == 2   # старые слова тоже понимаем
print("✅ зелёное/синее расписание")

# ---------- Настройки ----------
user = {"fio": "Иванов Иван Иванович", "group_name": "ТС 9-26-3,4"}
s = logic.settings_text(user, oct26, start, "Название специальности")
for piece in ("Иванов Иван Иванович", "Название специальности", "1 курс", "27 октября 2026"):
    assert piece in s, piece
print("✅ настройки")

# ---------- Разбор команд ----------
data = logic.parse_lesson_line(" тс  9-26-3 ; Пн; 1; Математика; 214; -; знам")
assert data == {"group_name": "ТС 9-26-3", "weekday": 1, "pair_num": 1, "subject": "Математика",
                "room": "214", "teacher": None, "week_type": 2}, data
for bad in ["ТС 9-26-3; пн; один; Математика", "ТС 9-26-3; вс; 1; Математика", "ТС 9-26-3; пн"]:
    try:
        logic.parse_lesson_line(bad)
        raise AssertionError(f"должна быть ошибка: {bad}")
    except ValueError:
        pass
assert logic.parse_bell_line("1 8:30 09:15 09:20 10:05") == (1, "08:30", "09:15", "09:20", "10:05")
assert logic.parse_lunch_line("11:50-12:20") == ("11:50", "12:20")
for bad in ["1 08:30 09:15", "1 10:00 09:00 09:20 10:05"]:
    try:
        logic.parse_bell_line(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
print("✅ разбор команд")

# ---------- Проверка ответа ИИ ----------
ai_answer = {
    "lessons": [
        {"group": "тс 9-26-3", "day": "Пн", "pair": 1, "subject": "Математика", "room": "214",
         "teacher": "", "week": "каждая"},
        {"group": "тс 9-26-3", "day": "пн", "pair": "1", "subject": "Математика", "room": "214",
         "teacher": "", "week": "каждая"},                      # дубль — должен отброситься
        {"group": "", "day": "вт", "pair": 2, "subject": "Физика", "room": "", "teacher": "",
         "week": "числитель"},                                 # группа из подписи к фото
        {"group": "ТС 9-26-3", "day": "вс", "pair": 1, "subject": "Химия"},   # воскресенья нет
        {"group": "ТС 9-26-3", "day": "ср", "pair": 2, "subject": ""},        # нет предмета
    ],
    "bells": [
        {"pair": 1, "start1": "8:30", "end1": "09:15", "start2": "09:20", "end2": "10:05"},
        {"pair": 2, "start1": "11:00", "end1": "10:15", "start2": "11:05", "end2": "11:50"},  # перепутано
    ],
}
ai_lessons, ai_bells, ai_problems = logic.lessons_from_ai(ai_answer, group_hint="ТС 9-26-3", today=oct26)
assert len(ai_lessons) == 2, ai_lessons
assert ai_lessons[1]["group_name"] == "ТС 9-26-3" and ai_lessons[1]["week_type"] == 1
assert ai_bells == [[1, "08:30", "09:15", "09:20", "10:05"]], ai_bells
assert len(ai_problems) == 3, ai_problems
preview = logic.ai_preview_text(ai_lessons, ai_bells, ai_problems)
assert "Математика" in preview and "Пропущено" in preview

# загрузка для 2 курса: группа 1 курса отбрасывается
l2, _, p2 = logic.lessons_from_ai(ai_answer, group_hint="ТС 9-26-3", course=2, today=oct26)
assert l2 == [] and any("1 курс" in p for p in p2), p2
print("✅ проверка ответа ИИ")

# ---------- База: звонки и обеды по курсам + миграция старой базы ----------
import config
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "old.db")
conn = sqlite3.connect(config.DB_PATH)
conn.execute("CREATE TABLE bells (pair_num INTEGER PRIMARY KEY, start1 TEXT, end1 TEXT, start2 TEXT, end2 TEXT)")
conn.execute("INSERT INTO bells VALUES (1, '08:30', '09:15', '09:20', '10:05')")
conn.commit()
conn.close()

import database as db
db.init_db()
assert len(db.get_bells(1)) == 1 and len(db.get_bells(4)) == 1      # старые звонки скопировались
db.set_bell(2, 1, "09:00", "09:45", "09:50", "10:35")               # у 2 курса свои
assert db.get_bells(2)[0]["start1"] == "09:00" and db.get_bells(1)[0]["start1"] == "08:30"
db.add_lunch(2, "11:50", "12:20")
assert len(db.get_lunches(2)) == 1 and db.get_lunches(1) == []
assert db.delete_lunch(2, "11:50") and db.delete_bell(2, 1)
db.set_specialty("ТС", "Тест")
assert db.get_specialty("ТС") == "Тест"
db.add_lesson("ТС 9-26-3", 1, 1, "Математика", None, None, 0)
assert db.delete_group_lessons("ТС 9-26-3") == 1
print("✅ база: курсы, обеды, миграция")

# ---------- автоисправление букв группы ----------
import logic as L
cases = {
    "Тэс 9-26-1,2": ("ТС 9-26-1,2", "fixed"),
    "тс9-26-3,4": ("ТС 9-26-3,4", "ok"),
    "TC 9-26-3": ("ТС 9-26-3", "ok"),          # латиница -> кириллица
    "ИТ Д 9-27-1": ("ИТД 9-27-1", "ok"),
    "гнх 9-25-2": ("ГНХ 9-25-2", "ok"),
    "ГН 9-26-1": ("ГНХ 9-26-1", "fixed"),
    "ТС 9–26–3": ("ТС 9-26-3", "ok"),          # длинное тире -> дефис
}
for src, (want, st) in cases.items():
    got = L.autocorrect_group(src)
    assert got == (want, st), (src, got)
assert L.autocorrect_group("ЦЦЦ 9-26-1")[1] == "unknown"
assert L.autocorrect_group("ТС")[1] == "bad_format"
# цифры никогда не меняются
assert L.autocorrect_group("Тэс 9-26-1,2")[0].split(" ", 1)[1] == "9-26-1,2"
print("✅ автоисправление букв группы")

# ---------- настройки без «определён по...» ----------
user = {"fio": "Иванов Иван", "group_name": "ТС 9-26-3,4"}
txt = logic.settings_text(user, date(2026, 10, 27), date(2026, 9, 1), None)
assert "определён по" not in txt and "1 курс" in txt
assert logic.course_info("ТС 9-26-3", date(2026, 10, 27)) == (1, "")
assert "не нашёл год" in logic.settings_text({"fio": "А Б", "group_name": "ТС"}, date(2026, 10, 27), date(2026, 9, 1), None)
print("✅ настройки без «определён по»")

# ---------- звонки от ИИ ----------
bells, probs = logic.bells_from_ai({"bells": [
    {"pair": 1, "start": "08:30", "end": "10:05"},                                  # только начало и конец
    {"pair": 2, "start1": "10:15", "end1": "11:00", "start2": "11:05", "end2": "11:50"},
    {"pair": 3, "start": "12:00", "end": "11:00"},                                  # перепутан порядок
    {"pair": "x", "start": "1", "end": "2"},
]})
assert bells[0] == [1, "08:30", "09:17", "09:17", "10:05"], bells
assert bells[1] == [2, "10:15", "11:00", "11:05", "11:50"] and len(bells) == 2 and len(probs) == 2
lun, lp = logic.lunches_from_ai({"lunches": [{"start": "11:50", "end": "12:20"}, {"start": "11:50", "end": "12:20"},
                                             {"start": "13:00", "end": "12:00"}]})
assert lun == [("11:50", "12:20")] and len(lp) == 1
assert "11:50–12:20" in logic.ai_bells_preview_text(1, bells, lun, probs)
# звонки из фото-ответа расписания тоже понимают «начало–конец»
_, b2, _ = logic.lessons_from_ai({"lessons": [], "bells": [{"pair": 1, "start": "08:30", "end": "10:00"}]}, None, 1, date(2026, 10, 27))
assert b2 == [[1, "08:30", "09:15", "09:15", "10:00"]]
print("✅ звонки от ИИ")

# ---------- ответ ИИ с «рассуждениями» и разбор запроса ----------
import sys, types
sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientTimeout=lambda **k: None, ClientSession=None,
                                                         ClientError=Exception))
import config, ai_parser
assert ai_parser.extract_json('<think>думаю {не json}</think>```json\n{"bells": []}\n```') == {"bells": []}
config.AI_API_KEY, config.AI_BASE_URL = "nvapi-abc", ""
assert ai_parser.provider_name() == "custom" and "nvidia.com" in ai_parser.resolve_base_url()
config.AI_API_KEY = "sk-or-abc"
assert "openrouter.ai" in ai_parser.resolve_base_url()
config.AI_API_KEY = "unknown-key"
try:
    ai_parser.resolve_base_url()
    raise SystemExit("нужна ошибка про AI_BASE_URL")
except ai_parser.AIError as e:
    assert "AI_BASE_URL" in str(e)
config.AI_BASE_URL = "https://example.com/v1/"
assert ai_parser.resolve_base_url() == "https://example.com/v1"
assert not ai_parser.vision_provider()                       # AI_VISION_MODEL не задан -> фото не читаем
config.AI_VISION_MODEL = "some-vl-model"
assert ai_parser.vision_provider() == "custom"
assert "НЕЛЬЗЯ" not in ai_parser._text_prompt("bells", "1 пара 8.30-10.05") and "8.30-10.05" in ai_parser._text_prompt("bells", "1 пара 8.30-10.05")
assert "ТС 9-26-3" in ai_parser._text_prompt("schedule", "пн 1 мат", "ТС 9-26-3")
assert "модель" in ai_parser.explain_custom_error(404, "", "m").lower() and "AI_MODEL" in ai_parser.explain_custom_error(404, "", "m")
print("✅ ИИ: адрес по ключу, текст, звонки, <think>")

print("\nВсе проверки пройдены!")
