"""Работа с базой данных SQLite. Файл базы создаётся сам при первом запуске.

Звонки и обеды хранятся ОТДЕЛЬНО для каждого курса (1–4).
Старая база (где звонки были общими) обновится сама: звонки скопируются на все 4 курса.
"""

import os
import sqlite3

import config


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row  # чтобы обращаться к полям по имени: row["fio"]
    return conn


def _run(sql: str, params=()):
    """Выполняет запрос и возвращает (строки, курсор)."""
    conn = _connect()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.commit()
        return rows, cur
    finally:
        conn.close()


def init_db():
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)

    conn = _connect()
    try:
        # --- миграция: раньше таблица bells не знала про курсы ---
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(bells)")]
        old_bells = []
        if columns and "course" not in columns:
            old_bells = conn.execute("SELECT * FROM bells").fetchall()
            conn.execute("DROP TABLE bells")

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id      INTEGER PRIMARY KEY,   -- id пользователя в Telegram
            fio        TEXT NOT NULL,
            group_name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bells (
            course   INTEGER NOT NULL,        -- курс 1..4
            pair_num INTEGER NOT NULL,        -- номер пары
            start1   TEXT NOT NULL,           -- начало первой половины, '08:30'
            end1     TEXT NOT NULL,           -- конец первой половины
            start2   TEXT NOT NULL,           -- начало второй половины
            end2     TEXT NOT NULL,           -- конец пары
            PRIMARY KEY (course, pair_num)
        );

        CREATE TABLE IF NOT EXISTS lunches (
            course INTEGER NOT NULL,          -- курс 1..4
            start  TEXT NOT NULL,             -- начало обеда, '11:50'
            end    TEXT NOT NULL,             -- конец обеда
            PRIMARY KEY (course, start)
        );

        CREATE TABLE IF NOT EXISTS lessons (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            weekday    INTEGER NOT NULL,      -- 1 = пн ... 6 = сб
            pair_num   INTEGER NOT NULL,
            subject    TEXT NOT NULL,
            room       TEXT,
            teacher    TEXT,
            week_type  INTEGER NOT NULL DEFAULT 0  -- 0 каждую, 1 числитель, 2 знаменатель
        );

        CREATE TABLE IF NOT EXISTS specialties (
            prefix TEXT PRIMARY KEY,          -- буквы в начале группы, например 'ТС'
            name   TEXT NOT NULL              -- полное название специальности
        );
        """)
        for row in old_bells:
            for course in (1, 2, 3, 4):
                conn.execute("INSERT OR REPLACE INTO bells VALUES (?, ?, ?, ?, ?, ?)",
                             (course, row["pair_num"], row["start1"], row["end1"],
                              row["start2"], row["end2"]))
        conn.commit()
    finally:
        conn.close()


# ---------- Пользователи ----------

def get_user(tg_id: int):
    rows, _ = _run("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    return rows[0] if rows else None


def save_user(tg_id: int, fio: str, group_name: str):
    _run("INSERT OR REPLACE INTO users (tg_id, fio, group_name) VALUES (?, ?, ?)",
         (tg_id, fio, group_name))


def count_users() -> int:
    rows, _ = _run("SELECT COUNT(*) AS n FROM users")
    return rows[0]["n"]


# ---------- Специальности ----------

def get_specialty(prefix: str):
    rows, _ = _run("SELECT name FROM specialties WHERE prefix = ?", (prefix,))
    return rows[0]["name"] if rows else None


def set_specialty(prefix: str, name: str):
    _run("INSERT OR REPLACE INTO specialties (prefix, name) VALUES (?, ?)", (prefix, name))


def get_specialties():
    rows, _ = _run("SELECT * FROM specialties ORDER BY prefix")
    return rows


# ---------- Звонки (по курсам) ----------

def set_bell(course: int, pair_num: int, start1: str, end1: str, start2: str, end2: str):
    _run("INSERT OR REPLACE INTO bells (course, pair_num, start1, end1, start2, end2) "
         "VALUES (?, ?, ?, ?, ?, ?)", (course, pair_num, start1, end1, start2, end2))


def delete_bell(course: int, pair_num: int) -> bool:
    _, cur = _run("DELETE FROM bells WHERE course = ? AND pair_num = ?", (course, pair_num))
    return cur.rowcount > 0


def get_bells(course: int):
    rows, _ = _run("SELECT * FROM bells WHERE course = ? ORDER BY pair_num", (course,))
    return rows


# ---------- Обеды (по курсам) ----------

def add_lunch(course: int, start: str, end: str):
    _run("INSERT OR REPLACE INTO lunches (course, start, end) VALUES (?, ?, ?)",
         (course, start, end))


def delete_lunch(course: int, start: str) -> bool:
    _, cur = _run("DELETE FROM lunches WHERE course = ? AND start = ?", (course, start))
    return cur.rowcount > 0


def get_lunches(course: int):
    rows, _ = _run("SELECT * FROM lunches WHERE course = ? ORDER BY start", (course,))
    return rows


# ---------- Расписание ----------

def add_lesson(group_name, weekday, pair_num, subject, room, teacher, week_type) -> int:
    _, cur = _run(
        "INSERT INTO lessons (group_name, weekday, pair_num, subject, room, teacher, week_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (group_name, weekday, pair_num, subject, room, teacher, week_type))
    return cur.lastrowid


def delete_lesson(lesson_id: int) -> bool:
    _, cur = _run("DELETE FROM lessons WHERE id = ?", (lesson_id,))
    return cur.rowcount > 0


def delete_group_lessons(group_name: str) -> int:
    _, cur = _run("DELETE FROM lessons WHERE group_name = ?", (group_name,))
    return cur.rowcount


def get_lessons_for_day(group_name: str, weekday: int, week_type: int):
    """Пары группы на день: те, что каждую неделю, плюс те, что на текущий тип недели."""
    rows, _ = _run(
        "SELECT * FROM lessons WHERE group_name = ? AND weekday = ? AND week_type IN (0, ?) "
        "ORDER BY pair_num",
        (group_name, weekday, week_type))
    return rows


def get_group_lessons(group_name: str):
    rows, _ = _run("SELECT * FROM lessons WHERE group_name = ? "
                   "ORDER BY weekday, pair_num, week_type", (group_name,))
    return rows


def get_groups() -> list:
    rows, _ = _run("SELECT DISTINCT group_name FROM lessons ORDER BY group_name")
    return [r["group_name"] for r in rows]
