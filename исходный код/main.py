import asyncio
import logging
import json
import os
import random
from typing import List, Dict, Any
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
from dotenv import load_dotenv
import uuid
import tempfile
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    PicklePersistence,
    MessageHandler,
    filters
)

from admin_panel.bot_status import set_bot_status
from notification_manager import NotificationManager

bot_application = None

# Загрузка переменных окружения
load_dotenv()

from pymongo import MongoClient
from pymongo.errors import PyMongoError

# Mongo init
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "coffee_bot")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB]

users_col = db.users
menu_col = db.menu
orders_col = db.orders

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы состояний
(
    START,
    REGISTRATION,
    CATEGORY_SELECTION,
    ITEM_SELECTION,
    CART_MANAGEMENT,
    CONFIRM_ORDER,
    ITEM_REMOVAL
) = range(7)

# Путь к файлу меню
MENU_FILE = 'menu.json'
USERS_FILE = 'users.json'


def load_users() -> dict:
    """Загружает пользователей из JSON-файла"""
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"Ошибка загрузки пользователей: {e}")
        return {}


def save_users(users: dict) -> None:
    """Атомарно сохраняет пользователей в JSON-файл"""
    try:
        # Создаем временный файл в той же директории
        temp_path = os.path.join(os.path.dirname(USERS_FILE), f"temp_{uuid.uuid4()}.json")

        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        # Атомарная замена файла
        os.replace(temp_path, USERS_FILE)
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователей: {e}")
        # Удаляем временный файл в случае ошибки
        if os.path.exists(temp_path):
            os.remove(temp_path)

def get_user(user_id: int) -> dict | None:
    return users_col.find_one({"_id": user_id})

def update_user(user_id: int, data: dict) -> None:
    users_col.update_one(
        {"_id": user_id},
        {"$set": data}
    )

def create_user(user: dict) -> None:
    user["_id"] = user.pop("id")
    users_col.insert_one(user)

# Загрузка меню
def load_menu() -> dict:
    try:
        menu = menu_col.find_one({"_id": "menu"})
        if not menu:
            return {"categories": [], "items": {}}
        menu.pop("_id", None)
        return menu
    except PyMongoError as e:
        logger.error(f"Ошибка загрузки меню: {e}")
        return {"categories": [], "items": {}}



# Генерация уникального ID заказа
def generate_order_id(user_id: int) -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{user_id}"


# Расчет суммы заказа
def calculate_total(cart: dict) -> int:
    return sum(item['price'] * item['quantity'] for item in cart.values())

def increment_orders(user_id: int) -> int:
    result = users_col.find_one_and_update(
        {"_id": user_id},
        {"$inc": {"orders_count": 1}},
        return_document=True
    )
    return result.get("orders_count", 0)

# Форматирование содержимого корзины
def format_cart(cart: dict) -> str:
    if not cart:
        return "🛒 Ваша корзина пуста"

    items = []
    for item_id, item in cart.items():
        items.append(
            f"▪ {item['name']} × {item['quantity']} = {item['price'] * item['quantity']}₽"
        )
    total = calculate_total(cart)
    return "🛒 Ваша корзина:\n" + "\n".join(items) + f"\n\n💳 Итого: {total}₽"


# Форматирование деталей заказа
def format_order_details(order_id: str, cart: dict) -> str:
    items = []
    for item_id, item in cart.items():
        items.append(
            f"▪ {item['name']} × {item['quantity']} = {item['price'] * item['quantity']}₽"
        )
    total = calculate_total(cart)
    return (
            f"📋 Детали заказа #{order_id}\n\n"
            + "\n".join(items)
            + f"\n\n💳 Итого: {total}₽"
    )


# Генерация клавиатуры категорий
def categories_keyboard(menu: dict) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in menu['categories']]


# Генерация клавиатуры товаров
def items_keyboard(category: str, menu: dict) -> list[list[InlineKeyboardButton]]:
    items_in_category = [
        (item_id, item['name'])
        for item_id, item in menu['items'].items()
        if item['category'] == category
    ]
    return [
        [InlineKeyboardButton(name, callback_data=f"item_{item_id}")]
        for item_id, name in items_in_category
    ]


