"""Общие помощники: клавиатуры, показ, права, уведомления, задания, баллы, Guard."""
import asyncio
import copy
import logging
import time
from datetime import datetime

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import logic
from perms import LADDER, LEVEL_RANK, POS_NAMES, STATUSES

STATUS_TXT = {"done": "✅ ВЫПОЛНЕНО", "invalid": "❌ НЕ ДЕЙСТВИТЕЛЬНО", "deleted": "🗑 УДАЛЕНО ИЗ ГРУППЫ"}
ICON = {"done": "✅", "invalid": "❌", "deleted": "🗑"}


def grace_seconds():
    return logic.grace_seconds(db.get_setting("grace_hours", "24"))


# ====================== клавиатуры ======================
def _btn(text, data):
    if data.startswith(("http://", "https://", "tg://")):
        return InlineKeyboardButton(text=text, url=data)
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(rows, menu=False, back=None):
    out = []
    for r in rows:
        out.append([_btn(t, d) for t, d in r] if isinstance(r, list) else [_btn(*r)])
    if back:
        out.append([_btn(*back)])
    if menu:
        out.append([_btn("🏠 В меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=out)


async def show(c: CallbackQuery, text, markup=None):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            await c.message.answer(text, reply_markup=markup)
    await c.answer()


def one_line(t, n=40):
    return (t or "").replace("\n", " ")[:n]


def fmt_msk(ts):
    return datetime.fromtimestamp(ts, logic.MSK).strftime("%d.%m %H:%M")


# ====================== пользователи и права ======================
def role_of(uid):
    u = db.get_user(uid)
    return u["role"] if u and u["role"] else None


def has_perm(uid, key):
    u = db.get_user(uid)
    if not u or u["banned"] or not u["role"]:
        return False
    if u["role"] == "owner":
        return True
    return key in db.perms_of(uid)


def any_perm(uid):
    u = db.get_user(uid)
    if not u or u["banned"] or not u["role"]:
        return False
    return u["role"] == "owner" or bool(db.perms_of(uid))


async def need(c: CallbackQuery, perm):
    if has_perm(c.from_user.id, perm):
        return True
    await c.answer("⛔ Нет разрешения", show_alert=True)
    return False


def uname(u):
    return u["name"] or (("@" + u["username"]) if u["username"] else str(u["id"]))


def level_rank(uid):
    u = db.get_user(uid)
    if not u or not u["role"]:
        return 0
    if u["role"] == "owner":
        return 99
    if u["admin_level"]:
        return LEVEL_RANK[u["admin_level"]]
    return 1 if (u["role"] == "admin" or db.perms_of(uid)) else 0


def can_edit(viewer, u):
    if u["role"] == "owner":
        return False
    return role_of(viewer) == "owner" or level_rank(viewer) > level_rank(u["id"])


def status_text(u):
    if u["role"] == "owner":
        return STATUSES["owner"]
    st = u["status"] or "newbie"
    t = STATUSES.get(st, STATUSES["newbie"])
    if st == "lead" and u["position"]:
        t += " (" + POS_NAMES[u["position"]].split(" ", 1)[1].lower() + ")"
    return t


def status_icon(u):
    return status_text(u).split()[0]


# ====================== материалы и меню ======================
def mat_visible(m, uid, pos):
    aud = m["aud"] or "all"
    if aud == "all":
        return True
    if aud.startswith("pos:"):
        return pos == aud[4:]
    if aud.startswith("users:"):
        return str(uid) in aud[6:].split(",")
    return False


def aud_text(aud):
    aud = aud or "all"
    if aud == "all":
        return "🌐 Всем"
    if aud.startswith("pos:"):
        return POS_NAMES.get(aud[4:], aud)
    if aud.startswith("users:"):
        n = len([x for x in aud[6:].split(",") if x])
        return f"👤 Отдельным участникам ({n})"
    return aud


def mat_button(m):
    if (m["kind"] or "text") == "link" and m["url"]:
        return (m["title"] or "Ссылка", m["url"])
    return (m["title"] or "Без названия", f"m:{m['id']}")


def main_menu(uid):
    rows = [("🎨 Должность", "position"), ("📋 Задания", "tasks"),
            ("📚 Материалы", "materials"), ("📢 Объявления", "announcements"),
            ("👤 Профиль", "profile")]
    u = db.get_user(uid)
    for m in db.materials():
        if m["pin"] and mat_visible(m, uid, u["position"] if u else None):
            rows.append(mat_button(m))
    if any_perm(uid):
        rows.append(("⚙️ Админ-панель", "admin"))
    if role_of(uid) == "owner":
        rows.append(("👑 Панель владельца", "owner"))
        rows.append(("💫 Оплата звёздами", "stars"))
    return kb(rows)


# ====================== рассылки ======================
async def notify(bot: Bot, pos, text, exclude=None):
    n = 0
    for u in db.all_users():
        if not u["role"] or u["banned"] or u["id"] == exclude:
            continue
        if pos is not None and u["position"] != pos:
            continue
        try:
            await bot.send_message(u["id"], text)
            n += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return n


async def tell_owner(bot: Bot, text):
    if config.OWNER_ID:
        try:
            await bot.send_message(config.OWNER_ID, text)
        except Exception:
            pass


# ====================== задания ======================
def task_name(t):
    return t["aid"] or f"#{t['id']}"


def task_kb(tid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"fin:{tid}:done"),
        InlineKeyboardButton(text="❌ Не действительно", callback_data=f"fin:{tid}:invalid")]])


