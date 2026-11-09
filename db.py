"""SQLite: все таблицы и функции."""
import json
import random
import re
import sqlite3
import time
from config import DB_PATH, SOURCE_GROUP_ID, SOURCE_TOPIC_ID
from logic import grace_seconds

_c = sqlite3.connect(DB_PATH, check_same_thread=False)
_c.row_factory = sqlite3.Row
_c.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, role TEXT, position TEXT, status TEXT DEFAULT 'newbie',
    perms TEXT, name TEXT, username TEXT, banned INTEGER DEFAULT 0, ban_src TEXT,
    points REAL DEFAULT 0, admin_level TEXT);
CREATE TABLE IF NOT EXISTS materials(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, title TEXT, aud TEXT DEFAULT 'all',
    kind TEXT DEFAULT 'text', file_id TEXT, url TEXT, pin INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, aid TEXT, pos TEXT, chat_id INTEGER,
    msg_id INTEGER, src TEXT, status TEXT DEFAULT 'active', done_at INTEGER, final INTEGER DEFAULT 0, note_msg INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, weekly TEXT, dates TEXT,
    chat_id INTEGER, thread INTEGER, active INTEGER DEFAULT 1, created_by INTEGER, dm_users TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS reminder_log(rid INTEGER, key TEXT, PRIMARY KEY(rid, key));
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, uid INTEGER, text TEXT,
    files TEXT, form_title TEXT, created INTEGER, status TEXT DEFAULT 'pending', rating INTEGER, result TEXT,
    comment TEXT, points REAL, reviewer INTEGER, best INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS review_msgs(sub_id INTEGER, chat_id INTEGER, msg_id INTEGER);
CREATE TABLE IF NOT EXISTS forms(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, questions TEXT, owner INTEGER);
CREATE TABLE IF NOT EXISTS phrases(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT);
CREATE TABLE IF NOT EXISTS points_log(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, delta REAL, reason TEXT,
    by_uid INTEGER, ts INTEGER, sub_id INTEGER);
CREATE TABLE IF NOT EXISTS passwords(id INTEGER PRIMARY KEY AUTOINCREMENT, password TEXT UNIQUE NOT NULL,
    role TEXT, perms TEXT, note TEXT, created_by INTEGER);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount INTEGER, reason TEXT,
    task_id INTEGER, period TEXT, created_by INTEGER, created_at INTEGER, status TEXT DEFAULT 'pending');
