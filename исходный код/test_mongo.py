from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB")]

print(client.list_database_names())

db.test.insert_one({"ok": True})
print("Запись успешна")
