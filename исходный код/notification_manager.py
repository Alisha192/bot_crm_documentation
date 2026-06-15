import asyncio
import logging
from typing import Dict, Any
from datetime import datetime, timedelta
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import os

logger = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self, bot, mongo_uri: str):
        self.bot = bot
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client[os.getenv("MONGO_DB", "coffee_bot")]
        self.orders_col = self.db.orders
        self.users_col = self.db.users
        self.is_running = False
        self.check_interval = 10  # секунд
        self._processed_orders = set()  # Кэш обработанных заказов

    async def start_monitoring(self):
        """Запуск мониторинга изменений статусов заказов"""
        self.is_running = True
        logger.info("Запущен мониторинг статусов заказов")

        while self.is_running:
            try:
                await self.check_order_changes()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Ошибка в мониторинге: {e}")
                await asyncio.sleep(30)

    async def check_order_changes(self):
        """Проверяет изменения в заказах"""
        try:
            # Ищем заказы где status_changed = True и status_updated_at > 30 секунд назад
            # (чтобы избежать повторной обработки)
            thirty_seconds_ago = datetime.now() - timedelta(seconds=30)

            orders_to_notify = list(self.orders_col.find({
                "status_changed": True,
                "status_updated_at": {"$gte": thirty_seconds_ago}
            }))

            logger.debug(f"Найдено {len(orders_to_notify)} заказов для уведомления")

            for order in orders_to_notify:
                try:
                    order_id = order["_id"]

                    # Проверяем, не обрабатывали ли мы уже этот заказ
                    if order_id in self._processed_orders:
                        continue

                    daily_id = order.get("daily_id", order_id)
                    user_id = order["user_id"]

                    # Отправляем уведомление
                    await self.send_order_updated_notification(user_id, order, daily_id)

                    # Сбрасываем флаг status_changed
                    self.orders_col.update_one(
                        {"_id": order_id},
                        {"$set": {"status_changed": False}}
                    )

                    # Добавляем в кэш обработанных
                    self._processed_orders.add(order_id)

                    logger.info(f"Уведомление отправлено для заказа #{daily_id}")

                except Exception as e:
                    logger.error(f"Ошибка обработки заказа {order_id}: {e}")

        except PyMongoError as e:
            logger.error(f"Ошибка БД: {e}")

    async def send_order_updated_notification(self, user_id: int, order: Dict, daily_id: int):
        """Отправляет уведомление что заказ обновлен"""
        try:
            # Проверяем, существует ли пользователь
            user = self.users_col.find_one({"_id": user_id})
            if not user:
                logger.warning(f"Пользователь {user_id} не найден, пропускаем уведомление")
                return

            total = order.get("total", 0)
            items = order.get("items", [])
            created_at = order.get("created_at", datetime.now())

            # Форматируем дату создания
            if isinstance(created_at, datetime):
                date_str = created_at.strftime("%d.%m.%Y %H:%M")
            else:
                date_str = str(created_at)

            # Форматируем товары
            items_text = "\n".join(
                f"▪ {item['name']} × {item['quantity']}"
                for item in items
            )

            message = (
                f"📦 *Ваш заказ обновлен!* #{daily_id}\n\n"
                f"*Заказ от:* {date_str}\n"
                f"*Сумма:* {total}₽\n\n"
                f"*Состав:*\n{items_text}\n\n"
                f"✅ *Статус: Готов к выдаче!*\n\n"
                f"_Спасибо, что выбираете нас!_ ☕"
            )

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="Markdown"
            )

            logger.info(f"Уведомление успешно отправлено пользователю {user_id}")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
            # Добавляем детали ошибки
            import traceback
            logger.error(f"Трассировка ошибки: {traceback.format_exc()}")

    def stop(self):
        """Остановка мониторинга"""
        self.is_running = False
        self.mongo_client.close()
        logger.info("Мониторинг остановлен")