"""Разрешения, статусы, уровни, должности, характеристики."""

PERMS = {
    "tasks_add": "Добавлять задания",
    "tasks_close": "Закрывать задания и смотреть архив",
    "materials_edit": "Материалы и кнопки (создавать, редактировать)",
    "announce_add": "Добавлять и удалять объявления",
    "place_set": "Выбирать место публикации",
    "stats_view": "Смотреть статистику",
    "users_view": "Смотреть список участников",
    "users_status": "Менять статусы участников",
    "users_ban": "Банить и разбанивать",
    "perms_edit": "Редактировать разрешения",
    "reminders_manage": "Напоминания в группу и личку",
    "works_review": "Оценивать присланные работы",
    "polls_create": "Создавать опросы по заданиям",
    "points_edit": "Начислять и снимать баллы",
    "scoring_edit": "Настройки баллов, уровней, времени удаления и фраз",
    "forms_edit": "Общие формы выполнения заданий",
    "ranks_edit": "Кураторы и уровни админов",
    "passwords_edit": "Управление паролями",
    "stars_manage": "Управление оплатой звёздами",
}

DEFAULT_ADMIN_PERMS = {
    "tasks_add", "tasks_close", "materials_edit", "announce_add",
    "place_set", "stats_view", "users_view", "works_review",
}

STATUSES = {
    "newbie": "🔵 Новичок",
    "member": "🟢 Участник",
    "best": "🟡 Лучший участник",
    "lead": "🟣 Ведущий",
    "owner": "🔴 Владелец",
}

LADDER = ["newbie", "member", "best", "lead"]

POS_NAMES = {
    "artist": "🎨 Художник",
    "writer": "✍️ Сценарист",
    "animator": "🎬 Аниматор",
    "helper": "🛠️ Помощник",
}

ADMIN_LEVELS = {
    "junior": "🥉 Младший",
    "middle": "🥈 Средний",
    "senior": "🥇 Старший",
}

LEVEL_RANK = {"junior": 1, "middle": 2, "senior": 3}

# Характеристики по выполнению заданий
WORK_CHARS = {
    "absent": "отсутствует",
    "never": "не выполняет",
    "rare": "редко выполняет",
    "normal": "выполняет",
    "often": "выполняет часто",
    "always": "выполняет постоянно",
}

# Характеристики по качеству работ
QUALITY_CHARS = {
    "bad": "плохо",
    "satisfactory": "удовлетворительно",
    "good": "хорошо",
    "perfect": "идеально",
}