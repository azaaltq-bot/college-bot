"""Telegram-бот «Помощник ЧТЖТ». Запуск: python bot.py"""

import asyncio
import contextlib
import logging
import time as pytime
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)

import ai_parser
import config
import database as db
import logic
import specialties

router = Router()

# Фильтр «сообщение/нажатие от администратора»
IsAdmin = F.from_user.id.in_(config.ADMIN_IDS)

BTN_NOW = "📍 Текущая пара"
BTN_TODAY = "📅 Расписание на сегодня"
BTN_SETTINGS = "⚙️ Настройки"
BTN_ADMIN = "🛠 Админ-панель"
MENU_TEXTS = {BTN_NOW, BTN_TODAY, BTN_SETTINGS, BTN_ADMIN}

COURSES = (1, 2, 3, 4)


class Registration(StatesGroup):
    """Шаги регистрации: сначала ФИО, потом группа."""
    fio = State()
    group = State()


class AdminInput(StatesGroup):
    """Админ в админ-панели ждёт от нас сообщение определённого вида."""
    photo = State()
    schedule_text = State()
    bells = State()
    del_bell = State()
    lunches = State()
    del_lunch = State()
    ai_schedule = State()
    ai_bells = State()


# ---------- Вспомогательное ----------

def now_local() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_NOW)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_SETTINGS)]]
    if user_id in config.ADMIN_IDS:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def groups_keyboard():
    """Кнопки с группами, которые уже есть в расписании."""
    groups = db.get_groups()
    if not groups:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=g)] for g in groups],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def load_day(user) -> dict:
    """Всё, что нужно знать про сегодняшний день пользователя: пары, звонки и обеды ЕГО курса."""
    now = now_local()
    week_type = logic.week_type_for(now.date(), config.SEMESTER_START)
    course = logic.course_of_group(user["group_name"], now.date())
    lessons = db.get_lessons_for_day(user["group_name"], now.isoweekday(), week_type)
    bells = logic.bells_from_rows(db.get_bells(course)) if course else {}
    lunches = logic.lunches_from_rows(db.get_lunches(course)) if course else []
    return {"week_type": week_type, "course": course,
            "lessons": lessons, "bells": bells, "lunches": lunches}


async def start_registration(message: Message, state: FSMContext):
    await state.set_state(Registration.fio)
    await message.answer(
        "Привет! Я помощник студента ЧТЖТ 🚆\n"
        "Подскажу, какая сейчас пара и сколько до её конца.\n\n"
        "Для начала напиши свои ФИО.\n"
        "Например: Иванов Иван Иванович",
        reply_markup=ReplyKeyboardRemove(),
    )


async def safe_edit(message: Message, text: str, reply_markup=None):
    """Правит сообщение и не падает, если текст не изменился."""
    with contextlib.suppress(TelegramBadRequest):
        await message.edit_text(text, reply_markup=reply_markup)


# ---------- Команды для всех ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user:
        words = user["fio"].split()
        name = logic.esc(words[1] if len(words) > 1 else user["fio"])
        await message.answer(f"С возвращением, {name}! Группа {logic.esc(user['group_name'])}.",
                             reply_markup=main_menu(message.from_user.id))
    else:
        await start_registration(message, state)


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}\n"
                         f"Чтобы стать админом, добавь его в переменную ADMIN_IDS.")


@router.message(Command("week"))
async def cmd_week(message: Message):
    await message.answer(logic.week_explain_text(now_local().date(), config.SEMESTER_START))


# ---------- Живая кнопка «Текущая пара» ----------

live_tasks: dict = {}      # chat_id -> задача, которая обновляет сообщение
live_messages: dict = {}   # chat_id -> id последнего сообщения «Текущая пара»


async def stop_live(bot: Bot, chat_id: int):
    """Останавливает обновление и удаляет предыдущее сообщение «Текущая пара»."""
    task = live_tasks.pop(chat_id, None)
    if task:
        task.cancel()
    message_id = live_messages.pop(chat_id, None)
    if message_id:
        with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
            await bot.delete_message(chat_id, message_id)


