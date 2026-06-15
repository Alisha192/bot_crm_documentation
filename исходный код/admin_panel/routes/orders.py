from collections import defaultdict

from bson.errors import InvalidId
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from admin_panel.db import db, users_col
from bson.objectid import ObjectId
from datetime import datetime

orders_bp = Blueprint(
    "orders",
    __name__
)

@orders_bp.route("/")
@login_required
def orders():
    orders_list = list(db.orders.find({"is_new": True}).sort("created_at", -1))
    return render_template("orders.html", orders=orders_list, current_page="orders")


@orders_bp.route("/mark_done", methods=["POST"])
@login_required
def mark_done():
    order_id = request.json.get("order_id")

    if not order_id:
        return jsonify({"status": "error", "msg": "no order_id"}), 400

    # пробуем ObjectId, если не вышло — считаем строкой
    query = {"_id": order_id}
    try:
        query = {"_id": ObjectId(order_id)}
    except Exception:
        pass  # order_id остаётся строкой

    # Получаем текущее время
    now = datetime.now()

    result = db.orders.update_one(
        query,
        {
            "$set": {
                "is_new": False,
                "status_changed": True,
                "status_updated_at": now  # Добавляем время обновления
            }
        }
    )

    if result.modified_count == 1:
        return jsonify({"status": "ok"})

    return jsonify({"status": "error", "msg": "order not found"}), 404


@orders_bp.route("/get_orders")
@login_required
def get_orders():
    orders_cursor = db.orders.find({"is_new": True}).sort("created_at", 1)

    result = []

    for order in orders_cursor:
        # --- пользователь ---
        user_name = None
        phone = None

        if "user_id" in order:
            user = users_col.find_one({"_id": order["user_id"]}) or {}
            user_name = user.get("preferred_name") or user.get("first_name")
            phone = user.get("phone")
        else:
            user_name = order.get("user_name")
            phone = order.get("phone")

        result.append({
            "_id": str(order["_id"]),
            "daily_id": order.get("daily_id"),
            "user_name": user_name or "—",
            "phone": phone,
            "total": order.get("total", 0),
            "created_at": order["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
            "items": order.get("items", [])
        })

    return jsonify(result)

@orders_bp.route("/create_order", methods=["POST"])
@login_required
def create_order():
    data = request.json

    if not data or "items" not in data or len(data["items"]) == 0:
        return jsonify({"status": "error", "msg": "Нет выбранных товаров"}), 400

    # вычисляем новый daily_id
    last_order_cursor = db.orders.find().sort("daily_id", -1).limit(1)
    last_order = next(last_order_cursor, None)
    daily_id = last_order["daily_id"] + 1 if last_order else 1

    # формируем список товаров для сохранения
    items = []
    for item in data["items"]:
        # ожидаем, что фронт передаёт: id, name, price, quantity
        items.append({
            "id": item.get("id"),
            "name": item.get("name", "Без имени"),
            "price": float(item.get("price", 0)),
            "quantity": int(item.get("quantity", 1))
        })

    # суммарная стоимость
    total = sum(i["price"] * i["quantity"] for i in items)

    order = {
        "daily_id": daily_id,
        "user_name": data.get("user_name"),
        "phone": data.get("phone"),
        "items": items,
        "total": total,
        "is_new": True,
        "created_at": datetime.now()
    }

    db.orders.insert_one(order)
    return jsonify({"status": "ok"})

