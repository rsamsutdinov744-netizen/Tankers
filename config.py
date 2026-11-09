"""Загрузка настроек из .env. Все секреты — только здесь."""
import os

def _load_dotenv():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8781470760:AAHwfi-Y09q8jdWB0U4w6bMXhF-tOevzckE")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
USER_PASSWORD = os.environ.get("USER_PASSWORD", "UT653IO212")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin5642iS2")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "Admin 5642iS25612#")
SOURCE_GROUP_ID = int(os.environ.get("SOURCE_GROUP_ID", "0"))
SOURCE_TOPIC_ID = int(os.environ.get("SOURCE_TOPIC_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "tank_bot.db")
PORT = int(os.environ.get("PORT", "0"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "120"))
PROVIDER_TOKEN = os.environ.get("PROVIDER_TOKEN", "")  # для Telegram Stars