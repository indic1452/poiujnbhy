"""Запуск backend одной кнопкой (в т.ч. из PyCharm: правой кнопкой → Run 'run').

Поднимает FastAPI через uvicorn с авто-перезагрузкой. Настройки берутся из
backend/.env. Аналог команды: uvicorn app.main:app --reload
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