async def live_loop(bot: Bot, chat_id: int, message_id: int, tg_id: int, last_text: str):
    """Раз в секунду пересчитывает время (по Чите) и правит сообщение."""
    started = pytime.monotonic()
    snapshot, loaded_at = None, 0.0
    try:
        while True:
            # пары и звонки из базы перечитываем раз в 30 секунд, а не каждую секунду
            if snapshot is None or pytime.monotonic() - loaded_at > 30:
                user = db.get_user(tg_id)
                if not user:
                    break
                snapshot, loaded_at = load_day(user), pytime.monotonic()

            now = now_local()
            text, live = logic.current_pair(now, snapshot["lessons"], snapshot["bells"],
                                            snapshot["lunches"])
            if live and pytime.monotonic() - started > config.LIVE_MAX_SECONDS:
                text += "\n\n⏸ Таймер остановлен. Нажми «Текущая пара», чтобы запустить снова."
                live = False

            if text != last_text:
                try:
                    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
                except TelegramRetryAfter as error:   # Telegram просит притормозить
                    await asyncio.sleep(error.retry_after)
                    continue
                except TelegramBadRequest as error:
                    if "not modified" not in str(error):
                        break                         # сообщение удалено — больше не обновляем
                except TelegramForbiddenError:
                    break                             # пользователь заблокировал бота
                last_text = text

            if not live:
                break
            if config.LIVE_INTERVAL <= 1:             # подстраиваемся под границу секунды
                await asyncio.sleep(1.0 - now.microsecond / 1_000_000 + 0.05)
            else:
                await asyncio.sleep(config.LIVE_INTERVAL)
    except asyncio.CancelledError:
        pass
    finally:
        if live_tasks.get(chat_id) is asyncio.current_task():
            live_tasks.pop(chat_id, None)


# ---------- Кнопки меню ----------

@router.message(F.text == BTN_NOW)
async def btn_current_pair(message: Message, state: FSMContext, bot: Bot):
    await state.set_state(None)
    user = db.get_user(message.from_user.id)
    if not user:
        await start_registration(message, state)
        return

    chat_id = message.chat.id
    await stop_live(bot, chat_id)                       # старое сообщение удаляем
    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
        await message.delete()                          # и само нажатие кнопки, чтобы не копился мусор

    snapshot = load_day(user)
    text, live = logic.current_pair(now_local(), snapshot["lessons"], snapshot["bells"],
                                    snapshot["lunches"])
    sent = await bot.send_message(chat_id, text)        # новое сообщение с таймером
    live_messages[chat_id] = sent.message_id
    if live:
        live_tasks[chat_id] = asyncio.create_task(
            live_loop(bot, chat_id, sent.message_id, message.from_user.id, text))


@router.message(F.text == BTN_TODAY)
async def btn_today(message: Message, state: FSMContext):
    await state.set_state(None)
    user = db.get_user(message.from_user.id)
    if not user:
        await start_registration(message, state)
        return
    snapshot = load_day(user)
    await message.answer(
        logic.today_schedule_text(now_local(), snapshot["lessons"], snapshot["bells"],
                                  snapshot["week_type"], snapshot["lunches"]),
        reply_markup=main_menu(message.from_user.id))


# ---------- Настройки ----------

def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить ФИО и группу", callback_data="set:edit")],
        [InlineKeyboardButton(text="🔔 Звонки и обеды моего курса", callback_data="set:bells")],
        [InlineKeyboardButton(text="❓ Зелёное и синее расписание", callback_data="set:week")],
    ])


@router.message(F.text == BTN_SETTINGS)
async def btn_settings(message: Message, state: FSMContext):
    await state.set_state(None)
    user = db.get_user(message.from_user.id)
    if not user:
        await start_registration(message, state)
        return
    today = now_local().date()
    specialty = db.get_specialty(logic.group_prefix(user["group_name"]))
    await message.answer(logic.settings_text(user, today, config.SEMESTER_START, specialty),
                         reply_markup=settings_keyboard())


