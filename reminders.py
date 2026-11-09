"""Напоминания: в место публикации, отдельную тему, личку. Вариант В — время для людей и дней."""
import asyncio
import json
import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import logic
from common import kb, show, need, has_perm, one_line

router = Router()
BACK = ("🔙 К напоминаниям", "rem")


class R(StatesGroup):
    text = State()
    once = State()
    time_same = State()
    time_each = State()
    thread_input = State()
    dm_input = State()
    dm_time = State()


def schedule_of(r):
    return json.loads(r["weekly"] or "{}"), json.loads(r["dates"] or "{}")


def dm_list(r):
    try:
        raw = json.loads(r["dm_users"] or "[]")
        if raw and isinstance(raw[0], str):
            return [{"uid": int(x), "time": None} for x in raw if x]
        return raw
    except (TypeError, ValueError, IndexError):
        return []


def rem_card(rid):
    r = db.reminder(rid)
    if not r:
        return "Напоминание не найдено", kb([], back=BACK)
    weekly, dates = schedule_of(r)
    nxt = logic.next_fire(weekly, dates, logic.now_msk())
    state = "▶️ включено" if r["active"] else "⏸ приостановлено / завершено"
    dms = dm_list(r)
    if dms:
        target = f"👤 В личку: {len(dms)} чел."
    else:
        target = f"Куда: {db.chat_title(r['chat_id'])}" + (f", тема {r['thread']}" if r["thread"] else "")
    text = (f"⏰ Напоминание #{rid}\n{state}\n\n{r['text']}\n\n{logic.describe(weekly, dates)}\n"
            f"Следующая отправка: {nxt.strftime('%d.%m.%Y %H:%M') + ' МСК' if nxt else '—'}\n{target}")
    return text, kb([("⏸ Приостановить" if r["active"] else "▶️ Включить", f"rpa:{rid}"),
                     ("📨 Отправить сейчас", f"rnow:{rid}"), ("🗑 Удалить", f"rdel:{rid}")], back=BACK)


def weekday_kb(days):
    rows = [[(("✅ " if i in days else "") + logic.WD_SHORT[i], f"rwd:{i}") for i in range(0, 4)],
            [(("✅ " if i in days else "") + logic.WD_SHORT[i], f"rwd:{i}") for i in range(4, 7)],
            ("➡️ Далее", "rwok")]
    return kb(rows, back=("❌ Отмена", "rcancel"))


def cal_kb(ym, selected):
    y, mo = map(int, ym.split("-"))
    today = logic.now_msk().date()
    prev_y, prev_m = (y, mo - 1) if mo > 1 else (y - 1, 12)
    next_y, next_m = (y, mo + 1) if mo < 12 else (y + 1, 1)
    can_prev = (y, mo) > (today.year, today.month)
    rows = [[("◀️" if can_prev else " ", f"rcm:{prev_y}-{prev_m:02d}" if can_prev else "noop"),
             (f"{logic.MONTHS[mo - 1]} {y}", "noop"),
             ("▶️", f"rcm:{next_y}-{next_m:02d}")],
            [(w, "noop") for w in logic.WD_SHORT]]
    for week in logic.month_weeks(y, mo):
        line = []
        for d in week:
            if d == 0:
                line.append((" ", "noop"))
                continue
            iso = f"{y}-{mo:02d}-{d:02d}"
            if iso < today.isoformat():
                line.append(("·", "noop"))
            elif iso in selected:
                line.append((f"✅{d}", f"rcd:{iso}"))
            else:
                line.append((str(d), f"rcd:{iso}"))
        rows.append(line)
    rows.append(("✅ Готово" + (f" (выбрано: {len(selected)})" if selected else ""), "rcok"))
    return kb(rows, back=("❌ Отмена", "rcancel"))


def keys_of(d):
    if d["kind"] == "weekly":
        return [str(x) for x in sorted(d["days"])]
    return sorted(d["dates"])


def label_of(d, key):
    if d["kind"] == "weekly":
        return logic.WD_FULL[int(key)]
    return f"{logic.fmt_date(key)} ({logic.WD_SHORT[logic.slot_dt(key, '00:00').weekday()]})"


def past_keys(d, times):
    now = logic.now_msk()
    bad = []
    for k, t in times.items():
        if d["kind"] != "weekly" and logic.slot_dt(k, t) <= now:
            bad.append(k)
    return bad


def build(d):
    return (dict(d["times"]), {}) if d["kind"] == "weekly" else ({}, dict(d["times"]))


