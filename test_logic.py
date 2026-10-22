"""Проверка логики без Telegram. Запуск: python test_logic.py"""

from datetime import date, datetime

import logic

bells = logic.bells_from_rows([
    {"pair_num": 1, "start1": "08:30", "end1": "09:15", "start2": "09:20", "end2": "10:05"},
    {"pair_num": 2, "start1": "10:15", "end1": "11:00", "start2": "11:05", "end2": "11:50"},
    {"pair_num": 3, "start1": "12:20", "end1": "13:05", "start2": "13:10", "end2": "13:55"},
])

# У группы 1 и 3 пара, на 2-й «окно»
lessons = [
    {"pair_num": 3, "subject": "Физика", "room": "305", "teacher": None},
    {"pair_num": 1, "subject": "Математика", "room": "214", "teacher": "Иванова И.И."},
]


def at(hh_mm: str) -> datetime:
    h, m = map(int, hh_mm.split(":"))
    return datetime(2026, 10, 22, h, m)


cases = [
    ("08:00", ["Пары ещё не начались", "Математика", "30 мин"]),
    ("08:40", ["Первая половина", "35 мин"]),
    ("09:17", ["Перемена между половинами", "3 мин"]),
    ("09:30", ["Вторая половина", "35 мин"]),
    ("10:30", ["перерыв", "Физика", "1 ч 50 мин"]),   # окно на месте 2-й пары
    ("13:55", ["закончились"]),
]

for moment, expected in cases:
    text = logic.current_pair_text(at(moment), lessons, bells)
    for piece in expected:
        assert piece in text, f"{moment}: ожидал «{piece}», а бот ответил:\n{text}"
    print(f"✅ {moment} → {text.splitlines()[0]}")

assert "пар нет" in logic.current_pair_text(at("10:00"), [], bells)
print("✅ день без пар")

# Числитель / знаменатель (1 сентября 2026 — вторник, эта неделя = числитель)
start = date(2026, 9, 1)
assert logic.week_type_for(date(2026, 8, 31), start) == 1
assert logic.week_type_for(date(2026, 9, 6), start) == 1
assert logic.week_type_for(date(2026, 9, 7), start) == 2
assert logic.week_type_for(date(2026, 9, 14), start) == 1
print("✅ числитель/знаменатель")

# Разбор строки расписания
data = logic.parse_lesson_line(" тс  9-26-3 ; Пн; 1; Математика; 214; -; знам")
assert data == {"group_name": "ТС 9-26-3", "weekday": 1, "pair_num": 1, "subject": "Математика",
                "room": "214", "teacher": None, "week_type": 2}, data
for bad in ["ТС 9-26-3; пн; один; Математика", "ТС 9-26-3; вс; 1; Математика", "ТС 9-26-3; пн"]:
    try:
        logic.parse_lesson_line(bad)
        raise AssertionError(f"должна быть ошибка: {bad}")
    except ValueError:
        pass
print("✅ разбор команды /add_lesson")

# Проверка ответа ИИ: хорошие пары проходят, плохие попадают в «пропущено»
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
ai_lessons, ai_bells, ai_problems = logic.lessons_from_ai(ai_answer, group_hint="ТС 9-26-3")
assert len(ai_lessons) == 2, ai_lessons
assert ai_lessons[1]["group_name"] == "ТС 9-26-3" and ai_lessons[1]["week_type"] == 1
assert ai_bells == [[1, "08:30", "09:15", "09:20", "10:05"]], ai_bells
assert len(ai_problems) == 3, ai_problems
preview = logic.ai_preview_text(ai_lessons, ai_bells, ai_problems)
assert "Математика" in preview and "Пропущено" in preview
print("✅ проверка ответа ИИ")

print("\nВсе проверки пройдены!")
