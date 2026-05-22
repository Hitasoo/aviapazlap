import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io
import requests
import os

# ---------------------- ДИАГНОСТИКА ТОКЕНА (временный блок) ----------------------
# Сначала пробуем получить из st.secrets, потом из переменных окружения
try:
    YANDEX_TOKEN = st.secrets["YANDEX_TOKEN"]
    st.info(f"✅ Токен загружен из st.secrets (длина: {len(YANDEX_TOKEN)})")
except Exception as e1:
    YANDEX_TOKEN = os.environ.get("YANDEX_TOKEN")
    if YANDEX_TOKEN:
        st.info(f"✅ Токен загружен из os.environ (длина: {len(YANDEX_TOKEN)})")
    else:
        st.error("❌ Токен Яндекс.Диска НЕ НАЙДЕН! Проверьте переменную YANDEX_TOKEN в настройках Render.")
# -----------------------------------------------------------------

# ---------------------- НАСТРОЙКА СТРАНИЦЫ ----------------------
st.set_page_config(page_title="AviaPazlAP", page_icon="logoSitr.jpg", layout="wide")

# ---------------------- ПОДКЛЮЧЕНИЕ К БАЗЕ ----------------------
engine = create_engine('sqlite:///suppliers.db', echo=False)

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
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_name ON parts(name)"))

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

# ---------------------- ФУНКЦИЯ ЗАГРУЗКИ НА ЯНДЕКС.ДИСК ----------------------
def upload_to_yandex_disk(file_bytes, filename, token):
    """Загружает файл на Яндекс.Диск в папку AviaPazlAP_exports и возвращает публичную ссылку"""
    if not token:
        return None

    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {token}"}
    params = {
        "path": f"/AviaPazlAP_exports/{filename}",
        "overwrite": "true"
    }
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        st.error(f"Ошибка получения ссылки для загрузки: {resp.text}")
        return None
    upload_url = resp.json()["href"]

    with io.BytesIO(file_bytes) as f:
        upload_resp = requests.put(upload_url, data=f.read())
    if upload_resp.status_code not in (200, 201):
        st.error(f"Ошибка загрузки файла: {upload_resp.text}")
        return None

    publish_url = "https://cloud-api.yandex.net/v1/disk/resources/publish"
    pub_resp = requests.put(publish_url, headers=headers, params={"path": f"/AviaPazlAP_exports/{filename}"})
    if pub_resp.status_code != 200:
        return f"https://disk.yandex.ru/client/disk/AviaPazlAP_exports/{filename}"

    info_resp = requests.get("https://cloud-api.yandex.net/v1/disk/resources", headers=headers, params={"path": f"/AviaPazlAP_exports/{filename}"})
    if info_resp.status_code == 200:
        public_url = info_resp.json().get("public_url")
        if public_url:
            return public_url
    return f"https://disk.yandex.ru/d/???"

# ---------------------- ЗАГОЛОВОК ----------------------
col1, col2 = st.columns([1, 10])
with col1:
    st.image("logoSitr.jpg", width=60)
with col2:
    st.title("AviaPazlAP — Общая база поставщиков")

tab1, tab2, tab3 = st.tabs(["Загрузка", "Поиск", "Весь файл + Статистика"])

# ---------------------- ЗАГРУЗКА ----------------------
with tab1:
    st.subheader("Загрузка Excel файла")
    supplier_name = st.text_input("Название поставщика")
    uploaded_file = st.file_uploader("Excel файл", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("Сохранить в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)
            rename_map = {
                'PN': 'article', 'P/N': 'article', 'TERM': 'article',
                'DES': 'name', 'UNIT/USD': 'price', 'QTY': 'stock'
            }
            for old, new in rename_map.items():
                if old in df.columns and new not in df.columns:
                    df = df.rename(columns={old: new})

            df['supplier'] = supplier_name
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str), axis=1)

            save_columns = ['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']
            for col in save_columns:
                if col not in df.columns:
                    df[col] = None
            df[save_columns].to_sql('parts', engine, if_exists='append', index=False)
            st.success(f"✅ Сохранено строк: {len(df)}")
        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")

