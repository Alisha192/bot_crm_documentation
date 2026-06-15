import json
import os
from pymongo import MongoClient
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "coffee_bot")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI не задан в .env")

# Подключение к MongoDB Atlas
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
menu_col = db.menu

# Загружаем menu.json
with open("menu.json", "r", encoding="utf-8") as f:
    menu_data = json.load(f)

# Гарантируем один документ меню
menu_data["_id"] = "menu"

# upsert — обновит или создаст
menu_col.replace_one(
    {"_id": "menu"},
    menu_data,
    upsert=True
)

print("✅ Меню успешно загружено в MongoDB")
