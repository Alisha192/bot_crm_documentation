# bot_status.py
import threading
from datetime import datetime
from admin_panel.db import db

system_col = db.system

# Глобальные переменные для кэширования
_status_cache = None
_cache_lock = threading.Lock()
_cache_timeout = 1  # секунда


def set_bot_status(status: str, error: str | None = None, token: str | None = None):
    """Установка статуса бота в БД"""
    global _status_cache

    update_data = {
        "status": status,
        "updated_at": datetime.utcnow()
    }

    if status == "running":
        update_data["started_at"] = datetime.utcnow()
    elif status in ["stopped", "error"]:
        update_data["started_at"] = None

    if error:
        update_data["error"] = str(error)
    else:
        update_data["error"] = None

    if token:
        update_data["token_preview"] = token

    try:
        with _cache_lock:
            result = system_col.update_one(
                {"_id": "telegram_bot"},
                {"$set": update_data},
                upsert=True
            )

            # Инвалидируем кэш
            _status_cache = None

            return result.modified_count > 0 or result.upserted_id is not None
    except Exception as e:
        print(f"Ошибка установки статуса бота: {e}")
        return False


def get_bot_status() -> dict:
    """Получение статуса бота из БД с кэшированием"""
    global _status_cache

    with _cache_lock:
        # Проверяем кэш
        if _status_cache and (
                datetime.utcnow() - _status_cache.get('_cached_at', datetime.min)).total_seconds() < _cache_timeout:
            return _status_cache.copy()

    try:
        doc = system_col.find_one({"_id": "telegram_bot"})

        if not doc:
            default_status = {
                "status": "stopped",
                "updated_at": datetime.utcnow(),
                "_cached_at": datetime.utcnow()
            }
            with _cache_lock:
                _status_cache = default_status
            return default_status.copy()

        # Добавляем время кэширования
        doc['_cached_at'] = datetime.utcnow()

        # Очищаем _id для JSON-сериализации
        if '_id' in doc:
            doc.pop('_id')

        with _cache_lock:
            _status_cache = doc

        return doc.copy()

    except Exception as e:
        print(f"Ошибка получения статуса бота: {e}")
        return {
            "status": "error",
            "error": str(e),
            "_cached_at": datetime.utcnow()
        }