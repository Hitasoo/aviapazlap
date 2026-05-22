import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io
import requests
import os

# ============================================================
# 1. НАСТРОЙКА ТОКЕНА ЯНДЕКС.ДИСКА
# ============================================================
# ❗ ВАЖНО: замените "ВАШ_ТОКЕН" на новый токен, полученный с правами:
#   - cloud_api:disk.info
#   - cloud_api:disk.read
#   - cloud_api:disk.write
#
# Как получить токен:
# 1. Перейдите по ссылке:
#    https://oauth.yandex.ru/authorize?response_type=token&client_id=add9034b7cb842e895c96f8f438dcee7
# 2. Поставьте галочки напротив всех трёх прав.
# 3. Нажмите «Разрешить».
# 4. Скопируйте токен из адресной строки после #access_token=
# ============================================================

YANDEX_TOKEN = "ВАШ_ТОКЕН"  # ← ЗАМЕНИТЕ НА РЕАЛЬНЫЙ ТОКЕН

# ============================================================
# 2. ДИАГНОСТИКА ПРАВ ТОКЕНА (выполняется при запуске)
# ============================================================
st.write("### 🧪 Проверка токена Яндекс.Диска")
if YANDEX_TOKEN == "ВАШ_ТОКЕН":
    st.error("❌ Токен не заменён! Укажите реальный токен в коде.")
elif not YANDEX_TOKEN:
    st.error("❌ Токен отсутствует.")
else:
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}
    test_folder = "/AviaPazlAP_test"
    try:
        # Пытаемся создать тестовую папку
        resp = requests.put("https://cloud-api.yandex.net/v1/disk/resources",
                            headers=headers, params={"path": test_folder})
        if resp.status_code in (200, 201):
            st.success("✅ Токен имеет право на запись (создание папок).")
            # Удаляем тестовую папку
            requests.delete("https://cloud-api.yandex.net/v1/disk/resources",
                            headers=headers, params={"path": test_folder, "permanently": "true"})
        elif resp.status_code == 403 or resp.status_code == 401:
            st.error("❌ Токен НЕ имеет права на запись (Forbidden/Unauthorized).")
            st.info("Получите новый токен с правами disk.info, disk.read, disk.write.")
        else:
            st.warning(f"⚠️ Неизвестный ответ API: {resp.status_code}")
    except Exception as e:
        st.error(f"Ошибка соединения: {e}")
st.divider()

# ============================================================
# 3. НАСТРОЙКА СТРАНИЦЫ И БАЗЫ ДАННЫХ
# ============================================================
st.set_page_config(page_title="AviaPazlAP", page_icon="logoSitr.jpg", layout="wide")
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


# ============================================================
# 4. ФУНКЦИЯ ЗАГРУЗКИ НА ЯНДЕКС.ДИСК
# ============================================================
def upload_to_yandex_disk(file_bytes, filename, token):
    """
    Загружает файл на Яндекс.Диск в папку AviaPazlAP_exports.
    Если папки нет – создаёт. Возвращает публичную ссылку.
    """
    if not token or token == "ВАШ_ТОКЕН":
        st.warning("Токен не задан или не заменён.")
        return None

    headers = {"Authorization": f"OAuth {token}"}
    folder_path = "/AviaPazlAP_exports"

    # 1. Проверка существования папки
    check_url = "https://cloud-api.yandex.net/v1/disk/resources"
    params = {"path": folder_path}
    resp = requests.get(check_url, headers=headers, params=params)

    if resp.status_code == 404:
        # Папки нет – создаём
        create_resp = requests.put(check_url, headers=headers, params=params)
        if create_resp.status_code not in (200, 201):
            st.error(f"Ошибка создания папки: {create_resp.text}")
            return None
        st.info(f"📁 Папка {folder_path} создана.")
    elif resp.status_code != 200:
        st.error(f"Ошибка проверки папки: {resp.text}")
        return None

    # 2. Получаем URL для загрузки файла
    upload_url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    params = {"path": f"{folder_path}/{filename}", "overwrite": "true"}
    resp = requests.get(upload_url, headers=headers, params=params)
    if resp.status_code != 200:
        st.error(f"Ошибка получения ссылки для загрузки: {resp.text}")
        return None
    upload_href = resp.json()["href"]

    # 3. Загружаем файл
    with io.BytesIO(file_bytes) as f:
        upload_resp = requests.put(upload_href, data=f.read())
    if upload_resp.status_code not in (200, 201):
        st.error(f"Ошибка загрузки файла: {upload_resp.text}")
        return None

    # 4. Делаем файл публичным
    publish_url = "https://cloud-api.yandex.net/v1/disk/resources/publish"
    pub_resp = requests.put(publish_url, headers=headers, params={"path": f"{folder_path}/{filename}"})
    if pub_resp.status_code != 200:
        return f"https://disk.yandex.ru/client/disk/AviaPazlAP_exports/{filename}"

    # 5. Получаем публичную ссылку
    info_resp = requests.get(check_url, headers=headers, params={"path": f"{folder_path}/{filename}"})
    if info_resp.status_code == 200:
        public_url = info_resp.json().get("public_url")
        if public_url:
            return public_url
    return f"https://disk.yandex.ru/d/???"


