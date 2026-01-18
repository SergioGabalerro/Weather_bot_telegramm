import os
from sqlalchemy import create_engine, Column, String, BigInteger
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# Пример: DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/dbname
# Или для SQLite: DATABASE_URL=sqlite:///bot.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """
    Модель пользователя для хранения данных телеграм-бота:
     - chat_id     (Primary Key, ID чата)
     - gender      (пол)
     - style       (стиль одежды)
     - horoscope   (нужен ли гороскоп - "да"/"нет")
     - city        (город пользователя)
     - frequency   (периодичность: "сейчас" или "каждый день")
     - time        (время в формате ЧЧ:ММ)
    """
    __tablename__ = "users"

    chat_id = Column(BigInteger, primary_key=True, index=True)
    gender = Column(String)
    style = Column(String)
    horoscope = Column(String)   # заменили forecast на horoscope
    city = Column(String)
    frequency = Column(String)
    time = Column(String)


def init_db():
    """Создаёт таблицы (если их нет)."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Генератор, возвращающий сессию к БД и закрывающий её по завершении."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


