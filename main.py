import logging
import requests
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.filters import Command
import os
from dotenv import load_dotenv

# Вместо локального списка предсказаний – обращение к GPT через litellm
from litellm import completion

from zoneinfo import ZoneInfo

# В файле db.py:
#   init_db(), get_db() - для работы с БД
#   User - модель с полями (chat_id, gender, style, horoscope, city, frequency, time)
from db import init_db, get_db, User

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEATHER_URL = "http://api.openweathermap.org/data/2.5/weather"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_data = {}

# Инициализируем БД при старте
init_db()


# ========== ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ И ФОРМАТИРОВАНИЯ ПОГОДЫ ==========

async def get_weather(city: str):
    params = {
        "q": city,
        "appid": WEATHER_API_KEY,
        "units": "metric",
        "lang": "ru"
    }
    resp = requests.get(WEATHER_URL, params=params)
    if resp.status_code == 200:
        return resp.json()
    return None


def format_weather(weather_data):
    city = weather_data.get("name", "Неизвестный город")
    description = weather_data["weather"][0]["description"].capitalize()
    temp = round(weather_data["main"]["temp"])
    feels_like = round(weather_data["main"]["feels_like"])
    return f"В {city} {description}, {temp}°C (ощущается как {feels_like}°C)."


# ========== РЕКОМЕНДАЦИИ ПО ОДЕЖДЕ И ГЕРОСКОП ==========