# ---------------------- ПОИСК ----------------------
with tab2:
    st.subheader("Поиск по общей базе")
    search = st.text_input("Введите артикул, название или любой текст")
    if st.button("Искать"):
        if len(search.strip()) < 2:
            st.warning("Введите минимум 2 символа")
        else:
            try:
                with engine.connect() as conn:
                    df = pd.read_sql("""
                        SELECT supplier, uploaded_at, filename, raw_data
                        FROM parts
                        WHERE article LIKE :s OR name LIKE :s OR raw_data LIKE :s
                        LIMIT 5000
                    """, conn, params={"s": f"%{search}%"})
                if len(df) == 0:
                    st.warning("Ничего не найдено")
                else:
                    rows = []
                    for _, r in df.iterrows():
                        item = json.loads(r['raw_data'])
                        item['_supplier'] = r['supplier']
                        item['_uploaded_at'] = r['uploaded_at']
                        item['_filename'] = r['filename']
                        rows.append(item)
                    result_df = pd.DataFrame(rows)
                    priority_cols = ['_supplier', '_uploaded_at', '_filename', 'article', 'PN', 'P/N', 'TERM', 'name', 'DES', 'price', 'UNIT/USD', 'stock', 'QTY']
                    existing_priority = [c for c in priority_cols if c in result_df.columns]
                    other_cols = [c for c in result_df.columns if c not in existing_priority]
                    result_df = result_df[existing_priority + other_cols]
                    st.success(f"✅ Найдено строк: {len(result_df)}")
                    st.dataframe(result_df, use_container_width=True, height=700)
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")

# ---------------------- ЭКСПОРТ И СТАТИСТИКА ----------------------
with tab3:
    st.subheader("Экспорт всей базы")

    if st.button("📥 Экспортировать ВСЮ базу в Excel", type="primary"):
        with st.spinner("Создаём Excel файл..."):
            try:
                with engine.connect() as conn:
                    df = pd.read_sql("SELECT raw_data FROM parts", conn)
                if len(df) == 0:
                    st.warning("База пустая")
                else:
                    all_rows = []
                    for row in df['raw_data']:
                        all_rows.append(json.loads(row))
                    full_df = pd.DataFrame(all_rows)

                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        full_df.to_excel(writer, index=False, sheet_name='All_Data')
                    output.seek(0)
                    file_bytes = output.getvalue()
                    filename = f"Полная_база_AviaPazlAP_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

                    if YANDEX_TOKEN:
                        public_link = upload_to_yandex_disk(file_bytes, filename, YANDEX_TOKEN)
                        if public_link:
                            with engine.connect() as conn:
                                conn.execute(
                                    text("INSERT INTO exports (filename, download_url, created_at, file_size) VALUES (:f, :u, :c, :s)"),
                                    {"f": filename, "u": public_link, "c": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "s": len(file_bytes)}
                                )
                                conn.commit()
                            st.success(f"✅ Файл загружен на Яндекс.Диск! [Скачать]({public_link})")
                        else:
                            st.warning("Не удалось загрузить файл на Яндекс.Диск. Файл только для скачивания.")
                    else:
                        st.info("Токен Яндекс.Диска не задан. Файл не будет сохранён в облаке.")

                    st.download_button(
                        label="⬇ Скачать полный Excel файл (локально)",
                        data=output,
                        file_name=filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
            except Exception as e:
                st.error(f"Ошибка экспорта: {e}")

    st.divider()
    st.subheader("Статистика базы")
    try:
        with engine.connect() as conn:
            total_rows = conn.execute(text("SELECT COUNT(*) FROM parts")).scalar()
            total_suppliers = conn.execute(text("SELECT COUNT(DISTINCT supplier) FROM parts")).scalar()
            total_files = conn.execute(text("SELECT COUNT(DISTINCT filename) FROM parts")).scalar()
        col1, col2, col3 = st.columns(3)
        col1.metric("Всего строк", total_rows)
        col2.metric("Поставщиков", total_suppliers)
        col3.metric("Файлов", total_files)
    except Exception as e:
        st.error(f"Ошибка статистики: {e}")

    st.divider()
    st.subheader("📁 История экспортов")
    try:
        with engine.connect() as conn:
            exports_df = pd.read_sql(
                "SELECT filename, created_at, download_url FROM exports ORDER BY created_at DESC LIMIT 50",
                conn
            )
        if not exports_df.empty:
            exports_df['Скачать'] = exports_df['download_url'].apply(lambda url: f'<a href="{url}" target="_blank">📥 Скачать</a>')
            st.markdown(exports_df[['filename', 'created_at', 'Скачать']].to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.info("Нет сохранённых экспортов.")
    except Exception as e:
        st.error(f"Ошибка загрузки истории: {e}")

    st.caption("Экспорт может занять время при большом объёме данных")