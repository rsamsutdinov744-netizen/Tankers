"""Сдача работ, оценка 1–10 + итог, лучшая работа, опросы, формы."""
import json
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import logic
from common import (kb, show, need, has_perm, uname, status_text, task_name, finish_task, award, dm,
                    scoring_cfg)
from perms import POS_NAMES

router = Router()
PRIVATE = F.chat.type == "private"
SEND = {"photo": "send_photo", "document": "send_document", "video": "send_video", "voice": "send_voice",
        "audio": "send_audio", "animation": "send_animation"}


class W(StatesGroup):
    sub = State()
    rev_comment = State()
    poll_q = State()
    form_title = State()
    form_q = State()
    form_rename = State()


def file_of(m: Message):
    if m.photo:
        return ["photo", m.photo[-1].file_id]
    for kind in ("document", "video", "voice", "audio", "animation"):
        obj = getattr(m, kind, None)
        if obj:
            return [kind, obj.file_id]
    return None


async def send_file(bot: Bot, chat, ftype, fid):
    await getattr(bot, SEND[ftype])(chat, fid)


def can_review(uid, t, u):
    me = db.get_user(uid)
    if not me or me["banned"] or not me["role"]:
        return False
    if has_perm(uid, "works_review"):
        return True
    pos = (t["pos"] if t else None) or (u["position"] if u else None)
    return bool(pos) and db.get_setting(f"curator:{pos}") == str(uid)


def reviewers_for(t, u):
    ids = {x["id"] for x in db.all_users() if x["role"] == "owner" and not x["banned"]}
    pos = t["pos"] or (u["position"] if u else None)
    if pos and db.get_setting(f"curator:{pos}"):
        ids.add(int(db.get_setting(f"curator:{pos}")))
    ids.discard(u["id"] if u else None)
    return ids


SEND_KB = kb([("✅ Отправить", "sd:ok")], back=("❌ Отмена", "sd:cancel"))
CANCEL_KB = kb([], back=("❌ Отмена", "sd:cancel"))


@router.callback_query(F.data.startswith("sub:"))
async def sub_start(c: CallbackQuery):
    t = db.task(int(c.data.split(":")[1]))
    if not db.task_is_open(t):
        return await c.answer("Приём работ по этому заданию закрыт", show_alert=True)
    rows = [("📝 Свободный отчёт", f"sf:{t['id']}:free")]
    for f in db.forms_visible(c.from_user.id):
        rows.append((f"🧾 {f['title']}" + (" (моя)" if f["owner"] else ""), f"sf:{t['id']}:{f['id']}"))
    await c.message.answer(f"📤 Сдача работы по заданию {task_name(t)}.\nВыберите формат:",
                           reply_markup=kb(rows, back=("❌ Отмена", "sd:cancel")))
    await c.answer()


@router.callback_query(F.data.startswith("sf:"))
async def sub_format(c: CallbackQuery, state: FSMContext):
    _, tid, mode = c.data.split(":")
    t = db.task(int(tid))
    if not db.task_is_open(t):
        return await c.answer("Приём работ закрыт", show_alert=True)
    data = {"tid": int(tid), "form": None, "form_title": None, "idx": 0, "answers": [], "files": [], "parts": []}
    if mode != "free":
        f = db.form(int(mode))
        if not f or (f["owner"] and f["owner"] != c.from_user.id):
            return await c.answer("Форма недоступна", show_alert=True)
        data["form"], data["form_title"] = json.loads(f["questions"]), f["title"]
    await state.set_state(W.sub)
    await state.set_data(data)
    if data["form"]:
        await c.message.answer(f"🧾 {data['form_title']}\n\nВопрос 1/{len(data['form'])}: {data['form'][0]}",
                               reply_markup=CANCEL_KB)
    else:
        await c.message.answer("Отправьте результат: текст, фото или файл. Можно несколько сообщений.\n"
                               "Когда закончите, нажмите «✅ Отправить».", reply_markup=SEND_KB)
    await c.answer()