# ======= Обработчики команд =======

# Стартовая команда
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    db_user = get_user(user.id)

    if not db_user:
        new_user = {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "registration_date": datetime.utcnow(),
            "orders_count": 0,
            "preferred_name": None,
            "phone": None
        }
        create_user(new_user)

        await update.message.reply_text(
            "👋 Добро пожаловать! Как нам к вам обращаться?",
            reply_markup=ReplyKeyboardRemove()
        )
        return REGISTRATION

    return await handle_existing_user(update, context)


async def handle_existing_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault('cart', {})
    menu = load_menu()
    context.user_data['menu'] = menu
    keyboard = categories_keyboard(menu)
    keyboard.append([InlineKeyboardButton("🛒 Просмотр корзины", callback_data="view_cart")])

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text="☕ Добро пожаловать обратно! Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            text="☕ Добро пожаловать обратно! Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    return CATEGORY_SELECTION


async def handle_preferred_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    preferred_name = update.message.text.strip()
    user_id = update.effective_user.id

    update_user(user_id, {"preferred_name": preferred_name})

    reply_markup = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await update.message.reply_text(
        f"Приятно познакомиться, {preferred_name}! 📱 Пожалуйста, поделитесь номером:",
        reply_markup=reply_markup
    )
    return REGISTRATION


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    phone = (
        update.message.contact.phone_number
        if update.message.contact
        else update.message.text.strip()
    )

    update_user(user_id, {"phone": phone})

    await update.message.reply_text(
        "✅ Регистрация завершена!",
        reply_markup=ReplyKeyboardRemove()
    )

    return await handle_existing_user(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_state = await context.application.persistence.get_conversation(update)

    # Если в состоянии регистрации - обрабатываем как часть регистрации
    if current_state == REGISTRATION:
        # Проверяем, какое поле мы ожидаем
        user_id = str(update.effective_user.id)
        users = load_users()
        user = users.get(user_id)

        if user:
            if user.get('preferred_name') is None:
                return await handle_preferred_name(update, context)
            elif user.get('phone') is None:
                return await handle_phone(update, context)

    # Стандартная обработка
    if update.message:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню или команду /start")


# Обработка выбора категории
async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    category = query.data.split('_', 1)[1]
    context.user_data['current_category'] = category
    menu = context.user_data['menu']

    # Создание клавиатуры товаров
    keyboard = items_keyboard(category, menu)
    keyboard.append([
        InlineKeyboardButton("◀ Назад", callback_data="back_to_categories"),
        InlineKeyboardButton("🛒 Корзина", callback_data="view_cart")
    ])

    await query.edit_message_text(
        text=f"🏷 Категория: {category}\nВыберите товар:",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return ITEM_SELECTION


# Обработка выбора товара
async def handle_item_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    item_id = query.data.split('_', 1)[1]
    menu = context.user_data['menu']

    # Проверка существования товара
    if item_id not in menu['items']:
        await query.edit_message_text(text="⚠ Товар не найден в меню!")
        return await back_to_categories(update, context)

    item_data = menu['items'][item_id]

    # Добавление товара в корзину
    cart = context.user_data['cart']
    if item_id in cart:
        cart[item_id]['quantity'] += 1
    else:
        cart[item_id] = {
            'name': item_data['name'],
            'price': item_data['price'],
            'quantity': 1
        }

    await query.edit_message_text(
        text=f"✅ Добавлено: {item_data['name']}\nЦена: {item_data['price']}₽")

    # Возврат к выбору товаров
    category = context.user_data['current_category']
    keyboard = items_keyboard(category, menu)
    keyboard.append([
        InlineKeyboardButton("◀ Назад", callback_data="back_to_categories"),
        InlineKeyboardButton("🛒 Корзина", callback_data="view_cart")
    ])

    await query.message.reply_text(
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return ITEM_SELECTION


# Просмотр корзины
async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    else:
        logger.warning("Просмотр корзины без callback_query")
        return await start(update, context)

    cart = context.user_data.get('cart', {})
    cart_text = format_cart(cart)

    # Кнопки управления корзиной
    keyboard = []
    if cart:
        # Кнопки для каждого товара (теперь показывают количество)
        for item_id in cart.keys():
            item = cart[item_id]
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 {item['name']} (x{item['quantity']})",
                    callback_data=f"remove_options_{item_id}")
            ])

        keyboard.append([
            InlineKeyboardButton("🧹 Очистить корзину", callback_data="clear_cart"),
            InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")
        ])

    keyboard.append([InlineKeyboardButton("◀ Вернуться в меню", callback_data="back_to_categories")])

    await query.edit_message_text(
        text=cart_text,
        reply_markup=InlineKeyboardMarkup(keyboard))
    return CART_MANAGEMENT


