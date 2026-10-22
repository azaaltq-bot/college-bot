"""Telegram-бот «Помощник ЧТЖТ». Запуск: python bot.py"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)

import ai_parser
import config
import database as db
import logic

router = Router()

# Фильтр «сообщение от администратора»
IsAdmin = F.from_user.id.in_(config.ADMIN_IDS)

BTN_NOW = "📍 Текущая пара"
BTN_TODAY = "📅 Расписание на сегодня"
BTN_EDIT = "✏️ Изменить данные"


class Registration(StatesGroup):
    """Шаги регистрации: сначала ФИО, потом группа."""
    fio = State()
    group = State()


# ---------- Вспомогательное ----------

def now_local() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NOW)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_EDIT)],
        ],
        resize_keyboard=True,
    )


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


def today_data(user):
    """Время, тип недели, пары группы на сегодня и звонки."""
    now = now_local()
    week_type = logic.week_type_for(now.date(), config.SEMESTER_START)
    lessons = db.get_lessons_for_day(user["group_name"], now.isoweekday(), week_type)
    bells = logic.bells_from_rows(db.get_bells())
    return now, week_type, lessons, bells


async def start_registration(message: Message, state: FSMContext):
    await state.set_state(Registration.fio)
    await message.answer(
        "Привет! Я помощник студента ЧТЖТ 🚆\n"
        "Подскажу, какая сейчас пара и сколько до её конца.\n\n"
        "Для начала напиши свои ФИО.\n"
        "Например: Иванов Иван Иванович",
        reply_markup=ReplyKeyboardRemove(),
    )


# ---------- Команды для всех ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user:
        name = user["fio"].split()[1] if len(user["fio"].split()) > 1 else user["fio"]
        await message.answer(f"С возвращением, {name}! Группа {user['group_name']}.",
                             reply_markup=main_menu())
    else:
        await start_registration(message, state)


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}\n"
                         f"Чтобы стать админом, добавь его в переменную ADMIN_IDS.")


@router.message(Command("week"))
async def cmd_week(message: Message):
    week_type = logic.week_type_for(now_local().date(), config.SEMESTER_START)
    await message.answer(f"Сейчас неделя: {logic.WEEK_NAMES[week_type]}")


# ---------- Команды администратора ----------

ADMIN_HELP = """🛠 Админ-панель

🤖 Загрузка по фото:
Просто пришли фото или скриншот расписания. Если на нём не видно
названия группы — подпиши фото, например: ТС 9-26-3
ИИ распознает пары, а ты проверишь и нажмёшь «Сохранить».

Звонки:
/add_bell 1 08:30 09:15 09:20 10:05
  (номер пары, начало и конец 1-й половины, начало и конец 2-й)
/bells — показать звонки
/del_bell 1 — удалить звонок пары

Расписание (можно несколько строк в одном сообщении):
/add_lesson ТС 9-26-3; пн; 1; Математика; 214; Иванова И.И.; каждая
  (группа; день; пара; предмет; кабинет; преподаватель; неделя)
  неделя: каждая / числитель / знаменатель
  кабинет и преподавателя можно пропустить или поставить -
/schedule ТС 9-26-3 — расписание группы с номерами записей
/del_lesson 5 — удалить запись №5

