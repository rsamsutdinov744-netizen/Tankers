"""Основной файл бота: авторизация, меню, админ-панель, владелец, звёзды."""
import asyncio
import logging
import html

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (Message, CallbackQuery, ChatMemberUpdated,
                           LabeledPrice, PreCheckoutQuery)

import config, db, logic
from common import (kb, show, one_line, fmt_msk, role_of, has_perm, need, uname, level_rank, can_edit,
                    status_text, status_icon, main_menu, notify, tell_owner, task_name, task_kb, finish_task,
                    check_deleted_once, finalize_due, scoring_cfg, Guard, ICON, grace_seconds)
from perms import (PERMS, DEFAULT_ADMIN_PERMS, STATUSES, LADDER, POS_NAMES, ADMIN_LEVELS, LEVEL_RANK,
                   WORK_CHARS, QUALITY_CHARS)
from tasks_filter import classify, visible_for, CODE_TO_POS
import materials, reminders, works, scoring

logging.basicConfig(level=logging.INFO)
router = Router()

LABELS = {"1": "🎨 Художникам", "2": "✍️ Сценаристам", "3": "🛠️ Помощникам", "4": "🎬 Аниматорам", "0": "👥 Всем"}
GROUP = F.chat.type.in_({"group", "supergroup"})
PRIVATE = F.chat.type == "private"
ANON_ADMIN_ID = 1087968824
PAGE = 8
BACK_ADMIN = ("🔙 В админ-панель", "admin")


class S(StatesGroup):
    password = State()
    send_task = State()
    add_announcement = State()
    grace_input = State()
    pwd_input = State()
    pwd_role = State()
    pwd_perms = State()
    pwd_note = State()
    stars_amount = State()


def describe_dest():
    d = db.dest()
    if not d:
        return "не выбрано"
    s = db.chat_title(d[0])
    return s + (f", тема {d[1]}" if d[1] else "")


def seed_passwords():
    """Первичная инициализация паролей из .env, если таблица пуста."""
    if db.passwords_list():
        return
    db.password_add(config.OWNER_PASSWORD, "owner", set(PERMS), "Владелец (из .env)", config.OWNER_ID or 0)
    db.password_add(config.ADMIN_PASSWORD, "admin", DEFAULT_ADMIN_PERMS, "Админ (из .env)", config.OWNER_ID or 0)
    db.password_add(config.USER_PASSWORD, "user", set(), "Пользователь (из .env)", config.OWNER_ID or 0)


# ====================== вход ======================
@router.message(CommandStart(), PRIVATE)
@router.message(Command("menu"), PRIVATE)
async def start(m: Message, state: FSMContext):
    await state.clear()
    if role_of(m.from_user.id):
        await m.answer("Главное меню:", reply_markup=main_menu(m.from_user.id))
    else:
        await state.set_state(S.password)
        await m.answer("🔐 Введите пароль:")


@router.message(S.password)
async def check_password(m: Message, state: FSMContext):
    p = (m.text or "").strip()
    role = None
    perms = set()
    rec = db.password_get(p)
    if rec:
        role = rec["role"]
        perms = set(filter(None, (rec["perms"] or "").split(",")))
    elif p == config.OWNER_PASSWORD:
        role = "owner"
        perms = set(PERMS)
    elif p == config.ADMIN_PASSWORD:
        role = "admin"
        perms = DEFAULT_ADMIN_PERMS
    elif p == config.USER_PASSWORD:
        role = "user"
    try:
        await m.delete()
    except Exception:
        pass
    if not role:
        await m.answer("❌ Неверный пароль, попробуйте ещё раз:")
        return
    uid = m.from_user.id
    cur = db.get_user(uid)
    db.set_role(uid, role)
    if perms:
        db.set_perms(uid, perms)
    elif role == "admin" and (cur is None or cur["perms"] is None):
        db.set_perms(uid, DEFAULT_ADMIN_PERMS)
    if role in ("admin", "owner") and (not cur or not cur["admin_level"]):
        db.set_level(uid, "senior" if role == "owner" else "junior")
    await state.clear()
    await m.answer(f"✅ Вы вошли как: {role}", reply_markup=main_menu(uid))


@router.callback_query(F.data == "menu")
async def menu_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "Главное меню:", main_menu(c.from_user.id))


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


# ====================== профиль ======================
@router.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    uid = c.from_user.id
    u = db.get_user(uid)
    perms = set(PERMS) if u["role"] == "owner" else db.perms_of(uid)
    cfg = scoring_cfg()
    pts = u["points"] or 0
    st = db.user_stats(uid)
    wc = logic.work_characteristic(st)
    qc = logic.quality_characteristic(st.get("avg_rating"))
    lines = [f"👤 {uname(u)}", f"ID: {u['id']}", f"Роль: {u['role']}",
             f"Статус: {status_text(u)}",
             f"Должность: {POS_NAMES.get(u['position'], 'не выбрана')}",
             f"Работа: {WORK_CHARS[wc]} · Качество: {QUALITY_CHARS[qc]}"]
    if u["admin_level"]:
        lines.append(f"Уровень админа: {ADMIN_LEVELS[u['admin_level']]}")
    lines.append(f"\n⭐ Баллы: {logic.fmt_num(pts)}")
    nt = logic.next_tier(u["status"] or "newbie", cfg["ladder"], LADDER) if u["role"] != "owner" else None
    if nt:
        nxt, lo, hi = nt
        lines.append(f"До {STATUSES[nxt]}: {logic.bar(pts, lo, hi)} {logic.fmt_num(pts)}/{logic.fmt_num(hi)}")
    if st["cnt"]:
        lines.append(f"\n📤 Работ сдано: {st['cnt']}, оценено: {st['reviewed']}\n"
                     f"✅ {st['r_done']} · 🟡 {st['r_part']} · ❌ {st['r_wrong']} · 🚫 {st['r_bad']}\n"
                     f"⭐ Средняя оценка: {logic.fmt_num(st['avg_rating'])}/10 · 🏆 Лучших: {st['best_cnt']}")
    if perms:
        lines.append("\nРазрешения:\n" + "\n".join("✅ " + PERMS[k] for k in PERMS if k in perms))
    await show(c, "\n".join(lines), kb([("🧾 Мои формы", "myfrm"), ("🚪 Выйти из аккаунта", "logout")], menu=True))


