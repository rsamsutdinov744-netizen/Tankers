"""Материалы и кнопки: текст, картинка, файл или ссылка; аудитория; закрепление."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
from common import kb, show, need, has_perm, uname, mat_visible, aud_text, mat_button
from perms import POS_NAMES

router = Router()
PAGE = 8
KINDS = {"text": "📝 Текст", "photo": "🖼 Картинка", "doc": "📎 Файл", "link": "🔗 Ссылка"}
BACK = ("🔙 В админ-панель", "admin")


class M(StatesGroup):
    title = State()
    content = State()
    rename = State()
    recontent = State()


def extract(m: Message, kind):
    if kind == "text":
        t = m.text or m.caption
        return (t, None, None) if t else None
    if kind == "photo" and m.photo:
        return (m.caption or "", m.photo[-1].file_id, None)
    if kind == "doc" and m.document:
        return (m.caption or "", m.document.file_id, None)
    if kind == "link":
        t = (m.text or "").strip()
        if t.startswith(("http://", "https://")):
            return ("", None, t)
    return None


PROMPT = {"text": "Отправьте текст материала:",
          "photo": "Отправьте картинку (подпись станет текстом под ней):",
          "doc": "Отправьте файл (подпись станет текстом под ним):",
          "link": "Отправьте ссылку (начинается с http:// или https://):"}


@router.callback_query(F.data == "materials")
async def materials(c: CallbackQuery):
    u = db.get_user(c.from_user.id)
    rows = [m for m in db.materials() if mat_visible(m, c.from_user.id, u["position"])]
    if not rows:
        return await show(c, "📭 Для вас пока нет материалов", kb([], menu=True))
    await show(c, "📚 Материалы:", kb([mat_button(m) for m in rows], menu=True))


@router.callback_query(F.data.startswith("m:"))
async def material_view(c: CallbackQuery):
    m = db.material(int(c.data.split(":")[1]))
    u = db.get_user(c.from_user.id)
    if not m or not mat_visible(m, c.from_user.id, u["position"]):
        return await c.answer("Материал недоступен", show_alert=True)
    kind = m["kind"] or "text"
    caption = m["title"] + (f"\n\n{m['text']}" if m["text"] else "")
    if kind == "photo":
        await c.message.answer_photo(m["file_id"], caption=caption[:1024])
        return await c.answer()
    if kind == "doc":
        await c.message.answer_document(m["file_id"], caption=caption[:1024])
        return await c.answer()
    if kind == "link":
        await c.message.answer(f"{m['title']}\n{m['url']}")
        return await c.answer()
    await show(c, f"📚 {m['title']}\n\n{m['text']}", kb([("⬅️ К материалам", "materials")], menu=True))


def card(mid):
    m = db.material(mid)
    if not m:
        return "Материал не найден", kb([], back=("⬅️ К списку", "mm"))
    kind = m["kind"] or "text"
    head = f"{KINDS[kind]}: {m['title']}\nКому показывать: {aud_text(m['aud'])}\n"
    head += f"📌 В главном меню: {'да' if m['pin'] else 'нет'}\n"
    body = m["url"] if kind == "link" else (m["text"] or "")[:500]
    rows = [("✏️ Название кнопки", f"mr:{mid}"), ("📝 Содержимое", f"mt:{mid}"),
            ("👁 Кому показывать", f"ma:{mid}"),
            (("📌 Открепить от меню" if m["pin"] else "📌 Показать в главном меню"), f"mpin:{mid}"),
            ("🗑 Удалить", f"md:{mid}")]
    return head + "\n" + body, kb(rows, back=("⬅️ К списку", "mm"))


def aud_screen(mid):
    m = db.material(mid)
    cur = m["aud"] or "all"
    mark = lambda key, label: (("☑ " if (cur == key or (key == "users" and cur.startswith("users:"))) else "") + label)
    return (f"👁 Кому показывать «{m['title']}»?",
            kb([(mark("all", "🌐 Всем"), f"mau:{mid}:all")]
               + [(mark(f"pos:{k}", v), f"mau:{mid}:pos:{k}") for k, v in POS_NAMES.items()]
               + [(mark("users", "👤 Отдельным участникам"), f"mau:{mid}:users")],
               back=("✅ Готово", f"mm:{mid}")))


def picker_screen(mid, page):
    m = db.material(mid)
    aud = m["aud"] or ""
    chosen = set(x for x in aud[6:].split(",") if x) if aud.startswith("users:") else set()
    rows = [u for u in db.members() if not u["banned"] and u["role"]]
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    btns = [(("☑ " if str(u["id"]) in chosen else "⬜ ") + uname(u), f"mut:{mid}:{u['id']}:{page}")
            for u in rows[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"mup:{mid}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("➡️", f"mup:{mid}:{page + 1}"))
    btns.append(nav)
    return (f"👤 Кому показывать «{m['title']}»: отметьте участников (выбрано: {len(chosen)})",
            kb(btns, back=("✅ Готово", f"mm:{mid}")))


@router.callback_query(F.data == "mm")
async def mat_list(c: CallbackQuery, state: FSMContext):
    if not await need(c, "materials_edit"):
        return
    await state.clear()
    rows = [(("📌 " if m["pin"] else "") + mat_button(m)[0], f"mm:{m['id']}") for m in db.materials()]
    rows.append(("➕ Новая кнопка / материал", "mnew"))
    await show(c, "📚 Материалы и кнопки (управление):", kb(rows, back=BACK))


@router.callback_query(F.data.startswith("mm:"))
async def mat_open(c: CallbackQuery, state: FSMContext):
    if not await need(c, "materials_edit"):
        return
    await state.clear()
    text, markup = card(int(c.data.split(":")[1]))
    await show(c, text, markup)


@router.callback_query(F.data == "mnew")
async def mat_new(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    await show(c, "Что откроется по нажатию кнопки?",
               kb([(v, f"mk:{k}") for k, v in KINDS.items()], back=("🔙 Назад", "mm")))


@router.callback_query(F.data.startswith("mk:"))
async def mat_kind(c: CallbackQuery, state: FSMContext):
    if not await need(c, "materials_edit"):
        return
    await state.set_state(M.title)
    await state.update_data(kind=c.data.split(":")[1])
    await c.message.answer("Введите название кнопки:")
    await c.answer()


@router.message(M.title)
async def mat_new_title(m: Message, state: FSMContext):
    if not has_perm(m.from_user.id, "materials_edit"):
        await state.clear()
        return await m.answer("⛔ Нет разрешения")
    title = (m.text or "").strip()[:60]
    if not title:
        return await m.answer("Нужно текстовое название.")
    d = await state.get_data()
    await state.update_data(title=title)
    await state.set_state(M.content)
    await m.answer(PROMPT[d["kind"]])


@router.message(M.content)
async def mat_new_content(m: Message, state: FSMContext):
    d = await state.get_data()
    if not has_perm(m.from_user.id, "materials_edit"):
        await state.clear()
        return await m.answer("⛔ Нет разрешения")
    got = extract(m, d["kind"])
    if not got:
        return await m.answer("Не подходит. " + PROMPT[d["kind"]])
    await state.clear()
    text, file_id, url = got
    mid = db.add_material(d["title"], text, d["kind"], file_id, url)
    text, markup = aud_screen(mid)
    await m.answer("✅ Создано. " + text, reply_markup=markup)


@router.callback_query(F.data.startswith("mr:"))
async def mat_rename(c: CallbackQuery, state: FSMContext):
    if not await need(c, "materials_edit"):
        return
    await state.set_state(M.rename)
    await state.update_data(mid=int(c.data.split(":")[1]))
    await c.message.answer("Введите новое название кнопки:")
    await c.answer()


@router.message(M.rename)
async def mat_rename_save(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    if not has_perm(m.from_user.id, "materials_edit"):
        return await m.answer("⛔ Нет разрешения")
    db.set_material(d["mid"], "title", (m.text or "").strip()[:60])
    text, markup = card(d["mid"])
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("mt:"))
async def mat_recontent(c: CallbackQuery, state: FSMContext):
    if not await need(c, "materials_edit"):
        return
    mid = int(c.data.split(":")[1])
    await state.set_state(M.recontent)
    await state.update_data(mid=mid)
    await c.message.answer(PROMPT[db.material(mid)["kind"] or "text"])
    await c.answer()


@router.message(M.recontent)
async def mat_recontent_save(m: Message, state: FSMContext):
    d = await state.get_data()
    if not has_perm(m.from_user.id, "materials_edit"):
        await state.clear()
        return await m.answer("⛔ Нет разрешения")
    kind = db.material(d["mid"])["kind"] or "text"
    got = extract(m, kind)
    if not got:
        return await m.answer("Не подходит. " + PROMPT[kind])
    await state.clear()
    text, file_id, url = got
    db.set_material(d["mid"], "text", text)
    db.set_material(d["mid"], "file_id", file_id)
    db.set_material(d["mid"], "url", url)
    text, markup = card(d["mid"])
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("mpin:"))
async def mat_pin(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    mid = int(c.data.split(":")[1])
    db.set_material(mid, "pin", 0 if db.material(mid)["pin"] else 1)
    text, markup = card(mid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("ma:"))
async def mat_aud(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    text, markup = aud_screen(int(c.data.split(":")[1]))
    await show(c, text, markup)


@router.callback_query(F.data.startswith("mau:"))
async def mat_aud_set(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    _, mid, rest = c.data.split(":", 2)
    mid = int(mid)
    if rest == "users":
        if not (db.material(mid)["aud"] or "").startswith("users:"):
            db.set_material(mid, "aud", "users:")
        text, markup = picker_screen(mid, 0)
    else:
        db.set_material(mid, "aud", rest)
        text, markup = aud_screen(mid)
    await show(c, text, markup)


@router.callback_query(F.data.startswith("mup:"))
async def mat_picker(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    _, mid, page = c.data.split(":")
    text, markup = picker_screen(int(mid), int(page))
    await show(c, text, markup)


@router.callback_query(F.data.startswith("mut:"))
async def mat_picker_toggle(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    _, mid, uid, page = c.data.split(":")
    mid = int(mid)
    aud = db.material(mid)["aud"] or ""
    chosen = set(x for x in aud[6:].split(",") if x) if aud.startswith("users:") else set()
    chosen ^= {uid}
    db.set_material(mid, "aud", "users:" + ",".join(sorted(chosen)))
    text, markup = picker_screen(mid, int(page))
    await show(c, text, markup)


@router.callback_query(F.data.startswith("md:"))
async def mat_delete_ask(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    mid = int(c.data.split(":")[1])
    m = db.material(mid)
    await show(c, f"Удалить «{m['title'] if m else mid}»?",
               kb([("✅ Да, удалить", f"md!:{mid}")], back=("❌ Отмена", f"mm:{mid}")))


@router.callback_query(F.data.startswith("md!:"))
async def mat_delete(c: CallbackQuery):
    if not await need(c, "materials_edit"):
        return
    db.delete("materials", int(c.data.split(":")[1]))
    await show(c, "🗑 Удалено", kb([], back=("⬅️ К списку", "mm")))