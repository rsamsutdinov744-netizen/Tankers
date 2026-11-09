"""Настройка баллов и шкалы, фразы поддержки, ручное начисление."""
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import logic
from common import kb, show, need, has_perm, uname, can_edit, award, dm, scoring_cfg, one_line
from perms import STATUSES

router = Router()
PRIVATE = F.chat.type == "private"
BACK = ("🔙 В админ-панель", "admin")


class C(StatesGroup):
    value = State()
    phrase_new = State()
    phrase_edit = State()
    pts_amount = State()
    pts_reason = State()


def sc_screen():
    cfg = scoring_cfg()
    f = logic.fmt_num
    rows = [(f"{logic.RES_SHORT[k]}: {f(cfg['res'][k])}", f"sce:res:{k}") for k in logic.RES_SHORT]
    rows += [(f"⭐ Оценка {lo}–{hi}: {f(p)}", f"sce:band:{i}") for i, (lo, hi, p) in enumerate(cfg["bands"])]
    rows.append((f"🏆 Бонус за лучшую работу: {f(cfg['best_bonus'])}", "sce:best:0"))
    rows += [(f"{STATUSES[k]} с {f(cfg['ladder'][k])} баллов", f"sce:lad:{k}") for k in ("member", "best", "lead")]
    rows.append(("⏱ Время удаления заданий", "grace"))
    rows.append(("↩️ Сбросить на стандартные", "sce:reset:0"))
    text = ("🎯 Баллы и уровни\n\nЗа работу = баллы за итог + баллы за оценку. Нажмите на пункт, чтобы изменить.\n"
            "Пороги — сколько баллов нужно для автоповышения.")
    return text, kb(rows, back=BACK)