async def confirm(target: Message, state: FSMContext):
    d = await state.get_data()
    weekly, dates = build(d)
    if d.get("dm_users"):
        target_txt = f"👤 В личку: {len(d['dm_users'])} чел."
    else:
        target_txt = f"Куда: {db.chat_title(d['chat_id'])}" + (f", тема {d['thread']}" if d.get("thread") else "")
    text = (f"Проверьте напоминание:\n\n{d['text']}\n\n{logic.describe(weekly, dates)}\n{target_txt}")
    await target.answer(text, reply_markup=kb([("✅ Создать", "rsave")], back=("❌ Отмена", "rcancel")))


@router.callback_query(F.data == "rem")
async def rem_list(c: CallbackQuery, state: FSMContext):
    if not await need(c, "reminders_manage"):
        return
    await state.clear()
    rows = [(("⏰ " if r["active"] else "⏸ ") + one_line(r["text"], 28), f"rem:{r['id']}") for r in db.reminders()]
    rows.append(("➕ Новое напоминание", "remnew"))
    await show(c, "⏰ Напоминания (время московское):", kb(rows, back=("🔙 В админ-панель", "admin")))


@router.callback_query(F.data.startswith("rem:"))
async def rem_open(c: CallbackQuery):
    if not await need(c, "reminders_manage"):
        return
    text, markup = rem_card(int(c.data.split(":")[1]))
    await show(c, text, markup)


@router.callback_query(F.data.startswith("rpa:"))
async def rem_pause(c: CallbackQuery):
    if not await need(c, "reminders_manage"):
        return
    rid = int(c.data.split(":")[1])
    r = db.reminder(rid)
    db.set_reminder_active(rid, 0 if r["active"] else 1)
    text, markup = rem_card(rid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("rnow:"))
async def rem_now(c: CallbackQuery, bot: Bot):
    if not await need(c, "reminders_manage"):
        return
    r = db.reminder(int(c.data.split(":")[1]))
    dms = dm_list(r)
    try:
        if dms:
            for item in dms:
                try:
                    await bot.send_message(item["uid"], "⏰ Напоминание\n\n" + r["text"])
                except Exception as e:
                    logging.warning("Не отправлено %s: %s", item["uid"], e)
        else:
            await bot.send_message(r["chat_id"], "⏰ Напоминание\n\n" + r["text"],
                                   message_thread_id=r["thread"] or None)
        await c.answer("Отправлено")
    except Exception as e:
        await c.answer(f"Не удалось отправить: {e}", show_alert=True)


@router.callback_query(F.data.startswith("rdel:"))
async def rem_del_ask(c: CallbackQuery):
    if not await need(c, "reminders_manage"):
        return
    rid = int(c.data.split(":")[1])
    await show(c, "Удалить это напоминание?", kb([("✅ Да, удалить", f"rdel!:{rid}")], back=("❌ Отмена", f"rem:{rid}")))


@router.callback_query(F.data.startswith("rdel!:"))
async def rem_del(c: CallbackQuery):
    if not await need(c, "reminders_manage"):
        return
    db.delete_reminder(int(c.data.split(":")[1]))
    await show(c, "🗑 Напоминание удалено", kb([], back=BACK))


@router.callback_query(F.data == "rcancel")
async def rem_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "Отменено.", kb([], back=BACK))


@router.callback_query(F.data == "remnew")
async def rem_new(c: CallbackQuery, state: FSMContext):
    if not await need(c, "reminders_manage"):
        return
    await state.clear()
    await state.set_state(R.text)
    await c.message.answer("✍️ Введите текст напоминания:")
    await c.answer()


@router.message(R.text)
async def rem_text(m: Message, state: FSMContext):
    if not has_perm(m.from_user.id, "reminders_manage"):
        await state.clear()
        return await m.answer("⛔ Нет разрешения")
    text = (m.text or "").strip()
    if not text:
        return await m.answer("Нужен текст.")
    await state.update_data(text=text[:3500])
    await m.answer("Куда отправлять?", reply_markup=kb([
        ("📢 В место публикации", "rt:dest"),
        ("📌 В отдельную тему группы", "rt:thread"),
        ("👤 В личку конкретным людям", "rt:dm")], back=("❌ Отмена", "rcancel")))