async def get_clothing_recommendation(weather_data, gender, style):
    """
    Короткая рекомендация (до 3 предложений), формируем через GPT.
    """
    prompt = (
        f"Погода: {weather_data['weather'][0]['description']}, "
        f"температура: {weather_data['main']['temp']}°C.\n"
        f"Пол: {gender}, стиль: {style}.\n"
        "Дай короткие рекомендации (макс. 3 предложения) с эмодзи, "
        "как одеться по погоде."
    )
    try:
        response = completion(
            model="gpt-3.5-turbo",  # или другой, если есть доступ
            messages=[{"role": "system", "content": prompt}],
            temperature=0.7,
            max_tokens=120
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Ошибка при получении рекомендаций: {str(e)}"


async def get_random_horoscope():
    """
    Генерируем «гороскоп» через GPT, максимум 2 предложения с эмодзи.
    """
    prompt = (
        "Сгенерируй позитивный гороскоп на день, "
        "не более 2 предложений, используй эмодзи. Текст на русском языке."
    )
    try:
        response = completion(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.7,
            max_tokens=60
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Ошибка при получении гороскопа: {str(e)}"


# ========== РАБОТА С БД ==========

async def save_user_settings(chat_id, gender, city, style, frequency, time_str, horoscope):
    """Сохраняем настройки пользователя в БД (поле horoscope вместо forecast)."""
    db = next(get_db())
    user = db.query(User).filter(User.chat_id == chat_id).first()

    if not user:
        user = User(
            chat_id=chat_id,
            gender=gender,
            city=city,
            style=style,
            frequency=frequency,
            time=time_str,
            horoscope=horoscope
        )
        db.add(user)
    else:
        user.gender = gender
        user.city = city
        user.style = style
        user.frequency = frequency
        user.time = time_str
        user.horoscope = horoscope

    db.commit()
    db.close()


# ========== ОТПРАВКА СФОРМИРОВАННОГО ОТВЕТА ПОЛЬЗОВАТЕЛЮ ==========

async def send_weather_update(chat_id):
    db = next(get_db())
    user = db.query(User).filter(User.chat_id == chat_id).first()
    db.close()

    if not user:
        return

    weather_data = await get_weather(user.city)
    if not weather_data:
        await bot.send_message(chat_id, "Не удалось получить данные о погоде. Попробуй позже.")
        return

    weather_text = format_weather(weather_data)
    clothing_advice = await get_clothing_recommendation(weather_data, user.gender, user.style)

    message = f"🌦 {weather_text}\n\n👕 Рекомендации:\n{clothing_advice}"

    # Если пользователь выбрал «да» для гороскопа
    if user.horoscope and user.horoscope.lower() == "да":
        horoscope_text = await get_random_horoscope()
        message += f"\n\n🔮 Гороскоп:\n{horoscope_text}"

    await bot.send_message(chat_id, message)


# ========== ПЛАНИРОВКА ЕЖЕДНЕВНОЙ РАССЫЛКИ ==========
async def schedule_weather_updates(chat_id):
    while True:
        db = next(get_db())
        user = db.query(User).filter(User.chat_id == chat_id).first()
        db.close()

        if not user or user.frequency != "каждый день":
            break

        try:
            scheduled_time = datetime.strptime(user.time, "%H:%M").time()
        except ValueError:
            await bot.send_message(chat_id, "Неверный формат времени. Используй ЧЧ:ММ.")
            break

        now = datetime.now(ZoneInfo("Europe/Moscow"))
        next_run = datetime.combine(now.date(), scheduled_time, tzinfo=ZoneInfo("Europe/Moscow"))
        if next_run <= now:
            next_run += timedelta(days=1)

        await asyncio.sleep((next_run - now).total_seconds())

        await send_weather_update(chat_id)
        # Добавим небольшую паузу и затем повторим через сутки
        await asyncio.sleep(1)


# ========== ХЕНДЛЕРЫ ДЛЯ КОМАНД /start, /reset И ОБЩИХ СООБЩЕНИЙ ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    chat_id = message.chat.id
    user_data[chat_id] = {}  # Начинаем новый диалог

    # Кнопки для продолжения или отмены
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Продолжить ✅")],
            [KeyboardButton(text="Отменить ❌")]
        ],
        resize_keyboard=True
    )

    # Текст с политикой конфиденциальности
    text = (
        "Пожалуйста, ознакомься с \"Политикой конфиденциальности, Пользовательским соглашением "
        "и Согласием на обработку данных\"\n\n"
        "https://docs.google.com/document/d/e/2PACX-1vS-O_VHQJ2mwW_nnpHyomE4OusWfVTwnaRjctndHB8-3OdoINUGz51MPR2XoX0ICy1Q_QGqVf8dsavq/pub\n\n"
        "Нажимая кнопку \"Продолжить ✅\" — ты принимаешь все условия."
    )
    await message.answer(text, reply_markup=keyboard)


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    """Сбрасываем все настройки пользователя и начинаем заново."""
    chat_id = message.chat.id

    # Очищаем данные пользователя в памяти
    if chat_id in user_data:
        del user_data[chat_id]

    # Очищаем данные пользователя в базе данных
    db = next(get_db())
    user = db.query(User).filter(User.chat_id == chat_id).first()
    if user:
        db.delete(user)
        db.commit()
    db.close()

    # Сразу перезапускаем диалог, чтобы избежать отправки предыдущих сообщений
    await cmd_start(message)  # Это вызывает перезапуск диалога