# Показать опции удаления для товара
async def show_remove_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    item_id = query.data.split('_', 2)[2]
    context.user_data['current_remove_item'] = item_id

    cart = context.user_data.get('cart', {})
    item = cart.get(item_id)

    if not item:
        await query.edit_message_text(text="⚠ Товар не найден в корзине!")
        return await view_cart(update, context)

    # Создание опций удаления
    keyboard = [
        [InlineKeyboardButton("➖ Удалить одну штуку", callback_data="remove_one")],
        [InlineKeyboardButton("❌ Удалить всю позицию", callback_data="remove_all")],
        [InlineKeyboardButton("↩️ Назад в корзину", callback_data="back_to_cart")]
    ]

    await query.edit_message_text(
        text=f"Выберите действие для товара:\n{item['name']} (x{item['quantity']})",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ITEM_REMOVAL


# Удалить одну штуку товара
async def remove_one_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    item_id = context.user_data.get('current_remove_item')
    cart = context.user_data.get('cart', {})

    if item_id in cart:
        if cart[item_id]['quantity'] > 1:
            cart[item_id]['quantity'] -= 1
            message = f"➖ Убрали одну штуку: {cart[item_id]['name']}"
        else:
            # Если осталась последняя штука, удаляем всю позицию
            item_name = cart[item_id]['name']
            del cart[item_id]
            message = f"❌ Удалено: {item_name} (последняя штука)"
    else:
        message = "⚠ Товар не найден в корзине!"

    # Очистка временных данных
    if 'current_remove_item' in context.user_data:
        del context.user_data['current_remove_item']

    await query.edit_message_text(text=message)
    return await view_cart(update, context)


# Удалить всю позицию товара
async def remove_entire_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    item_id = context.user_data.get('current_remove_item')
    cart = context.user_data.get('cart', {})

    if item_id in cart:
        item_name = cart[item_id]['name']
        del cart[item_id]
        message = f"❌ Удалено: {item_name}"
    else:
        message = "⚠ Товар не найден в корзине!"

    # Очистка временных данных
    if 'current_remove_item' in context.user_data:
        del context.user_data['current_remove_item']

    await query.edit_message_text(text=message)
    return await view_cart(update, context)


# Вернуться в корзину
async def back_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    # Очистка временных данных
    if 'current_remove_item' in context.user_data:
        del context.user_data['current_remove_item']

    return await view_cart(update, context)


# Очистка корзины
async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    context.user_data['cart'] = {}
    await query.edit_message_text(text="🧹 Корзина очищена")
    return await view_cart(update, context)


# Оформление заказа
async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get("cart", {})
    if not cart:
        await query.edit_message_text("⚠ Корзина пуста")
        return START

    user_id = query.from_user.id

    # ==== вычисление daily_id ====
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=999999)

    last_order = orders_col.find(
        {"created_at": {"$gte": today_start, "$lte": today_end}}
    ).sort("daily_id", -1).limit(1)

    last_daily_id = 0
    for o in last_order:
        last_daily_id = o.get("daily_id", 0)
        break

    daily_id = last_daily_id + 1
    order_id = f"{datetime.now().strftime('%Y%m%d')}-{daily_id}"
    total = calculate_total(cart)

    # Подготовка позиций заказа
    menu = context.user_data.get("menu", {})
    menu_items = menu.get("items", {})

    order_items = []
    for item_id, item in cart.items():
        menu_item = menu_items.get(item_id, {})
        order_items.append({
            "item_id": item_id,
            "name": item["name"],
            "category": menu_item.get("category"),
            "price": item["price"],
            "quantity": item["quantity"],
            "total": item["price"] * item["quantity"]
        })

    # Сохранение в Mongo с флагами для уведомлений
    # Обратите внимание: is_new=True и status_changed=False при создании
    orders_col.insert_one({
        "_id": order_id,
        "user_id": user_id,
        "daily_id": daily_id,
        "items": order_items,
        "total": total,
        "is_new": True,  # флаг нового заказа (НИКОГДА не меняется)
        "status_changed": False,  # будет установлено в True когда статус изменится через админку
        "created_at": datetime.now(),  # Используем локальное время
        "status_updated_at": datetime.now()
    })

    # Очистка корзины
    context.user_data["cart"] = {}

    # Уведомление админа
    await notify_admin(context, order_id, cart, total)

    # 1. Отправляем сообщение с номером заказа
    await query.message.reply_text(
        f"✅ Заказ #{daily_id} успешно оформлен!\n\n"
        f"Итоговая сумма: {total}₽\n"
        f"Статус: 📝 Принят в обработку\n\n"
        f"Спасибо за заказ! Наш бариста уже готовит ваш кофе ☕\n\n"
        f"_Вы получите уведомление при изменении статуса заказа._"
    )

    # 2. Отправляем ОТДЕЛЬНОЕ сообщение с кнопкой для возврата в меню
    # УБИРАЕМ кнопку "Мои заказы", если не используем
    await query.message.reply_text(
        "Вы можете продолжить выбор товаров:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Вернуться в меню", callback_data="back_to_categories")]
        ])
    )

    return START



