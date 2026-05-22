import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io
import requests
import os

st.set_page_config(page_title="AviaPazlAP", layout="wide")

# ===================== НАСТРОЙКИ =====================
DB_PATH = "suppliers.db"
YANDEX_TOKEN = "y0__wgBEJj5gowHGNeNQiDFi-7NF4t1owTsAz6dBNIoV8vvXDfCA5au"  # твой токен

engine = create_engine(f'sqlite:///{DB_PATH}', echo=False)


# ===================== ЗАГРУЗКА БАЗЫ С ЯНДЕКС ДИСКА =====================
def download_db_from_yandex():
    if not YANDEX_TOKEN:
        return False
    try:
        url = "https://cloud-api.yandex.net/v1/disk/resources/download"
        params = {"path": "/AviaPazlAP/suppliers.db"}
        headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            download_url = resp.json()["href"]
            r = requests.get(download_url)
            with open(DB_PATH, "wb") as f:
                f.write(r.content)
            st.success("✅ База загружена с Яндекс.Диска")
            return True
    except:
        pass
    return False


# ===================== ЗАГРУЗКА БАЗЫ НА ЯНДЕКС =====================
def upload_db_to_yandex():
    if not YANDEX_TOKEN or not os.path.exists(DB_PATH):
        return False
    try:
        with open(DB_PATH, "rb") as f:
            file_bytes = f.read()

        upload_url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
        headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
        params = {"path": "/AviaPazlAP/suppliers.db", "overwrite": "true"}

        resp = requests.get(upload_url, headers=headers, params=params)
        if resp.status_code == 200:
            put_url = resp.json()["href"]
            requests.put(put_url, data=file_bytes)
            return True
    except:
        pass
    return False


# ===================== ИНИЦИАЛИЗАЦИЯ =====================
if not os.path.exists(DB_PATH):
    download_db_from_yandex()

init_db = lambda: engine.connect().execute(text("""
    CREATE TABLE IF NOT EXISTS parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT NOT NULL,
        article TEXT,
        name TEXT,
        price REAL,
        stock TEXT,
        uploaded_at TEXT,
        filename TEXT,
        raw_data TEXT
    )
""")).commit()

init_db()

st.title("📦 AviaPazlAP — Общая база поставщиков")

# ===================== ЗАГРУЗКА =====================
with st.sidebar:
    st.button("💾 Сохранить базу на Яндекс.Диск", on_click=upload_db_to_yandex)

tab1, tab2 = st.tabs(["Загрузка", "Поиск"])

with tab1:
    supplier_name = st.text_input("Название поставщика")
    uploaded_file = st.file_uploader("Excel файл", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("Сохранить в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)
            # ... (твой обычный код загрузки)
            df['supplier'] = supplier_name
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str), axis=1)

            df[['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']].to_sql(
                'parts', engine, if_exists='append', index=False)

            st.success("Сохранено!")
            upload_db_to_yandex()  # Автосохранение на диск
        except Exception as e:
            st.error(e)

with tab2:
    search = st.text_input("Поиск")
    if st.button("Искать"):
        with engine.connect() as conn:
            df = pd.read_sql("SELECT * FROM parts WHERE article LIKE :s OR raw_data LIKE :s", conn,
                             params={"s": f"%{search}%"})
            st.dataframe(df)

st.caption("База теперь сохраняется на Яндекс.Диск")