@router.callback_query(F.data.startswith("rt:"))
async def rem_target(c: CallbackQuery, state: FSMContext):
    kind = c.data.split(":")[1]
    if kind == "dest":
        d = db.dest()
        if not d:
            return await c.answer("Сначала выберите место публикации (⚙️ → 📍)", show_alert=True)
        await state.update_data(chat_id=d[0], thread=d[1], dm_users=[])
        await c.message.answer("Как отправлять?", reply_markup=kb([
            ("📅 Разово", "rk:once"), ("🔁 Каждую неделю", "rk:weekly"), ("🗓 По датам", "rk:dates")],
            back=("❌ Отмена", "rcancel")))
    elif kind == "thread":
        await state.set_state(R.thread_input)
        await c.message.answer("Отправьте ссылку на тему (t.me/c/.../123) или ID темы числом:")
    else:
        await state.set_state(R.dm_input)
        await c.message.answer("Отправьте ID пользователей через запятую (например: 123456789,987654321):")
    await c.answer()


@router.message(R.thread_input)
async def rem_thread_input(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    thread = 0
    if "t.me/c/" in text:
        try:
            thread = int(text.split("/")[-1])
        except ValueError:
            return await m.answer("Не удалось распарсить ссылку. Пример: https://t.me/c/1234567890/12")
    else:
        try:
            thread = int(text)
        except ValueError:
            return await m.answer("Отправьте число или ссылку.")
    d = db.dest()
    if not d:
        return await m.answer("Сначала выберите место публикации.")
    await state.update_data(chat_id=d[0], thread=thread, dm_users=[])
    await m.answer("Как отправлять?", reply_markup=kb([
        ("📅 Разово", "rk:once"), ("🔁 Каждую неделю", "rk:weekly"), ("🗓 По датам", "rk:dates")],
        back=("❌ Отмена", "rcancel")))
    await state.set_state(None)


@router.message(R.dm_input)
async def rem_dm_input(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    ids = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    if not ids:
        return await m.answer("Не нашёл ни одного ID. Пример: 123456789,987654321")
    await state.update_data(chat_id=0, thread=0, dm_users=[{"uid": x, "time": None} for x in ids])
    await m.answer("Как отправлять?", reply_markup=kb([
        ("📅 Разово", "rk:once"), ("🔁 Каждую неделю", "rk:weekly"), ("🗓 По датам", "rk:dates")],
        back=("❌ Отмена", "rcancel")))
    await state.set_state(None)


@router.callback_query(F.data.startswith("rk:"))
async def rem_kind(c: CallbackQuery, state: FSMContext):
    if not await need(c, "reminders_manage"):
        return
    kind = c.data.split(":")[1]
    d = await state.get_data()
    if "text" not in d:
        return await c.answer("Начните заново: ➕ Новое напоминание", show_alert=True)
    if kind == "once":
        await state.update_data(kind="once")
        await state.set_state(R.once)
        await c.message.answer("📅 Введите дату и время по Москве: ДД.ММ.ГГГГ ЧЧ:ММ\nНапример: 25.12.2026 18:30")
        return await c.answer()
    if kind == "weekly":
        await state.update_data(kind="weekly", days=[])
        return await show(c, "🔁 Выберите дни недели (можно несколько):", weekday_kb([]))
    ym = logic.now_msk().strftime("%Y-%m")
    await state.update_data(kind="dates", dates=[], cal=ym)
    await show(c, "🗓 Отметьте дни в календаре (можно несколько месяцев):", cal_kb(ym, []))


@router.message(R.once)
async def rem_once(m: Message, state: FSMContext):
    dt = logic.parse_datetime(m.text)
    if not dt:
        return await m.answer("Не понял формат. Пример: 25.12.2026 18:30")
    if dt <= logic.now_msk():
        return await m.answer("Это время уже прошло. Введите будущую дату и время.")
    iso = dt.date().isoformat()
    await state.update_data(kind="dates", dates=[iso], times={iso: dt.strftime("%H:%M")})
    await state.set_state(None)
    await confirm(m, state)


@router.callback_query(F.data.startswith("rwd:"))
async def rem_weekday(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    days = set(d.get("days", []))
    days ^= {int(c.data.split(":")[1])}
    await state.update_data(days=sorted(days))
    await show(c, "🔁 Выберите дни недели (можно несколько):", weekday_kb(days))


@router.callback_query(F.data.startswith("rcm:"))
async def rem_month(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    ym = c.data.split(":", 1)[1]
    await state.update_data(cal=ym)
    await show(c, "🗓 Отметьте дни в календаре:", cal_kb(ym, set(d.get("dates", []))))


@router.callback_query(F.data.startswith("rcd:"))
async def rem_day(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    dates = set(d.get("dates", []))
    dates ^= {c.data.split(":", 1)[1]}
    await state.update_data(dates=sorted(dates))
    await show(c, "🗓 Отметьте дни в календаре:", cal_kb(d.get("cal") or logic.now_msk().strftime("%Y-%m"), dates))


async def ask_mode(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    keys = keys_of(d)
    if len(keys) == 1:
        await state.set_state(R.time_same)
        await c.message.answer(f"🕒 Введите время ЧЧ:ММ по Москве ({label_of(d, keys[0])}):")
        return await c.answer()
    await show(c, f"Выбрано: {len(keys)}. Какое время?", kb([
        ("🕒 Одно время для всех", "rtm:same"), ("🕘 Своё время для каждого", "rtm:each")],
        back=("❌ Отмена", "rcancel")))


@router.callback_query(F.data == "rwok")
async def rem_weekday_ok(c: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("days"):
        return await c.answer("Выберите хотя бы один день", show_alert=True)
    await ask_mode(c, state)


@router.callback_query(F.data == "rcok")
async def rem_cal_ok(c: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("dates"):
        return await c.answer("Выберите хотя бы один день", show_alert=True)
    await ask_mode(c, state)


@router.callback_query(F.data.startswith("rtm:"))
async def rem_mode(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if c.data.endswith("same"):
        await state.set_state(R.time_same)
        await c.message.answer("🕒 Введите время ЧЧ:ММ по Москве (для всех выбранных дней):")
    else:
        keys = keys_of(d)
        await state.update_data(queue=keys, times={})
        await state.set_state(R.time_each)
        await c.message.answer(f"🕘 Время для {label_of(d, keys[0])} (ЧЧ:ММ, МСК):")
    await c.answer()


@router.message(R.time_same)
async def rem_time_same(m: Message, state: FSMContext):
    t = logic.parse_time(m.text)
    if not t:
        return await m.answer("Не понял. Формат: ЧЧ:ММ, например 18:30")
    d = await state.get_data()
    times = {k: t for k in keys_of(d)}
    if past_keys(d, times):
        return await m.answer("Для сегодняшнего дня это время уже прошло. Введите другое время.")
    await state.update_data(times=times)
    await state.set_state(None)
    await confirm(m, state)


@router.message(R.time_each)
async def rem_time_each(m: Message, state: FSMContext):
    t = logic.parse_time(m.text)
    if not t:
        return await m.answer("Не понял. Формат: ЧЧ:ММ, например 18:30")
    d = await state.get_data()
    queue, times = d["queue"], d["times"]
    key = queue[0]
    if past_keys(d, {key: t}):
        return await m.answer("Это время уже прошло. Введите другое.")
    times[key] = t
    queue = queue[1:]
    await state.update_data(queue=queue, times=times)
    if queue:
        return await m.answer(f"🕘 Время для {label_of(d, queue[0])} (ЧЧ:ММ, МСК):")
    await state.set_state(None)
    await confirm(m, state)


@router.callback_query(F.data == "rsave")
async def rem_save(c: CallbackQuery, state: FSMContext):
    if not await need(c, "reminders_manage"):
        return
    d = await state.get_data()
    if "times" not in d:
        return await c.answer("Начните заново", show_alert=True)
    weekly, dates = build(d)
    dm_users = d.get("dm_users") or []
    rid = db.add_reminder(d["text"], weekly, dates, d.get("chat_id", 0), d.get("thread", 0),
                          c.from_user.id, dm_users)
    await state.clear()
    text, markup = rem_card(rid)
    await show(c, "✅ Создано!\n\n" + text, markup)


async def tick(bot: Bot):
    now = logic.now_msk()
    keep = (now - timedelta(days=3)).date().isoformat()
    for r in db.reminders():
        if not r["active"]:
            continue
        weekly, dates = schedule_of(r)
        fired = db.fired_keys(r["id"], keep)
        for key in logic.due_keys(weekly, dates, now, fired):
            db.mark_fired(r["id"], key)
            dms = dm_list(r)
            try:
                if dms:
                    for item in dms:
                        try:
                            await bot.send_message(item["uid"], "⏰ Напоминание\n\n" + r["text"])
                        except Exception as e:
                            logging.warning("Напоминание %s для %s: %s", r["id"], item["uid"], e)
                else:
                    await bot.send_message(r["chat_id"], "⏰ Напоминание\n\n" + r["text"],
                                           message_thread_id=r["thread"] or None)
            except Exception as e:
                logging.warning("Напоминание %s не отправлено: %s", r["id"], e)
        if logic.finished(weekly, dates, now, db.fired_keys(r["id"], keep)):
            db.set_reminder_active(r["id"], 0)


async def run_scheduler(bot: Bot):
    while True:
        try:
            await tick(bot)
        except Exception:
            logging.exception("reminders")
        await asyncio.sleep(20)