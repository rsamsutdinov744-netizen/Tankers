"""Разбор меток заданий и фильтрация по должности."""
import re

POSITIONS = {"художник": "artist", "сценарист": "writer", "аниматор": "animator", "помощник": "helper"}

AID_RE = re.compile(r"(?<![A-Za-z0-9])aid(\d+)_([0-4])(?!\d)", re.I)
CODE_TO_POS = {"0": None, "1": "artist", "2": "writer", "3": "helper", "4": "animator"}
POS_TO_CODE = {None: "0", "artist": "1", "writer": "2", "helper": "3", "animator": "4"}


def parse_aid(text: str):
    m = AID_RE.search(text or "")
    if not m:
        return None, None
    return m.group(0).lower(), CODE_TO_POS[m.group(2)]


def task_position(text: str):
    if "🎨" in text or "🖌" in text:
        return "artist"
    if "🎬" in text:
        return "animator"
    words = text.lower().split()
    for i in (0, 1):
        if len(words) > i:
            w = words[i].strip(".,:;!?-—")
            if w in POSITIONS:
                return POSITIONS[w]
    return None


def classify(text: str):
    aid, pos = parse_aid(text)
    if aid:
        return aid, pos
    return None, task_position(text)


def visible_for(task_pos, user_pos):
    return task_pos is None or task_pos == user_pos