# Уведомление администратора
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, order_id: str, cart: dict, total: int) -> None:
    try:
        admin_channel = os.getenv('ADMIN_CHANNEL')
        if not admin_channel:
            logger.error("ADMIN_CHANNEL не настроен в .env файле")
            return

        try:
            admin_channel = int(admin_channel)
        except ValueError:
            pass

        # Форматирование сообщения для админа
        items_text = "\n".join(
            f"▪ {item['name']} × {item['quantity']} ({item['price']}₽)"
            for item in cart.values()
        )

        # Загрузка данных пользователя
        user_id = str(context.user_data['order']['user']['id'])
        users = load_users()
        user_data = users.get(user_id, {})

        # Определяем статус клиента
        orders_count = user_data.get('orders_count', 0)
        client_status = "🆕 НОВЫЙ КЛИЕНТ" if orders_count <= 1 else f"👑 Постоянный клиент"

        # Формируем информацию о клиенте
        preferred_name = user_data.get('preferred_name', 'Не указано')
        phone = user_data.get('phone', 'Не указан')
        first_name = user_data.get('first_name', 'Неизвестный')

        client_info = (
            f"👤 Клиент: {preferred_name} ({first_name})\n"
            f"📱 Телефон: {phone}\n"
            f"🆔 ID: {user_id}\n"
            f"👑 Статус: {client_status}\n"
            f"📊 Всего заказов: {orders_count}"
        )

        message = (
            f"🆕 [Заказ #{order_id}]\n"
            f"========================\n"
            f"{items_text}\n"
            f"========================\n"
            f"💳 Итого: {total}₽\n"
            f"========================\n"
            f"{client_info}\n"
            f"========================\n"
            f"✅ Принят в обработку"
        )

        await context.bot.send_message(chat_id=admin_channel, text=message)
        logger.info(f"Уведомление отправлено в {admin_channel}")

    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {str(e)}")

