"""Работа с базой данных SQLite. Файл базы college_bot.db создаётся сам при первом запуске."""

import os
import sqlite3

import config

DB_PATH = config.DB_PATH


def _run(sql: str, params=()):
    """Выполняет запрос и возвращает (строки, курсор)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # чтобы обращаться к полям по имени: row["fio"]
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.commit()
        return rows, cur
    finally:
        conn.close()


def init_db():
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)  # на случай, если папки ещё нет
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id      INTEGER PRIMARY KEY,   -- id пользователя в Telegram
            fio        TEXT NOT NULL,
            group_name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bells (
            pair_num INTEGER PRIMARY KEY,     -- номер пары
            start1   TEXT NOT NULL,           -- начало первой половины, '08:30'
            end1     TEXT NOT NULL,           -- конец первой половины
            start2   TEXT NOT NULL,           -- начало второй половины
            end2     TEXT NOT NULL            -- конец пары
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
        """)
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


# ---------- Звонки ----------

def set_bell(pair_num: int, start1: str, end1: str, start2: str, end2: str):
    _run("INSERT OR REPLACE INTO bells (pair_num, start1, end1, start2, end2) "
         "VALUES (?, ?, ?, ?, ?)", (pair_num, start1, end1, start2, end2))


def delete_bell(pair_num: int) -> bool:
    _, cur = _run("DELETE FROM bells WHERE pair_num = ?", (pair_num,))
    return cur.rowcount > 0


def get_bells():
    rows, _ = _run("SELECT * FROM bells ORDER BY pair_num")
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
    """Удаляет всё расписание группы (перед загрузкой нового). Возвращает, сколько удалено."""
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