@router.callback_query(F.data == "logout")
async def logout(c: CallbackQuery, state: FSMContext):
    await state.clear()
    db.clear_role(c.from_user.id)
    await c.message.answer("🚪 Вы вышли из аккаунта. Чтобы войти снова, отправьте /start")
    await c.answer()


# ====================== должность ======================
@router.callback_query(F.data == "position")
async def position(c: CallbackQuery):
    await show(c, "Выберите должность:", kb([(v, f"pos:{k}") for k, v in POS_NAMES.items()], menu=True))


@router.callback_query(F.data.startswith("pos:"))
async def set_pos(c: CallbackQuery):
    pos = c.data.split(":")[1]
    db.set_position(c.from_user.id, pos)
    await show(c, f"Должность: {POS_NAMES[pos]}", kb([], menu=True))


# ====================== задания (просмотр) ======================
@router.callback_query(F.data == "tasks")
async def tasks(c: CallbackQuery):
    u = db.get_user(c.from_user.id)
    pos = u["position"] if u else None
    rows = [r for r in db.open_tasks() if visible_for(r["pos"], pos)][:10]
    hint = "" if pos else "ℹ️ Выберите должность, чтобы видеть свои задания.\n\n"
    if not rows:
        text = hint + "📭 Заданий нет"
    else:
        items = []
        for r in rows:
            s = f"• {html.escape(r['text'][:500])}"
            if r["status"] == "done" and r["done_at"]:
                grace = grace_seconds()
                s += f"\n✅ Выполнено. Работы принимаются до {fmt_msk(r['done_at'] + grace)} МСК"
            items.append(s)
        text = hint + "📋 Задания:\n\n" + "\n\n".join(items)
    buttons = [("📤 Сдать работу: " + task_name(r), f"sub:{r['id']}") for r in rows]
    await c.message.answer(text, parse_mode="HTML", reply_markup=kb(buttons, menu=True))
    await c.answer()


# ====================== команды и события в группе ======================
@router.message(Command("id"))
async def show_id(m: Message):
    await m.answer(f"chat_id: {m.chat.id}\nthread_id: {m.message_thread_id}")


@router.message(Command("here"), GROUP)
async def here(m: Message):
    if not m.from_user or not has_perm(m.from_user.id, "place_set"):
        return await m.reply("⛔ Нет разрешения (нужно «Выбирать место публикации»).")
    thread = m.message_thread_id if m.is_topic_message else 0
    db.add_chat(m.chat.id, m.chat.title or str(m.chat.id))
    db.set_setting("dest_chat", m.chat.id)
    db.set_setting("dest_thread", thread or 0)
    await m.reply("✅ Задания и напоминания будут публиковаться здесь" + (" (в этой теме)." if thread else "."))


@router.my_chat_member()
async def on_bot_member(e: ChatMemberUpdated):
    if e.chat.type not in ("group", "supergroup"):
        return
    if e.new_chat_member.status in ("member", "administrator"):
        db.add_chat(e.chat.id, e.chat.title or str(e.chat.id))
    elif e.new_chat_member.status in ("left", "kicked"):
        db.remove_chat(e.chat.id)


@router.chat_member()
async def on_member(e: ChatMemberUpdated, bot: Bot):
    d = db.dest()
    user = e.new_chat_member.user
    if not d or e.chat.id != d[0] or user.is_bot:
        return
    new, old = e.new_chat_member.status, e.old_chat_member.status
    db.touch_user(user.id, user.full_name, user.username)
    if new == "kicked" and not db.is_banned(user.id):
        db.ban(user.id, "group")
        await tell_owner(bot, f"🚫 {user.full_name} (ID {user.id}) забанен в группе, поэтому забанен и в боте.")
    elif old == "kicked" and new != "kicked" and db.is_banned(user.id):
        if db.get_user(user.id)["ban_src"] == "group":
            db.unban(user.id)
            await tell_owner(bot, f"♻️ {user.full_name} (ID {user.id}) разбанен в группе, поэтому разбанен и в боте (права сброшены).")


@router.message(Command("done", "cancel"), GROUP)
async def close_by_reply(m: Message, bot: Bot):
    if not m.from_user or not has_perm(m.from_user.id, "tasks_close"):
        return await m.reply("⛔ Нет разрешения (нужно «Закрывать задания»).")
    if not m.reply_to_message:
        return await m.reply("Ответьте этой командой на сообщение с заданием.")
    t = db.task_by_msg(m.chat.id, m.reply_to_message.message_id)
    if not t or t["status"] != "active":
        return await m.reply("Не нашёл активное задание в этом сообщении (бот знает только сообщения, написанные после его запуска).")
    await finish_task(bot, t, "done" if m.text.startswith("/done") else "invalid")


@router.message(GROUP)
@router.edited_message(GROUP)
async def collect_task(m: Message, bot: Bot):
    db.add_chat(m.chat.id, m.chat.title or str(m.chat.id))
    d = db.dest()
    if not d or m.chat.id != d[0]:
        return
    if m.from_user and m.from_user.is_bot and m.from_user.id != ANON_ADMIN_ID:
        return
    if m.from_user and db.is_banned(m.from_user.id):
        return
    text = m.text or m.caption
    if not text or text.startswith("/"):
        return
    aid, pos = classify(text)
    if not aid and d[1] and m.message_thread_id != d[1]:
        return
    row = db.task_by_msg(m.chat.id, m.message_id)
    if row:
        if row["status"] == "active" and row["src"] != "bot":
            db.update_task(row["id"], text, aid, pos)
        return
    tid = db.add_task(text, aid, pos, m.chat.id, m.message_id, "group")
    if tid:
        logging.info("Задание сохранено: %s", text[:50])
        if aid:
            await notify(bot, pos, "📋 Новое задание:\n\n" + text)


