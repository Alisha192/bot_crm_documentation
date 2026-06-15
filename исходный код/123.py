from admin_panel.db import orders_col
from bson import ObjectId  # если нужно преобразовывать

# 1. Находим заказы, содержащие элементы items с item_id = null
query = {"items.item_id": None}  # или {"items.item_id": {"$exists": False}} если поле отсутствует
# Для проверки также можно искать {"items.item_id": {"$type": 10}} (null)

affected_orders = list(orders_col.find(query, {"_id": 1, "items": 1, "total": 1}))
print(f"Найдено заказов с item_id = null: {len(affected_orders)}")

if not affected_orders:
    print("Нет заказов с item_id = null.")
    exit()

# Покажем примеры
for order in affected_orders[:5]:
    print(f"Заказ {order['_id']}, всего позиций: {len(order.get('items', []))}")
    # Найдём проблемные позиции в этом заказе
    bad_items = [item for item in order.get('items', []) if item.get('item_id') is None]
    print(f"  Проблемных позиций: {len(bad_items)}")

# Выбор действия
print("\nВыберите действие:")
print("1 - Удалить все найденные заказы целиком")
print("2 - Удалить только позиции с item_id = null (пересчитать total)")
print("3 - Ничего не делать, выйти")

choice = input("Введите номер действия: ").strip()

if choice == "1":
    confirm = input(f"Удалить {len(affected_orders)} заказов? (yes/no): ")
    if confirm.lower() == "yes":
        result = orders_col.delete_many(query)
        print(f"Удалено заказов: {result.deleted_count}")
    else:
        print("Операция отменена.")

elif choice == "2":
    updated_count = 0
    for order in affected_orders:
        original_items = order.get('items', [])
        # Оставляем только те позиции, где item_id не null
        new_items = [item for item in original_items if item.get('item_id') is not None]

        if len(new_items) == len(original_items):
            continue  # на всякий случай, если вдруг false positive

        # Пересчитываем total (предполагается, что total = сумма price * quantity)
        new_total = sum(item.get('price', 0) * item.get('quantity', 1) for item in new_items)

        # Обновляем документ
        orders_col.update_one(
            {"_id": order["_id"]},
            {"$set": {"items": new_items, "total": new_total}}
        )
        updated_count += 1
        print(f"Обновлён заказ {order['_id']}: удалено {len(original_items) - len(new_items)} позиций")

    print(f"Обработано заказов: {updated_count}")

else:
    print("Выход без изменений.")