# 🎓 Ruobr Telegram Bot

Telegram-бот для родителей, позволяющий следить за:
- 💰 Балансом школьного питания
- 📅 Расписанием уроков
- 📘 Домашними заданиями
- ⭐ Оценками

## Установка

```bash
git clone https://github.com/vbardanos/ruobr-telegram-bot.git
cd ruobr-telegram-bot

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Настройка

Создайте файл `.env`:
```
BOT_TOKEN=your_telegram_bot_token
ENCRYPTION_KEY=your_fernet_key
ADMIN_IDS=123456789
```

Генерация ключа шифрования:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

## Запуск

```bash
python main.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Начало работы |
| `/set_login` | Настройка логина/пароля Ruobr |
| `/balance` | Баланс питания |
| `/ttoday` | Расписание сегодня |
| `/ttomorrow` | Расписание завтра |
| `/hwtomorrow` | ДЗ на завтра |
| `/markstoday` | Оценки за сегодня |

## Технологии

- Python 3.10+
- aiogram 3.x
- aiosqlite
- cryptography

## Лицензия

MIT