@router.message(W.sub, PRIVATE)
async def sub_message(m: Message, state: FSMContext):
    d = await state.get_data()
    text = (m.text or m.caption or "").strip()
    f = file_of(m)
    if not text and not f:
        return await m.answer("Нужен текст, фото или файл.")
    if f and len(d["files"]) < 8:
        d["files"].append(f)
    if d["form"]:
        d["answers"].append(text or "(вложение)")
        d["idx"] += 1
        await state.set_data(d)
        if d["idx"] < len(d["form"]):
            return await m.answer(f"Вопрос {d['idx'] + 1}/{len(d['form'])}: {d['form'][d['idx']]}", reply_markup=CANCEL_KB)
        return await m.answer("Все ответы получены. Отправить?", reply_markup=SEND_KB)
    if text:
        d["parts"].append(text)
    await state.set_data(d)
    await m.answer(f"Принято (текстов: {len(d['parts'])}, файлов: {len(d['files'])}). Отправьте ещё или нажмите «✅ Отправить».",
                   reply_markup=SEND_KB)


@router.callback_query(F.data == "sd:cancel")
async def sub_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("Отменено.")
    await c.answer()


@router.callback_query(F.data == "sd:ok")
async def sub_send(c: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if not d or "tid" not in d:
        return await c.answer("Нечего отправлять", show_alert=True)
    t = db.task(d["tid"])
    if d["form"]:
        if d["idx"] < len(d["form"]):
            return await c.answer("Ответьте на все вопросы формы", show_alert=True)
        text = "\n\n".join(f"{i + 1}. {q}\n{a}" for i, (q, a) in enumerate(zip(d["form"], d["answers"])))
    else:
        text = "\n\n".join(d["parts"])
    if not text and not d["files"]:
        return await c.answer("Сначала отправьте результат", show_alert=True)
    if not db.task_is_open(t):
        await state.clear()
        return await c.answer("Приём работ по заданию уже закрыт", show_alert=True)
    sid = db.add_submission(t["id"], c.from_user.id, text, d["files"], d["form_title"])
    await state.clear()
    await send_to_reviewers(bot, sid)
    await c.message.answer("✅ Работа отправлена на проверку.\n\n" + db.random_phrase())
    await c.answer()


def review_text(sid):
    s = db.submission(sid)
    t = db.task(s["task_id"])
    u = db.get_user(s["uid"])
    pos = POS_NAMES.get(u["position"], "должность не выбрана")
    lines = [f"📥 По заданию {task_name(t)} пришла работа от {uname(u)} ({pos}, {status_text(u)}).",
             "" if not s["form_title"] else f"🧾 Форма: {s['form_title']}", "",
             s["text"] or "(без текста)", "",
             "Пожалуйста, оцените работу от 1 до 10 и отметьте, выполнено ли задание. "
             "При желании добавьте корректировки и комментарий."]
    cur = []
    if s["rating"]:
        cur.append(f"⭐ Оценка: {s['rating']}/10")
    if s["result"]:
        cur.append(logic.RES_LABELS[s["result"]])
    if s["comment"]:
        cur.append("💬 " + s["comment"])
    if cur:
        lines += ["", " · ".join(cur)]
    return "\n".join(lines)[:4000]


def review_kb(sid):
    s = db.submission(sid)
    rows = []
    for rng in (range(1, 6), range(6, 11)):
        rows.append([((f"✅{n}" if s["rating"] == n else str(n)), f"rv:{sid}:r:{n}") for n in rng])
    keys = list(logic.RES_SHORT)
    for i in (0, 2):
        rows.append([(("☑ " if s["result"] == k else "") + logic.RES_SHORT[k], f"rv:{sid}:res:{k}") for k in keys[i:i + 2]])
    rows.append(("✍️ Комментарий / корректировки", f"rv:{sid}:cm"))
    rows.append(("📨 Отправить оценку", f"rv:{sid}:ok"))
    if len(db.submissions_for_task(s["task_id"])) >= 2:
        rows.append([("🏆 Выбрать лучшую", f"bst:{s['task_id']}"), ("📊 Опрос", f"pol:{s['task_id']}")])
    return kb(rows)


async def deliver_review(bot: Bot, chat_id, sid):
    s = db.submission(sid)
    for ftype, fid in json.loads(s["files"] or "[]"):
        await send_file(bot, chat_id, ftype, fid)
    msg = await bot.send_message(chat_id, review_text(sid), reply_markup=review_kb(sid))
    db.add_review_msg(sid, chat_id, msg.message_id)


async def send_to_reviewers(bot: Bot, sid):
    s = db.submission(sid)
    t = db.task(s["task_id"])
    u = db.get_user(s["uid"])
    for rid in reviewers_for(t, u):
        try:
            await deliver_review(bot, rid, sid)
        except Exception as e:
            logging.warning("Работа %s не доставлена %s: %s", sid, rid, e)


async def refresh(c: CallbackQuery, sid):
    try:
        await c.message.edit_text(review_text(sid), reply_markup=review_kb(sid))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("rv:"))
