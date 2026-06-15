# admin_panel/broadcast.py
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from datetime import datetime, timedelta
import json
import logging
import os
from pymongo import MongoClient
from bson import ObjectId

from admin_panel.bot_manager import bot_manager

broadcast_bp = Blueprint("broadcast", __name__, url_prefix="/broadcast")

logger = logging.getLogger(__name__)


# MongoDB подключение (такое же как в bot_manager)
def get_mongo_connection():
    """Получение подключения к MongoDB"""
    MONGO_URI = os.getenv("MONGO_URI")
    MONGO_DB = os.getenv("MONGO_DB", "coffee_bot")

    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    return db


def get_users_collection():
    """Получение коллекции пользователей"""
    db = get_mongo_connection()
    return db.users


def get_orders_collection():
    """Получение коллекции заказов"""
    db = get_mongo_connection()
    return db.orders


def get_broadcasts_collection():
    """Получение коллекции рассылок"""
    db = get_mongo_connection()
    return db.broadcasts


@broadcast_bp.route("/")
@login_required
def broadcast():
    """Главная страница рассылок"""
    try:
        users_col = get_users_collection()
        orders_col = get_orders_collection()

        # Получаем статистику пользователей
        total_users = users_col.count_documents({})

        # Вычисляем активных пользователей (с заказами за последние 30 дней)
        thirty_days_ago = datetime.now() - timedelta(days=30)

        # Находим ID пользователей, которые делали заказы за последние 30 дней
        active_user_ids = orders_col.distinct("user_id", {
            "created_at": {"$gte": thirty_days_ago}
        })

        active_users = len(active_user_ids) if active_user_ids else 0

        # Получаем информацию о состоянии бота
        bot_status = bot_manager.get_status()

        return render_template(
            "broadcast.html",
            total_users=total_users,
            active_users=active_users,
            bot_status=bot_status.get('status', 'unknown'),
            bot_token_short=bot_status.get('token_short', '')
        )
    except Exception as e:
        logger.error(f"Error in broadcast route: {e}", exc_info=True)
        return render_template(
            "broadcast.html",
            total_users=0,
            active_users=0,
            bot_status="error",
            bot_token_short=""
        )


