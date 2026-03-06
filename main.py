#!/usr/bin/env python3
"""
Ruobr Telegram Bot - Главный файл запуска.

Улучшенная версия с:
- Модульной архитектурой
- Шифрованием паролей
- Асинхронными вызовами API
- Rate limiting
- Кэшированием
- Персистентным FSM
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import config
from bot.database import db_pool
from bot.middlewares import (
    RateLimitMiddleware,
    AuthMiddleware,
    LoggingMiddleware,
    ThrottlingMiddleware
)
from aiogram.types import CallbackQuery
from bot.handlers import auth, balance, schedule
from bot.services.notifications import NotificationService
from bot.services.cache import periodic_cache_cleanup


# Настройка логирования
def setup_logging() -> None:
    """Настройка системы логирования."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                config.data_dir / "bot.log",
                encoding="utf-8"
            )
        ]
    )
    
    # Уменьшаем уровень логирования для aiogram (но оставляем INFO для отладки)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def main() -> None:
    """Главная функция запуска бота."""
    # Настройка логирования
    setup_logging()
    logger.info("Starting Ruobr Telegram Bot v2.0")
    
    # Инициализация базы данных
    await db_pool.initialize()
    logger.info("Database initialized")
    
    # Создание бота и диспетчера
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Используем MemoryStorage для FSM (можно заменить на RedisStorage)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрация middleware для сообщений
    dp.message.middleware(RateLimitMiddleware(
        limit=config.rate_limit_per_minute,
        window_seconds=60,
        block_duration=30
    ))
    dp.message.middleware(AuthMiddleware())
    dp.message.middleware(LoggingMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    
    # Регистрация middleware для callback queries
    dp.callback_query.middleware(AuthMiddleware())
    
    # Регистрация routers
    dp.include_router(auth.router)
    dp.include_router(balance.router)
    dp.include_router(schedule.router)
    
    # Сервис уведомлений
    notification_service = NotificationService(bot)
    
    # Удаляем вебхук если был установлен (обязательно для polling!)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted, starting polling...")
    
    # Запуск фоновых задач
    notification_task = asyncio.create_task(notification_service.start())
    cache_cleanup_task = asyncio.create_task(periodic_cache_cleanup(interval=300))
    
    logger.info("Bot started. Press Ctrl+C to stop.")
    
    # Обработка сигналов для корректного завершения
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        notification_service.stop()
        notification_task.cancel()
        cache_cleanup_task.cancel()
        dp.stop_polling()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass
    
    try:
        # Запуск polling (обрабатываем сообщения и callback queries)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except asyncio.CancelledError:
        logger.info("Polling cancelled")
    finally:
        # Очистка ресурсов
        logger.info("Shutting down...")
        
        # Отмена фоновых задач
        notification_task.cancel()
        cache_cleanup_task.cancel()
        
        try:
            await notification_task
        except asyncio.CancelledError:
            pass
        
        try:
            await cache_cleanup_task
        except asyncio.CancelledError:
            pass
        
        # Закрытие базы данных
        await db_pool.close()
        
        # Закрытие сессии бота
        await bot.session.close()
        
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.exception(f"Fatal error: {e}")
        sys.exit(1)