@router.callback_query(F.data.startswith("fin:"))
async def fin(c: CallbackQuery, bot: Bot):
    if not has_perm(c.from_user.id, "tasks_close"):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    _, tid, status = c.data.split(":")
    t = db.task(int(tid))
    if not t or t["status"] != "active":
        return await c.answer("Уже закрыто")
    await finish_task(bot, t, status)
    await c.answer("Готово")
    if c.message.chat.type == "private":
        await c.message.answer(f"Задание {task_name(t)}: " + ("✅ выполнено" if status == "done" else "❌ не действительно"))


# ====================== объявления ======================
@router.callback_query(F.data == "announcements")
async def announcements(c: CallbackQuery):
    rows = db.all_rows("announcements")
    text = "📭 Пока пусто" if not rows else "📢 Объявления:\n\n" + "\n\n".join(
        f"• {html.escape(r['text'])}" for r in rows)
    await c.message.answer(text, parse_mode="HTML", reply_markup=kb([], menu=True))
    await c.answer()


@router.callback_query(F.data == "a:ann")
async def a_ann(c: CallbackQuery, state: FSMContext):
    if not await need(c, "announce_add"):
        return
    await state.set_state(S.add_announcement)
    await c.message.answer("Отправьте текст объявления:")
    await c.answer()


@router.message(S.add_announcement)
async def save_ann(m: Message, state: FSMContext):
    await state.clear()
    if not has_perm(m.from_user.id, "announce_add"):
        return await m.answer("⛔ Нет разрешения")
    db.add("announcements", m.text or "")
    await m.answer("✅ Объявление добавлено", reply_markup=kb([], back=BACK_ADMIN))


@router.callback_query(F.data == "d:announcements")
async def delete_list(c: CallbackQuery):
    if not await need(c, "announce_add"):
        return
    rows = db.all_rows("announcements", 20)
    if not rows:
        return await show(c, "📭 Пока пусто", kb([], back=BACK_ADMIN))
    await show(c, "Что удалить?", kb([(r["text"][:40], f"x:announcements:{r['id']}") for r in rows], back=BACK_ADMIN))


@router.callback_query(F.data.startswith("x:"))
async def do_delete(c: CallbackQuery):
    if not await need(c, "announce_add"):
        return
    _, table, rid = c.data.split(":")
    if table == "announcements":
        db.delete(table, int(rid))
    await show(c, "🗑 Удалено", kb([], back=BACK_ADMIN))


# ====================== админ-панель ======================
def admin_rows(uid):
    P = lambda k: has_perm(uid, k)
    rows = []
    if P("tasks_add"):
        rows.append(("📤 Отправить задание", "send"))
    if P("tasks_close"):
        rows += [("🗂 Активные задания", "tl:act"), ("🗄 Архив заданий", "tl:arch"),
                 ("🔄 Проверить удалённые", "chk")]
    if P("works_review"):
        rows.append(("📥 Присланные работы", "wk"))
    if P("reminders_manage"):
        rows.append(("⏰ Напоминания", "rem"))
    if P("place_set"):
        rows.append(("📍 Место публикации", "place"))
    if P("materials_edit"):
        rows.append(("📚 Материалы и кнопки", "mm"))
    if P("forms_edit"):
        rows.append(("🧾 Формы выполнения", "frm"))
    if P("announce_add"):
        rows += [("📢 Добавить объявление", "a:ann"), ("🗑 Удалить объявление", "d:announcements")]
    if P("scoring_edit"):
        rows += [("🎯 Баллы и уровни", "sc"), ("💬 Фразы поддержки", "ph"),
                 ("⏱ Время удаления заданий", "grace")]
    if P("ranks_edit"):
        rows.append(("🎓 Кураторы по должностям", "cur"))
    if P("stats_view"):
        rows.append(("📊 Статистика", "a:stats"))
    if P("users_view"):
        rows.append(("👥 Участники", "ul:0"))
    if P("users_ban"):
        rows.append(("🚫 Забаненные", "bl"))
    return rows


@router.callback_query(F.data == "admin")
async def admin_panel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = admin_rows(c.from_user.id)
    if not rows:
        return await c.answer("⛔ У вас нет разрешений для админ-панели", show_alert=True)
    await show(c, "⚙️ Админ-панель:", kb(rows, back=("🔙 Выйти из панели", "menu")))


@router.callback_query(F.data == "a:stats")
async def stats(c: CallbackQuery):
    if not await need(c, "stats_view"):
        return
    best = db.top_users(10, worst=False)
    worst = db.top_users(10, worst=True)
    def fmt(rows):
        out = []
        for u in rows:
            st = db.user_stats(u["id"])
            wc = logic.work_characteristic(st)
            qc = logic.quality_characteristic(st.get("avg_rating"))
            out.append(f"{status_icon(u)} {uname(u)} — {logic.fmt_num(u['points'] or 0)} б. "
                       f"({WORK_CHARS[wc]}, {QUALITY_CHARS[qc]})")
        return "\n".join(out) or "—"
    await show(c,
        f"📊 Участников: {len(db.members())} (забанено: {len(db.banned_users())})\n"
        f"Материалов: {db.count('materials')} · Объявлений: {db.count('announcements')}\n"
        f"Заданий активных: {db.count_active()} · Работ: {db.count('submissions')}\n"
        f"Напоминаний: {db.count('reminders')}\n"
        f"Место публикации: {describe_dest()}\n\n"
        f"🏆 <b>Топ-10 лучших:</b>\n{fmt(best)}\n\n"
        f"📉 <b>Топ-10 худших:</b>\n{fmt(worst)}", kb([], back=BACK_ADMIN))


@router.callback_query(F.data == "chk")
async def check_now(c: CallbackQuery, bot: Bot):
    if not await need(c, "tasks_close"):
        return
    await c.answer("Проверяю…")
    n = await check_deleted_once(bot)
    await c.message.answer(f"🔄 Проверка завершена. Найдено удалённых заданий: {n}", reply_markup=kb([], back=BACK_ADMIN))


