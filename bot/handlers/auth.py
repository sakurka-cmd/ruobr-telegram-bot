"""
Обработчики аутентификации и базовых команд.
"""
import logging
from typing import Dict, Any, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from ..config import config
from ..database import get_user, create_or_update_user, UserConfig
from ..states import LoginStates
from ..services import get_children_async, AuthenticationError

logger = logging.getLogger(__name__)

router = Router()


# ===== Клавиатуры =====

def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Создание главной клавиатуры."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Расписание сегодня"),
                KeyboardButton(text="📅 Расписание завтра"),
            ],
            [
                KeyboardButton(text="📘 ДЗ на завтра"),
                KeyboardButton(text="⭐ Оценки сегодня"),
            ],
            [
                KeyboardButton(text="💰 Баланс питания"),
                KeyboardButton(text="🍽 Питание сегодня"),
            ],
            [
                KeyboardButton(text="⚙️ Настройки"),
                KeyboardButton(text="❓ Помощь"),
            ],
        ],
        resize_keyboard=True,
        persistent=True
    )


def get_settings_keyboard() -> ReplyKeyboardMarkup:
    """Создание клавиатуры настроек."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔑 Изменить логин/пароль"),
                KeyboardButton(text="💰 Порог баланса"),
            ],
            [
                KeyboardButton(text="🔔 Уведомления"),
                KeyboardButton(text="👤 Мой профиль"),
            ],
            [
                KeyboardButton(text="◀️ Назад"),
            ],
        ],
        resize_keyboard=True
    )


# ===== Команды =====

@router.message(Command("start"))
async def cmd_start(message: Message, user_config: Optional[UserConfig] = None):
    """Обработка команды /start."""
    # Создаём пользователя если не существует
    if user_config is None:
        user_config = await create_or_update_user(message.chat.id)
    
    is_auth = user_config.login and user_config.password
    
    welcome_text = (
        "👋 <b>Добро пожаловать в школьный бот!</b>\n\n"
        "Я помогаю родителям следить за:\n"
        "• 💰 Балансом школьного питания\n"
        "• 📅 Расписанием уроков\n"
        "• 📘 Домашними заданиями\n"
        "• ⭐ Оценками\n\n"
    )
    
    if not is_auth:
        welcome_text += (
            "⚠️ <b>Требуется настройка!</b>\n"
            "Используйте /set_login для ввода учётных данных от cabinet.ruobr.ru\n\n"
        )
    else:
        welcome_text += "✅ Учётные данные настроены. Используйте кнопки меню или команды.\n\n"
    
    welcome_text += (
        "📖 <b>Основные команды:</b>\n"
        "/set_login — настроить логин/пароль\n"
        "/balance — баланс питания\n"
        "/ttoday — расписание на сегодня\n"
        "/ttomorrow — расписание на завтра\n"
        "/hwtomorrow — ДЗ на завтра\n"
        "/markstoday — оценки за сегодня"
    )
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard()
    )


@router.message(Command("set_login"))
async def cmd_set_login(message: Message, state: FSMContext):
    """Начало процесса ввода логина."""
    await state.clear()
    await message.answer(
        "🔐 <b>Настройка учётных данных</b>\n\n"
        "Введите логин от cabinet.ruobr.ru:"
    )
    await state.set_state(LoginStates.waiting_for_login)


@router.message(LoginStates.waiting_for_login)
async def process_login(message: Message, state: FSMContext):
    """Обработка введённого логина."""
    login = message.text.strip()
    
    if not login:
        await message.answer("❌ Логин не может быть пустым. Попробуйте ещё раз:")
        return
    
    if len(login) > 100:
        await message.answer("❌ Логин слишком длинный. Попробуйте ещё раз:")
        return
    
    await state.update_data(login=login)
    await message.answer(
        "✅ Логин сохранён.\n\n"
        "Теперь введите пароль от cabinet.ruobr.ru:"
    )
    await state.set_state(LoginStates.waiting_for_password)


@router.message(LoginStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    """Обработка введённого пароля и проверка учётных данных."""
    password = message.text.strip()
    
    if not password:
        await message.answer("❌ Пароль не может быть пустым. Попробуйте ещё раз:")
        return
    
    data = await state.get_data()
    login = data.get("login", "")
    
    # Удаляем сообщение с паролем для безопасности
    try:
        await message.delete()
    except Exception:
        pass
    
    status_message = await message.answer("🔄 Проверка учётных данных...")
    
    try:
        # Проверяем учётные данные
        children = await get_children_async(login, password)
        
        if not children:
            await status_message.edit_text(
                "⚠️ Учётные данные верны, но дети не найдены в аккаунте.\n"
                "Данные сохранены. Проверьте аккаунт на cabinet.ruobr.ru"
            )
        else:
            children_list = "\n".join([
                f"  • {child.full_name} ({child.group})"
                for child in children
            ])
            await status_message.edit_text(
                f"✅ <b>Успешная авторизация!</b>\n\n"
                f"Найдены дети:\n{children_list}\n\n"
                f"Теперь доступны все функции бота.",
                reply_markup=get_main_keyboard()
            )
        
        # Сохраняем учётные данные
        await create_or_update_user(
            message.chat.id,
            login=login,
            password=password
        )
        
    except AuthenticationError:
        await status_message.edit_text(
            "❌ <b>Ошибка авторизации!</b>\n\n"
            "Неверный логин или пароль. Попробуйте снова с /set_login"
        )
    except Exception as e:
        logger.error(f"Error during login for user {message.chat.id}: {e}")
        await status_message.edit_text(
            "❌ <b>Ошибка соединения!</b>\n\n"
            "Не удалось проверить учётные данные. Попробуйте позже."
        )
    
    await state.clear()


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущей операции."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активной операции для отмены.")
        return
    
    await state.clear()
    await message.answer(
        "❌ Операция отменена.",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message):
    """Показать справку."""
    help_text = (
        "📖 <b>Справка по боту</b>\n\n"
        
        "<b>🔐 Настройка:</b>\n"
        "• /set_login — ввести логин/пароль от Ruobr\n"
        
        "\n<b>💰 Питание:</b>\n"
        "• /balance — баланс питания всех детей\n"
        "• /foodtoday — что ели сегодня и сколько списано\n"
        
        "\n<b>📅 Расписание:</b>\n"
        "• /ttoday — расписание на сегодня\n"
        "• /ttomorrow — расписание на завтра\n"
        
        "\n<b>📘 Домашнее задание:</b>\n"
        "• /hwtomorrow — ДЗ на завтра\n"
        
        "\n<b>⭐ Оценки:</b>\n"
        "• /markstoday — оценки за сегодня\n"
        
        "\n<b>⚙️ Настройки:</b>\n"
        "• /enable — включить автоуведомления\n"
        "• /disable — выключить автоуведомления\n"
        "• /set_threshold — настроить порог баланса\n"
        
        "\n<b>🔔 Уведомления:</b>\n"
        "Бот автоматически уведомляет о:\n"
        "• Снижении баланса питания ниже порога\n"
        "• Новых оценках\n"
    )
    await message.answer(help_text)


@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message):
    """Показать меню настроек."""
    await message.answer(
        "⚙️ <b>Настройки</b>",
        reply_markup=get_settings_keyboard()
    )


@router.message(F.text == "🔑 Изменить логин/пароль")
async def btn_change_login(message: Message, state: FSMContext):
    """Начать процесс изменения логина/пароля."""
    await cmd_set_login(message, state)


@router.message(F.text == "◀️ Назад")
async def btn_back(message: Message):
    """Возврат в главное меню."""
    await message.answer(
        "🏠 <b>Главное меню</b>",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "👤 Мой профиль")
async def btn_profile(message: Message, user_config: Optional[UserConfig] = None):
    """Показать профиль пользователя."""
    if user_config is None:
        user_config = await get_user(message.chat.id)
    
    if user_config is None:
        await message.answer("Профиль не найден. Используйте /start")
        return
    
    status = "✅ Настроен" if user_config.login and user_config.password else "❌ Не настроен"
    notif_status = "🔔 Включены" if user_config.enabled else "🔕 Выключены"
    marks_status = "🔔 Включены" if user_config.marks_enabled else "🔕 Выключены"
    
    profile_text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Логин:</b> {user_config.login or 'не указан'}\n\n"
        f"<b>Уведомления о балансе:</b> {notif_status}\n"
        f"<b>Уведомления об оценках:</b> {marks_status}\n\n"
        f"{'⚠️ Введите логин/пароль через Настройки' if not user_config.login else ''}"
    )
    
    await message.answer(profile_text)


@router.message(Command("enable"))
async def cmd_enable(message: Message):
    """Включение уведомлений."""
    user = await create_or_update_user(
        message.chat.id,
        enabled=True,
        marks_enabled=True
    )
    
    await message.answer(
        "🔔 <b>Уведомления включены!</b>\n\n"
        "Вы будете получать уведомления о:\n"
        "• Снижении баланса питания\n"
        "• Новых оценках"
    )


@router.message(Command("disable"))
async def cmd_disable(message: Message):
    """Выключение уведомлений."""
    user = await create_or_update_user(
        message.chat.id,
        enabled=False,
        marks_enabled=False
    )
    
    await message.answer("🔕 <b>Уведомления отключены.</b>")


# ===== Inline клавиатуры для настроек =====

def get_notification_keyboard(user_config: UserConfig) -> InlineKeyboardMarkup:
    """Создание inline клавиатуры для настроек уведомлений."""
    balance_status = "✅" if user_config.enabled else "❌"
    marks_status = "✅" if user_config.marks_enabled else "❌"
    food_status = "✅" if user_config.food_enabled else "❌"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💰 Баланс: {balance_status}",
                    callback_data="toggle_balance"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ Оценки: {marks_status}",
                    callback_data="toggle_marks"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🍽 Питание: {food_status}",
                    callback_data="toggle_food"
                )
            ],
        ]
    )


@router.message(F.text == "🔔 Уведомления")
async def btn_notifications_inline(message: Message, user_config: Optional[UserConfig] = None):
    """Показать настройки уведомлений с inline кнопками."""
    if user_config is None:
        user_config = await get_user(message.chat.id)
    
    if user_config is None:
        user_config = await create_or_update_user(message.chat.id)
    
    text = (
        "🔔 <b>Настройки уведомлений</b>\n\n"
        "Нажмите на кнопку, чтобы включить/выключить уведомление:"
    )
    
    await message.answer(
        text,
        reply_markup=get_notification_keyboard(user_config)
    )


@router.callback_query(F.data == "toggle_balance")
async def cb_toggle_balance(callback: CallbackQuery, user_config: Optional[UserConfig] = None):
    """Переключение уведомлений о балансе."""
    if user_config is None:
        user_config = await get_user(callback.message.chat.id)
    
    if user_config is None:
        await callback.answer("Ошибка: профиль не найден")
        return
    
    new_status = not user_config.enabled
    await create_or_update_user(
        callback.message.chat.id,
        enabled=new_status
    )
    
    status_text = "включены" if new_status else "выключены"
    await callback.answer(f"Уведомления о балансе {status_text}!")
    
    # Обновляем клавиатуру
    updated_config = await get_user(callback.message.chat.id)
    await callback.message.edit_reply_markup(
        reply_markup=get_notification_keyboard(updated_config)
    )


@router.callback_query(F.data == "toggle_marks")
async def cb_toggle_marks(callback: CallbackQuery, user_config: Optional[UserConfig] = None):
    """Переключение уведомлений об оценках."""
    if user_config is None:
        user_config = await get_user(callback.message.chat.id)
    
    if user_config is None:
        await callback.answer("Ошибка: профиль не найден")
        return
    
    new_status = not user_config.marks_enabled
    await create_or_update_user(
        callback.message.chat.id,
        marks_enabled=new_status
    )
    
    status_text = "включены" if new_status else "выключены"
    await callback.answer(f"Уведомления об оценках {status_text}!")
    
    # Обновляем клавиатуру
    updated_config = await get_user(callback.message.chat.id)
    await callback.message.edit_reply_markup(
        reply_markup=get_notification_keyboard(updated_config)
    )


@router.callback_query(F.data == "toggle_food")
async def cb_toggle_food(callback: CallbackQuery, user_config: Optional[UserConfig] = None):
    """Переключение уведомлений о питании."""
    if user_config is None:
        user_config = await get_user(callback.message.chat.id)
    
    if user_config is None:
        await callback.answer("Ошибка: профиль не найден")
        return
    
    new_status = not user_config.food_enabled
    await create_or_update_user(
        callback.message.chat.id,
        food_enabled=new_status
    )
    
    status_text = "включены" if new_status else "выключены"
    await callback.answer(f"Уведомления о питании {status_text}!")
    
    # Обновляем клавиатуру
    updated_config = await get_user(callback.message.chat.id)
    await callback.message.edit_reply_markup(
        reply_markup=get_notification_keyboard(updated_config)
    )
