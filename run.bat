@echo off
chcp 65001 >nul
echo Активация окружения...
call .venv\Scripts\activate
echo Запуск приложения...
streamlit run app.py
pause