from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017")
db = client["coffee_bot"]
users_col = db.users

# Удалить пользователя с _id = 834997288
result = users_col.delete_one({"_id": 834997288})

print(f"Удалено документов: {result.deleted_count}")