@dp.message()
async def handle_message(message: Message):
    chat_id = message.chat.id
    text = message.text.strip().lower()
    user = user_data.setdefault(chat_id, {})

    # 1) Принятие условий
    if "agreement_accepted" not in user:
        if text == "продолжить ✅":
            user["agreement_accepted"] = True
            # Спрашиваем пол
            gender_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Мужской")],
                    [KeyboardButton(text="Женский")]
                ],
                resize_keyboard=True
            )
            await message.answer("Какой у вас пол?", reply_markup=gender_kb)
        elif text == "отменить ❌":
            await message.answer("Действие отменено. Введите /start, чтобы начать заново.")
        else:
            await message.answer("Нажми «Продолжить ✅» или «Отменить ❌».")
        return

    # 2) Пол
    if "gender" not in user:
        user["gender"] = message.text.lower()
        style_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Деловой")],
                [KeyboardButton(text="Повседневный")],
                [KeyboardButton(text="Спортивный")]
            ],
            resize_keyboard=True
        )
        await message.answer("Какой стиль одежды предпочитаешь?", reply_markup=style_kb)
        return

    # 3) Стиль
    if "style" not in user:
        user["style"] = message.text.lower()
        horoscope_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Да")],
                [KeyboardButton(text="Нет")]
            ],
            resize_keyboard=True
        )
        await message.answer("Хотите ли получить Предсказание дня? (Да/Нет)", reply_markup=horoscope_kb)
        return

    # 4) Гороскоп
    if "horoscope" not in user:
        user["horoscope"] = message.text.lower()
        await message.answer("В каком городе находишься?", reply_markup=ReplyKeyboardRemove())
        return

    # 5) Город
    if "city" not in user:
        user["city"] = message.text
        freq_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Сейчас")],
                [KeyboardButton(text="Каждый день")]
            ],
            resize_keyboard=True
        )
        await message.answer("Когда присылать прогноз погоды? Сейчас или Каждый день?", reply_markup=freq_kb)
        return

    # 6) Частота
    if "frequency" not in user:
        user["frequency"] = text

        # Проверяем на нажатие кнопок "Сбросить настройки" или "Начать заново"
        if text == "сбросить настройки":
            # Сразу сбрасываем настройки и перезапускаем диалог
            await cmd_reset(message)  # Сброс настроек и перезапуск диалога
            return  # Прерываем выполнение, чтобы не продолжать дальше

        if text == "начать заново":
            # Сброс данных пользователя до шага с выбором стиля
            user_data[chat_id] = {}
            style_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Деловой")],
                    [KeyboardButton(text="Повседневный")],
                    [KeyboardButton(text="Спортивный")]
                ],
                resize_keyboard=True
            )
            await message.answer("Какой стиль одежды предпочитаешь?", reply_markup=style_kb)
            return  # Прерываем выполнение, чтобы не продолжать дальше

        if user["frequency"] == "сейчас":
            # Сохраняем и отправляем сразу
            await save_user_settings(
                chat_id, user["gender"], user["city"], user["style"],
                user["frequency"], "сейчас", user["horoscope"]
            )
            await send_weather_update(chat_id)

            opts_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Сбросить настройки")],
                    [KeyboardButton(text="Начать заново")]
                ],
                resize_keyboard=True
            )
            await message.answer("Готово! Можешь сбросить настройки или начать заново.", reply_markup=opts_kb)
        else:
            # Если "каждый день"
            await message.answer("Во сколько (ЧЧ:ММ) отправлять прогноз?", reply_markup=ReplyKeyboardRemove())
        return

    # 7) Время
    if "time" not in user:
        user["time"] = message.text
        await save_user_settings(
            chat_id,
            user["gender"],
            user["city"],
            user["style"],
            user["frequency"],
            user["time"],
            user["horoscope"]
        )

        # Проверяем на нажатие кнопок "Сбросить настройки" или "Начать заново"
        if text == "сбросить настройки":
            # Сразу сбрасываем настройки и перезапускаем диалог
            await cmd_reset(message)  # Сброс настроек и перезапуск диалога
            return  # Прерываем выполнение, чтобы не продолжать дальше

        if text == "начать заново":
            # Сброс данных пользователя до шага с выбором стиля
            user_data[chat_id] = {}
            style_kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Деловой")],
                    [KeyboardButton(text="Повседневный")],
                    [KeyboardButton(text="Спортивный")]
                ],
                resize_keyboard=True
            )
            await message.answer("Какой стиль одежды предпочитаешь?", reply_markup=style_kb)
            return  # Прерываем выполнение, чтобы не продолжать дальше

        # Если настройки не были сброшены и пользователь не начал заново
        asyncio.create_task(schedule_weather_updates(chat_id))  # Запускаем асинхронное обновление прогноза погоды

        opts_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Сбросить настройки")],
                [KeyboardButton(text="Начать заново")]
            ],
            resize_keyboard=True
        )
        await message.answer("Отлично! Буду присылать обновления ежедневно.", reply_markup=opts_kb)
        return

# Запуск бота
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())



