from flask import Blueprint, render_template, request, jsonify, redirect
from flask_login import login_required
from admin_panel.db import db
import uuid

menu_bp = Blueprint("menu", __name__)

@menu_bp.route("/")
@login_required
def menu():
    menu_doc = db.menu.find_one({"_id": "menu"}) or {"categories": [], "items": {}}
    items = menu_doc.get("items", {})
    grouped = {}
    for cat in menu_doc.get("categories", []):
        grouped[cat] = []
    for item_id, item in items.items():
        cat = item.get("category")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append({
            "id": item_id,
            "name": item["name"],
            "price": item["price"]
        })
    return render_template("menu.html", categories=grouped, current_page="menu")

# ===== CRUD для категорий =====

@menu_bp.route("/add_category", methods=["POST"])
@login_required
def add_category():
    name = request.json.get("name")
    if not name:
        return jsonify({"status": "error", "msg": "Название категории не указано"}), 400
    db.menu.update_one({"_id": "menu"}, {"$addToSet": {"categories": name}}, upsert=True)
    return jsonify({"status": "ok", "name": name})

@menu_bp.route("/rename_category", methods=["POST"])
@login_required
def rename_category():
    old = request.json.get("old")
    new = request.json.get("new")
    if not old or not new:
        return jsonify({"status": "error"}), 400
    db.menu.update_one({"_id": "menu"}, {"$pull": {"categories": old}})
    db.menu.update_one({"_id": "menu"}, {"$addToSet": {"categories": new}})
    # обновляем категорию у товаров
    db.menu.update_one({"_id": "menu"}, {"$set": {f"items.{k}.category": new for k, v in db.menu.find_one({'_id':'menu'})['items'].items() if v['category']==old}})
    return jsonify({"status": "ok"})

@menu_bp.route("/delete_category", methods=["POST"])
@login_required
def delete_category():
    cat = request.json.get("name")
    if not cat:
        return jsonify({"status": "error"}), 400
    # удаляем категорию
    db.menu.update_one({"_id": "menu"}, {"$pull": {"categories": cat}})
    # удаляем все товары этой категории
    menu_doc = db.menu.find_one({"_id": "menu"})
    items = menu_doc.get("items", {})
    for item_id, item in list(items.items()):
        if item.get("category") == cat:
            db.menu.update_one({"_id": "menu"}, {"$unset": {f"items.{item_id}": ""}})
    return jsonify({"status": "ok"})

# ===== CRUD для товаров =====

@menu_bp.route("/add_item", methods=["POST"])
@login_required
def add_item():
    data = request.json
    item_id = str(uuid.uuid4())[:8]
    item = {
        "name": data.get("name", "Новый товар"),
        "price": float(data.get("price", 0)),
        "category": data.get("category")
    }
    db.menu.update_one({"_id": "menu"}, {"$set": {f"items.{item_id}": item}}, upsert=True)
    return jsonify({"status": "ok", "item_id": item_id, "item": item})

@menu_bp.route("/edit_item", methods=["POST"])
@login_required
def edit_item():
    data = request.json
    item_id = data.get("id")
    updates = {}
    if "name" in data:
        updates[f"items.{item_id}.name"] = data["name"]
    if "price" in data:
        updates[f"items.{item_id}.price"] = float(data["price"])
    if "category" in data:
        updates[f"items.{item_id}.category"] = data["category"]
    db.menu.update_one({"_id": "menu"}, {"$set": updates})
    return jsonify({"status": "ok"})

@menu_bp.route("/delete_item", methods=["POST"])
@login_required
def delete_item():
    item_id = request.json.get("id")
    db.menu.update_one({"_id": "menu"}, {"$unset": {f"items.{item_id}": ""}})
    return jsonify({"status": "ok"})

@menu_bp.route("/api")
@login_required
def menu_api():
    menu = db.menu.find_one({"_id": "menu"})
    return jsonify(menu.get("items", {}))

@menu_bp.route("/get_items")
@login_required
def get_items():
    menu_doc = db.menu.find_one({"_id": "menu"}) or {"items": {}}
    items = menu_doc.get("items", {})
    # Преобразуем в список для фронтенда
    result = []
    for item_id, item in items.items():
        result.append({
            "id": item_id,
            "name": item["name"],
            "price": item["price"],
            "category": item.get("category", "")
        })
    return jsonify(result)
