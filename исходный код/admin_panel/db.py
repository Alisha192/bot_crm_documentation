import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

client = MongoClient("mongodb://localhost:27017")
db = client["coffee_bot"]

users_col = db.users
orders_col = db.orders
menu_col = db.menu