@router.callback_query(F.data == "sc")
async def sc_open(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    await state.clear()
    text, markup = sc_screen()
    await show(c, text, markup)


PROMPTS = {
    "res": "Введите число баллов за этот итог (например 2 или 1,5):",
    "band": "Введите диапазон оценок и баллы в формате «от-до баллы», например: 4-7 1",
    "best": "Введите бонус за лучшую работу (число):",
    "lad": "Введите, сколько баллов нужно для этого статуса (число):",
}


@router.callback_query(F.data.startswith("sce:"))
async def sc_edit(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    _, kind, arg = c.data.split(":")
    if kind == "reset":
        db.set_json("scoring", {})
        text, markup = sc_screen()
        return await show(c, "↩️ Сброшено на стандартные.\n\n" + text, markup)
    await state.set_state(C.value)
    await state.update_data(kind=kind, arg=arg)
    await c.message.answer(PROMPTS[kind])
    await c.answer()


@router.message(C.value, PRIVATE)
async def sc_save(m: Message, state: FSMContext):
    d = await state.get_data()
    if not has_perm(m.from_user.id, "scoring_edit"):
        await state.clear()
        return await m.answer("⛔ Нет разрешения")
    stored = db.get_json("scoring", {})
    cfg = scoring_cfg()
    kind, arg = d["kind"], d["arg"]
    if kind == "band":
        b = logic.parse_band(m.text)
        if not b:
            return await m.answer("Не понял. Пример: 4-7 1 (оценки от 1 до 10)")
        bands = cfg["bands"]
        bands[int(arg)] = [b[0], b[1], b[2]]
        stored["bands"] = bands
    else:
        x = logic.parse_number(m.text)
        if x is None or x < 0:
            return await m.answer("Нужно неотрицательное число, например 2 или 1,5")
        if kind == "res":
            stored.setdefault("res", {})[arg] = x
        elif kind == "best":
            stored["best_bonus"] = x
        else:
            stored.setdefault("ladder", {})[arg] = x
    db.set_json("scoring", stored)
    await state.clear()
    text, markup = sc_screen()
    await m.answer("✅ Сохранено.\n\n" + text, reply_markup=markup)


@router.callback_query(F.data == "grace")
async def grace_menu(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    cur = db.get_setting("grace_hours", "24")
    await state.set_state(C.value)
    await state.update_data(kind="grace", arg="0")
    await c.message.answer(f"⏱ Сейчас задания удаляются через **{cur} ч** после «выполнено».\n"
                           "Введите новое число часов (можно дробное, например 0.5):", parse_mode="Markdown")
    await c.answer()


def ph_screen():
    rows = [(f"{i}. {one_line(p['text'], 38)}", f"ph:{p['id']}") for i, p in enumerate(db.phrases(), 1)]
    rows.append(("➕ Добавить фразу", "phnew"))
    return ("💬 Фразы поддержки: бот присылает случайную при сдаче работы и при оценке.", kb(rows, back=BACK))


@router.callback_query(F.data == "ph")
async def ph_open(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    await state.clear()
    text, markup = ph_screen()
    await show(c, text, markup)


@router.callback_query(F.data.startswith("ph:"))
async def ph_card(c: CallbackQuery):
    if not await need(c, "scoring_edit"):
        return
    p = db.phrase(int(c.data.split(":")[1]))
    if not p:
        return await c.answer("Не найдено")
    await show(c, f"💬 {p['text']}", kb([("✏️ Изменить", f"phe:{p['id']}"), ("🗑 Удалить", f"phd:{p['id']}")],
                                         back=("⬅️ К фразам", "ph")))


@router.callback_query(F.data == "phnew")
async def ph_new(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    await state.set_state(C.phrase_new)
    await c.message.answer("Отправьте текст новой фразы:")
    await c.answer()


@router.message(C.phrase_new, PRIVATE)
async def ph_new_save(m: Message, state: FSMContext):
    await state.clear()
    if not has_perm(m.from_user.id, "scoring_edit"):
        return await m.answer("⛔ Нет разрешения")
    t = (m.text or "").strip()[:300]
    if not t:
        return await m.answer("Нужен текст.")
    db.add_phrase(t)
    text, markup = ph_screen()
    await m.answer("✅ Добавлено.\n\n" + text, reply_markup=markup)


@router.callback_query(F.data.startswith("phe:"))
async def ph_edit(c: CallbackQuery, state: FSMContext):
    if not await need(c, "scoring_edit"):
        return
    await state.set_state(C.phrase_edit)
    await state.update_data(pid=int(c.data.split(":")[1]))
    await c.message.answer("Отправьте новый текст фразы:")
    await c.answer()


@router.message(C.phrase_edit, PRIVATE)
async def ph_edit_save(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    if not has_perm(m.from_user.id, "scoring_edit"):
        return await m.answer("⛔ Нет разрешения")
    t = (m.text or "").strip()[:300]
    if not t:
        return await m.answer("Нужен текст.")
    db.edit_phrase(d["pid"], t)
    text, markup = ph_screen()
    await m.answer("✅ Изменено.\n\n" + text, reply_markup=markup)


@router.callback_query(F.data.startswith("phd:"))
async def ph_del_ask(c: CallbackQuery):
    if not await need(c, "scoring_edit"):
        return
    pid = int(c.data.split(":")[1])
    await show(c, "Удалить эту фразу?", kb([("✅ Да, удалить", f"phd!:{pid}")], back=("❌ Отмена", f"ph:{pid}")))


@router.callback_query(F.data.startswith("phd!:"))
async def ph_del(c: CallbackQuery):
    if not await need(c, "scoring_edit"):
        return
    db.delete("phrases", int(c.data.split(":")[1]))
    text, markup = ph_screen()
    await show(c, "🗑 Удалено.\n\n" + text, markup)


@router.callback_query(F.data.startswith("pts:"))
async def pts_start(c: CallbackQuery, state: FSMContext):
    if not await need(c, "points_edit"):
        return
    uid = int(c.data.split(":")[1])
    u = db.get_user(uid)
    if not u or not can_edit(c.from_user.id, u):
        return await c.answer("⛔ Нельзя", show_alert=True)
    await state.set_state(C.pts_amount)
    await state.update_data(uid=uid)
    await c.message.answer(f"⭐ {uname(u)}: сейчас {logic.fmt_num(u['points'] or 0)} баллов.\n"
                           "Введите число: положительное — начислить, отрицательное — снять (например 2 или -1,5):")
    await c.answer()


@router.message(C.pts_amount, PRIVATE)
async def pts_amount(m: Message, state: FSMContext):
    x = logic.parse_number(m.text)
    if x is None or x == 0:
        return await m.answer("Нужно число, не равное нулю. Например 2 или -1,5")
    await state.update_data(delta=x)
    await state.set_state(C.pts_reason)
    await m.answer("Теперь напишите причину (её получит участник):")


@router.message(C.pts_reason, PRIVATE)
async def pts_reason(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    u = db.get_user(d["uid"])
    if not has_perm(m.from_user.id, "points_edit") or not u or not can_edit(m.from_user.id, u):
        return await m.answer("⛔ Нельзя")
    reason = (m.text or "").strip()[:300] or "без причины"
    delta = d["delta"]
    total, promo = await award(d["uid"], delta, reason, m.from_user.id)
    if delta > 0:
        text = f"🎉 Вам начислили +{logic.fmt_num(delta)} баллов.\nПричина: {reason}"
    else:
        text = f"⚠️ У вас сняли {logic.fmt_num(abs(delta))} баллов.\nПричина: {reason}"
    text += f"\n⭐ Теперь у вас: {logic.fmt_num(total)}" + (f"\n\n{promo}" if promo else "")
    await dm(bot, d["uid"], text)
    await m.answer(f"✅ Готово. У {uname(u)} теперь {logic.fmt_num(total)} баллов.")