@router.callback_query(F.data == "send")
async def send_choose(c: CallbackQuery):
    if not await need(c, "tasks_add"):
        return
    await show(c, "Для кого задание?", kb([(v, f"snd:{k}") for k, v in LABELS.items()], back=BACK_ADMIN))


@router.callback_query(F.data.startswith("snd:"))
async def send_ask(c: CallbackQuery, state: FSMContext):
    if not await need(c, "tasks_add"):
        return
    code = c.data.split(":")[1]
    await state.set_state(S.send_task)
    await state.update_data(code=code)
    await c.message.answer(f"{LABELS[code]}\nОтправьте текст задания (метку aid добавлю сам):")
    await c.answer()


@router.message(S.send_task)
async def send_do(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    if not has_perm(m.from_user.id, "tasks_add"):
        return await m.answer("⛔ Нет разрешения")
    code = data["code"]
    text = (m.text or m.caption or "").strip()
    if not text:
        return await m.answer("Нужен текст задания.")
    pos = CODE_TO_POS[code]
    aid = f"aid{db.next_aid_number()}_{code}"
    body = f"{aid} — {LABELS[code]}\n\n{text}"
    tid = db.add_task(body, aid, pos, src="bot")
    report = []
    d = db.dest()
    if d:
        try:
            sent = await bot.send_message(d[0], body, message_thread_id=d[1] or None, reply_markup=task_kb(tid))
            db.set_msg(tid, d[0], sent.message_id)
            report.append("✅ опубликовано в группе")
        except Exception as e:
            report.append(f"⚠️ не удалось опубликовать: {e}")
    else:
        report.append("⚠️ место публикации не выбрано")
    n = await notify(bot, pos, "📋 Новое задание:\n\n" + body, exclude=m.from_user.id)
    report.append(f"📨 отправлено в личку: {n}")
    await m.answer(f"Задание {aid} создано.\n" + "\n".join(report), reply_markup=main_menu(m.from_user.id))


@router.callback_query(F.data.in_({"tl:act", "tl:arch"}))
async def task_lists(c: CallbackQuery):
    if not await need(c, "tasks_close"):
        return
    if c.data == "tl:act":
        rows = db.list_tasks(True)
        if not rows:
            return await show(c, "📭 Активных заданий нет", kb([], back=BACK_ADMIN))
        await show(c, "🗂 Активные задания:", kb([(("✅ " if r["status"] == "done" else "") + one_line(r["text"]), f"t:{r['id']}") for r in rows], back=BACK_ADMIN))
    else:
        rows = db.list_tasks(False)
        if not rows:
            return await show(c, "📭 Архив пуст", kb([], back=BACK_ADMIN))
        await show(c, "🗄 Архив:\n\n" + "\n".join(f"{ICON.get(r['status'], '•')} {one_line(r['text'], 70)}" for r in rows), kb([], back=BACK_ADMIN))


@router.callback_query(F.data.startswith("t:"))
async def task_open(c: CallbackQuery):
    if not await need(c, "tasks_close"):
        return
    t = db.task(int(c.data.split(":")[1]))
    if not t:
        return await c.answer("Не найдено")
    rows = [[("✅ Выполнено", f"fin:{t['id']}:done"), ("❌ Не действительно", f"fin:{t['id']}:invalid")]] if t["status"] == "active" else []
    rows.append((f"📥 Работы ({len(db.submissions_for_task(t['id']))})", f"ws:{t['id']}"))
    await c.message.answer(t["text"], reply_markup=kb(rows))
    await c.answer()


@router.callback_query(F.data == "place")
async def place(c: CallbackQuery):
    if not await need(c, "place_set"):
        return
    rows = db.chats()
    text = (f"📍 Сейчас: {describe_dest()}\n\nВыберите группу из списка.\n"
            "Для конкретной темы: напишите /here в нужной теме группы.")
    await show(c, text, kb([("📍 " + (r["title"] or str(r["id"])), f"pl:{r['id']}") for r in rows], back=BACK_ADMIN))


@router.callback_query(F.data.startswith("pl:"))
async def place_set(c: CallbackQuery):
    if not await need(c, "place_set"):
        return
    cid = int(c.data.split(":", 1)[1])
    db.set_setting("dest_chat", cid)
    db.set_setting("dest_thread", 0)
    await show(c, f"✅ Место публикации: {db.chat_title(cid)}", kb([], back=BACK_ADMIN))


# ====================== время удаления ======================
@router.callback_query(F.data == "grace")
async def grace_menu(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    cur = db.get_setting("grace_hours", "24")
    await state.set_state(S.grace_input)
    await c.message.answer(f"⏱ Сейчас задания удаляются через **{cur} ч** после «выполнено».\n"
                           "Введите новое число часов (можно дробное, например 0.5):", parse_mode="Markdown")
    await c.answer()


@router.message(S.grace_input)
async def grace_save(m: Message, state: FSMContext):
    await state.clear()
    if not has_perm(m.from_user.id, "scoring_edit"):
        return await m.answer("⛔ Нет разрешения")
    try:
        h = float(m.text.replace(",", "."))
        if h < 0 or h > 720:
            raise ValueError
    except ValueError:
        return await m.answer("Нужно число от 0 до 720 (часов).")
    db.set_setting("grace_hours", str(h))
    await m.answer(f"✅ Теперь задания удаляются через {h} ч после «выполнено».")


# ====================== пароли (владелец) ======================
@router.callback_query(F.data == "pwd")
async def pwd_list(c: CallbackQuery, state: FSMContext):
    if not await need(c, "passwords_edit"):
        return
    await state.clear()
    rows = []
    for p in db.passwords_list():
        rows.append((f"🔑 {p['note'] or p['role'] or '—'}", f"pwd:{p['id']}"))
    rows.append(("➕ Добавить пароль", "pwdnew"))
    await show(c, "🔑 Управление паролями:", kb(rows, back=("🔙 В панель владельца", "owner")))


@router.callback_query(F.data.startswith("pwd:"))
async def pwd_card(c: CallbackQuery):
    if not await need(c, "passwords_edit"):
        return
    pid = int(c.data.split(":")[1])
    p = next((x for x in db.passwords_list() if x["id"] == pid), None)
    if not p:
        return await c.answer("Не найдено")
    perms = p["perms"] or ""
    text = (f"🔑 Пароль #{pid}\nПароль: `{p['password']}`\nРоль: {p['role'] or '—'}\n"
            f"Заметка: {p['note'] or '—'}\nРазрешения: {perms or '—'}")
    await show(c, text, kb([("🗑 Удалить", f"pwddel:{pid}")], back=("⬅️ К паролям", "pwd")))


@router.callback_query(F.data.startswith("pwddel:"))
async def pwd_del_ask(c: CallbackQuery):
    if not await need(c, "passwords_edit"):
        return
    pid = int(c.data.split(":")[1])
    await show(c, "Удалить пароль?", kb([("✅ Да", f"pwddel!:{pid}")], back=("❌ Отмена", f"pwd:{pid}")))


@router.callback_query(F.data.startswith("pwddel!:"))
async def pwd_del(c: CallbackQuery):
    if not await need(c, "passwords_edit"):
        return
    db.password_delete(int(c.data.split(":")[1]))
    await show(c, "🗑 Удалено", kb([], back=("⬅️ К паролям", "pwd")))


@router.callback_query(F.data == "pwdnew")
async def pwd_new(c: CallbackQuery, state: FSMContext):
    if not await need(c, "passwords_edit"):
        return
    await state.set_state(S.pwd_input)
    await c.message.answer("Введите новый пароль (текст):")
    await c.answer()


@router.message(S.pwd_input)
async def pwd_input(m: Message, state: FSMContext):
    if not has_perm(m.from_user.id, "passwords_edit"):
        await state.clear()
        return await m.answer("⛔ Нет разрешения")
    await state.update_data(password=(m.text or "").strip())
    await state.set_state(S.pwd_role)
    await m.answer("Укажите роль: user / admin / owner (или «-» для без роли):")


@router.message(S.pwd_role)
async def pwd_role(m: Message, state: FSMContext):
    role = (m.text or "").strip().lower()
    if role not in ("user", "admin", "owner", "-"):
        return await m.answer("Нужно: user / admin / owner / -")
    await state.update_data(role=None if role == "-" else role)
    await state.set_state(S.pwd_perms)
    await m.answer("Перечислите разрешения через запятую (или «-» для пустого):\n" + "\n".join(f"{k} — {v}" for k, v in PERMS.items()))


@router.message(S.pwd_perms)
async def pwd_perms(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    perms = set()
    if raw != "-":
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part in PERMS:
                perms.add(part)
    await state.update_data(perms=perms)
    await state.set_state(S.pwd_note)
    await m.answer("Введите заметку (например «Художник-стажёр»):")


@router.message(S.pwd_note)
async def pwd_note(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    if not has_perm(m.from_user.id, "passwords_edit"):
        return await m.answer("⛔ Нет разрешения")
    pid = db.password_add(d["password"], d.get("role"), d.get("perms"), (m.text or "").strip()[:60], m.from_user.id)
    if pid:
        await m.answer(f"✅ Пароль добавлен (ID {pid}).", reply_markup=kb([("🔑 К паролям", "pwd")]))
    else:
        await m.answer("❌ Такой пароль уже существует.")


# ====================== кураторы ======================
def curator_name(pos):
    cid = db.get_setting(f"curator:{pos}")
    u = db.get_user(int(cid)) if cid else None
    return uname(u) if u else "не назначен"


@router.callback_query(F.data == "cur")
async def cur_list(c: CallbackQuery):
    if not await need(c, "ranks_edit"):
        return
    await show(c, "🎓 Кураторы: им вместе с владельцем приходят работы по их должности.",
               kb([(f"{v}: {curator_name(k)}", f"cu:{k}:0") for k, v in POS_NAMES.items()], back=BACK_ADMIN))


@router.callback_query(F.data.startswith("cu:"))
async def cur_pick(c: CallbackQuery):
    if not await need(c, "ranks_edit"):
        return
    _, pos, page = c.data.split(":")
    page = int(page)
    rows = [u for u in db.members() if u["role"] and not u["banned"]]
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    btns = [(f"{status_icon(u)} {uname(u)}", f"cus:{pos}:{u['id']}") for u in rows[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"cu:{pos}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("➡️", f"cu:{pos}:{page + 1}"))
    btns += [nav, ("❌ Снять куратора", f"cus:{pos}:0")]
    await show(c, f"Куратор {POS_NAMES[pos]}: {curator_name(pos)}\nВыберите человека:", kb(btns, back=("🔙 К должностям", "cur")))


@router.callback_query(F.data.startswith("cus:"))
async def cur_set(c: CallbackQuery, bot: Bot):
    if not await need(c, "ranks_edit"):
        return
    _, pos, uid = c.data.split(":")
    uid = int(uid)
    if uid == 0:
        db.del_setting(f"curator:{pos}")
        text = f"Куратор {POS_NAMES[pos]} снят."
    else:
        db.set_setting(f"curator:{pos}", uid)
        text = f"✅ Куратор {POS_NAMES[pos]}: {curator_name(pos)}"
        try:
            await bot.send_message(uid, f"🎓 Вас назначили куратором {POS_NAMES[pos]}.")
        except Exception:
            pass
    await show(c, text, kb([], back=("🔙 К должностям", "cur")))


# ====================== участники ======================
def list_screen(viewer, page, banned_only=False):
    rows = db.banned_users() if banned_only else db.members()
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    btns = [(f"{'🚫' if u['banned'] else status_icon(u)} {uname(u)}", f"u:{u['id']}") for u in rows[page * PAGE:(page + 1) * PAGE]]
    if not banned_only:
        nav = []
        if page > 0:
            nav.append(("⬅️", f"ul:{page - 1}"))
        nav.append((f"{page + 1}/{pages}", "noop"))
        if page < pages - 1:
            nav.append(("➡️", f"ul:{page + 1}"))
        btns.append(nav)
    title = f"🚫 Забаненные ({len(rows)}):" if banned_only else f"👥 Участники ({len(rows)}):"
    return title, kb(btns, back=("🔙 Назад", "owner" if role_of(viewer) == "owner" else "admin"))


def card(viewer, uid):
    u = db.get_user(uid)
    if not u:
        return "Пользователь не найден", kb([], back=("⬅️ К списку", "ul:0"))
    perms = set(PERMS) if u["role"] == "owner" else db.perms_of(uid)
    st = db.user_stats(uid)
    wc = logic.work_characteristic(st)
    qc = logic.quality_characteristic(st.get("avg_rating"))
    lines = [f"👤 {uname(u)}" + (f" (@{u['username']})" if u["username"] else ""),
             f"ID: {u['id']}", f"Роль: {u['role'] or 'не вошёл'}", f"Статус: {status_text(u)}",
             f"Должность: {POS_NAMES.get(u['position'], '—')}", f"⭐ Баллы: {logic.fmt_num(u['points'] or 0)}",
             f"Работа: {WORK_CHARS[wc]} · Качество: {QUALITY_CHARS[qc]}"]
    if u["admin_level"]:
        lines.append(f"Уровень админа: {ADMIN_LEVELS[u['admin_level']]}")
    cfg = scoring_cfg()
    nt = logic.next_tier(u["status"] or "newbie", cfg["ladder"], LADDER) if u["role"] != "owner" else None
    if nt:
        nxt, lo, hi = nt
        lines.append(f"До {STATUSES[nxt]}: {logic.bar(u['points'] or 0, lo, hi)} {logic.fmt_num(u['points'] or 0)}/{logic.fmt_num(hi)}")
    cur = [POS_NAMES[p] for p in POS_NAMES if db.get_setting(f"curator:{p}") == str(uid)]
    if cur:
        lines.append("🎓 Куратор: " + ", ".join(cur))
    if st["cnt"]:
        lines.append(f"📤 Работ: {st['cnt']}, оценено: {st['reviewed']}, средняя: {logic.fmt_num(st['avg_rating'])}/10")
    if u["banned"]:
        lines.append("🚫 ЗАБАНЕН" + (" (из-за бана в группе)" if u["ban_src"] == "group" else ""))
    lines.append("\nРазрешения:")
    lines += [("✅ " if k in perms else "❌ ") + v for k, v in PERMS.items()]
    rows = []
    if can_edit(viewer, u):
        if has_perm(viewer, "users_status"):
            rows.append(("🏷 Статус", f"us:{uid}"))
        if has_perm(viewer, "points_edit"):
            rows.append(("⭐ Баллы", f"pts:{uid}"))
        if has_perm(viewer, "perms_edit") and viewer != uid:
            rows.append(("🔐 Разрешения", f"pe:{uid}"))
        if has_perm(viewer, "ranks_edit") and viewer != uid:
            rows.append(("🎚 Уровень админа", f"lv:{uid}"))
        if has_perm(viewer, "users_ban") and viewer != uid:
            rows.append(("♻️ Разбанить", f"unban:{uid}") if u["banned"] else ("🚫 Забанить навсегда", f"ban:{uid}"))
        if role_of(viewer) == "owner" and viewer != uid:
            rows.append(("🛡 Снять админа", f"rma:{uid}") if u["role"] == "admin" else ("🛡 Сделать админом", f"mka:{uid}"))
    return "\n".join(lines), kb(rows, back=("⬅️ К списку", "ul:0"))


@router.callback_query(F.data == "owner")
async def owner_panel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только для владельца", show_alert=True)
    await show(c, "👑 Панель владельца:", kb([
        ("👥 Участники", "ul:0"), ("🚫 Забаненные", "bl"),
        ("🔑 Пароли", "pwd"), ("💫 Оплата звёздами", "stars")],
        back=("🔙 Выйти из панели", "menu")))


@router.callback_query(F.data.startswith("ul:"))
async def users_list(c: CallbackQuery):
    if not await need(c, "users_view"):
        return
    text, markup = list_screen(c.from_user.id, int(c.data.split(":")[1]))
    await show(c, text, markup)


@router.callback_query(F.data == "bl")
async def banned_list(c: CallbackQuery):
    if not await need(c, "users_ban"):
        return
    text, markup = list_screen(c.from_user.id, 0, banned_only=True)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("u:"))
async def user_card(c: CallbackQuery):
    if not await need(c, "users_view"):
        return
    text, markup = card(c.from_user.id, int(c.data.split(":")[1]))
    await show(c, text, markup)


@router.callback_query(F.data.startswith("us:"))
async def status_menu(c: CallbackQuery):
    if not await need(c, "users_status"):
        return
    uid = int(c.data.split(":")[1])
    keys = list(STATUSES) if role_of(c.from_user.id) == "owner" else [k for k in STATUSES if k != "owner"]
    await show(c, "Выберите статус:", kb([(STATUSES[k], f"ss:{uid}:{k}") for k in keys], back=("⬅️ Назад", f"u:{uid}")))


@router.callback_query(F.data.startswith("ss:"))
async def status_set(c: CallbackQuery):
    if not await need(c, "users_status"):
        return
    _, uid, key = c.data.split(":")
    uid = int(uid)
    u = db.get_user(uid)
    if not u or not can_edit(c.from_user.id, u) or key not in STATUSES:
        return await c.answer("⛔ Нельзя", show_alert=True)
    if key == "owner" and role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Статус «Владелец» даёт только владелец", show_alert=True)
    db.set_user_status(uid, key)
    text, markup = card(c.from_user.id, uid)
    await show(c, text, markup)


def perms_screen(viewer, uid):
    cur = db.perms_of(uid)
    viewer_owner = role_of(viewer) == "owner"
    rows = []
    for k, label in PERMS.items():
        locked = not (viewer_owner or has_perm(viewer, k))
        rows.append((("✅ " if k in cur else "❌ ") + label + (" 🔒" if locked else ""), f"pt:{uid}:{k}"))
    u = db.get_user(uid)
    return (f"🔐 Разрешения: {uname(u)}\nНажмите, чтобы включить или выключить.\n"
            "🔒 — нельзя менять: этого разрешения нет у вас.", kb(rows, back=("⬅️ Назад", f"u:{uid}")))


@router.callback_query(F.data.startswith("pe:"))
async def perms_menu(c: CallbackQuery):
    if not await need(c, "perms_edit"):
        return
    uid = int(c.data.split(":")[1])
    u = db.get_user(uid)
    if not u or not can_edit(c.from_user.id, u) or uid == c.from_user.id:
        return await c.answer("⛔ Нельзя", show_alert=True)
    text, markup = perms_screen(c.from_user.id, uid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("pt:"))
async def perm_toggle(c: CallbackQuery):
    if not await need(c, "perms_edit"):
        return
    _, uid, key = c.data.split(":")
    uid = int(uid)
    u = db.get_user(uid)
    if not u or key not in PERMS or not can_edit(c.from_user.id, u) or uid == c.from_user.id:
        return await c.answer("⛔ Нельзя", show_alert=True)
    if role_of(c.from_user.id) != "owner" and not has_perm(c.from_user.id, key):
        return await c.answer("⛔ Нельзя выдать то, чего нет у вас", show_alert=True)
    cur = db.perms_of(uid)
    cur ^= {key}
    db.set_perms(uid, cur)
    text, markup = perms_screen(c.from_user.id, uid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("lv:"))
async def level_menu(c: CallbackQuery):
    if not await need(c, "ranks_edit"):
        return
    uid = int(c.data.split(":")[1])
    u = db.get_user(uid)
    if not u or not can_edit(c.from_user.id, u) or uid == c.from_user.id:
        return await c.answer("⛔ Нельзя", show_alert=True)
    vr = level_rank(c.from_user.id)
    opts = [(lbl, f"lvs:{uid}:{k}") for k, lbl in ADMIN_LEVELS.items() if LEVEL_RANK[k] < vr]
    opts.append(("➖ Без уровня", f"lvs:{uid}:none"))
    await show(c, "Выберите уровень админа:", kb(opts, back=("⬅️ Назад", f"u:{uid}")))


@router.callback_query(F.data.startswith("lvs:"))
async def level_set(c: CallbackQuery):
    if not await need(c, "ranks_edit"):
        return
    _, uid, key = c.data.split(":")
    uid = int(uid)
    u = db.get_user(uid)
    if not u or not can_edit(c.from_user.id, u) or uid == c.from_user.id:
        return await c.answer("⛔ Нельзя", show_alert=True)
    if key != "none" and (key not in LEVEL_RANK or LEVEL_RANK[key] >= level_rank(c.from_user.id)):
        return await c.answer("⛔ Нельзя назначить уровень не ниже своего", show_alert=True)
    db.set_level(uid, None if key == "none" else key)
    text, markup = card(c.from_user.id, uid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("mka:"))
async def make_admin(c: CallbackQuery):
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только владелец", show_alert=True)
    uid = int(c.data.split(":")[1])
    db.set_role(uid, "admin")
    db.set_perms(uid, DEFAULT_ADMIN_PERMS)
    if not db.get_user(uid)["admin_level"]:
        db.set_level(uid, "junior")
    text, markup = card(c.from_user.id, uid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("rma:"))
async def remove_admin(c: CallbackQuery):
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только владелец", show_alert=True)
    uid = int(c.data.split(":")[1])
    db.set_role(uid, "user")
    db.set_perms(uid, set())
    db.set_level(uid, None)
    text, markup = card(c.from_user.id, uid)
    await show(c, text, markup)


def ban_ok(viewer, uid):
    u = db.get_user(uid)
    return bool(u) and can_edit(viewer, u) and viewer != uid and has_perm(viewer, "users_ban")


@router.callback_query(F.data.startswith("ban:"))
async def ban_ask(c: CallbackQuery):
    uid = int(c.data.split(":")[1])
    if not ban_ok(c.from_user.id, uid):
        return await c.answer("⛔ Нельзя", show_alert=True)
    u = db.get_user(uid)
    await show(c, f"Забанить навсегда {uname(u)}?\nОн потеряет доступ к боту, а бот попробует забанить его и в группе.",
               kb([("✅ Да, забанить", f"ban!:{uid}")], back=("❌ Отмена", f"u:{uid}")))


@router.callback_query(F.data.startswith("ban!:"))
async def ban_do(c: CallbackQuery, bot: Bot):
    uid = int(c.data.split(":")[1])
    if not ban_ok(c.from_user.id, uid):
        return await c.answer("⛔ Нельзя", show_alert=True)
    db.ban(uid, "bot")
    note = ""
    d = db.dest()
    if d:
        try:
            await bot.ban_chat_member(d[0], uid)
            note = "\n✅ Также забанен в группе."
        except Exception as e:
            note = f"\n⚠️ В группе забанить не удалось: {e}"
    text, markup = card(c.from_user.id, uid)
    await show(c, text + note, markup)


@router.callback_query(F.data.startswith("unban:"))
async def unban_do(c: CallbackQuery, bot: Bot):
    uid = int(c.data.split(":")[1])
    if not ban_ok(c.from_user.id, uid):
        return await c.answer("⛔ Нельзя", show_alert=True)
    db.unban(uid)
    note = "\n♻️ Все права сброшены (разрешения, уровень, роль админа, куратор)."
    d = db.dest()
    if d:
        try:
            await bot.unban_chat_member(d[0], uid, only_if_banned=True)
            note += "\n✅ Также разбанен в группе."
        except Exception as e:
            note += f"\n⚠️ В группе разбанить не удалось: {e}"
    text, markup = card(c.from_user.id, uid)
    await show(c, text + note, markup)


# ====================== ОПЛАТА ЗВЁЗДАМИ ======================
@router.callback_query(F.data == "stars")
async def stars_panel(c: CallbackQuery):
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только владелец", show_alert=True)
    await show(c, "💫 Оплата звёздами (скрытая функция):\n"
                  "Выберите, за что платить.",
               kb([("За конкретное задание", "stars:task"),
                   ("За неделю", "stars:week"),
                   ("За месяц", "stars:month")],
                  back=("🔙 В панель владельца", "owner")))


def stars_candidates(period=None):
    """Возвращает список (uid, name, recommended)."""
    out = []
    for u in db.members():
        if not u["role"] or u["banned"]:
            continue
        st = db.user_stats(u["id"])
        if period is None:
            # за конкретное задание — по последней оценённой работе
            rec = 10
            for s in reversed(db._c.execute("SELECT * FROM submissions WHERE uid=? AND status='reviewed' ORDER BY id DESC LIMIT 5", (u["id"],)).fetchall()):
                rec = logic.stars_recommendation(s["result"], s["rating"])
                break
        else:
            works = st["cnt"] or 0
            avg = st.get("avg_rating") or 0
            rec = logic.stars_for_period(works, avg)
        if rec > 0:
            out.append((u["id"], uname(u), rec))
    return out


@router.callback_query(F.data.startswith("stars:"))
async def stars_list(c: CallbackQuery, state: FSMContext):
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только владелец", show_alert=True)
    period = c.data.split(":")[1]
    period = None if period == "task" else period
    await state.update_data(stars_period=period)
    rows = stars_candidates(period)
    if not rows:
        return await show(c, "Нет участников для оплаты.", kb([], back=("🔙 Назад", "stars")))
    btns = []
    for uid, name, rec in rows[:10]:
        btns.append((f"{name} — {rec} ⭐", f"starspick:{uid}:{rec}"))
    btns.append(("💫 Оплатить всем (по рекомендации)", "starsall"))
    await show(c, "Кому платить?", kb(btns, back=("🔙 Назад", "stars")))


@router.callback_query(F.data.startswith("starspick:"))
async def stars_pick(c: CallbackQuery, state: FSMContext):
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только владелец", show_alert=True)
    _, uid, rec = c.data.split(":")
    await state.update_data(stars_uid=int(uid), stars_amount=int(rec))
    await state.set_state(S.stars_amount)
    await c.message.answer(f"Введите сумму в звёздах для этого участника (рекомендация: {rec}):")
    await c.answer()


@router.message(S.stars_amount)
async def stars_amount(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    if role_of(m.from_user.id) != "owner":
        return await m.answer("⛔ Только владелец")
    try:
        amount = int((m.text or "").strip())
        if amount <= 0 or amount > 10000:
            raise ValueError
    except ValueError:
        return await m.answer("Нужно целое число от 1 до 10000.")
    uid = d.get("stars_uid")
    if not uid:
        return await m.answer("Ошибка: не выбран участник.")
    db.add_payment(uid, amount, "звёзды", period=d.get("stars_period"), by=m.from_user.id)
    if config.PROVIDER_TOKEN:
        try:
            await bot.send_invoice(
                chat_id=uid,
                title="Оплата работы",
                description=f"Начисление {amount} звёзд",
                payload=f"pay_{uid}_{amount}",
                provider_token=config.PROVIDER_TOKEN,
                currency="XTR",
                prices=[LabeledPrice(label="Звёзды", amount=amount)],
            )
            await m.answer(f"✅ Счёт на {amount} ⭐ отправлен участнику {uid}.")
        except Exception as e:
            await m.answer(f"⚠️ Не удалось отправить счёт: {e}")
    else:
        await m.answer(f"✅ Запись об оплате {amount} ⭐ для {uid} создана. "
                       "PROVIDER_TOKEN не задан — реальный счёт не отправлен.")


@router.callback_query(F.data == "starsall")
async def stars_all(c: CallbackQuery, state: FSMContext, bot: Bot):
    if role_of(c.from_user.id) != "owner":
        return await c.answer("⛔ Только владелец", show_alert=True)
    d = await state.get_data()
    period = d.get("stars_period")
    rows = stars_candidates(period)
    if not rows:
        return await c.answer("Нет участников", show_alert=True)
    total = sum(rec for _, _, rec in rows)
    for uid, _, rec in rows:
        db.add_payment(uid, rec, "звёзды (всем)", period=period, by=c.from_user.id)
        if config.PROVIDER_TOKEN:
            try:
                await bot.send_invoice(
                    chat_id=uid, title="Оплата работы", description=f"Начисление {rec} звёзд",
                    payload=f"pay_{uid}_{rec}", provider_token=config.PROVIDER_TOKEN,
                    currency="XTR", prices=[LabeledPrice(label="Звёзды", amount=rec)])
            except Exception:
                pass
    await c.message.answer(f"✅ Созданы записи об оплате для {len(rows)} участников. Всего: {total} ⭐.")
    await c.answer()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(m: Message, bot: Bot):
    await m.answer("✅ Оплата получена. Спасибо!")


# ====================== фоновые задачи ======================
async def watch(bot: Bot):
    while True:
        await asyncio.sleep(config.CHECK_INTERVAL)
        try:
            await check_deleted_once(bot)
            await finalize_due(bot)
        except Exception:
            logging.exception("watch")


async def health_server():
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()


async def main():
    seed_passwords()
    bot = Bot(config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(Guard())
    dp.callback_query.outer_middleware(Guard())
    dp.include_router(router)
    for r in (materials.router, reminders.router, works.router, scoring.router):
        dp.include_router(r)
    if config.OWNER_ID and not role_of(config.OWNER_ID):
        db.set_role(config.OWNER_ID, "owner")
        db.set_level(config.OWNER_ID, "senior")
    if config.PORT:
        await health_server()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(watch(bot))
    asyncio.create_task(reminders.run_scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())