/stats — сколько студентов зарегистрировано
/week — числитель или знаменатель сейчас"""


@router.message(Command("admin"), IsAdmin)
async def cmd_admin(message: Message):
    await message.answer(ADMIN_HELP)


@router.message(Command("add_bell"), IsAdmin)
async def cmd_add_bell(message: Message, command: CommandObject):
    args = (command.args or "").split()
    if len(args) != 5:
        await message.answer("Формат: /add_bell 1 08:30 09:15 09:20 10:05")
        return
    try:
        pair_num = int(args[0])
        times = [logic.parse_time(t) for t in args[1:]]
    except ValueError:
        await message.answer("Не понял. Номер пары — число, время — в формате ЧЧ:ММ.")
        return
    if not (times[0] < times[1] <= times[2] < times[3]):
        await message.answer("Время должно идти по порядку: начало1 < конец1 ≤ начало2 < конец2.")
        return
    db.set_bell(pair_num, *(t.strftime("%H:%M") for t in times))
    await message.answer(f"✅ Звонки для {pair_num} пары сохранены.")


@router.message(Command("bells"), IsAdmin)
async def cmd_bells(message: Message):
    rows = db.get_bells()
    if not rows:
        await message.answer("Звонки ещё не заданы. Добавь: /add_bell 1 08:30 09:15 09:20 10:05")
        return
    lines = ["🔔 Расписание звонков:"]
    for r in rows:
        lines.append(f"{r['pair_num']} пара: {r['start1']}–{r['end1']} | {r['start2']}–{r['end2']}")
    await message.answer("\n".join(lines))


@router.message(Command("del_bell"), IsAdmin)
async def cmd_del_bell(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Формат: /del_bell 1")
        return
    ok = db.delete_bell(int(arg))
    await message.answer("✅ Удалено." if ok else "Такой пары в звонках нет.")


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
        reply.append("✅ Добавлено:\n" + "\n".join(added))
    if errors:
        reply.append("⚠️ Ошибки:\n" + "\n".join(errors))
    await message.answer("\n\n".join(reply))


@router.message(Command("schedule"), IsAdmin)
async def cmd_schedule(message: Message, command: CommandObject):
    group = logic.normalize_group(command.args or "")
    if not group:
        await message.answer("Формат: /schedule ТС 9-26-3")
        return
    lessons = db.get_group_lessons(group)
    if not lessons:
        await message.answer(f"У группы {group} пока нет пар.")
        return
    lines = [f"Расписание {group}:"]
    current_day = None
    for l in lessons:
        if l["weekday"] != current_day:
            current_day = l["weekday"]
            lines.append(f"\n{logic.WEEKDAY_NAMES[current_day]}")
        week = "" if l["week_type"] == 0 else f" [{logic.WEEK_NAMES[l['week_type']]}]"
        lines.append(f"  #{l['id']} · {l['pair_num']} пара — {logic.lesson_title(l)}{week}")
    await message.answer("\n".join(lines))


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

    status = await message.answer("⏳ Распознаю расписание, это может занять до минуты...")
    image = (await bot.download(file_id)).read()
    group_hint = logic.normalize_group(message.caption) if message.caption else None

    try:
        raw = await ai_parser.recognize_schedule(image, mime, group_hint)
    except ai_parser.AIError as error:
        await status.edit_text(f"❌ {error}")
        return

    lessons, bells, problems = logic.lessons_from_ai(raw, group_hint)
    if not lessons and not bells:
        text = "🤷 ИИ не нашёл на картинке ни одной пары."
        if problems:
            text += "\n\nЧто не получилось разобрать:\n" + "\n".join(f"• {p}" for p in problems)
        await status.edit_text(text)
        return

    # Запоминаем результат до нажатия кнопки
    await state.update_data(ai_lessons=lessons, ai_bells=bells)
    await status.delete()
    await send_long(message, logic.ai_preview_text(lessons, bells, problems),
                    reply_markup=confirm_keyboard())


@router.callback_query(F.data.startswith("ai:"), IsAdmin)
async def ai_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    lessons, bells = data.get("ai_lessons"), data.get("ai_bells") or []
    await callback.message.edit_reply_markup(reply_markup=None)  # убираем кнопки

    if lessons is None:
        await callback.answer("Это распознавание уже неактуально. Пришли фото заново.", show_alert=True)
        return
    await state.update_data(ai_lessons=None, ai_bells=None)

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
    for bell in bells:
        db.set_bell(*bell)

    check = "\n".join(f"/schedule {g}" for g in groups)
    await callback.message.answer(f"✅ Сохранено: пар — {len(lessons)}, звонков — {len(bells)}.\n"
                                  f"Проверить:\n{check}")
    await callback.answer()


# ---------- Кнопки меню ----------

@router.message(F.text == BTN_NOW)
async def btn_current_pair(message: Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if not user:
        await start_registration(message, state)
        return
    now, _, lessons, bells = today_data(user)
    await message.answer(logic.current_pair_text(now, lessons, bells), reply_markup=main_menu())


@router.message(F.text == BTN_TODAY)
async def btn_today(message: Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if not user:
        await start_registration(message, state)
        return
    now, week_type, lessons, bells = today_data(user)
    await message.answer(logic.today_schedule_text(now, lessons, bells, week_type),
                         reply_markup=main_menu())


@router.message(F.text == BTN_EDIT)
async def btn_edit(message: Message, state: FSMContext):
    await start_registration(message, state)


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
    await message.answer("Отлично! Теперь выбери свою группу из списка или напиши её.\n"
                         "Например: ТС 9-26-3",
                         reply_markup=groups_keyboard())


@router.message(Registration.group, F.text)
async def reg_group(message: Message, state: FSMContext):
    group = logic.normalize_group(message.text)
    known_groups = db.get_groups()
    if known_groups and group not in known_groups:
        await message.answer("Такой группы пока нет в расписании. Выбери из списка или проверь написание.",
                             reply_markup=groups_keyboard())
        return
    data = await state.get_data()
    db.save_user(message.from_user.id, data["fio"], group)
    await state.clear()
    await message.answer(f"Готово! ✅\n{data['fio']}, группа {group}.\n"
                         f"Пользуйся кнопками внизу 👇",
                         reply_markup=main_menu())


# ---------- Всё остальное ----------

@router.message()
async def fallback(message: Message, state: FSMContext):
    if not db.get_user(message.from_user.id):
        await start_registration(message, state)
        return
    await message.answer("Не понял 🙂 Пользуйся кнопками внизу.", reply_markup=main_menu())


async def main():
    logging.basicConfig(level=logging.INFO)
    if not config.BOT_TOKEN:
        print("Не задан BOT_TOKEN. На Railway добавь его во вкладке Variables.")
        return
    if not config.DEEPSEEK_API_KEY:
        print("Внимание: не задан DEEPSEEK_API_KEY — загрузка расписания по фото работать не будет.")
    db.init_db()
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот запущен. Остановить: Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