# Вспомогательные обработчики
async def back_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    else:
        return await start(update, context)

    menu = context.user_data['menu']
    keyboard = categories_keyboard(menu)
    keyboard.append([InlineKeyboardButton("🛒 Просмотр корзины", callback_data="view_cart")])

    await query.edit_message_text(
        text="Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return CATEGORY_SELECTION


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user:
        logger.info(f"Пользователь {user.first_name} отменил действие")

    if update.message:
        await update.message.reply_text(
            "Действие отменено",
            reply_markup=ReplyKeyboardRemove())
    elif update.callback_query:
        await update.callback_query.message.reply_text("Действие отменено")

    return ConversationHandler.END


# Обработчик текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню или команду /start")


# Тестовая команда для проверки канала
async def test_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admin_channel = os.getenv('ADMIN_CHANNEL')
        if not admin_channel:
            await update.message.reply_text("ADMIN_CHANNEL не настроен в .env файле")
            return

        try:
            admin_channel = int(admin_channel)
        except ValueError:
            pass

        await update.message.reply_text(f"Пытаюсь отправить сообщение в канал: {admin_channel}")
        await context.bot.send_message(chat_id=admin_channel, text="✅ Тестовое сообщение от бота!")
        await update.message.reply_text("Сообщение отправлено успешно!")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")


# ======= ФУНКЦИЯ ДЛЯ РАССЫЛКИ =======
async def send_broadcast_message(
        bot: Bot,
        chat_id: int,
        message_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Отправляет сообщение рассылки пользователю

    Args:
        bot: Экземпляр Telegram бота
        chat_id: ID чата пользователя
        message_data: Данные сообщения для рассылки

    Returns:
        Словарь с результатом отправки
    """
    try:
        message_type = message_data.get('type', 'text')
        content = message_data.get('content', '')

        if message_type == 'text':
            await bot.send_message(
                chat_id=chat_id,
                text=content,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        elif message_type == 'photo':
            await bot.send_photo(
                chat_id=chat_id,
                photo=message_data.get('photo'),
                caption=content if content else None,
                parse_mode='HTML'
            )
        elif message_type == 'document':
            await bot.send_document(
                chat_id=chat_id,
                document=message_data.get('document'),
                caption=content if content else None,
                parse_mode='HTML'
            )
        else:
            return {
                'success': False,
                'chat_id': chat_id,
                'error': f'Неизвестный тип сообщения: {message_type}'
            }

        return {
            'success': True,
            'chat_id': chat_id
        }

    except TelegramError as e:
        error_message = str(e)

        # Определяем тип ошибки
        if "bot was blocked" in error_message.lower():
            error_type = "user_blocked"
        elif "chat not found" in error_message.lower():
            error_type = "chat_not_found"
        elif "user is deactivated" in error_message.lower():
            error_type = "user_deactivated"
        else:
            error_type = "other"

        return {
            'success': False,
            'chat_id': chat_id,
            'error': error_message,
            'error_type': error_type
        }
    except Exception as e:
        return {
            'success': False,
            'chat_id': chat_id,
            'error': str(e),
            'error_type': 'exception'
        }


async def send_broadcast_to_users(
        bot: Bot,
        message_data: Dict[str, Any],
        user_ids: List[int] = None,
        max_concurrent: int = 5,  # Уменьшаем до 5 одновременных запросов
        delay_between_messages: float = 0.3  # Увеличиваем задержку
) -> Dict[str, Any]:
    """
    Массовая рассылка сообщений пользователям с ограничением одновременных запросов

    Args:
        bot: Экземпляр Telegram бота
        message_data: Данные сообщения для рассылки
        user_ids: Список ID пользователей (если None - всем пользователям)
        max_concurrent: Максимальное количество одновременных запросов
        delay_between_messages: Задержка между сообщениями (секунды)

    Returns:
        Статистика рассылки
    """
    results = {
        'total': 0,
        'success': 0,
        'failed': 0,
        'blocked': 0,
        'not_found': 0,
        'errors': []
    }

    try:
        # Получаем список пользователей
        if user_ids is None:
            # Используем ту же логику получения пользователей, что и в broadcast.py
            import os
            from pymongo import MongoClient

            MONGO_URI = os.getenv("MONGO_URI")
            MONGO_DB = os.getenv("MONGO_DB", "coffee_bot")

            mongo_client = MongoClient(MONGO_URI)
            db = mongo_client[MONGO_DB]
            users_col = db.users

            users_cursor = users_col.find({}, {'_id': 1})
            user_ids = [user['_id'] for user in users_cursor]

        results['total'] = len(user_ids)

        if not user_ids:
            return results

        logger.info(f"Начинаем рассылку для {results['total']} пользователей")

        # Создаем семафор для ограничения одновременных запросов
        semaphore = asyncio.Semaphore(max_concurrent)

        async def send_with_rate_limit(chat_id):
            """Отправка сообщения с ограничением скорости"""
            async with semaphore:
                try:
                    # Задержка для rate limiting
                    await asyncio.sleep(delay_between_messages)
                    return await send_broadcast_message(bot, chat_id, message_data)
                except Exception as e:
                    return {
                        'success': False,
                        'chat_id': chat_id,
                        'error': str(e),
                        'error_type': 'exception'
                    }

        # Отправляем сообщения пачками для логирования прогресса
        batch_size = 20  # Размер пачки для логирования
        total_batches = (len(user_ids) + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, len(user_ids))
            current_batch = user_ids[start_idx:end_idx]

            logger.info(f"Обрабатываем пачку {batch_num + 1}/{total_batches} "
                        f"({len(current_batch)} пользователей)")

            # Создаем задачи для текущей пачки
            tasks = []
            for chat_id in current_batch:
                task = asyncio.create_task(send_with_rate_limit(chat_id))
                tasks.append(task)

            # Выполняем задачи текущей пачки
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Обрабатываем результаты пачки
            for result in batch_results:
                if isinstance(result, Exception):
                    results['failed'] += 1
                    results['errors'].append({
                        'chat_id': 'unknown',
                        'error': str(result)
                    })
                elif result.get('success'):
                    results['success'] += 1
                else:
                    results['failed'] += 1
                    error_type = result.get('error_type', 'other')

                    if error_type == 'user_blocked':
                        results['blocked'] += 1
                    elif error_type == 'chat_not_found':
                        results['not_found'] += 1

                    results['errors'].append({
                        'chat_id': result.get('chat_id'),
                        'error': result.get('error'),
                        'error_type': error_type
                    })

            # Дополнительная задержка между пачками
            if batch_num < total_batches - 1:
                await asyncio.sleep(1.0)

        logger.info(f"Рассылка завершена: успешно {results['success']}, "
                    f"не отправлено {results['failed']}")

        return results

    except Exception as e:
        logger.error(f"Ошибка при выполнении рассылки: {str(e)}", exc_info=True)
        results['errors'].append({
            'chat_id': 'all',
            'error': f'Ошибка при выполнении рассылки: {str(e)}'
        })
        return results

# Основная функция
def main() -> None:
    # Логирование загруженных переменных окружения
    logger.info(f"BOT_TOKEN: {os.getenv('BOT_TOKEN') is not None}")
    logger.info(f"ADMIN_CHANNEL: {os.getenv('ADMIN_CHANNEL')}")

    # Загрузка токена бота
    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        raise ValueError("Не задан BOT_TOKEN в переменных окружения")

    run_bot(bot_token)


def run_bot(bot_token: str) -> None:
    global bot_application, bot_status

    logger.info("Запуск Telegram-бота с увеличенным пулом соединений")

    if not bot_token:
        raise ValueError("BOT_TOKEN пустой")

    set_bot_status("starting")
    bot_status = "starting"

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        persistence = PicklePersistence(filepath='coffee_bot_persistence.pkl')

        # Создаем приложение с увеличенным пулом соединений
        application = (
            Application.builder()
            .token(bot_token)
            .persistence(persistence)
            .pool_timeout(60.0)  # Увеличиваем таймаут пула
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .get_updates_read_timeout(30.0)
            .connection_pool_size(30)  # Увеличиваем размер пула
            .build()
        )

        global bot_application
        bot_application = application

        try:
            from admin_panel.bot_manager import bot_manager
            bot_manager.set_bot_application(application)
            logger.info("Bot application set in manager from main.py")
        except ImportError as e:
            logger.warning(f"Could not import bot_manager: {e}")

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                REGISTRATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_preferred_name),
                    MessageHandler(filters.CONTACT | filters.TEXT, handle_phone)
                ],
                CATEGORY_SELECTION: [
                    CallbackQueryHandler(handle_category_selection, pattern=r'^cat_'),
                    CallbackQueryHandler(view_cart, pattern=r'^view_cart$'),
                    CallbackQueryHandler(back_to_categories, pattern=r'^back_to_categories$'),
                ],
                ITEM_SELECTION: [
                    CallbackQueryHandler(handle_item_selection, pattern=r'^item_'),
                    CallbackQueryHandler(back_to_categories, pattern=r'^back_to_categories$'),
                    CallbackQueryHandler(view_cart, pattern=r'^view_cart$'),
                ],
                CART_MANAGEMENT: [
                    CallbackQueryHandler(show_remove_options, pattern=r'^remove_options_'),
                    CallbackQueryHandler(clear_cart, pattern=r'^clear_cart$'),
                    CallbackQueryHandler(checkout, pattern=r'^checkout$'),
                    CallbackQueryHandler(back_to_categories, pattern=r'^back_to_categories$'),
                    CallbackQueryHandler(view_cart, pattern=r'^view_cart$'),
                ],
                ITEM_REMOVAL: [
                    CallbackQueryHandler(remove_one_unit, pattern=r'^remove_one$'),
                    CallbackQueryHandler(remove_entire_item, pattern=r'^remove_all$'),
                    CallbackQueryHandler(back_to_cart, pattern=r'^back_to_cart$'),
                ],
                START: [
                    CallbackQueryHandler(back_to_categories, pattern=r'^back_to_categories$'),
                    CommandHandler('start', start)
                ]
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            name="coffee_shop_bot",
            persistent=True,
            allow_reentry=True
        )

        application.add_handler(conv_handler)
        application.add_handler(CommandHandler("test_channel", test_channel))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

        # УСПЕШНЫЙ ЗАПУСК - обновляем статус
        set_bot_status("running", token=bot_token[:20] + "...")
        logger.info("🤖 Бот успешно запущен и работает")

        async def runner():
            try:
                await application.initialize()
                await application.start()
                await application.updater.start_polling()

                # Создаем менеджер уведомлений после запуска бота
                notification_manager = NotificationManager(
                    bot=application.bot,
                    mongo_uri=MONGO_URI
                )

                # Запускаем мониторинг в фоновом режиме
                monitoring_task = asyncio.create_task(notification_manager.start_monitoring())
                logger.info("✅ Система уведомлений запущена")

                # Проверяем, что менеджер работает
                async def health_check():
                    while True:
                        await asyncio.sleep(60)
                        logger.info(
                            f"Мониторинг работает. Обработано заказов: {len(notification_manager._processed_orders)}")

                health_task = asyncio.create_task(health_check())

                # Бесконечный цикл работы
                while True:
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info("Работа бота прервана")
            except Exception as e:
                logger.error(f"Ошибка в боте: {e}")
                set_bot_status("error", str(e))
            finally:
                # При завершении устанавливаем статус "stopped"
                set_bot_status("stopped")

        loop.run_until_complete(runner())

    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        set_bot_status("error", str(e))
        raise


def stop_bot():
    global bot_application, bot_status

    if bot_application:
        try:
            # Получаем текущий event loop
            loop = asyncio.get_event_loop()

            # Останавливаем бота
            if loop.is_running():
                # Создаем задачу для graceful shutdown
                async def shutdown():
                    await bot_application.stop()
                    await bot_application.shutdown()

                # Запускаем shutdown в event loop
                loop.run_until_complete(shutdown())

            bot_application = None
            bot_status = "stopped"
            set_bot_status("stopped")
            logger.info("🛑 Бот остановлен")

        except Exception as e:
            logger.error(f"Ошибка при остановке бота: {e}")
            bot_status = "error"
            set_bot_status("error", str(e))
    else:
        logger.info("Бот не был запущен")