@router.callback_query(F.data.startswith("set:"))
async def settings_callback(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    user = db.get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        await start_registration(callback.message, state)
        return

    if action == "edit":
        await callback.answer()
        await start_registration(callback.message, state)
    elif action == "week":
        await callback.answer()
        await callback.message.answer(logic.week_explain_text(now_local().date(), config.SEMESTER_START))
    elif action == "bells":
        await callback.answer()
        course = logic.course_of_group(user["group_name"], now_local().date())
        if not course:
            await callback.message.answer("Не смог определить твой курс по группе 🤔")
            return
        await callback.message.answer(
            logic.bells_text(course, db.get_bells(course), db.get_lunches(course)))


# ---------- Регистрация ----------

@router.message(Registration.fio, F.text)
async def reg_fio(message: Message, state: FSMContext):
    fio = " ".join(message.text.split())
    words = fio.split()
    if len(words) < 2 or not all(w.replace("-", "").isalpha() for w in words):
        await message.answer("Напиши ФИО буквами: минимум фамилию и имя.")
        return
    await state.update_data(fio=fio.title())
    await state.set_state(Registration.group)
    await message.answer(group_prompt(), reply_markup=groups_keyboard())


def group_prompt() -> str:
    prefixes = ", ".join(sorted(logic.KNOWN_PREFIXES))
    return (
        "Отлично! Теперь выбери свою группу из списка или напиши её.\n\n"
        "📝 <b>Шаблон:</b> буквы специальности, пробел, номер группы.\n"
        f"<b>НАПРИМЕР:</b> <code>{specialties.GROUP_TEMPLATE}</code>\n\n"
        f"Буквы специальности: {logic.esc(prefixes)}\n"
        "Если ошибёшься в букве — я сам исправлю (цифры не меняю)."
    )


@router.message(Registration.group, F.text)
async def reg_group(message: Message, state: FSMContext):
    group, status = logic.autocorrect_group(message.text)
    if status == "bad_format":
        await message.answer("Не вижу номер группы 🤔\n\n" + group_prompt(),
                             reply_markup=groups_keyboard())
        return
    if status in ("unknown", "ambiguous"):
        why = ("Не нашёл такой специальности" if status == "unknown"
               else "Не уверен, какая именно специальность")
        await message.answer(f"{why}: «{logic.esc(logic.group_prefix(group))}».\n\n" + group_prompt(),
                             reply_markup=groups_keyboard())
        return
    if logic.admission_year(group) is None:
        await message.answer("Не вижу в номере группы год поступления (например, 9-26).\n\n" + group_prompt(),
                             reply_markup=groups_keyboard())
        return
    known_groups = db.get_groups()
    if known_groups and group not in known_groups:
        await message.answer("Такой группы пока нет в расписании. Выбери из списка или проверь написание.\n"
                             f"Шаблон: <code>{specialties.GROUP_TEMPLATE}</code>",
                             reply_markup=groups_keyboard())
        return
    if status == "fixed":
        await message.answer(f"✏️ Исправил на <b>{logic.esc(group)}</b>")
    data = await state.get_data()
    db.save_user(message.from_user.id, data["fio"], group)
    await state.clear()

    course, note = logic.course_info(group, now_local().date())
    course_line = f"🔢 {course} курс" if course else f"🔢 Курс не определён: {note}"
    await message.answer(f"Готово! ✅\n👤 {logic.esc(data['fio'])}\n👥 Группа {logic.esc(group)}\n"
                         f"{course_line}\n\nПользуйся кнопками внизу 👇",
                         reply_markup=main_menu(message.from_user.id))


# ---------- Админ-панель ----------

def course_of_group_or_none(group: str):
    return logic.course_of_group(group, now_local().date())


def groups_of_course(course: int) -> list:
    return [g for g in db.get_groups() if course_of_group_or_none(g) == course]


def back_button(data: str, text: str = "⬅️ Назад") -> list:
    return [InlineKeyboardButton(text=text, callback_data=data)]


def admin_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 курс", callback_data="adm:c:1"),
         InlineKeyboardButton(text="2 курс", callback_data="adm:c:2")],
        [InlineKeyboardButton(text="3 курс", callback_data="adm:c:3"),
         InlineKeyboardButton(text="4 курс", callback_data="adm:c:4")],
        [InlineKeyboardButton(text="👥 Статистика", callback_data="adm:stats"),
         InlineKeyboardButton(text="🎓 Специальности", callback_data="adm:spec")],
    ])


ADMIN_ROOT_TEXT = (
    "🛠 <b>Админ-панель</b>\n"
    f"{logic.SEP}\n"
    "Выбери курс — для каждого курса расписание, звонки и обеды заполняются отдельно.\n\n"
    "🤖 Расписание и звонки разбирает ИИ: внутри курса выбери раздел и пришли текст "
    "(Nemotron читает текст; фото — если подключена модель с распознаванием картинок).\n\n"
    "Полезные команды:\n"
    "/set_specialty ТС Название специальности\n"
    "/schedule ТС 9-26-3 — расписание группы\n"
    "/del_lesson 5 — удалить запись №5\n"
    "/add_lesson ТС 9-26-3; пн; 1; Математика; 214; Иванова И.И.; каждая"
)


def course_menu(course: int):
    groups = groups_of_course(course)
    text = (f"🎓 <b>{course} курс</b>\n{logic.SEP}\n"
            f"👥 Групп с расписанием: {len(groups)}"
            + (f" ({logic.esc(', '.join(groups))})" if groups else "") + "\n"
            f"🔔 Звонков: {len(db.get_bells(course))}\n"
            f"🍽 Обедов: {len(db.get_lunches(course))}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Расписание", callback_data=f"adm:sch:{course}")],
        [InlineKeyboardButton(text="🔔 Звонки", callback_data=f"adm:bel:{course}")],
        [InlineKeyboardButton(text="🍽 Обеды", callback_data=f"adm:lun:{course}")],
        back_button("adm:root", "⬅️ К выбору курса"),
    ])
    return text, kb


