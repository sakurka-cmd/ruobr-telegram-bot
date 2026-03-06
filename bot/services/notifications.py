"""
Фоновые задачи для уведомлений.
"""
import asyncio
import logging
from datetime import date, timedelta
from typing import Dict, List, Set

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from ..config import config
from ..database import (
    get_all_enabled_users,
    get_all_thresholds_for_chat,
    is_notification_sent,
    mark_notification_sent,
    cleanup_old_notifications
)
from ..services import (
    get_children_async,
    get_food_for_children,
    get_timetable_for_children,
    Child
)
from ..utils.formatters import truncate_text

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Сервис фоновых уведомлений.
    Отслеживает изменения баланса и новые оценки.
    """
    
    MARKS_CHECK_DAYS = 14  # Проверять оценки за последние 14 дней
    
    def __init__(self, bot: Bot):
        self._bot = bot
        self._running = False
        self._prev_balances: Dict[int, Dict[int, float]] = {}
        self._prev_marks: Dict[int, Set[str]] = {}
        self._prev_food_visits: Dict[int, Set[str]] = {}  # Для отслеживания питания
    
    async def start(self) -> None:
        """Запуск фонового мониторинга."""
        self._running = True
        logger.info("Notification service started")
        
        while self._running:
            try:
                await self._check_all_users()
            except Exception as e:
                logger.error(f"Error in notification loop: {e}", exc_info=True)
            
            # Периодическая очистка старых записей
            await cleanup_old_notifications(days=30)
            
            await asyncio.sleep(config.check_interval_seconds)
    
    def stop(self) -> None:
        """Остановка мониторинга."""
        self._running = False
        logger.info("Notification service stopped")
    
    async def _check_all_users(self) -> None:
        """Проверка всех пользователей с включёнными уведомлениями."""
        users = await get_all_enabled_users()
        
        if not users:
            logger.debug("No users with enabled notifications")
            return
        
        logger.info(f"Checking notifications for {len(users)} users")
        
        # Обрабатываем пользователей параллельно с ограничением
        semaphore = asyncio.Semaphore(5)  # Максимум 5 параллельных запросов
        
        async def process_with_limit(user):
            async with semaphore:
                try:
                    await self._process_user(user)
                except Exception as e:
                    logger.error(f"Error processing user {user.chat_id}: {e}")
        
        tasks = [process_with_limit(user) for user in users]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _process_user(self, user) -> None:
        """Обработка уведомлений для одного пользователя."""
        if not user.login or not user.password:
            return
        
        try:
            children = await get_children_async(user.login, user.password)
        except Exception as e:
            logger.warning(f"Failed to get children for user {user.chat_id}: {e}")
            return
        
        if not children:
            return
        
        # Проверяем уведомления о балансе
        if user.enabled:
            await self._check_balance_notifications(user.chat_id, user.login, user.password, children)
        
        # Проверяем уведомления об оценках
        if user.marks_enabled:
            await self._check_marks_notifications(user.chat_id, user.login, user.password, children)
        
        # Проверяем уведомления о питании
        if hasattr(user, 'food_enabled') and user.food_enabled:
            await self._check_food_notifications(user.chat_id, user.login, user.password, children)
    
    async def _check_balance_notifications(
        self,
        chat_id: int,
        login: str,
        password: str,
        children: List[Child]
    ) -> None:
        """Проверка и отправка уведомлений о балансе."""
        try:
            food_info = await get_food_for_children(login, password, children)
            thresholds = await get_all_thresholds_for_chat(chat_id)
            
            alerts = []
            new_balances: Dict[int, float] = {}
            
            for child in children:
                info = food_info.get(child.id)
                if not info or not info.has_food:
                    new_balances[child.id] = 0.0
                    continue
                
                balance = info.balance
                new_balances[child.id] = balance
                
                threshold = thresholds.get(child.id, config.default_balance_threshold)
                prev_balance = self._prev_balances.get(chat_id, {}).get(child.id)
                
                should_notify = False
                reason = ""
                
                # Условие 1: Баланс упал ниже порога
                if balance < threshold:
                    if prev_balance is None or prev_balance >= threshold:
                        should_notify = True
                        reason = f"баланс упал ниже порога {threshold:.0f} ₽"
                
                # Условие 2: Произошло списание
                if prev_balance is not None:
                    delta = balance - prev_balance
                    if delta < -0.01:  # Списание более 1 копейки
                        should_notify = True
                        reason = f"списано {abs(delta):.0f} ₽"
                
                if should_notify:
                    # Проверяем дедупликацию
                    notif_key = f"balance:{child.id}:{int(balance)}"
                    if await is_notification_sent(chat_id, "balance", notif_key):
                        continue
                    
                    alerts.append(
                        f"{child.full_name} ({child.group}):\n"
                        f"  📉 {reason}\n"
                        f"  💰 Текущий баланс: <b>{balance:.0f} ₽</b>"
                    )
                    
                    await mark_notification_sent(chat_id, "balance", notif_key)
            
            self._prev_balances[chat_id] = new_balances
            
            if alerts:
                text = "⚠️ <b>Уведомление о балансе</b>\n\n" + "\n\n".join(alerts)
                await self._send_notification(chat_id, text)
                
        except Exception as e:
            logger.error(f"Error checking balance for user {chat_id}: {e}")
    
    async def _check_marks_notifications(
        self,
        chat_id: int,
        login: str,
        password: str,
        children: List[Child]
    ) -> None:
        """Проверка и отправка уведомлений о новых оценках."""
        try:
            today = date.today()
            start = today - timedelta(days=self.MARKS_CHECK_DAYS)  # За последние 14 дней
            
            timetable = await get_timetable_for_children(
                login, password, children, start, today
            )
            
            all_marks: List[dict] = []
            
            for child in children:
                lessons = timetable.get(child.id, [])
                for lesson in lessons:
                    for mark in lesson.marks:
                        all_marks.append({
                            "child_name": child.full_name,
                            "child_group": child.group,
                            "date": lesson.date,
                            "subject": lesson.subject,
                            "question_type": mark.get("question_type") or mark.get("question_name"),
                            "value": mark.get("mark"),
                            "question_id": mark.get("question_id")
                        })
            
            # Формируем ключи для сравнения
            new_keys: Set[str] = set()
            for m in all_marks:
                key = f"{m['date']}|{m['subject']}|{m['question_id']}|{m['value']}"
                new_keys.add(key)
            
            prev_keys = self._prev_marks.get(chat_id)
            self._prev_marks[chat_id] = new_keys
            
            if prev_keys is None:
                # Первый запуск, пропускаем
                return
            
            # Находим новые оценки
            new_marks = [m for m in all_marks 
                        if f"{m['date']}|{m['subject']}|{m['question_id']}|{m['value']}" not in prev_keys]
            
            if new_marks:
                lines = ["⭐ <b>Новые оценки!</b>\n"]
                
                for m in new_marks:
                    lines.append(
                        f"👤 {m['child_name']} ({m['child_group']})\n"
                        f"📚 {m['subject']}: {m['question_type']} → <b>{m['value']}</b>\n"
                        f"📅 {m['date']}"
                    )
                
                text = truncate_text("\n".join(lines))
                await self._send_notification(chat_id, text)
                
        except Exception as e:
            logger.error(f"Error checking marks for user {chat_id}: {e}")
    
    async def _check_food_notifications(
        self,
        chat_id: int,
        login: str,
        password: str,
        children: List[Child]
    ) -> None:
        """Проверка и отправка уведомлений о питании (когда ребёнок поел)."""
        try:
            today = date.today()
            today_str = today.strftime("%Y-%m-%d")
            
            food_info = await get_food_for_children(login, password, children)
            
            alerts = []
            new_visits: Set[str] = set()
            
            for child in children:
                info = food_info.get(child.id)
                if not info or not info.visits:
                    continue
                
                for visit in info.visits:
                    visit_date = visit.get("date", "")
                    
                    # Проверяем только за сегодня
                    if visit_date != today_str:
                        continue
                    
                    # Проверяем, было ли подтверждённое питание
                    if not visit.get("ordered") and visit.get("state") != 30:
                        continue
                    
                    # Формируем уникальный ключ визита
                    visit_key = f"{child.id}:{visit_date}:{visit.get('line', 0)}:{visit.get('price', 0)}"
                    new_visits.add(visit_key)
                    
                    # Проверяем, новое ли это питание
                    prev_visits = self._prev_food_visits.get(chat_id, set())
                    
                    if visit_key not in prev_visits:
                        # Новое питание!
                        price = visit.get("price", 0)
                        meal_type = visit.get("line_name", "Питание")
                        
                        alerts.append(
                            f"🍽 {child.full_name} ({child.group}):\n"
                            f"  🕐 {meal_type}\n"
                            f"  💰 Списано: <b>{price:.0f} ₽</b>"
                        )
            
            # Обновляем список визитов
            self._prev_food_visits[chat_id] = new_visits
            
            if alerts:
                text = f"🍽 <b>Ребёнок поел!</b> ({today_str})\n\n" + "\n\n".join(alerts)
                await self._send_notification(chat_id, text)
                
        except Exception as e:
            logger.error(f"Error checking food for user {chat_id}: {e}")
    
    async def _send_notification(self, chat_id: int, text: str) -> None:
        """Отправка уведомления пользователю."""
        try:
            await self._bot.send_message(chat_id, text)
            logger.info(f"Notification sent to user {chat_id}")
        except TelegramAPIError as e:
            logger.error(f"Failed to send notification to {chat_id}: {e}")
            
            # Если пользователь заблокировал бота, отключаем уведомления
            if "blocked" in str(e).lower() or "user is deactivated" in str(e).lower():
                from ..database import create_or_update_user
                await create_or_update_user(chat_id, enabled=False, marks_enabled=False)
                logger.info(f"Disabled notifications for blocked user {chat_id}")
