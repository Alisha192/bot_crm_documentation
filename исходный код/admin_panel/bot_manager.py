# bot_manager.py
import threading
import asyncio
import time
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging
from admin_panel.bot_status import set_bot_status, get_bot_status

# Импортируем requests в начале файла
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class BotManager:
    def __init__(self):
        self.bot_thread: Optional[threading.Thread] = None
        self.bot_token: Optional[str] = None
        self.is_running = False
        self.start_time: Optional[datetime] = None
        self.bot_application = None
        self.bot_instance = None
        self._session = None  # Сессия для рассылок

    def _get_session(self):
        """Создает сессию requests с повторными попытками"""
        if self._session is None:
            session = requests.Session()

            # Настройка повторных попыток
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST"]
            )

            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=10,
                pool_maxsize=10
            )

            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session = session

        return self._session

    def set_bot_application(self, application):
        """Установка приложения бота из main.py"""
        self.bot_application = application
        if application and hasattr(application, 'bot'):
            self.bot_instance = application.bot
            logger.info("Bot application set in manager")

    def get_bot_instance(self):
        """Получение экземпляра бота"""
        if self.bot_instance:
            return self.bot_instance
        elif self.bot_application and hasattr(self.bot_application, 'bot'):
            return self.bot_application.bot
        return None

    def start_bot(self, token: str) -> bool:
        """Запуск бота в отдельном потоке"""
        if self.is_running:
            raise Exception("Бот уже запущен")

        self._cleanup_old_thread()

        self.bot_token = token
        self.start_time = datetime.now()

        set_bot_status("starting", token=token[:20] + "...")
        self.is_running = True

        self.bot_thread = threading.Thread(
            target=self._run_bot_thread,
            args=(token,),
            daemon=True,
            name="TelegramBotThread"
        )
        self.bot_thread.start()

        logger.info(f"Запущен поток бота с токеном: {token[:10]}...")
        return True

    def _run_bot_thread(self, token: str):
        """Функция, которая выполняется в потоке бота"""
        try:
            from main import run_bot

            logger.info(f"Начинаем запуск бота в потоке")
            run_bot(token)

        except Exception as e:
            logger.error(f"Ошибка при запуске бота: {e}", exc_info=True)
            set_bot_status("error", str(e))
            self.is_running = False
        finally:
            logger.info("Поток бота завершен")

    def _cleanup_old_thread(self):
        """Очистка старого потока"""
        if self.bot_thread and self.bot_thread.is_alive():
            try:
                self.bot_thread.join(timeout=3)
                if self.bot_thread.is_alive():
                    logger.warning("Старый поток бота не завершился корректно")
            except Exception as e:
                logger.error(f"Ошибка при очистке старого потока: {e}")

        self.bot_thread = None
        self.is_running = False
        # Закрываем сессию при очистке
        if self._session:
            self._session.close()
            self._session = None

    def stop_bot(self) -> bool:
        """Остановка бота"""
        try:
            from main import stop_bot as stop_bot_main

            stop_bot_main()

            if self.bot_thread and self.bot_thread.is_alive():
                self.bot_thread.join(timeout=5)
                if self.bot_thread.is_alive():
                    logger.warning("Поток бота не завершился за 5 секунд")

            self.bot_thread = None
            self.bot_token = None
            self.is_running = False
            self.start_time = None

            # Закрываем сессию
            if self._session:
                self._session.close()
                self._session = None

            set_bot_status("stopped")
            logger.info("Бот успешно остановлен")
            return True

        except Exception as e:
            logger.error(f"Ошибка при остановке бота: {e}", exc_info=True)
            set_bot_status("error", str(e))
            return False

    def get_status(self) -> dict:
        """Получение текущего статуса бота"""
        try:
            status_data = get_bot_status()

            if self.start_time and status_data.get('status') == 'running':
                status_data['started_at'] = self.start_time.isoformat()
                status_data['uptime'] = str(datetime.now() - self.start_time)

            status_data['thread_alive'] = self.bot_thread.is_alive() if self.bot_thread else False
            status_data['is_running'] = self.is_running

            if status_data.get('status') == 'running' and not self.is_running:
                logger.warning("Статус в БД 'running', но бот не запущен. Исправляем...")
                set_bot_status("stopped")
                status_data = get_bot_status()

            return status_data

        except Exception as e:
            logger.error(f"Ошибка получения статуса: {e}")
            return {"status": "error", "error": str(e)}

    # ===== ФУНКЦИИ ДЛЯ РАССЫЛКИ =====

    def send_broadcast(self, broadcast_data: Dict[str, Any], user_ids: List[int] = None) -> Dict[str, Any]:
        """Синхронная рассылка через requests (отдельный пул соединений)"""
        if not self.is_running or not self.bot_token:
            raise Exception("Бот не запущен или токен не найден")

        # Получаем пользователей
        if user_ids is None:
            user_ids = self.get_user_ids()

        if not user_ids:
            return {
                'total': 0,
                'success': 0,
                'failed': 0,
                'blocked': 0,
                'not_found': 0,
                'errors': []
            }

        results = {
            'total': len(user_ids),
            'success': 0,
            'failed': 0,
            'blocked': 0,
            'not_found': 0,
            'errors': []
        }

        base_url = f"https://api.telegram.org/bot{self.bot_token}"

        logger.info(f"Начинаем синхронную рассылку для {len(user_ids)} пользователей")

        # Используем отдельную сессию для рассылки
        session = self._get_session()

        for i, chat_id in enumerate(user_ids, 1):
            try:
                payload = {
                    'chat_id': chat_id,
                    'text': broadcast_data.get('content', ''),
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True
                }

                response = session.post(
                    f"{base_url}/sendMessage",
                    json=payload,
                    timeout=30
                )

                if response.status_code == 200:
                    results['success'] += 1
                else:
                    results['failed'] += 1
                    try:
                        response_data = response.json()
                        if 'description' in response_data:
                            desc = response_data['description'].lower()
                            if 'blocked' in desc or 'forbidden' in desc:
                                results['blocked'] += 1
                            elif 'chat not found' in desc or 'user not found' in desc:
                                results['not_found'] += 1
                    except:
                        pass

                    if len(results['errors']) < 10:
                        results['errors'].append({
                            'chat_id': chat_id,
                            'error': response.text[:200] if response.text else f"HTTP {response.status_code}",
                            'error_type': 'api_error'
                        })

                # Логируем прогресс каждые 10 сообщений
                if i % 10 == 0:
                    logger.info(f"Прогресс: {i}/{len(user_ids)} ({(i / len(user_ids)) * 100:.1f}%)")

                # Большая задержка для избежания лимитов Telegram
                time.sleep(2.5)  # ~0.4 сообщений в секунду

            except requests.exceptions.Timeout:
                results['failed'] += 1
                logger.warning(f"Таймаут отправки пользователю {chat_id}")

                if len(results['errors']) < 10:
                    results['errors'].append({
                        'chat_id': chat_id,
                        'error': 'Таймаут отправки',
                        'error_type': 'timeout'
                    })

                time.sleep(5.0)  # Большая пауза при таймауте

            except Exception as e:
                results['failed'] += 1
                logger.error(f"Ошибка отправки пользователю {chat_id}: {e}")

                if len(results['errors']) < 10:
                    results['errors'].append({
                        'chat_id': chat_id,
                        'error': str(e)[:200],
                        'error_type': 'exception'
                    })

                time.sleep(3.0)  # Пауза при ошибке

        logger.info(f"Рассылка завершена: успешно {results['success']}, "
                    f"не отправлено {results['failed']}")

        # Ограничиваем количество ошибок в ответе
        results['errors'] = results['errors'][:10]

        return results

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Получение всех пользователей из БД"""
        try:
            from pymongo import MongoClient
            import os

            MONGO_URI = os.getenv("MONGO_URI")
            MONGO_DB = os.getenv("MONGO_DB", "coffee_bot")

            mongo_client = MongoClient(MONGO_URI)
            db = mongo_client[MONGO_DB]
            users_col = db.users

            users = list(users_col.find(
                {},
                {
                    '_id': 1,
                    'first_name': 1,
                    'last_name': 1,
                    'username': 1,
                    'preferred_name': 1,
                    'phone': 1,
                    'registration_date': 1,
                    'orders_count': 1
                }
            ).sort('registration_date', -1))

            for user in users:
                user['id'] = str(user.pop('_id'))

            mongo_client.close()
            return users

        except Exception as e:
            logger.error(f"Ошибка при получении пользователей: {e}", exc_info=True)
            return []

    def get_user_ids(self) -> List[int]:
        """Получение всех ID пользователей"""
        try:
            from pymongo import MongoClient
            import os

            MONGO_URI = os.getenv("MONGO_URI")
            MONGO_DB = os.getenv("MONGO_DB", "coffee_bot")

            mongo_client = MongoClient(MONGO_URI)
            db = mongo_client[MONGO_DB]
            users_col = db.users

            users = users_col.find({}, {'_id': 1})
            user_ids = [user['_id'] for user in users]

            mongo_client.close()
            return user_ids

        except Exception as e:
            logger.error(f"Ошибка при получении ID пользователей: {e}", exc_info=True)
            return []

    def test_broadcast_single(self, chat_id: int, message: str) -> Dict[str, Any]:
        """Тестовая рассылка одному пользователю"""
        broadcast_data = {
            'type': 'text',
            'content': f'<b>📢 Тестовое сообщение</b>\n\n{message}'
        }

        return self.send_broadcast(broadcast_data, [chat_id])


# Глобальный экземпляр менеджера
bot_manager = BotManager()