def section_menu(kind: str, course: int):
    """Меню раздела: sch — расписание, bel — звонки, lun — обеды."""
    if kind == "sch":
        text = (f"📅 <b>Расписание — {course} курс</b>\n{logic.SEP}\n"
                "Загрузи расписание фото/скриншотом или впиши пары текстом. "
                "Группы других курсов бот не примет.")
        rows = [[InlineKeyboardButton(text="📷 Загрузить по фото", callback_data=f"adm:schphoto:{course}")],
                [InlineKeyboardButton(text="🤖 ИИ разберёт текст расписания", callback_data=f"adm:schai:{course}")],
                [InlineKeyboardButton(text="✍️ Добавить вручную (по строкам)", callback_data=f"adm:schtext:{course}")],
                [InlineKeyboardButton(text="📋 Посмотреть", callback_data=f"adm:schview:{course}")]]
    elif kind == "bel":
        text = (f"🔔 <b>Звонки — {course} курс</b>\n{logic.SEP}\n"
                "Звонки этого курса. Другие курсы не затрагиваются.")
        rows = [[InlineKeyboardButton(text="🤖 ИИ разберёт звонки (текст или фото)", callback_data=f"adm:belai:{course}")],
                [InlineKeyboardButton(text="➕ Добавить / изменить вручную", callback_data=f"adm:beladd:{course}")],
                [InlineKeyboardButton(text="📋 Показать", callback_data=f"adm:belshow:{course}"),
                 InlineKeyboardButton(text="🗑 Удалить пару", callback_data=f"adm:beldel:{course}")]]
    else:
        text = (f"🍽 <b>Обеды — {course} курс</b>\n{logic.SEP}\n"
                "Время обеда этого курса. Бот покажет «Сейчас обед» и таймер до его конца.")
        rows = [[InlineKeyboardButton(text="➕ Добавить обед", callback_data=f"adm:lunadd:{course}")],
                [InlineKeyboardButton(text="📋 Показать", callback_data=f"adm:lunshow:{course}"),
                 InlineKeyboardButton(text="🗑 Удалить обед", callback_data=f"adm:lundel:{course}")]]
    rows.append(back_button(f"adm:c:{course}", f"⬅️ К {course} курсу"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


PROMPTS = {
    "schphoto": (AdminInput.photo,
                 "📷 Пришли фото или скриншот расписания (можно файлом).\n"
                 "Если на нём не видно название группы — подпиши фото, например: ТС 9-26-3\n"
                 "Отмена: нажми любую кнопку меню."),
    "schai": (AdminInput.ai_schedule,
              "🤖 Пришли расписание <b>текстом</b> — скопируй его как есть, формат любой.\n"
              "Обязательно укажи группу, например: ТС 9-26-3.\n"
              "Если пара идёт только по одной неделе, пометь её: зелёная (🟢) или синяя (🔵).\n"
              "Дальше ИИ всё разберёт, а ты проверишь и подтвердишь.\n"
              "Отмена: нажми любую кнопку меню."),
    "belai": (AdminInput.ai_bells,
              "🤖 Пришли расписание звонков <b>текстом</b> — как есть, формат любой. "
              "Например:\n<code>1 пара 8:30–10:05\n2 пара 10:15–11:50\nОбед 11:50–12:20</code>\n"
              "Можно и фото, если подключена модель с распознаванием картинок.\n"
              "ИИ разберёт время пар (и обед, если он указан), а ты проверишь и подтвердишь.\n"
              "Отмена: нажми любую кнопку меню."),
    "schtext": (AdminInput.schedule_text,
                "✍️ Пришли пары — по одной в строке:\n"
                "<code>группа; день; пара; предмет; кабинет; преподаватель; неделя</code>\n\n"
                "Например:\n<code>ТС 9-26-3; пн; 1; Математика; 214; Иванова И.И.; каждая\n"
                "ТС 9-26-3; пн; 2; Физика; 305; -; зелёная</code>\n"
                "Неделя: каждая, зелёная или синяя. Кабинет, преподавателя и неделю можно пропустить."),
    "beladd": (AdminInput.bells,
               "🔔 Пришли звонки — по одной паре в строке:\n"
               "<code>номер_пары начало1 конец1 начало2 конец2</code>\n\n"
               "Например:\n<code>1 08:30 09:15 09:20 10:05\n2 10:15 11:00 11:05 11:50</code>\n"
               "Если у пары нет перерыва посередине, конец 1-й половины = начало 2-й."),
    "beldel": (AdminInput.del_bell, "🗑 Пришли номер пары, звонок которой удалить. Например: 3"),
    "lunadd": (AdminInput.lunches,
               "🍽 Пришли время обеда — по одному в строке:\n"
               "<code>11:50 12:20</code>\n"
               "Можно несколько строк, если обедов несколько (например, и 15:40–16:00)."),
    "lundel": (AdminInput.del_lunch, "🗑 Пришли время НАЧАЛА обеда, который удалить. Например: 11:50"),
}


@router.message(Command("admin"), IsAdmin)
async def cmd_admin(message: Message, state: FSMContext):
    await state.set_state(None)
    await message.answer(ADMIN_ROOT_TEXT, reply_markup=admin_root_kb())


@router.message(F.text == BTN_ADMIN, IsAdmin)
async def btn_admin(message: Message, state: FSMContext):
    await state.set_state(None)
    await message.answer(ADMIN_ROOT_TEXT, reply_markup=admin_root_kb())


@router.callback_query(F.data.startswith("adm:"), IsAdmin)
async def admin_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    action = parts[1]
    course = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    message = callback.message
    await callback.answer()

    if action == "root":
        await state.set_state(None)
        await safe_edit(message, ADMIN_ROOT_TEXT, admin_root_kb())
    elif action == "c" and course:
        await state.set_state(None)
        text, kb = course_menu(course)
        await safe_edit(message, text, kb)
    elif action in ("sch", "bel", "lun") and course:
        await state.set_state(None)
        text, kb = section_menu(action, course)
        await safe_edit(message, text, kb)
    elif action in PROMPTS and course:
        target_state, prompt = PROMPTS[action]
        await state.set_state(target_state)
        await state.update_data(course=course)
        await message.answer(prompt)
    elif action == "schview" and course:
        groups = groups_of_course(course)
        if not groups:
            await message.answer(f"Для {course} курса расписания пока нет.")
        for group in groups:
            await send_long(message, logic.group_schedule_text(group, db.get_group_lessons(group)))
    elif action == "belshow" and course:
        await message.answer(logic.bells_text(course, db.get_bells(course), []))
    elif action == "lunshow" and course:
        rows = db.get_lunches(course)
        lines = [f"🍽 <b>Обеды — {course} курс</b>", logic.SEP]
        lines += [f"{r['start']}–{r['end']}" for r in rows] or ["Пока не заданы."]
        await message.answer("\n".join(lines))
    elif action == "stats":
        per_course = []
        for c in COURSES:
            per_course.append(f"  {c} курс: групп — {len(groups_of_course(c))}, "
                              f"звонков — {len(db.get_bells(c))}, обедов — {len(db.get_lunches(c))}")
        await message.answer(f"👥 Зарегистрировано студентов: {db.count_users()}\n"
                             f"📚 Групп в расписании: {len(db.get_groups())}\n" + "\n".join(per_course))
    elif action == "spec":
        rows = db.get_specialties()
        lines = ["🎓 <b>Специальности</b>", logic.SEP]
        lines += [f"{logic.esc(r['prefix'])} — {logic.esc(r['name'])}" for r in rows] or ["Пока не заданы."]
        lines.append("\nДобавить: <code>/set_specialty ТС Название специальности</code>")
        lines += ["", "📋 <b>Специальности ЧТЖТ (irgups.ru)</b>", logic.SEP]
        lines += [f"{code} — {logic.esc(name)}" for code, name in specialties.OFFICIAL_SPECIALTIES]
        await message.answer("\n".join(lines))


@router.message(Command("set_specialty"), IsAdmin)
async def cmd_set_specialty(message: Message, command: CommandObject):
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /set_specialty ТС Название специальности")
        return
    prefix = logic.group_prefix(parts[0])
    if not prefix:
        await message.answer("Первое слово — буквы группы, например ТС.")
        return
    db.set_specialty(prefix, parts[1].strip())
    logic.KNOWN_PREFIXES.add(prefix)
    await message.answer(f"✅ Специальность для групп «{logic.esc(prefix)}» сохранена.")


# ---------- Ввод админа: текст ----------

@router.message(StateFilter(AdminInput.schedule_text, AdminInput.bells, AdminInput.del_bell,
                            AdminInput.lunches, AdminInput.del_lunch), F.text, ~F.text.startswith("/"), IsAdmin)
async def admin_text_input(message: Message, state: FSMContext):
    current = await state.get_state()
    course = (await state.get_data()).get("course")
    lines = [line for line in message.text.splitlines() if line.strip()]
    today = now_local().date()
    done, errors = [], []

    if current == AdminInput.schedule_text.state:
        for number, line in enumerate(lines, start=1):
            try:
                data = logic.parse_lesson_line(line)
                group_course = logic.course_of_group(data["group_name"], today)
                if group_course and group_course != course:
                    raise ValueError(f"группа {data['group_name']} — это {group_course} курс, "
                                     f"а ты на {course} курсе")
            except ValueError as error:
                errors.append(f"Строка {number}: {error}")
                continue
            lesson_id = db.add_lesson(**data)
            done.append(f"#{lesson_id} {data['group_name']}, {logic.WEEKDAY_NAMES[data['weekday']]}, "
                        f"{data['pair_num']} пара — {data['subject']}")
    elif current == AdminInput.bells.state:
        for number, line in enumerate(lines, start=1):
            try:
                pair, *times = logic.parse_bell_line(line)
            except ValueError as error:
                errors.append(f"Строка {number}: {error}")
                continue
            db.set_bell(course, pair, *times)
            done.append(f"{pair} пара: {times[0]}–{times[1]} | {times[2]}–{times[3]}")
    elif current == AdminInput.lunches.state:
        for number, line in enumerate(lines, start=1):
            try:
                start, end = logic.parse_lunch_line(line)
            except ValueError as error:
                errors.append(f"Строка {number}: {error}")
                continue
            db.add_lunch(course, start, end)
            done.append(f"Обед {start}–{end}")
    elif current == AdminInput.del_bell.state:
        arg = message.text.strip()
        if arg.isdigit() and db.delete_bell(course, int(arg)):
            done.append(f"Звонок {arg} пары удалён")
        else:
            errors.append("Такой пары в звонках этого курса нет.")
    elif current == AdminInput.del_lunch.state:
        try:
            start = logic.parse_time(message.text).strftime("%H:%M")
        except ValueError:
            errors.append("Пришли время начала обеда в формате ЧЧ:ММ, например 11:50.")
        else:
            if db.delete_lunch(course, start):
                done.append(f"Обед, начинавшийся в {start}, удалён")
            else:
                errors.append("Обеда с таким началом нет.")

    reply = []
    if done:
        reply.append(f"✅ Готово ({course} курс):\n" + "\n".join(logic.esc(d) for d in done))
    if errors:
        reply.append("⚠️ Ошибки:\n" + "\n".join(logic.esc(e) for e in errors))
    if done:
        await state.set_state(None)   # успешно — выходим из режима ввода
        kb = InlineKeyboardMarkup(inline_keyboard=[back_button(f"adm:c:{course}", f"⬅️ К {course} курсу")])
    else:
        reply.append("Попробуй ещё раз или нажми любую кнопку меню для отмены.")
        kb = None
    await send_long(message, "\n\n".join(reply), reply_markup=kb)


# ---------- Загрузка расписания по фото (только админ) ----------

def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить (заменить расписание групп)", callback_data="ai:replace")],
        [InlineKeyboardButton(text="➕ Добавить к существующему", callback_data="ai:add")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ai:cancel")],
    ])


async def send_long(message: Message, text: str, reply_markup=None):
    """Telegram не принимает сообщения длиннее 4096 символов — режем по строкам."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await message.answer(chunk, reply_markup=reply_markup if is_last else None)


@router.message(F.photo | F.document, IsAdmin)
async def admin_schedule_image(message: Message, state: FSMContext, bot: Bot):
    if message.photo:
        file_id, mime = message.photo[-1].file_id, "image/jpeg"  # [-1] — самое большое качество
    else:
        doc = message.document
        if not (doc.mime_type or "").startswith("image/"):
            await message.answer("Пришли расписание картинкой: фото или файл PNG/JPG.")
            return
        file_id, mime = doc.file_id, doc.mime_type

    if not ai_parser.vision_provider():
        await message.answer(ai_parser.NO_VISION_MESSAGE)
        return

    # Если админ зашёл через «Расписание → Загрузить по фото» или «Звонки → ИИ», курс уже выбран
    current_state = await state.get_state()
    bells_mode = current_state == AdminInput.ai_bells.state
    in_photo_mode = current_state in (AdminInput.photo.state, AdminInput.ai_schedule.state, AdminInput.ai_bells.state)
    course = (await state.get_data()).get("course") if in_photo_mode else None

    status = await message.answer("⏳ Распознаю расписание, это может занять до минуты...")
    image = (await bot.download(file_id)).read()
    group_hint = logic.normalize_group(message.caption) if message.caption else None

    try:
        raw = await ai_parser.recognize_image("bells" if bells_mode else "schedule", image, mime, group_hint)
    except ai_parser.AIError as error:
        await status.edit_text(f"❌ {logic.esc(error)}")
        return
    if bells_mode:
        await finish_bells_preview(message, state, status, raw, course)
        return

    await finish_schedule_preview(message, state, status, raw, course, group_hint)


async def finish_schedule_preview(message: Message, state: FSMContext, status: Message,
                                  raw: dict, course, group_hint):
    """Проверяет ответ ИИ и показывает админу, что распознано, с кнопками подтверждения."""
    lessons, bells, problems = logic.lessons_from_ai(raw, group_hint, course, now_local().date())
    if not lessons and not bells:
        text = "🤷 ИИ не нашёл ни одной подходящей пары."
        if problems:
            text += "\n\nЧто не получилось разобрать:\n" + "\n".join(f"• {logic.esc(p)}" for p in problems)
        await status.edit_text(text)
        return

    # Запоминаем результат до нажатия кнопки
    await state.set_state(None)
    await state.update_data(ai_lessons=lessons, ai_bells=bells, ai_course=course)
    await status.delete()
    await send_long(message, logic.ai_preview_text(lessons, bells, problems),
                    reply_markup=confirm_keyboard())


def bells_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить звонки", callback_data="aib:save")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="aib:cancel")],
    ])


async def finish_bells_preview(message: Message, state: FSMContext, status: Message, raw: dict, course):
    bells, problems = logic.bells_from_ai(raw)
    lunches, lunch_problems = logic.lunches_from_ai(raw)
    problems = problems + lunch_problems
    if not bells and not lunches:
        text = "🤷 ИИ не нашёл в тексте звонков."
        if problems:
            text += "\n\nЧто не получилось разобрать:\n" + "\n".join(f"• {logic.esc(p)}" for p in problems)
        await status.edit_text(text)
        return
    await state.set_state(None)
    await state.update_data(aib_bells=bells, aib_lunches=lunches, aib_course=course)
    await status.delete()
    await send_long(message, logic.ai_bells_preview_text(course, bells, lunches, problems),
                    reply_markup=bells_confirm_keyboard())


@router.message(StateFilter(AdminInput.ai_schedule, AdminInput.ai_bells), F.text,
                ~F.text.startswith("/"), IsAdmin)
async def admin_ai_text(message: Message, state: FSMContext):
    """Админ прислал текст расписания или звонков — отдаём его ИИ."""
    current = await state.get_state()
    course = (await state.get_data()).get("course")
    kind = "bells" if current == AdminInput.ai_bells.state else "schedule"
    status = await message.answer("⏳ ИИ разбирает текст, это может занять до минуты...")
    try:
        raw = await ai_parser.analyze_text(kind, message.text)
    except ai_parser.AIError as error:
        await status.edit_text(f"❌ {logic.esc(error)}")
        return
    if kind == "bells":
        await finish_bells_preview(message, state, status, raw, course)
    else:
        await finish_schedule_preview(message, state, status, raw, course, None)


@router.callback_query(F.data.startswith("aib:"), IsAdmin)
async def ai_bells_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    bells, lunches, course = data.get("aib_bells"), data.get("aib_lunches") or [], data.get("aib_course")
    await callback.message.edit_reply_markup(reply_markup=None)
    if course is None or bells is None:
        await callback.answer("Это распознавание уже неактуально. Пришли звонки заново.", show_alert=True)
        return
    await state.update_data(aib_bells=None, aib_lunches=None, aib_course=None)
    if action == "cancel":
        await callback.message.answer("Отменено. В базу ничего не записано.")
        await callback.answer()
        return
    for bell in bells:
        db.set_bell(course, *bell)
    for start, end in lunches:
        db.add_lunch(course, start, end)
    await callback.message.answer(f"✅ Сохранено для {course} курса: звонков — {len(bells)}, обедов — {len(lunches)}.\n"
                                  "Посмотреть: Админ-панель → курс → Звонки → Показать.")
    await callback.answer()


@router.callback_query(F.data.startswith("ai:"), IsAdmin)
async def ai_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    lessons, bells = data.get("ai_lessons"), data.get("ai_bells") or []
    course = data.get("ai_course")
    await callback.message.edit_reply_markup(reply_markup=None)  # убираем кнопки

    if lessons is None:
        await callback.answer("Это распознавание уже неактуально. Пришли фото заново.", show_alert=True)
        return
    await state.update_data(ai_lessons=None, ai_bells=None, ai_course=None)

    if action == "cancel":
        await callback.message.answer("Отменено. В базу ничего не записано.")
        await callback.answer()
        return

    groups = sorted({l["group_name"] for l in lessons})
    if action == "replace":
        for group in groups:
            db.delete_group_lessons(group)
    for lesson in lessons:
        db.add_lesson(**lesson)

    # Звонки сохраняем на выбранный курс; если фото прислали без выбора курса — на курсы групп из фото
    if course:
        bell_courses = {course}
    else:
        bell_courses = {c for c in (course_of_group_or_none(g) for g in groups) if c}
    for bell_course in bell_courses:
        for bell in bells:
            db.set_bell(bell_course, *bell)

    note = ""
    if bells and not bell_courses:
        note = "\n⚠️ Звонки не сохранены: не смог понять курс. Добавь их через админ-панель."
    elif bells:
        note = f"\n🔔 Звонки сохранены для курсов: {', '.join(map(str, sorted(bell_courses)))}"
    check = "\n".join(f"/schedule {g}" for g in groups)
    await callback.message.answer(f"✅ Сохранено: пар — {len(lessons)}, звонков — {len(bells)}.{note}\n"
                                  f"Проверить:\n{check}")
    await callback.answer()


# ---------- Остальные команды администратора ----------

@router.message(Command("add_lesson"), IsAdmin)
async def cmd_add_lesson(message: Message, command: CommandObject):
    lines = [line for line in (command.args or "").splitlines() if line.strip()]
    if not lines:
        await message.answer("Формат: /add_lesson ТС 9-26-3; пн; 1; Математика; 214; Иванова И.И.; каждая")
        return

    added, errors = [], []
    for number, line in enumerate(lines, start=1):
        try:
            data = logic.parse_lesson_line(line)
        except ValueError as error:
            errors.append(f"Строка {number}: {error}")
            continue
        lesson_id = db.add_lesson(**data)
        added.append(f"#{lesson_id} {data['group_name']}, "
                     f"{logic.WEEKDAY_NAMES[data['weekday']]}, {data['pair_num']} пара — {data['subject']}")

    reply = []
    if added:
        reply.append("✅ Добавлено:\n" + "\n".join(logic.esc(a) for a in added))
    if errors:
        reply.append("⚠️ Ошибки:\n" + "\n".join(logic.esc(e) for e in errors))
    await message.answer("\n\n".join(reply))


@router.message(Command("schedule"), IsAdmin)
async def cmd_schedule(message: Message, command: CommandObject):
    group = logic.normalize_group(command.args or "")
    if not group:
        await message.answer("Формат: /schedule ТС 9-26-3")
        return
    await send_long(message, logic.group_schedule_text(group, db.get_group_lessons(group)))


@router.message(Command("del_lesson"), IsAdmin)
async def cmd_del_lesson(message: Message, command: CommandObject):
    arg = (command.args or "").strip().lstrip("#")
    if not arg.isdigit():
        await message.answer("Формат: /del_lesson 5 (номер смотри в /schedule)")
        return
    ok = db.delete_lesson(int(arg))
    await message.answer("✅ Удалено." if ok else "Записи с таким номером нет.")


@router.message(Command("stats"), IsAdmin)
async def cmd_stats(message: Message):
    await message.answer(f"👥 Зарегистрировано студентов: {db.count_users()}\n"
                         f"📚 Групп в расписании: {len(db.get_groups())}")


# ---------- Всё остальное ----------

@router.message()
async def fallback(message: Message, state: FSMContext):
    if not db.get_user(message.from_user.id):
        await start_registration(message, state)
        return
    await message.answer("Не понял 🙂 Пользуйся кнопками внизу.",
                         reply_markup=main_menu(message.from_user.id))


async def main():
    logging.basicConfig(level=logging.INFO)
    if not config.BOT_TOKEN:
        print("Не задан BOT_TOKEN. На Railway добавь его во вкладке Variables.")
        return
    provider = ai_parser.provider_name()
    if provider == "custom":
        print(f"ИИ для текста: {config.AI_MODEL}")
    elif provider == "claude":
        print(f"ИИ для текста: Claude ({config.CLAUDE_MODEL})")
    elif provider == "deepseek":
        print(f"ИИ для текста: DeepSeek ({config.DEEPSEEK_MODEL})")
    else:
        print("Внимание: не задан AI_API_KEY — ИИ-разбор расписания и звонков работать не будет "
              "(вручную добавлять можно).")
    if not ai_parser.vision_provider():
        print("Фото расписания не принимаются: нет модели, которая видит картинки (AI_VISION_MODEL).")
    db.init_db()
    logic.set_known_prefixes(specialties.PREFIXES | {r["prefix"] for r in db.get_specialties()})
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот запущен. Остановить: Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
