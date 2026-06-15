# broadcast_controller.py
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
from bot_manager import bot_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broadcast", tags=["broadcast"])


class BroadcastMessage(BaseModel):
    """Модель сообщения для рассылки"""
    type: str = "text"  # text, photo, document
    content: str
    photo: Optional[str] = None  # file_id или URL для фото
    document: Optional[str] = None  # file_id или URL для документа
    user_ids: Optional[List[int]] = None  # Если None - рассылка всем


class BroadcastResponse(BaseModel):
    """Модель ответа рассылки"""
    status: str
    message: str
    results: Optional[Dict[str, Any]] = None


@router.get("/users", response_model=Dict[str, Any])
async def get_users():
    """Получить список всех пользователей"""
    try:
        users = bot_manager.get_all_users()
        return {
            "status": "success",
            "total": len(users),
            "users": users
        }
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send", response_model=BroadcastResponse)
async def send_broadcast(
        message: BroadcastMessage,
        background_tasks: BackgroundTasks
):
    """Отправить рассылку пользователям"""
    try:
        if not bot_manager.is_running:
            raise HTTPException(
                status_code=400,
                detail="Бот не запущен. Запустите бота перед рассылкой."
            )

        # Валидация типа сообщения
        if message.type not in ["text", "photo", "document"]:
            raise HTTPException(
                status_code=400,
                detail="Неверный тип сообщения. Допустимые типы: text, photo, document"
            )

        # Подготовка данных для рассылки
        broadcast_data = {
            "type": message.type,
            "content": message.content
        }

        if message.type == "photo" and message.photo:
            broadcast_data["photo"] = message.photo
        elif message.type == "document" and message.document:
            broadcast_data["document"] = message.document

        # Запуск рассылки в фоновой задаче
        background_tasks.add_task(
            bot_manager.send_broadcast,
            broadcast_data,
            message.user_ids
        )

        total_users = len(message.user_ids) if message.user_ids else "всех"

        return BroadcastResponse(
            status="processing",
            message=f"Рассылка начата для {total_users} пользователей",
            results={"total": total_users}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при запуске рассылки: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/{chat_id}", response_model=BroadcastResponse)
async def test_broadcast(chat_id: int, message: str = "Тестовое сообщение"):
    """Тестовая рассылка на один chat_id"""
    try:
        if not bot_manager.is_running:
            raise HTTPException(
                status_code=400,
                detail="Бот не запущен"
            )

        results = bot_manager.test_broadcast_single(chat_id, message)

        return BroadcastResponse(
            status="success" if results.get("success", 0) > 0 else "error",
            message=f"Тестовая рассылка отправлена",
            results=results
        )

    except Exception as e:
        logger.error(f"Ошибка тестовой рассылки: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", response_model=Dict[str, Any])
async def get_broadcast_stats():
    """Получить статистику по пользователям"""
    try:
        user_ids = bot_manager.get_user_ids()

        return {
            "status": "success",
            "total_users": len(user_ids),
            "active_users": len(user_ids)  # Здесь можно добавить логику определения активных
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(status_code=500, detail=str(e))