@broadcast_bp.route("/users", methods=["GET"])
@login_required
def get_users():
    """Получение списка пользователей"""
    try:
        users_col = get_users_collection()

        users = list(users_col.find({}, {
            '_id': 1,
            'first_name': 1,
            'last_name': 1,
            'username': 1,
            'preferred_name': 1,
            'phone': 1,
            'registration_date': 1,
            'orders_count': 1
        }).sort('registration_date', -1))

        # Преобразуем ObjectId в строку и форматируем дату
        for user in users:
            user['id'] = user['_id']  # Сохраняем числовой ID
            if 'registration_date' in user and user['registration_date']:
                if isinstance(user['registration_date'], datetime):
                    user['registration_date'] = user['registration_date'].strftime('%Y-%m-%d %H:%M')
                else:
                    user['registration_date'] = str(user['registration_date'])
            user.pop('_id', None)

        return jsonify({
            "success": True,
            "users": users,
            "total": len(users)
        })
    except Exception as e:
        logger.error(f"Error getting users: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# В admin_panel/broadcast.py, обновите функцию send_broadcast:
@broadcast_bp.route("/send", methods=["POST"])
@login_required
def send_broadcast():
    """Отправка рассылки"""
    try:
        # Проверяем, запущен ли бот
        if not bot_manager.is_running:
            return jsonify({
                "success": False,
                "error": "Бот не запущен. Запустите бота в панели управления."
            }), 400

        # Получаем данные из формы
        message = request.form.get('message', '').strip()
        message_type = request.form.get('type', 'text')
        send_to_all = request.form.get('send_to_all', 'true') == 'true'
        segment = request.form.get('segment', 'all')
        user_ids_str = request.form.get('user_ids', '')

        logger.info(f"Broadcast params: type={message_type}, send_to_all={send_to_all}, segment={segment}")

        # Подготавливаем данные для рассылки
        broadcast_data = {
            'type': message_type,
            'content': message
        }

        # Обработка файла (если есть)
        if 'file' in request.files:
            file = request.files['file']
            if file.filename != '':
                from werkzeug.utils import secure_filename

                filename = secure_filename(file.filename)
                upload_dir = current_app.config.get('UPLOAD_FOLDER', 'uploads')
                os.makedirs(upload_dir, exist_ok=True)

                filepath = os.path.join(upload_dir, f"broadcast_{datetime.now().timestamp()}_{filename}")
                file.save(filepath)

                broadcast_data['file_path'] = filepath
                logger.info(f"File saved to: {filepath}")

        # Получаем ID пользователей для рассылки
        user_ids = None
        if not send_to_all and user_ids_str:
            try:
                user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.strip()]
            except ValueError as e:
                logger.warning(f"Error parsing user_ids: {e}")
                user_ids = None

        if user_ids is None and not send_to_all:
            # Получаем пользователей по сегменту
            user_ids = get_users_by_segment(segment)

        logger.info(f"Sending broadcast to {len(user_ids) if user_ids else 'all'} users")

        # ВЫЗЫВАЕМ ПРЯМО, а не в отдельном потоке для тестирования
        result = bot_manager.send_broadcast(broadcast_data, user_ids)

        # Сохраняем историю рассылки
        save_broadcast_history(broadcast_data, result, current_user.id)

        return jsonify({
            "success": True,
            "message": f"Рассылка отправлена",
            "data": result
        })

    except Exception as e:
        logger.error(f"Error in send_broadcast: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def get_users_by_segment(segment):
    """Получение пользователей по сегменту"""
    try:
        users_col = get_users_collection()
        orders_col = get_orders_collection()

        thirty_days_ago = datetime.now() - timedelta(days=30)

        if segment == 'active':
            # Пользователи с заказами за последние 30 дней
            active_user_ids = orders_col.distinct("user_id", {
                "created_at": {"$gte": thirty_days_ago}
            })
            return active_user_ids

        elif segment == 'with_orders':
            # Пользователи с любыми заказами
            users_with_orders = list(users_col.find(
                {"orders_count": {"$gt": 0}},
                {"_id": 1}
            ))
            return [user['_id'] for user in users_with_orders]

        elif segment == 'no_orders':
            # Пользователи без заказов
            users_without_orders = list(users_col.find(
                {"orders_count": 0},
                {"_id": 1}
            ))
            return [user['_id'] for user in users_without_orders]

        elif segment == 'recent':
            # Пользователи, зарегистрированные за последние 7 дней
            week_ago = datetime.now() - timedelta(days=7)
            recent_users = list(users_col.find(
                {"registration_date": {"$gte": week_ago}},
                {"_id": 1}
            ))
            return [user['_id'] for user in recent_users]

        else:  # 'all' или любой другой
            # Все пользователи - используем метод из bot_manager
            return bot_manager.get_user_ids()

    except Exception as e:
        logger.error(f"Error getting users by segment: {e}", exc_info=True)
        return None


def save_broadcast_history(broadcast_data, result, admin_id):
    """Сохранение истории рассылки"""
    try:
        broadcasts_col = get_broadcasts_collection()

        history_entry = {
            'admin_id': admin_id,
            'type': broadcast_data.get('type'),
            'content': broadcast_data.get('content', '')[:500],  # Сохраняем только начало
            'total_users': result.get('total', 0),
            'success_count': result.get('success', 0),
            'failed_count': result.get('failed', 0),
            'blocked_count': result.get('blocked', 0),
            'not_found_count': result.get('not_found', 0),
            'sent_at': datetime.now(),
            'errors': result.get('errors', [])[:10]  # Сохраняем только первые 10 ошибок
        }

        broadcasts_col.insert_one(history_entry)

    except Exception as e:
        logger.error(f"Error saving broadcast history: {e}", exc_info=True)


@broadcast_bp.route("/preview", methods=["POST"])
@login_required
def preview_broadcast():
    """Предпросмотр сообщения рассылки"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Нет данных"
            }), 400

        message = data.get('message', '')
        message_type = data.get('type', 'text')

        # Простой анализ сообщения
        has_html = '<' in message and '>' in message
        has_links = 'http://' in message.lower() or 'https://' in message.lower()

        # Подсчет специальных элементов
        link_count = message.lower().count('http://') + message.lower().count('https://')
        tag_count = message.count('<')  # грубый подсчет HTML тегов

        return jsonify({
            "success": True,
            "preview": message,
            "length": len(message),
            "has_html": has_html,
            "has_links": has_links,
            "link_count": link_count,
            "tag_count": tag_count,
            "type": message_type
        })

    except Exception as e:
        logger.error(f"Error in preview_broadcast: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@broadcast_bp.route("/history", methods=["GET"])
@login_required
def get_history():
    """Получение истории рассылок"""
    try:
        broadcasts_col = get_broadcasts_collection()

        broadcasts = list(broadcasts_col.find(
            {},
            {
                'type': 1,
                'content': 1,
                'total_users': 1,
                'success_count': 1,
                'failed_count': 1,
                'sent_at': 1,
                'admin_id': 1
            }
        ).sort('sent_at', -1).limit(50))

        # Преобразуем данные для фронтенда
        for bc in broadcasts:
            bc['id'] = str(bc.pop('_id'))
            if 'sent_at' in bc and bc['sent_at']:
                if isinstance(bc['sent_at'], datetime):
                    bc['sent_at'] = bc['sent_at'].strftime('%Y-%m-%d %H:%M')
                else:
                    bc['sent_at'] = str(bc['sent_at'])

            # Вычисляем процент успеха
            total = bc.get('total_users', 0)
            success = bc.get('success_count', 0)
            bc['success_rate'] = round((success / total * 100), 1) if total > 0 else 0

        return jsonify({
            "success": True,
            "broadcasts": broadcasts,
            "total": len(broadcasts)
        })
    except Exception as e:
        logger.error(f"Error getting broadcast history: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@broadcast_bp.route("/test", methods=["POST"])
@login_required
def test_broadcast():
    """Тестовая рассылка"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Нет данных"
            }), 400

        # Проверяем, запущен ли бот
        if not bot_manager.is_running:
            return jsonify({
                "success": False,
                "error": "Бот не запущен"
            }), 400

        message = data.get('message', 'Тестовое сообщение').strip()
        chat_id = data.get('chat_id')

        if not chat_id:
            return jsonify({
                "success": False,
                "error": "Не указан chat_id"
            }), 400

        try:
            chat_id_int = int(chat_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": "Некорректный chat_id"
            }), 400

        broadcast_data = {
            'type': 'text',
            'content': f'<b>📢 Тестовая рассылка</b>\n\n{message}'
        }

        result = bot_manager.send_broadcast(broadcast_data, [chat_id_int])

        return jsonify({
            "success": True,
            "message": "Тестовая рассылка отправлена",
            "data": result
        })

    except Exception as e:
        logger.error(f"Error in test_broadcast: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@broadcast_bp.route("/stats", methods=["GET"])
@login_required
def broadcast_stats():
    """Статистика рассылок"""
    try:
        broadcasts_col = get_broadcasts_collection()

        # Общая статистика
        total_broadcasts = broadcasts_col.count_documents({})

        # Статистика за сегодня
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)

        today_broadcasts = broadcasts_col.count_documents({
            'sent_at': {'$gte': today_start, '$lte': today_end}
        })

        # Агрегация по успешности
        pipeline = [
            {
                '$group': {
                    '_id': None,
                    'total_users': {'$sum': '$total_users'},
                    'total_success': {'$sum': '$success_count'},
                    'total_failed': {'$sum': '$failed_count'}
                }
            }
        ]

        stats_result = list(broadcasts_col.aggregate(pipeline))

        if stats_result:
            stats = stats_result[0]
            total_users_sent = stats.get('total_users', 0)
            total_success = stats.get('total_success', 0)
            success_rate = (total_success / total_users_sent * 100) if total_users_sent > 0 else 0
        else:
            total_users_sent = 0
            success_rate = 0

        return jsonify({
            "success": True,
            "stats": {
                "total_broadcasts": total_broadcasts,
                "today_broadcasts": today_broadcasts,
                "total_users_sent": total_users_sent,
                "success_rate": round(success_rate, 1),
                "last_broadcast": get_last_broadcast_time(broadcasts_col)
            }
        })
    except Exception as e:
        logger.error(f"Error getting broadcast stats: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def get_last_broadcast_time(collection):
    """Получение времени последней рассылки"""
    last = collection.find_one({}, {'sent_at': 1}, sort=[('sent_at', -1)])
    if last and 'sent_at' in last:
        if isinstance(last['sent_at'], datetime):
            return last['sent_at'].strftime('%Y-%m-%d %H:%M')
        else:
            return str(last['sent_at'])
    return None


@broadcast_bp.route("/status", methods=["GET"])
@login_required
def bot_status():
    """Получение статуса бота"""
    try:
        status = bot_manager.get_status()
        return jsonify({
            "success": True,
            "status": status
        })
    except Exception as e:
        logger.error(f"Error getting bot status: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@broadcast_bp.route("/user_stats", methods=["GET"])
@login_required
def user_stats():
    """Получение статистики пользователей"""
    try:
        users_col = get_users_collection()

        # Общее количество пользователей
        total_users = users_col.count_documents({})

        # Пользователи с заказами
        users_with_orders = users_col.count_documents({
            "orders_count": {"$gt": 0}
        })

        # Новые пользователи за последние 7 дней
        week_ago = datetime.now() - timedelta(days=7)
        new_users = users_col.count_documents({
            "registration_date": {"$gte": week_ago}
        })

        # Распределение по количеству заказов
        orders_distribution = list(users_col.aggregate([
            {
                "$group": {
                    "_id": {
                        "$cond": [
                            {"$eq": ["$orders_count", 0]},
                            "0 заказов",
                            {
                                "$cond": [
                                    {"$lte": ["$orders_count", 5]},
                                    "1-5 заказов",
                                    {
                                        "$cond": [
                                            {"$lte": ["$orders_count", 20]},
                                            "6-20 заказов",
                                            "20+ заказов"
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]))

        return jsonify({
            "success": True,
            "stats": {
                "total_users": total_users,
                "users_with_orders": users_with_orders,
                "users_without_orders": total_users - users_with_orders,
                "new_users_last_week": new_users,
                "orders_distribution": orders_distribution
            }
        })
    except Exception as e:
        logger.error(f"Error getting user stats: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500