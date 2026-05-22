import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io
import requests
import os

st.set_page_config(page_title="AviaPazlAP", page_icon="logoSitr.jpg", layout="wide")

# ===================== НАСТРОЙКИ =====================
DB_PATH = "suppliers.db"  # Можно сделать абсолютный путь
YANDEX_TOKEN = "y0__wgBEJj5gowHGNeNQiDFi-7NF4t1owTsAz6dBNIoV8vvXDfCA5au"

engine = create_engine(f'sqlite:///{DB_PATH}', echo=False)


def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
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
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_article ON parts(article)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_supplier ON parts(supplier)"))

        # Таблица истории экспортов
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                download_url TEXT,
                created_at TEXT,
                file_size INTEGER
            )
        """))
        conn.commit()


init_db()


# ===================== ФУНКЦИЯ ЗАГРУЗКИ НА ЯНДЕКС =====================
def upload_to_yandex_disk(file_bytes, filename, token):
    headers = {"Authorization": f"OAuth {token}"}
    folder = "/AviaPazlAP_exports"

    # Создаём папку если нет
    requests.put("https://cloud-api.yandex.net/v1/disk/resources",
                 headers=headers, params={"path": folder})

    # Загружаем файл
    upload_url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    resp = requests.get(upload_url, headers=headers, params={"path": f"{folder}/{filename}", "overwrite": "true"})

    if resp.status_code == 200:
        upload_href = resp.json()["href"]
        requests.put(upload_href, data=file_bytes)

        # Публикуем
        requests.put("https://cloud-api.yandex.net/v1/disk/resources/publish",
                     headers=headers, params={"path": f"{folder}/{filename}"})

        # Получаем ссылку
        info = requests.get("https://cloud-api.yandex.net/v1/disk/resources",
                            headers=headers, params={"path": f"{folder}/{filename}"})
        return info.json().get("public_url")
    return None


# ===================== ИНТЕРФЕЙС =====================
st.title("📦 AviaPazlAP — Общая база поставщиков")

tab1, tab2, tab3 = st.tabs(["Загрузка", "Поиск", "Экспорт + Статистика"])

# ===================== ЗАГРУЗКА =====================
with tab1:
    supplier_name = st.text_input("Название поставщика *")
    uploaded_file = st.file_uploader("Excel файл", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("💾 Сохранить в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)

            rename_map = {'PN': 'article', 'P/N': 'article', 'TERM': 'article', 'DES': 'name',
                          'UNIT/USD': 'price', 'QTY': 'stock'}
            for old, new in rename_map.items():
                if old in df.columns and new not in df.columns:
                    df = df.rename(columns={old: new})

            df['supplier'] = supplier_name.strip()
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str), axis=1)

            df[['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']].to_sql(
                'parts', engine, if_exists='append', index=False)

            st.success(f"✅ Успешно сохранено {len(df)} позиций")
        except Exception as e:
            st.error(f"Ошибка: {e}")

# ===================== ПОИСК =====================
with tab2:
    search = st.text_input("Поиск (артикул, название)")
    if st.button("Искать"):
        with engine.connect() as conn:
            df = pd.read_sql("""
                SELECT supplier, raw_data 
                FROM parts 
                WHERE article LIKE :s OR name LIKE :s OR raw_data LIKE :s
            """, conn, params={"s": f"%{search}%"})

        if len(df) > 0:
            rows = [json.loads(r['raw_data']) for _, r in df.iterrows()]
            result_df = pd.DataFrame(rows)
            st.dataframe(result_df, use_container_width=True)
        else:
            st.warning("Ничего не найдено")

# ===================== ЭКСПОРТ =====================
with tab3:
    if st.button("📥 Экспортировать ВСЮ базу в Excel", type="primary"):
        with st.spinner("Создаём файл..."):
            try:
                with engine.connect() as conn:
                    df_raw = pd.read_sql("SELECT raw_data FROM parts", conn)

                all_rows = [json.loads(row['raw_data']) for _, row in df_raw.iterrows()]
                full_df = pd.DataFrame(all_rows)

                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    full_df.to_excel(writer, index=False)
                output.seek(0)

                filename = f"Full_AviaPazlAP_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

                # Загрузка на Яндекс.Диск
                public_link = upload_to_yandex_disk(output.getvalue(), filename, YANDEX_TOKEN)

                if public_link:
                    st.success(f"✅ Файл загружен на Яндекс.Диск!")
                    st.write(f"[Скачать по ссылке]({public_link})")
                else:
                    st.warning("Не удалось загрузить на Яндекс.Диск")

                st.download_button("⬇ Скачать локально", output.getvalue(), filename)

            except Exception as e:
                st.error(f"Ошибка экспорта: {e}")

    st.divider()
    st.subheader("Статистика")
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM parts")).scalar()
    st.metric("Всего позиций в базе", total)

st.caption("Если данные продолжают стираться — напиши")