""")


def _migrate(table, cols):
    have = [r["name"] for r in _c.execute(f"PRAGMA table_info({table})")]
    for col, ddl in cols.items():
        if col not in have:
            _c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


_migrate("tasks", {"aid": "TEXT", "pos": "TEXT", "chat_id": "INTEGER", "msg_id": "INTEGER", "src": "TEXT",
                   "status": "TEXT DEFAULT 'active'", "done_at": "INTEGER", "final": "INTEGER DEFAULT 0",
                   "note_msg": "INTEGER"})
_migrate("users", {"status": "TEXT DEFAULT 'newbie'", "perms": "TEXT", "name": "TEXT", "username": "TEXT",
                   "banned": "INTEGER DEFAULT 0", "ban_src": "TEXT", "points": "REAL DEFAULT 0",
                   "admin_level": "TEXT"})
_migrate("materials", {"title": "TEXT", "aud": "TEXT DEFAULT 'all'", "kind": "TEXT DEFAULT 'text'",
                       "file_id": "TEXT", "url": "TEXT", "pin": "INTEGER DEFAULT 0"})
_migrate("reminders", {"dm_users": "TEXT DEFAULT '[]'"})
_c.execute("UPDATE materials SET title=substr(text,1,30) WHERE title IS NULL")
_c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_aid ON tasks(aid) WHERE aid IS NOT NULL")
_c.commit()

DEFAULT_PHRASES = [
    "Отличная работа! Так держать! 💪",
    "Каждый шаг приближает нас к готовой игре 🚀",
    "Ты молодец, что не сдаёшься! 🌟",
    "Спасибо за твой вклад, команда тебя ценит ❤️",
    "Ещё немного, и танки поедут! 🛡️",
    "Ошибки — часть пути. Ты уже на шаг впереди! 😉",
    "Классный результат! Продолжай в том же духе 🔥",
    "Твоя работа делает нашу игру лучше 🎮",
    "Гордимся тобой! Отдохни и возвращайся с новыми силами ☕",
    "Вместе мы делаем невозможное возможным! 🤝",
]
if _c.execute("SELECT COUNT(*) FROM phrases").fetchone()[0] == 0:
    _c.executemany("INSERT INTO phrases(text) VALUES(?)", [(p,) for p in DEFAULT_PHRASES])
    _c.commit()


# ---------- пользователи ----------
def touch_user(uid, name, username):
    _c.execute("INSERT INTO users(id, name, username) VALUES(?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username", (uid, name, username))
    _c.commit()


def get_user(uid):
    return _c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def all_users():
    return _c.execute("SELECT * FROM users").fetchall()


def members():
    return _c.execute("SELECT * FROM users WHERE role IS NOT NULL OR banned=1 ORDER BY id").fetchall()


def banned_users():
    return _c.execute("SELECT * FROM users WHERE banned=1 ORDER BY id").fetchall()


def set_role(uid, role):
    _c.execute("INSERT INTO users(id, role) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET role=excluded.role", (uid, role))
    _c.commit()


def clear_role(uid):
    _c.execute("UPDATE users SET role=NULL WHERE id=?", (uid,))
    _c.commit()


def set_position(uid, pos):
    _c.execute("UPDATE users SET position=? WHERE id=?", (pos, uid))
    _c.commit()


def set_user_status(uid, status):
    _c.execute("UPDATE users SET status=? WHERE id=?", (status, uid))
    _c.commit()


def set_level(uid, level):
    _c.execute("UPDATE users SET admin_level=? WHERE id=?", (level, uid))
    _c.commit()


def perms_of(uid):
    u = get_user(uid)
    return set(filter(None, (u["perms"] or "").split(","))) if u else set()


def set_perms(uid, perms):
    val = None if perms is None else ",".join(sorted(perms))
    _c.execute("UPDATE users SET perms=? WHERE id=?", (val, uid))
    _c.commit()


def ban(uid, src):
    _c.execute("INSERT INTO users(id, banned, ban_src) VALUES(?,1,?) "
               "ON CONFLICT(id) DO UPDATE SET banned=1, ban_src=excluded.ban_src", (uid, src))
    _c.commit()


def unban(uid):
    _c.execute("UPDATE users SET banned=0, ban_src=NULL, perms='', admin_level=NULL, "
               "role=CASE WHEN role='admin' THEN 'user' ELSE role END WHERE id=?", (uid,))
    for r in _c.execute("SELECT key FROM settings WHERE key LIKE 'curator:%' AND value=?", (str(uid),)).fetchall():
        _c.execute("DELETE FROM settings WHERE key=?", (r["key"],))
    _c.commit()


def is_banned(uid):
    u = get_user(uid)
    return bool(u and u["banned"])


# ---------- пароли ----------
def passwords_list():
    return _c.execute("SELECT * FROM passwords ORDER BY id").fetchall()


def password_get(pwd):
    return _c.execute("SELECT * FROM passwords WHERE password=?", (pwd,)).fetchone()


def password_add(pwd, role, perms, note, by):
    try:
        cur = _c.execute("INSERT INTO passwords(password, role, perms, note, created_by) VALUES(?,?,?,?,?)",
                         (pwd, role, ",".join(sorted(perms)) if perms else None, note, by))
        _c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def password_update(pid, field, value):
    assert field in ("password", "role", "perms", "note")
    _c.execute(f"UPDATE passwords SET {field}=? WHERE id=?", (value, pid))
    _c.commit()


def password_delete(pid):
    _c.execute("DELETE FROM passwords WHERE id=?", (pid,))
    _c.commit()


# ---------- баллы ----------
def points_of(uid):
    u = get_user(uid)
    return float(u["points"] or 0) if u else 0.0


def add_points(uid, delta, reason, by=None, sub_id=None):
    _c.execute("UPDATE users SET points=MAX(0, COALESCE(points,0)+?) WHERE id=?", (delta, uid))
    _c.execute("INSERT INTO points_log(uid, delta, reason, by_uid, ts, sub_id) VALUES(?,?,?,?,?,?)",
               (uid, delta, reason, by, int(time.time()), sub_id))
    _c.commit()
    return points_of(uid)


def user_stats(uid):
    r = _c.execute("SELECT COUNT(*) AS cnt, SUM(status='reviewed') AS reviewed, SUM(result='done') AS r_done, "
                   "SUM(result='part') AS r_part, SUM(result='wrong') AS r_wrong, SUM(result='bad') AS r_bad, "
                   "AVG(rating) AS avg_rating, SUM(best) AS best_cnt FROM submissions WHERE uid=?", (uid,)).fetchone()
    return {k: (r[k] or 0) for k in r.keys()}


def top_users(limit=10, worst=False):
    order = "ASC" if worst else "DESC"
    return _c.execute(f"SELECT * FROM users WHERE role IS NOT NULL AND banned=0 ORDER BY points {order} LIMIT ?",
                      (limit,)).fetchall()


# ---------- общие ----------
def add(table, text):
    _c.execute(f"INSERT INTO {table}(text) VALUES(?)", (text,))
    _c.commit()


def delete(table, rid):
    _c.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    _c.commit()


def all_rows(table, limit=50):
    return _c.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def count(table):
    return _c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- материалы ----------
def add_material(title, text, kind="text", file_id=None, url=None):
    cur = _c.execute("INSERT INTO materials(title, text, aud, kind, file_id, url) VALUES(?,?,'all',?,?,?)",
                     (title, text, kind, file_id, url))
    _c.commit()
    return cur.lastrowid


def material(mid):
    return _c.execute("SELECT * FROM materials WHERE id=?", (mid,)).fetchone()


def materials():
    return _c.execute("SELECT * FROM materials ORDER BY id").fetchall()


def set_material(mid, field, value):
    assert field in ("title", "text", "aud", "file_id", "url", "pin", "kind")
    _c.execute(f"UPDATE materials SET {field}=? WHERE id=?", (value, mid))
    _c.commit()


# ---------- задания ----------
_OPEN = "(status='active' OR (status='done' AND COALESCE(final,0)=0))"


def add_task(text, aid=None, pos=None, chat_id=None, msg_id=None, src="group"):
    try:
        cur = _c.execute("INSERT INTO tasks(text, aid, pos, chat_id, msg_id, src, status) VALUES(?,?,?,?,?,?,'active')",
                         (text, aid, pos, chat_id, msg_id, src))
        _c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def update_task(tid, text, aid, pos):
    try:
        _c.execute("UPDATE tasks SET text=?, aid=?, pos=? WHERE id=?", (text, aid, pos, tid))
    except sqlite3.IntegrityError:
        _c.execute("UPDATE tasks SET text=?, pos=? WHERE id=?", (text, pos, tid))
    _c.commit()


def set_msg(tid, chat_id, msg_id):
    _c.execute("UPDATE tasks SET chat_id=?, msg_id=? WHERE id=?", (chat_id, msg_id, tid))
    _c.commit()


def set_note_msg(tid, msg_id):
    _c.execute("UPDATE tasks SET note_msg=? WHERE id=?", (msg_id, tid))
    _c.commit()


def task(tid):
    return _c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()


def task_by_msg(chat_id, msg_id):
    return _c.execute("SELECT * FROM tasks WHERE chat_id=? AND msg_id=?", (chat_id, msg_id)).fetchone()


def set_task_status(tid, status):
    _c.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
    _c.commit()


def mark_done(tid, ts):
    _c.execute("UPDATE tasks SET status='done', done_at=?, final=0 WHERE id=?", (ts, tid))
    _c.commit()


def set_final(tid):
    _c.execute("UPDATE tasks SET final=1 WHERE id=?", (tid,))
    _c.commit()


def task_is_open(t):
    return bool(t) and (t["status"] == "active" or (t["status"] == "done" and not t["final"]))


def tasks_due_final(now):
    hours = get_setting("grace_hours", "24")
    return _c.execute("SELECT * FROM tasks WHERE status='done' AND COALESCE(final,0)=0 "
                      "AND done_at IS NOT NULL AND done_at<=?", (now - grace_seconds(hours),)).fetchall()


def probe_tasks():
    return _c.execute("SELECT * FROM tasks WHERE status='active' AND chat_id IS NOT NULL AND msg_id IS NOT NULL").fetchall()


def active_bot_tasks():
    return _c.execute("SELECT * FROM tasks WHERE status='active' AND src='bot' AND msg_id IS NOT NULL").fetchall()


def open_tasks(limit=200):
    return _c.execute(f"SELECT * FROM tasks WHERE {_OPEN} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def list_tasks(active, limit=15):
    return _c.execute(f"SELECT * FROM tasks WHERE {'' if active else 'NOT '}{_OPEN} ORDER BY id DESC LIMIT ?",
                      (limit,)).fetchall()


def count_active():
    return _c.execute("SELECT COUNT(*) FROM tasks WHERE status='active'").fetchone()[0]


def next_aid_number():
    nums = []
    for r in _c.execute("SELECT aid FROM tasks WHERE aid IS NOT NULL"):
        m = re.match(r"aid(\d+)_", r["aid"])
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


# ---------- работы ----------
SUB_FIELDS = {"status", "rating", "result", "comment", "points", "reviewer", "best"}


def add_submission(task_id, uid, text, files, form_title=None):
    cur = _c.execute("INSERT INTO submissions(task_id, uid, text, files, form_title, created) VALUES(?,?,?,?,?,?)",
                     (task_id, uid, text, json.dumps(files), form_title, int(time.time())))
    _c.commit()
    return cur.lastrowid


def submission(sid):
    return _c.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()


def submissions_for_task(tid):
    return _c.execute("SELECT * FROM submissions WHERE task_id=? ORDER BY id", (tid,)).fetchall()


def pending_submissions(limit=15):
    return _c.execute("SELECT * FROM submissions WHERE status='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()


def update_submission(sid, **fields):
    assert fields and set(fields) <= SUB_FIELDS
    sets = ", ".join(f"{k}=?" for k in fields)
    _c.execute(f"UPDATE submissions SET {sets} WHERE id=?", (*fields.values(), sid))
    _c.commit()


def add_review_msg(sid, chat_id, msg_id):
    _c.execute("INSERT INTO review_msgs(sub_id, chat_id, msg_id) VALUES(?,?,?)", (sid, chat_id, msg_id))
    _c.commit()


def review_msgs(sid):
    return _c.execute("SELECT * FROM review_msgs WHERE sub_id=?", (sid,)).fetchall()


def task_best(tid):
    return _c.execute("SELECT * FROM submissions WHERE task_id=? AND best=1", (tid,)).fetchone()


# ---------- формы ----------
def add_form(title, questions, owner):
    cur = _c.execute("INSERT INTO forms(title, questions, owner) VALUES(?,?,?)", (title, json.dumps(questions), owner))
    _c.commit()
    return cur.lastrowid


def form(fid):
    return _c.execute("SELECT * FROM forms WHERE id=?", (fid,)).fetchone()


def forms_visible(uid):
    return _c.execute("SELECT * FROM forms WHERE owner IS NULL OR owner=? ORDER BY id", (uid,)).fetchall()


def shared_forms():
    return _c.execute("SELECT * FROM forms WHERE owner IS NULL ORDER BY id").fetchall()


def own_forms(uid):
    return _c.execute("SELECT * FROM forms WHERE owner=? ORDER BY id", (uid,)).fetchall()


def rename_form(fid, title):
    _c.execute("UPDATE forms SET title=? WHERE id=?", (title, fid))
    _c.commit()


# ---------- фразы ----------
def phrases():
    return _c.execute("SELECT * FROM phrases ORDER BY id").fetchall()


def phrase(pid):
    return _c.execute("SELECT * FROM phrases WHERE id=?", (pid,)).fetchone()


def add_phrase(text):
    _c.execute("INSERT INTO phrases(text) VALUES(?)", (text,))
    _c.commit()


def edit_phrase(pid, text):
    _c.execute("UPDATE phrases SET text=? WHERE id=?", (text, pid))
    _c.commit()


def random_phrase():
    rows = phrases()
    return random.choice(rows)["text"] if rows else ""


# ---------- напоминания ----------
def add_reminder(text, weekly, dates, chat_id, thread, by, dm_users=None):
    cur = _c.execute("INSERT INTO reminders(text, weekly, dates, chat_id, thread, active, created_by, dm_users) "
                     "VALUES(?,?,?,?,?,1,?,?)",
                     (text, json.dumps(weekly), json.dumps(dates), chat_id, thread, by,
                      json.dumps(dm_users or [])))
    _c.commit()
    return cur.lastrowid


def reminders():
    return _c.execute("SELECT * FROM reminders ORDER BY id").fetchall()


def reminder(rid):
    return _c.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()


def set_reminder_active(rid, v):
    _c.execute("UPDATE reminders SET active=? WHERE id=?", (v, rid))
    _c.commit()


def delete_reminder(rid):
    _c.execute("DELETE FROM reminders WHERE id=?", (rid,))
    _c.execute("DELETE FROM reminder_log WHERE rid=?", (rid,))
    _c.commit()


def fired_keys(rid, keep_from_iso):
    _c.execute("DELETE FROM reminder_log WHERE rid=? AND key LIKE 'w:%' AND substr(key,3,10)<?", (rid, keep_from_iso))
    return {r["key"] for r in _c.execute("SELECT key FROM reminder_log WHERE rid=?", (rid,))}


def mark_fired(rid, key):
    _c.execute("INSERT OR IGNORE INTO reminder_log(rid, key) VALUES(?,?)", (rid, key))
    _c.commit()


# ---------- платежи ----------
def add_payment(uid, amount, reason, task_id=None, period=None, by=None):
    cur = _c.execute("INSERT INTO payments(uid, amount, reason, task_id, period, created_by, created_at) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (uid, amount, reason, task_id, period, by, int(time.time())))
    _c.commit()
    return cur.lastrowid


def payments_list(limit=50):
    return _c.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---------- настройки ----------
def get_setting(key, default=None):
    r = _c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key, value):
    _c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    _c.commit()


def del_setting(key):
    _c.execute("DELETE FROM settings WHERE key=?", (key,))
    _c.commit()


def get_json(key, default):
    v = get_setting(key)
    return json.loads(v) if v else default


def set_json(key, value):
    set_setting(key, json.dumps(value))


# ---------- группы ----------
def add_chat(cid, title):
    _c.execute("INSERT INTO chats(id,title) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title", (cid, title))
    _c.commit()


def remove_chat(cid):
    _c.execute("DELETE FROM chats WHERE id=?", (cid,))
    _c.commit()


def chats():
    return _c.execute("SELECT * FROM chats ORDER BY title").fetchall()


def chat_title(cid):
    r = _c.execute("SELECT title FROM chats WHERE id=?", (cid,)).fetchone()
    return r["title"] if r else str(cid)


def dest():
    chat = int(get_setting("dest_chat", SOURCE_GROUP_ID))
    if not chat:
        return None
    return chat, int(get_setting("dest_thread", SOURCE_TOPIC_ID))