async def finish_task(bot: Bot, t, status):
    now = int(time.time())
    name = task_name(t)
    if status == "done":
        db.mark_done(t["id"], now)
        grace = grace_seconds()
        suffix = f"✅ ВЫПОЛНЕНО. Работы ещё принимаются {grace // 3600} ч (до {fmt_msk(now + grace)} МСК)"
        msg = f"✅ Задание {name} выполнено. Работы по нему ещё принимаются {grace // 3600} ч."
    elif status == "invalid":
        db.set_task_status(t["id"], "invalid")
        suffix = STATUS_TXT["invalid"]
        msg = f"❌ Задание {name} отмечено как не действительное."
    else:
        db.set_task_status(t["id"], "deleted")
        suffix = None
        msg = f"🗑 Задание {name} удалено."
    if suffix and t["chat_id"] and t["msg_id"]:
        try:
            if t["src"] == "bot":
                await bot.edit_message_text(f"{t['text']}\n\n{suffix}", chat_id=t["chat_id"],
                                            message_id=t["msg_id"], reply_markup=None)
            else:
                sent = await bot.send_message(t["chat_id"], suffix, reply_to_message_id=t["msg_id"],
                                              allow_sending_without_reply=True)
                db.set_note_msg(t["id"], sent.message_id)
        except Exception as e:
            logging.warning("Не удалось пометить сообщение: %s", e)
    await notify(bot, t["pos"], f"{msg}\n\n{one_line(t['text'], 120)}")


async def finalize_task(bot: Bot, t):
    db.set_final(t["id"])
    for mid in (t["msg_id"], t["note_msg"]):
        if mid and t["chat_id"]:
            try:
                await bot.delete_message(t["chat_id"], mid)
            except Exception as e:
                logging.info("Не удалось удалить сообщение %s: %s", mid, e)
                try:
                    await tell_owner(bot, f"⚠️ Не удалось удалить задание {task_name(t)} в группе: {e}. "
                                          "Проверьте, что бот — админ с правом «Удалять сообщения».")
                except Exception:
                    pass


_GONE = ("message to edit not found", "message to react not found", "message_id_invalid", "message not found")


async def message_exists(bot: Bot, t):
    try:
        if t["src"] == "bot":
            await bot.edit_message_reply_markup(chat_id=t["chat_id"], message_id=t["msg_id"],
                                                reply_markup=task_kb(t["id"]))
        else:
            await bot.set_message_reaction(chat_id=t["chat_id"], message_id=t["msg_id"], reaction=[])
        return True
    except TelegramBadRequest as e:
        s = str(e).lower()
        if any(k in s for k in _GONE):
            return False
        if "not modified" in s:
            return True
        logging.info("Проверка сообщения %s: %s", t["msg_id"], e)
        return None
    except Exception:
        return None


async def check_deleted_once(bot: Bot):
    n = 0
    for t in db.probe_tasks():
        if await message_exists(bot, t) is False:
            await finish_task(bot, db.task(t["id"]), "deleted")
            n += 1
        await asyncio.sleep(0.2)
    return n


async def finalize_due(bot: Bot):
    for t in db.tasks_due_final(int(time.time())):
        await finalize_task(bot, t)


# ====================== баллы ======================
def scoring_cfg():
    cfg = copy.deepcopy(logic.DEFAULT_SCORING)
    stored = db.get_json("scoring", {})
    for k in ("res", "ladder"):
        cfg[k].update(stored.get(k, {}))
    for k in ("bands", "best_bonus"):
        if k in stored:
            cfg[k] = stored[k]
    return cfg


async def award(uid, delta, reason, by=None, sub_id=None):
    total = db.add_points(uid, delta, reason, by, sub_id)
    promo = None
    u = db.get_user(uid)
    if delta > 0 and u and u["role"] != "owner":
        new = logic.promoted(total, u["status"] or "newbie", scoring_cfg()["ladder"], LADDER)
        if new:
            db.set_user_status(uid, new)
            promo = f"🎉 Поздравляем! Вы повышены: {status_text(db.get_user(uid))}"
    return total, promo


async def dm(bot: Bot, uid, text):
    try:
        await bot.send_message(uid, text)
    except Exception:
        pass


# ====================== Guard ======================
class Guard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if isinstance(event, Message):
            chat = event.chat
        else:
            chat = event.message.chat if getattr(event, "message", None) else None
        if user and chat and chat.type == "private":
            db.touch_user(user.id, user.full_name, user.username)
            if db.is_banned(user.id):
                if isinstance(event, CallbackQuery):
                    await event.answer("⛔ Вы заблокированы", show_alert=True)
                else:
                    await event.answer("⛔ Вы заблокированы в этом боте.")
                return
            if isinstance(event, CallbackQuery) and not role_of(user.id):
                await event.answer("Сначала войдите: отправьте /start", show_alert=True)
                return
        return await handler(event, data)