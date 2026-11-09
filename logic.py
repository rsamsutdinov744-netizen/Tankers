"""Чистая логика: время, расписания, баллы, характеристики, звёзды."""
import calendar
import math
import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WD_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
          "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
LATE = timedelta(minutes=15)


def now_msk():
    return datetime.now(MSK)


def grace_seconds(hours=24):
    try:
        return int(float(hours) * 3600)
    except (TypeError, ValueError):
        return 24 * 3600


def parse_time(s):
    m = re.fullmatch(r"\s*(\d{1,2})[:.\s](\d{2})\s*", s or "")
    if not m:
        return None
    h, mi = int(m[1]), int(m[2])
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def parse_datetime(s):
    s = s or ""
    m = re.fullmatch(r"\s*(\d{1,2})[./](\d{1,2})[./](\d{4})\s*(?:в\s*)?(\d{1,2})[:.](\d{2})\s*", s)
    if m:
        d, mo, y, h, mi = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2})[:.](\d{2})\s*", s)
        if not m:
            return None
        y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=MSK)
    except ValueError:
        return None


def parse_number(s):
    try:
        x = float((s or "").strip().replace(",", "."))
    except ValueError:
        return None
    return round(x, 2) if math.isfinite(x) else None


def parse_band(s):
    m = re.fullmatch(r"\s*(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+([\d.,]+)\s*", s or "")
    if not m:
        return None
    lo, hi, pts = int(m[1]), int(m[2]), parse_number(m[3])
    if pts is None or not (1 <= lo <= hi <= 10):
        return None
    return lo, hi, pts


def fmt_num(x):
    return ("%g" % round(float(x or 0), 2)).replace(".", ",")


def slot_dt(iso, t):
    h, m = map(int, t.split(":"))
    y, mo, d = map(int, iso.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=MSK)


def fmt_date(iso):
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def due_keys(weekly, dates, now, fired, late=LATE):
    out = []
    for back in (0, 1):
        d = (now - timedelta(days=back)).date()
        t = weekly.get(str(d.weekday()))
        if t:
            iso = d.isoformat()
            key = f"w:{iso}:{t}"
            if slot_dt(iso, t) <= now <= slot_dt(iso, t) + late and key not in fired:
                out.append(key)
    for iso, t in dates.items():
        key = f"d:{iso}:{t}"
        if slot_dt(iso, t) <= now <= slot_dt(iso, t) + late and key not in fired:
            out.append(key)
    return out


def finished(weekly, dates, now, fired=(), late=LATE):
    if weekly or not dates:
        return False
    return all(f"d:{d}:{t}" in fired or slot_dt(d, t) + late < now for d, t in dates.items())


def next_fire(weekly, dates, now):
    cands = [slot_dt(d, t) for d, t in dates.items() if slot_dt(d, t) > now]
    for i in range(0, 8):
        d = (now + timedelta(days=i)).date()
        t = weekly.get(str(d.weekday()))
        if t and slot_dt(d.isoformat(), t) > now:
            cands.append(slot_dt(d.isoformat(), t))
    return min(cands) if cands else None


def describe(weekly, dates):
    lines = []
    if weekly:
        days = sorted(weekly, key=int)
        times = set(weekly.values())
        if len(times) == 1:
            lines.append("🔁 Каждую неделю: " + ", ".join(WD_SHORT[int(d)] for d in days) + f" в {next(iter(times))}")
        else:
            lines.append("🔁 Каждую неделю:\n" + "\n".join(f"   {WD_SHORT[int(d)]} — {weekly[d]}" for d in days))
    if dates:
        ds = sorted(dates)
        times = set(dates.values())
        if len(ds) == 1:
            lines.append(f"📅 {fmt_date(ds[0])} в {dates[ds[0]]}")
        elif len(times) == 1:
            lines.append("🗓 Даты: " + ", ".join(fmt_date(d)[:5] for d in ds) + f" в {next(iter(times))}")
        else:
            lines.append("🗓 Даты:\n" + "\n".join(f"   {fmt_date(d)} — {dates[d]}" for d in ds))
    return "\n".join(lines) + "\n(время московское)"


def month_weeks(year, month):
    return calendar.monthcalendar(year, month)


DEFAULT_SCORING = {
    "res": {"done": 2, "part": 1.5, "wrong": 0.6, "bad": 0},
    "bands": [[1, 3, 0], [4, 7, 1], [8, 10, 2]],
    "best_bonus": 1,
    "ladder": {"member": 10, "best": 30, "lead": 60},
}
RES_LABELS = {"done": "✅ Правильно (выполнено)", "part": "🟡 Частично правильно",
              "wrong": "❌ Неправильно", "bad": "🚫 Совсем неверно"}
RES_SHORT = {"done": "✅ Правильно", "part": "🟡 Частично", "wrong": "❌ Неправильно", "bad": "🚫 Совсем неверно"}


def band_points(rating, bands):
    for lo, hi, p in bands:
        if lo <= rating <= hi:
            return p
    return 0


def calc_points(result, rating, cfg):
    return round(cfg["res"].get(result, 0) + band_points(rating, cfg["bands"]), 2)


def promoted(points, current, ladder, order):
    if current not in order:
        return None
    target = current
    for st in order[1:]:
        thr = ladder.get(st)
        if thr is not None and points >= thr and order.index(st) > order.index(target):
            target = st
    return target if target != current else None


def next_tier(current, ladder, order):
    if current not in order or order.index(current) + 1 >= len(order):
        return None
    nxt = order[order.index(current) + 1]
    if ladder.get(nxt) is None:
        return None
    return nxt, (ladder.get(current, 0) if current != order[0] else 0), ladder[nxt]


def bar(points, lo, hi, width=10):
    span = max(hi - lo, 0.0001)
    filled = int(max(0, min(1, (points - lo) / span)) * width)
    return "▓" * filled + "░" * (width - filled)


# ============================================================
# ХАРАКТЕРИСТИКИ
# ============================================================
def work_characteristic(stats):
    """stats: dict с ключами cnt (всего работ), reviewed, r_done (выполнено)."""
    cnt = stats.get("cnt", 0) or 0
    done = stats.get("r_done", 0) or 0
    if cnt == 0:
        return "absent"
    if done == 0:
        return "never"
    if done <= 2:
        return "rare"
    if done <= 5:
        return "normal"
    if done <= 10:
        return "often"
    return "always"


def quality_characteristic(avg_rating):
    """avg_rating: средняя оценка 1–10 или 0, если оценок нет."""
    if not avg_rating:
        return "bad"
    if avg_rating < 4:
        return "bad"
    if avg_rating <= 6:
        return "satisfactory"
    if avg_rating <= 8:
        return "good"
    return "perfect"


# ============================================================
# ЗВЁЗДЫ (РЕКОМЕНДАЦИИ)
# ============================================================
def stars_recommendation(result, rating):
    """Рекомендованная сумма звёзд за работу: 1, 10 или 15."""
    if result == "done" and rating and rating >= 9:
        return 15
    if result in ("done", "part") and rating and rating >= 6:
        return 10
    return 1


def stars_for_period(works_count, avg_rating):
    """Рекомендация за неделю/месяц: среднее 10, лучшее 15, плохо 1."""
    if works_count == 0:
        return 0
    if avg_rating and avg_rating >= 9:
        return 15
    if avg_rating and avg_rating >= 6:
        return 10
    return 1