# ============================================================
# 5. ИНТЕРФЕЙС ПОЛЬЗОВАТЕЛЯ (Streamlit)
# ============================================================
col1, col2 = st.columns([1, 10])
with col1:
    st.image("logoSitr.jpg", width=60)
with col2:
    st.title("AviaPazlAP — Общая база поставщиков")

tab1, tab2, tab3 = st.tabs(["Загрузка", "Поиск", "Весь файл + Статистика"])

# --- Вкладка "Загрузка" ---
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

# --- Вкладка "Поиск" ---
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
                if df.empty:
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
                    priority_cols = ['_supplier', '_uploaded_at', '_filename', 'article', 'PN', 'P/N', 'TERM', 'name',
                                     'DES', 'price', 'UNIT/USD', 'stock', 'QTY']
                    existing_priority = [c for c in priority_cols if c in result_df.columns]
                    other_cols = [c for c in result_df.columns if c not in existing_priority]
                    result_df = result_df[existing_priority + other_cols]
                    st.success(f"✅ Найдено строк: {len(result_df)}")
                    st.dataframe(result_df, use_container_width=True, height=700)
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")

# --- Вкладка "Весь файл + Статистика" ---
with tab3:
    st.subheader("Экспорт всей базы")
    if st.button("📥 Экспортировать ВСЮ базу в Excel", type="primary"):
        with st.spinner("Создаём Excel файл..."):
            try:
                with engine.connect() as conn:
                    df = pd.read_sql("SELECT raw_data FROM parts", conn)
                if df.empty:
                    st.warning("База пустая")
                else:
                    all_rows = [json.loads(row) for row in df['raw_data']]
                    full_df = pd.DataFrame(all_rows)
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        full_df.to_excel(writer, index=False, sheet_name='All_Data')
                    output.seek(0)
                    file_bytes = output.getvalue()
                    filename = f"Полная_база_AviaPazlAP_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

                    # Загрузка на Яндекс.Диск
                    if YANDEX_TOKEN and YANDEX_TOKEN != "ВАШ_ТОКЕН":
                        public_link = upload_to_yandex_disk(file_bytes, filename, YANDEX_TOKEN)
                        if public_link:
                            with engine.connect() as conn:
                                conn.execute(
                                    text(
                                        "INSERT INTO exports (filename, download_url, created_at, file_size) VALUES (:f, :u, :c, :s)"),
                                    {"f": filename, "u": public_link, "c": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                     "s": len(file_bytes)}
                                )
                                conn.commit()
                            st.success(f"✅ Файл загружен на Яндекс.Диск! [Скачать]({public_link})")
                        else:
                            st.warning("Не удалось загрузить файл на Яндекс.Диск.")
                    else:
                        st.info("Токен не задан – загрузка в облако пропущена.")

                    # Локальное скачивание
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
        c1, c2, c3 = st.columns(3)
        c1.metric("Всего строк", total_rows)
        c2.metric("Поставщиков", total_suppliers)
        c3.metric("Файлов", total_files)
    except Exception as e:
        st.error(f"Ошибка статистики: {e}")

    st.divider()
    st.subheader("📁 История экспортов")
    try:
        with engine.connect() as conn:
            exports_df = pd.read_sql(
                "SELECT filename, created_at, download_url FROM exports ORDER BY created_at DESC LIMIT 50", conn)
        if not exports_df.empty:
            exports_df['Скачать'] = exports_df['download_url'].apply(
                lambda url: f'<a href="{url}" target="_blank">📥 Скачать</a>')
            st.markdown(exports_df[['filename', 'created_at', 'Скачать']].to_html(escape=False, index=False),
                        unsafe_allow_html=True)
        else:
            st.info("Нет сохранённых экспортов.")
    except Exception as e:
        st.error(f"Ошибка загрузки истории: {e}")

    st.caption("Экспорт может занять время при большом объёме данных")

# ============================================================
# АЛЬТЕРНАТИВА: GOOGLE DRIVE (раскомментируйте, если Яндекс не работает)
# Для Google Drive нужно установить библиотеки:
#   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
# Затем создать сервисный аккаунт в Google Cloud и включить Drive API.
# Код для загрузки на Google Drive можно заменить вместо функций Яндекс.Диска.
# При необходимости – напишите, я предоставлю полный код для Google Drive.
# ============================================================