async def review_action(c: CallbackQuery, state: FSMContext, bot: Bot):
    parts = c.data.split(":")
    sid, action = int(parts[1]), parts[2]
    s = db.submission(sid)
    if not s:
        return await c.answer("Работа не найдена", show_alert=True)
    t, u = db.task(s["task_id"]), db.get_user(s["uid"])
    if not can_review(c.from_user.id, t, u):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    if s["status"] != "pending":
        return await c.answer("Эта работа уже оценена", show_alert=True)
    if action == "r":
        db.update_submission(sid, rating=int(parts[3]))
        await refresh(c, sid)
        return await c.answer()
    if action == "res":
        db.update_submission(sid, result=parts[3])
        await refresh(c, sid)
        return await c.answer()
    if action == "cm":
        await state.set_state(W.rev_comment)
        await state.update_data(sid=sid, chat=c.message.chat.id, msg=c.message.message_id)
        await c.message.answer("✍️ Напишите комментарий или корректировки для участника:")
        return await c.answer()
    if action == "ok":
        if not s["rating"] or not s["result"]:
            return await c.answer("Выберите оценку 1–10 и итог", show_alert=True)
        await finalize_review(bot, sid, c.from_user.id)
        return await c.answer("Оценка отправлена")


@router.message(W.rev_comment, PRIVATE)
async def review_comment(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    s = db.submission(d["sid"])
    if not s or s["status"] != "pending" or not can_review(m.from_user.id, db.task(s["task_id"]), db.get_user(s["uid"])):
        return await m.answer("Работа уже оценена или нет доступа.")
    db.update_submission(d["sid"], comment=(m.text or m.caption or "").strip()[:800])
    try:
        await bot.edit_message_text(review_text(d["sid"]), chat_id=d["chat"], message_id=d["msg"],
                                    reply_markup=review_kb(d["sid"]))
    except TelegramBadRequest:
        pass
    await m.answer("💬 Комментарий сохранён. Нажмите «📨 Отправить оценку».")


async def finalize_review(bot: Bot, sid, reviewer):
    s = db.submission(sid)
    t, u = db.task(s["task_id"]), db.get_user(s["uid"])
    cfg = scoring_cfg()
    pts = logic.calc_points(s["result"], s["rating"], cfg)
    db.update_submission(sid, status="reviewed", reviewer=reviewer, points=pts)
    name = task_name(t)
    _, promo = await award(s["uid"], pts, f"Работа по {name}: {s['rating']}/10, {logic.RES_SHORT[s['result']]}", reviewer, sid)
    text = (f"🧾 Ваша работа по заданию {name} оценена.\n⭐ Оценка: {s['rating']}/10\n{logic.RES_LABELS[s['result']]}\n"
            f"Баллы: +{logic.fmt_num(pts)}")
    if s["comment"]:
        text += f"\n\n💬 Комментарий: {s['comment']}"
    text += "\n\n" + db.random_phrase()
    if promo:
        text += "\n\n" + promo
    await dm(bot, s["uid"], text)
    if s["result"] == "done" and t["status"] == "active":
        await finish_task(bot, t, "done")
    rv = db.get_user(reviewer)
    final = review_text(sid) + f"\n\n✅ Оценил(а): {uname(rv)}. Баллы участнику: +{logic.fmt_num(pts)}"
    for r in db.review_msgs(sid):
        try:
            await bot.edit_message_text(final, chat_id=r["chat_id"], message_id=r["msg_id"], reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data == "wk")
async def works_pending(c: CallbackQuery):
    if not await need(c, "works_review"):
        return
    rows = db.pending_submissions()
    back = ("🔙 В админ-панель", "admin")
    if not rows:
        return await show(c, "📭 Работ на проверку нет", kb([], back=back))
    btns = []
    for s in rows:
        t, u = db.task(s["task_id"]), db.get_user(s["uid"])
        btns.append((f"{uname(u)} · {task_name(t)}", f"wv:{s['id']}"))
    await show(c, "📥 Работы на проверку:", kb(btns, back=back))


@router.callback_query(F.data.startswith("ws:"))
async def works_of_task(c: CallbackQuery):
    tid = int(c.data.split(":")[1])
    t = db.task(tid)
    if not t or not (has_perm(c.from_user.id, "works_review") or has_perm(c.from_user.id, "tasks_close")):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    rows = db.submissions_for_task(tid)
    if not rows:
        return await c.answer("Работ по этому заданию пока нет", show_alert=True)
    btns = []
    for s in rows:
        icon = "🏆" if s["best"] else ("✅" if s["status"] == "reviewed" else "🕓")
        btns.append((f"{icon} {uname(db.get_user(s['uid']))} — {s['rating'] or '—'}/10", f"wv:{s['id']}"))
    await c.message.answer(f"📥 Работы по заданию {task_name(t)}:", reply_markup=kb(btns))
    await c.answer()


@router.callback_query(F.data.startswith("wv:"))
async def work_view(c: CallbackQuery, bot: Bot):
    sid = int(c.data.split(":")[1])
    s = db.submission(sid)
    if not s or not can_review(c.from_user.id, db.task(s["task_id"]), db.get_user(s["uid"])):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    if s["status"] == "pending":
        await deliver_review(bot, c.message.chat.id, sid)
    else:
        for ftype, fid in json.loads(s["files"] or "[]"):
            await send_file(bot, c.message.chat.id, ftype, fid)
        await c.message.answer(review_text(sid) + f"\n\nБаллы: +{logic.fmt_num(s['points'])}")
    await c.answer()


@router.callback_query(F.data.startswith("bst:"))
async def best_list(c: CallbackQuery):
    tid = int(c.data.split(":")[1])
    t = db.task(tid)
    if not can_review(c.from_user.id, t, None):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    if db.task_best(tid):
        return await c.answer("Лучшая работа по этому заданию уже выбрана", show_alert=True)
    rows = db.submissions_for_task(tid)
    btns = [(f"{i}. {uname(db.get_user(s['uid']))} — {s['rating'] or '—'}/10", f"bsp:{s['id']}") for i, s in enumerate(rows, 1)]
    await c.message.answer(f"🏆 Какая работа по заданию {task_name(t)} лучшая?", reply_markup=kb(btns))
    await c.answer()


@router.callback_query(F.data.startswith("bsp:"))
async def best_pick(c: CallbackQuery, bot: Bot):
    s = db.submission(int(c.data.split(":")[1]))
    t = db.task(s["task_id"]) if s else None
    if not s or not can_review(c.from_user.id, t, None):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    if db.task_best(t["id"]):
        return await c.answer("Лучшая работа уже выбрана", show_alert=True)
    db.update_submission(s["id"], best=1)
    bonus = scoring_cfg()["best_bonus"]
    _, promo = await award(s["uid"], bonus, f"Лучшая работа по {task_name(t)}", c.from_user.id, s["id"])
    text = (f"🏆 Ваша работа по заданию {task_name(t)} выбрана лучшей!\nБонус: +{logic.fmt_num(bonus)} балла(ов)\n\n"
            + db.random_phrase() + (f"\n\n{promo}" if promo else ""))
    await dm(bot, s["uid"], text)
    await c.message.answer(f"🏆 Выбрана лучшая: {uname(db.get_user(s['uid']))}")
    await c.answer()


async def make_poll(bot: Bot, tid, question):
    t = db.task(tid)
    subs = db.submissions_for_task(tid)[:10]
    dest = db.dest()
    if len(subs) < 2:
        return "Для опроса нужно минимум две работы."
    if not dest:
        return "Не выбрано место публикации (⚙️ → 📍)."
    options = [f"Работа {i}: {uname(db.get_user(s['uid']))}"[:100] for i, s in enumerate(subs, 1)]
    q = (question or f"Какая работа по заданию {task_name(t)} лучше?")[:300]
    try:
        await bot.send_poll(dest[0], q, options, is_anonymous=True, message_thread_id=dest[1] or None)
        return "📊 Опрос опубликован в группе."
    except Exception as e:
        return f"⚠️ Не удалось создать опрос: {e}"


@router.callback_query(F.data.startswith("pol:"))
async def poll_start(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(":")[1])
    if not has_perm(c.from_user.id, "polls_create"):
        return await c.answer("⛔ Нет разрешения (нужно «Создавать опросы»)", show_alert=True)
    await state.set_state(W.poll_q)
    await state.update_data(tid=tid)
    await c.message.answer("📊 Отправьте текст вопроса для опроса или нажмите «Стандартный».",
                           reply_markup=kb([("Стандартный вопрос", f"polstd:{tid}")], back=("❌ Отмена", "sd:cancel")))
    await c.answer()


@router.callback_query(F.data.startswith("polstd:"))
async def poll_std(c: CallbackQuery, state: FSMContext, bot: Bot):
    if not has_perm(c.from_user.id, "polls_create"):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    await state.clear()
    await c.message.answer(await make_poll(bot, int(c.data.split(":")[1]), None))
    await c.answer()


@router.message(W.poll_q, PRIVATE)
async def poll_custom(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    if not has_perm(m.from_user.id, "polls_create"):
        return await m.answer("⛔ Нет разрешения")
    await m.answer(await make_poll(bot, d["tid"], (m.text or "").strip()))


def forms_screen(shared, uid):
    rows = db.shared_forms() if shared else db.own_forms(uid)
    btns = [(f"🧾 {f['title']}", f"fm:{f['id']}") for f in rows]
    btns.append(("➕ Создать форму", "fmnew:shared" if shared else "fmnew:me"))
    title = ("🧾 Общие формы выполнения заданий (видны всем участникам):" if shared
             else "🧾 Мои формы (видны только вам при сдаче работы):")
    return title, kb(btns, back=("🔙 В админ-панель", "admin") if shared else None, menu=not shared)


@router.callback_query(F.data == "frm")
async def forms_shared(c: CallbackQuery):
    if not await need(c, "forms_edit"):
        return
    text, markup = forms_screen(True, c.from_user.id)
    await show(c, text, markup)


@router.callback_query(F.data == "myfrm")
async def forms_mine(c: CallbackQuery):
    text, markup = forms_screen(False, c.from_user.id)
    await show(c, text, markup)


def form_allowed(uid, f):
    return bool(f) and (has_perm(uid, "forms_edit") if f["owner"] is None else f["owner"] == uid)


@router.callback_query(F.data.startswith("fm:"))
async def form_card(c: CallbackQuery):
    f = db.form(int(c.data.split(":")[1]))
    if not form_allowed(c.from_user.id, f):
        return await c.answer("⛔ Нет доступа", show_alert=True)
    qs = json.loads(f["questions"])
    text = f"🧾 {f['title']}\n\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1))
    back = ("⬅️ К формам", "frm" if f["owner"] is None else "myfrm")
    await show(c, text, kb([("✏️ Название", f"fmren:{f['id']}"), ("🗑 Удалить", f"fmdel:{f['id']}")], back=back))


@router.callback_query(F.data.startswith("fmdel:"))
async def form_del_ask(c: CallbackQuery):
    f = db.form(int(c.data.split(":")[1]))
    if not form_allowed(c.from_user.id, f):
        return await c.answer("⛔ Нет доступа", show_alert=True)
    await show(c, f"Удалить форму «{f['title']}»?", kb([("✅ Да", f"fmdel!:{f['id']}")], back=("❌ Отмена", f"fm:{f['id']}")))


@router.callback_query(F.data.startswith("fmdel!:"))
async def form_del(c: CallbackQuery):
    f = db.form(int(c.data.split(":")[1]))
    if not form_allowed(c.from_user.id, f):
        return await c.answer("⛔ Нет доступа", show_alert=True)
    db.delete("forms", f["id"])
    await show(c, "🗑 Форма удалена", kb([], back=("⬅️ К формам", "frm" if f["owner"] is None else "myfrm")))


@router.callback_query(F.data.startswith("fmren:"))
async def form_ren(c: CallbackQuery, state: FSMContext):
    f = db.form(int(c.data.split(":")[1]))
    if not form_allowed(c.from_user.id, f):
        return await c.answer("⛔ Нет доступа", show_alert=True)
    await state.set_state(W.form_rename)
    await state.update_data(fid=f["id"])
    await c.message.answer("Введите новое название формы:")
    await c.answer()


@router.message(W.form_rename, PRIVATE)
async def form_ren_save(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    f = db.form(d["fid"])
    if not form_allowed(m.from_user.id, f):
        return await m.answer("⛔ Нет доступа")
    db.rename_form(f["id"], (m.text or "").strip()[:60])
    await m.answer("✅ Название изменено", reply_markup=kb([("🧾 К форме", f"fm:{f['id']}")]))


@router.callback_query(F.data.startswith("fmnew:"))
async def form_new(c: CallbackQuery, state: FSMContext):
    scope = c.data.split(":")[1]
    if scope == "shared" and not has_perm(c.from_user.id, "forms_edit"):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    await state.set_state(W.form_title)
    await state.update_data(scope=scope, questions=[])
    await c.message.answer("Введите название формы (например «Отчёт по арту»):")
    await c.answer()


@router.message(W.form_title, PRIVATE)
async def form_title(m: Message, state: FSMContext):
    title = (m.text or "").strip()[:60]
    if not title:
        return await m.answer("Нужно текстовое название.")
    await state.update_data(title=title)
    await state.set_state(W.form_q)
    await m.answer("Отправляйте вопросы формы по одному сообщению. Когда закончите, нажмите «✅ Готово».",
                   reply_markup=kb([("✅ Готово", "fmok")], back=("❌ Отмена", "sd:cancel")))


@router.message(W.form_q, PRIVATE)
async def form_question(m: Message, state: FSMContext):
    q = (m.text or "").strip()
    d = await state.get_data()
    if not q:
        return await m.answer("Вопрос должен быть текстом.")
    if len(d["questions"]) >= 15:
        return await m.answer("Максимум 15 вопросов. Нажмите «✅ Готово».")
    d["questions"].append(q[:300])
    await state.set_data(d)
    await m.answer(f"Вопрос {len(d['questions'])} добавлен. Ещё или «✅ Готово».",
                   reply_markup=kb([("✅ Готово", "fmok")], back=("❌ Отмена", "sd:cancel")))


@router.callback_query(F.data == "fmok")
async def form_done(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("questions"):
        return await c.answer("Добавьте хотя бы один вопрос", show_alert=True)
    if d["scope"] == "shared" and not has_perm(c.from_user.id, "forms_edit"):
        return await c.answer("⛔ Нет разрешения", show_alert=True)
    fid = db.add_form(d["title"], d["questions"], None if d["scope"] == "shared" else c.from_user.id)
    await state.clear()
    await c.message.answer(f"✅ Форма «{d['title']}» создана ({len(d['questions'])} вопр.).",
                           reply_markup=kb([("🧾 Открыть", f"fm:{fid}")]))
    await c.answer()