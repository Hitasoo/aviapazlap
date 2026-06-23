import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool  # <-- Добавлен импорт
from datetime import datetime
import json
import io
import requests
import os
import re
import time

# ============================================================
# Настройка страницы (ОБЯЗАТЕЛЬНО должна быть самой первой командой)
# ============================================================
st.set_page_config(page_title="AviaPazlAP", page_icon="logoSitr.jpg", layout="wide")

# ============================================================
# 1. ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ И ПОДКЛЮЧЕНИЕ К БАЗЕ
# ============================================================
YANDEX_TOKEN = os.environ.get("YANDEX_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL")


@st.cache_resource
def get_db_engine(url):
    if url:
        # Добавляем sslmode=require, если его нет
        if "sslmode" not in url:
            url += "&sslmode=require" if "?" in url else "?sslmode=require"

        # Настройки TCP Keepalive для предотвращения обрыва соединения
        connect_args = {
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
            "connect_timeout": 10,
        }

        # Используем NullPool — каждое соединение создаётся заново и сразу закрывается
        # Это единственный надёжный способ избежать ошибок с "мёртвыми" соединениями на Render
        return create_engine(
            url,
            poolclass=NullPool,
            connect_args=connect_args
        )
    # Локальный SQLite для разработки
    return create_engine('sqlite:///suppliers.db', echo=False)


engine = get_db_engine(DATABASE_URL)

if DATABASE_URL:
    st.sidebar.success("✅ Подключено к PostgreSQL")
else:
    st.sidebar.warning("💻 Локальный режим: SQLite")


# ============================================================
# 2. ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (ВСЕ ТАБЛИЦЫ) С ПОВТОРНЫМИ ПОПЫТКАМИ
# ============================================================
def init_db():
    is_sqlite = engine.url.drivername == 'sqlite'
    id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "SERIAL PRIMARY KEY"

    with engine.begin() as conn:
        # 1. Справочник общих прайсов поставщиков
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS parts (
                id {id_type},
                supplier TEXT NOT NULL,
                article TEXT,
                name TEXT,
                price FLOAT,
                stock TEXT,
                uploaded_at TEXT,
                filename TEXT,
                raw_data TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_article ON parts(article)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_name ON parts(name)"))

        # 2. Лог общих экспортов всей базы
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS exports (
                id {id_type},
                filename TEXT,
                download_url TEXT,
                created_at TEXT,
                file_size INTEGER
            )
        """))

        # 3. Ежедневные запросы клиентов (ЮТэйр)
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS customer_requests (
                id {id_type},
                request_number TEXT,        -- Наш сквозной признак (номер запроса)
                customer_name TEXT,         -- ЮТэйр
                article TEXT,               -- Партийный номер
                name TEXT,
                qty_required INTEGER,
                created_at TEXT,
                status TEXT DEFAULT 'Open'   -- Open, Sent, Closed
            )
        """))

        # 4. Ответы и ценовые предложения поставщиков под конкретный запрос
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS supplier_offers (
                id {id_type},
                request_number TEXT,        -- Признак нашего запроса
                supplier TEXT,
                article TEXT,               -- Партийный номер
                qty_offered INTEGER,
                price_offered FLOAT,
                received_at TEXT
            )
        """))


# Пытаемся инициализировать базу с повторными попытками
max_retries = 5
retry_delay = 3
init_success = False
for attempt in range(max_retries):
    try:
        init_db()
        st.sidebar.success("✅ База данных инициализирована успешно")
        init_success = True
        break
    except Exception as e:
        st.sidebar.warning(f"Попытка {attempt+1}/{max_retries} инициализации БД не удалась: {e}")
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
        else:
            st.error(
                f"❌ Ошибка инициализации базы данных после {max_retries} попыток.\n\n"
                "**Пожалуйста, проверьте:**\n"
                "1. Переменная DATABASE_URL задана корректно (используйте **External Database URL** из настроек Render).\n"
                "2. В настройках PostgreSQL (Access Control) добавлено правило **0.0.0.0/0** (временно для проверки).\n"
                "3. Попробуйте перезапустить базу данных в панели Render.\n"
                "4. Убедитесь, что ваше приложение и база данных находятся в одном регионе (например, Oregon).\n\n"
                "**Техническая ошибка:** {e}"
            )
            st.stop()


# ============================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (Яндекс.Диск, Очистка цены)
# ============================================================
def upload_to_yandex_disk(file_bytes, filename, token):
    if not token:
        st.warning("Токен Яндекс.Диска не задан")
        return None

    headers = {"Authorization": f"OAuth {token}"}
    folder_path = "/AviaPazlAP_exports"

    check_url = "https://yandex.net"
    resp = requests.get(check_url, headers=headers, params={"path": folder_path})

    if resp.status_code == 404:
        create_resp = requests.put(check_url, headers=headers, params={"path": folder_path})
        if create_resp.status_code not in (200, 201):
            st.error(f"Ошибка создания папки: {create_resp.text}")
            return None
        st.info(f"📁 Папка {folder_path} создана на Яндекс.Диске")
    elif resp.status_code != 200:
        st.error(f"Ошибка проверки папки: {resp.text}")
        return None

    upload_url = "https://yandex.net/upload"
    full_path = f"{folder_path}/{filename}"

    resp = requests.get(upload_url, headers=headers, params={"path": full_path, "overwrite": "true"})
    if resp.status_code != 200:
        st.error(f"Ошибка получения ссылки для загрузки: {resp.text}")
        return None
    upload_href = resp.json()["href"]

    with io.BytesIO(file_bytes) as f:
        upload_resp = requests.put(upload_href, data=f.read())
    if upload_resp.status_code not in (200, 201):
        st.error(f"Ошибка загрузки файла на сервер: {upload_resp.text}")
        return None

    publish_url = "https://yandex.net/publish"
    pub_resp = requests.put(publish_url, headers=headers, params={"path": full_path})
    if pub_resp.status_code != 200:
        return f"https://yandex.ru{folder_path}"

    info_resp = requests.get(check_url, headers=headers, params={"path": full_path})
    if info_resp.status_code == 200:
        public_url = info_resp.json().get("public_url")
        if public_url:
            return public_url

    return f"https://yandex.ru{folder_path}"


def clean_price(value):
    if pd.isna(value):
        return None
    s = str(value).strip().replace(',', '.')
    cleaned = re.sub(r'[^\d\.\-]', '', s)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# ============================================================
# 4. ВЕРХНЯЯ ШАБЛОННАЯ ПАНЕЛЬ ИНТЕРФЕЙСА
# ============================================================
col1, col2 = st.columns(2)
with col1:
    if os.path.exists("logoSitr.jpg"):
        st.image("logoSitr.jpg", width=60)
    else:
        st.title("✈️")
with col2:
    st.title("AviaPazlAP — Общая база поставщиков")

tab1, tab2, tab3, tab4 = st.tabs([
    "📥 Загрузка Прайсов",
    "🔎 Поиск",
    "📊 Статистика базы",
    "🗂️ Документооборот ЮТэйр"
])


# ============================================================
# ВКЛАДКА 1: ЗАГРУЗКА ОБЩИХ ПРАЙСОВ ПОСТАВЩИКОВ
# ============================================================
with tab1:
    st.subheader("Загрузка прайс-листов поставщиков")
    supplier_name = st.text_input("Название поставщика").strip()
    uploaded_file = st.file_uploader("Excel файл прайса", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("Сохранить в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)
            df.columns = [str(col).strip() for col in df.columns]

            price_col = None
            price_candidates = ['price', 'unit/usd', 'total/usd', 'цена', 'стоимость', 'стоимость usd']

            for col in df.columns:
                if col.lower() in price_candidates:
                    price_col = col
                    break

            if price_col:
                df[price_col] = df[price_col].apply(clean_price)
                df = df.rename(columns={price_col: 'price'})

            mapping = {
                'article': ['PN', 'P/N', 'TERM', 'article', 'артикул', 'номер детали'],
                'name': ['DES', 'description', 'name', 'наименование', 'описание'],
                'stock': ['QTY', 'stock', 'quantity', 'кол-во', 'количество', 'остаток']
            }

            for target_col, synonyms in mapping.items():
                if target_col in df.columns:
                    continue
                for col in df.columns:
                    if col.lower() in [s.lower() for s in synonyms]:
                        df = df.rename(columns={col: target_col})
                        break

            df['supplier'] = supplier_name
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(
                lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str),
                axis=1
            )

            save_columns = ['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']
            for col in save_columns:
                if col not in df.columns:
                    df[col] = None

            df[save_columns].to_sql(
                'parts',
                engine,
                if_exists='append',
                index=False,
                chunksize=1000,
                method='multi'
            )
            st.success(f"✅ Успешно сохранено строк: {len(df)}")
        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")


# ============================================================
# ВКЛАДКА 2: ИНТЕЛЛЕКТУАЛЬНЫЙ ОБЩИЙ ПОИСК
# ============================================================
with tab2:
    st.subheader("Поиск по общей базе")
    search = st.text_input("Введите артикул (P/N) или наименование")
    if st.button("Искать"):
        if len(search.strip()) < 2:
            st.warning("Введите минимум 2 символа")
        else:
            try:
                is_postgres = "postgres" in engine.url.drivername
                like_op = "ILIKE" if is_postgres else "LIKE"
                query = text(
                    f"SELECT supplier, uploaded_at, filename, raw_data "
                    f"FROM parts WHERE article {like_op} :s OR name {like_op} :s LIMIT 1000"
                )
                search_param = f"%{search.strip()}%"
                with engine.connect() as conn:
                    df = pd.read_sql_query(query, conn, params={"s": search_param})

                if df.empty:
                    st.warning("Ничего не найдено")
                else:
                    def safe_loads(val):
                        try:
                            return json.loads(val) if val else {}
                        except:
                            return {}

                    parsed_rows = df['raw_data'].apply(safe_loads).tolist()
                    suppliers = df['supplier'].tolist()
                    uploaded_ats = df['uploaded_at'].tolist()
                    filenames = df['filename'].tolist()

                    for idx, item in enumerate(parsed_rows):
                        item['_supplier'] = suppliers[idx]
                        item['_uploaded_at'] = uploaded_ats[idx]
                        item['_filename'] = filenames[idx]

                    result_df = pd.DataFrame(parsed_rows)

                    priority_cols = [
                        '_supplier', '_uploaded_at', '_filename',
                        'article', 'PN', 'P/N', 'TERM', 'name', 'DES',
                        'price', 'UNIT/USD', 'stock', 'QTY'
                    ]
                    existing_priority = [c for c in priority_cols if c in result_df.columns]
                    other_cols = [c for c in result_df.columns if c not in existing_priority]
                    result_df = result_df[existing_priority + other_cols]

                    st.success(f"✅ Найдено строк: {len(result_df)}")
                    st.dataframe(result_df, use_container_width=True, height=700)
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")


# ============================================================
# ВКЛАДКА 3: ВЕСЬ ФАЙЛ + СТАТИСТИКА БАЗЫ
# ============================================================
with tab3:
    st.subheader("Экспорт всей базы")
    if st.button("📥 Экспортировать ВСЮ базу в Excel", type="primary"):
        with st.spinner("Создаём Excel файл..."):
            try:
                with engine.connect() as conn:
                    df = pd.read_sql_query(text("SELECT raw_data FROM parts"), conn)

                if df.empty:
                    st.warning("База пустая")
                else:
                    def safe_loads(val):
                        try:
                            return json.loads(val) if val else {}
                        except:
                            return {}

                    all_rows = df['raw_data'].apply(safe_loads).tolist()
                    full_df = pd.DataFrame(all_rows)

                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        full_df.to_excel(writer, index=False, sheet_name='All_Data')

                    file_bytes = output.getvalue()
                    filename = f"Full_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

                    public_link = None
                    if YANDEX_TOKEN:
                        public_link = upload_to_yandex_disk(file_bytes, filename, YANDEX_TOKEN)

                    if public_link:
                        with engine.begin() as conn:
                            conn.execute(
                                text("""
                                    INSERT INTO exports (filename, download_url, created_at, file_size)
                                    VALUES (:f, :u, :c, :s)
                                """),
                                {
                                    "f": filename,
                                    "u": public_link,
                                    "c": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "s": len(file_bytes)
                                }
                            )
                        st.success(f"✅ Файл загружен на Яндекс.Диск! [Скачать]({public_link})")
                    else:
                        st.warning("Не удалось загрузить файл на Яндекс.Диск.")
                        st.info("Токен Яндекс.Диска не задан. Доступно только локальное скачивание.")
                        st.download_button(
                            label="⬇ Скачать полный Excel файл (локально)",
                            data=file_bytes,
                            file_name=filename,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
            except Exception as e:
                st.error(f"Ошибка экспорта: {e}")

    st.divider()
    st.subheader("Статистика базы")
    try:
        with engine.connect() as conn:
            total_rows = conn.execute(text("SELECT COUNT(*) FROM parts")).scalar() or 0
            total_suppliers = conn.execute(text("SELECT COUNT(DISTINCT supplier) FROM parts")).scalar() or 0
            total_files = conn.execute(text("SELECT COUNT(DISTINCT filename) FROM parts")).scalar() or 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Всего строк", f"{total_rows:,}".replace(",", " "))
        c2.metric("Поставщиков", total_suppliers)
        c3.metric("Файлов", total_files)
    except Exception as e:
        st.error(f"Ошибка статистики: {e}")

    st.divider()
    st.subheader("📁 История экспортов")
    try:
        with engine.connect() as conn:
            exports_df = pd.read_sql_query(
                text("SELECT filename, created_at, download_url FROM exports ORDER BY id DESC LIMIT 50"),
                conn
            )
        if not exports_df.empty:
            exports_df['Скачать'] = exports_df['download_url'].apply(
                lambda url: f'<a href="{url}" target="_blank">📥 Скачать</a>'
                if url and str(url).startswith('http')
                else '❌ Нет ссылки'
            )
            st.markdown(
                exports_df[['filename', 'created_at', 'Скачать']].to_html(escape=False, index=False),
                unsafe_allow_html=True
            )
        else:
            st.info("Нет сохранённых экспортов.")
    except Exception as e:
        st.error(f"Ошибка загрузки истории: {e}")

    st.caption("Экспорт может занять время при большом объёме данных")


# ============================================================
# ВКЛАДКА 4: ЮТЭЙР — СУПЕР-МОДУЛЬ ДОКУМЕНТООБОРОТА СНАБЖЕНИЯ
# ============================================================
with tab4:
    st.subheader("✈️ Модуль автоматизации снабжения (ЮТэйр)")
    sub_tab1, sub_tab2, sub_tab3, sub_tab4 = st.tabs([
        "1. Загрузка файла Ютов и Сводный RFQ",
        "2. Сбор предложений поставщиков",
        "3. Формирование КП для Ютов",
        "4. Формирование заказов"
    ])

    # 1. Разбор запроса Ютов и авто-генерация сводного RFQ поставщикам
    with sub_tab1:
        st.markdown("### Шаг 1. Импорт ежедневного запроса покупателя")
        req_date = datetime.now().strftime("%Y%m%d")
        generated_req_num = st.text_input(
            "Внутренний номер запроса (Признак)",
            value=f"REQ-{req_date}-01"
        )
        ut_file = st.file_uploader(
            "Загрузите Excel файл от Ютов",
            type=['xlsx', 'xls'],
            key="ut_uploader"
        )

        if ut_file and st.button("Разобрать и проверить файл Ютов", type="primary"):
            try:
                ut_df = pd.read_excel(ut_file)
                ut_df.columns = [str(c).strip() for c in ut_df.columns]

                found_art = next(
                    (c for c in ut_df.columns
                     if c.lower() in ['p/n', 'pn', 'part number', 'артикул', 'номер детали']),
                    None
                )
                found_qty = next(
                    (c for c in ut_df.columns
                     if c.lower() in ['qty', 'quantity', 'кол-во', 'количество', 'кол']),
                    None
                )

                if not found_art or not found_qty:
                    st.error(
                        "❌ ФАЙЛ НЕ РАЗОБРАЛСЯ! Не найдены обязательные колонки с Артикулом (P/N) или Количеством (Qty)."
                    )
                    st.info("Доступные заголовки в файле:")
                    st.write(list(ut_df.columns))
                else:
                    ut_df = ut_df.rename(columns={found_art: 'article', found_qty: 'qty'})
                    ut_df['Ошибка'] = ""
                    ut_df.loc[
                        ut_df['article'].isna() | (ut_df['article'].astype(str).str.strip() == ""),
                        'Ошибка'
                    ] += "Пустой P/N; "
                    ut_df.loc[
                        ut_df['qty'].isna() | (pd.to_numeric(ut_df['qty'], errors='coerce').isna()),
                        'Ошибка'
                    ] += "Неверное кол-во; "

                    errors_df = ut_df[ut_df['Ошибка'] != ""]
                    if not errors_df.empty:
                        st.error(f"⚠️ Найдено строк с ошибками: {len(errors_df)}. Они подсвечены ниже:")
                        st.dataframe(errors_df[['article', 'qty', 'Ошибка']], use_container_width=True)

                    clean_df = ut_df[ut_df['Ошибка'] == ""].copy()
                    if not clean_df.empty:
                        clean_df['qty'] = clean_df['qty'].astype(int)
                        with engine.begin() as conn:
                            for _, row in clean_df.iterrows():
                                name_val = str(
                                    row.get('name', row.get('Description', row.get('Наименование', 'Авиадеталь')))
                                )
                                conn.execute(
                                    text("""
                                        INSERT INTO customer_requests
                                        (request_number, customer_name, article, name, qty_required, created_at)
                                        VALUES (:r, 'ЮТэйр', :art, :name, :q, :dt)
                                    """),
                                    {
                                        "r": generated_req_num,
                                        "art": str(row['article']).strip(),
                                        "name": name_val,
                                        "q": int(row['qty']),
                                        "dt": datetime.now().strftime("%Y-%m-%d %H:%M")
                                    }
                                )
                        st.success(f"🎉 Файл успешно сохранен под признаком: {generated_req_num}")

                        rfq_grouped = clean_df.groupby('article', as_index=False)['qty'].sum()
                        rfq_out = io.BytesIO()
                        with pd.ExcelWriter(rfq_out, engine='openpyxl') as w:
                            rfq_grouped.to_excel(w, index=False)
                        st.download_button(
                            "📥 Скачать Сводное RFQ для Поставщиков",
                            rfq_out.getvalue(),
                            f"RFQ_{generated_req_num}.xlsx"
                        )
            except Exception as e:
                st.error(f"Ошибка разбора: {e}")

    # 2. Сбор предложений от различных поставщиков
    with sub_tab2:
        st.markdown("### Шаг 2. Сбор цен от поставщиков")
        try:
            with engine.connect() as conn:
                active_reqs = pd.read_sql_query(
                    text("SELECT DISTINCT request_number FROM customer_requests ORDER BY created_at DESC"),
                    conn
                )
            if not active_reqs.empty:
                chosen_req = st.selectbox(
                    "Выберите признак (номер вашего запроса)",
                    active_reqs['request_number'],
                    key="sel_req"
                )
                sup_name = st.text_input("Название ответившего поставщика").strip()
                sup_file = st.file_uploader(
                    "Загрузите Excel-ответ поставщика",
                    type=['xlsx', 'xls']
                )

                if sup_file and sup_name and st.button("Импортировать цены поставщика"):
                    s_df = pd.read_excel(sup_file)
                    s_df.columns = [str(c).strip() for c in s_df.columns]

                    s_art = next(
                        (c for c in s_df.columns
                         if c.lower() in ['p/n', 'pn', 'part number', 'артикул', 'номер детали']),
                        None
                    )
                    s_prc = next(
                        (c for c in s_df.columns
                         if c.lower() in ['price', 'cost', 'цена', 'unit/usd', 'total/usd']),
                        None
                    )
                    s_qty = next(
                        (c for c in s_df.columns
                         if c.lower() in ['qty', 'stock', 'quantity', 'кол-во', 'количество']),
                        None
                    )

                    if not s_art or not s_prc:
                        st.error("Файл не распознан. Нужны колонки P/N (Артикул) и Цена.")
                    else:
                        s_df = s_df.rename(columns={s_art: 'article', s_prc: 'price'})
                        s_df['price'] = s_df['price'].apply(clean_price)
                        c_sdf = s_df[s_df['article'].notna() & s_df['price'].notna()].copy()

                        with engine.begin() as conn:
                            for _, r in c_sdf.iterrows():
                                q_val = (
                                    int(r[s_qty])
                                    if s_qty and pd.notna(r[s_qty]) and str(r[s_qty]).isdigit()
                                    else 1
                                )
                                conn.execute(
                                    text("""
                                        INSERT INTO supplier_offers
                                        (request_number, supplier, article, qty_offered, price_offered, received_at)
                                        VALUES (:r, :s, :a, :q, :p, :dt)
                                    """),
                                    {
                                        "r": chosen_req,
                                        "s": sup_name,
                                        "a": str(r['article']).strip(),
                                        "q": q_val,
                                        "p": float(r['price']),
                                        "dt": datetime.now().strftime("%Y-%m-%d %H:%M")
                                    }
                                )
                        st.success(f"✅ Успешно добавлено позиций от {sup_name}: {len(c_sdf)}")
            else:
                st.info("Нет активных запросов.")
        except Exception as e:
            st.error(f"Ошибка сбора предложений: {e}")

    # 3. Автоматический расчет КП для Ютов (Алгоритм лучшей цены + наценка)
    with sub_tab3:
        st.markdown("### Шаг 3. Расчет КП для ЮТэйр (Лучшая цена)")
        margin_pct = st.number_input(
            "Ваша наценка на стоимость (%)",
            min_value=0.0,
            value=15.0,
            step=1.0
        )

        try:
            with engine.connect() as conn:
                reqs_for_kp = pd.read_sql_query(
                    text("SELECT DISTINCT request_number FROM customer_requests ORDER BY created_at DESC"),
                    conn
                )
            if not reqs_for_kp.empty:
                kp_req = st.selectbox(
                    "Сформировать КП по запросу:",
                    reqs_for_kp['request_number'],
                    key="kp_box"
                )

                if st.button("📊 Сгенерировать Коммерческое Предложение"):
                    with engine.connect() as conn:
                        req_items = pd.read_sql_query(
                            text("""
                                SELECT article, name, qty_required
                                FROM customer_requests
                                WHERE request_number = :r
                            """),
                            conn,
                            params={"r": kp_req}
                        )
                        offers = pd.read_sql_query(
                            text("""
                                SELECT supplier, article, price_offered
                                FROM supplier_offers
                                WHERE request_number = :r
                            """),
                            conn,
                            params={"r": kp_req}
                        )

                    if req_items.empty:
                        st.warning("Запрос пустой")
                    elif offers.empty:
                        st.warning(
                            "От поставщиков пока нет ответов по этому запросу. "
                            "Сначала загрузите их на Шаге 2."
                        )
                    else:
                        # Находим минимальную цену для каждого артикула
                        best_offers = (
                            offers
                            .sort_values('price_offered')
                            .groupby('article', as_index=False)
                            .first()
                        )
                        kp_df = pd.merge(req_items, best_offers, on='article', how='left')

                        # Расчет стоимости с наценкой
                        kp_df['Цена для ЮТэйр (USD)'] = kp_df['price_offered'].apply(
                            lambda x: round(x * (1 + margin_pct / 100), 2) if pd.notna(x) else "Нет предложения"
                        )
                        kp_df = kp_df.rename(columns={
                            'article': 'P/N',
                            'name': 'Наименование',
                            'qty_required': 'Кол-во',
                            'supplier': 'Лучший Поставщик',
                            'price_offered': 'Базовая цена (USD)'
                        })

                        st.write("### Превью Коммерческого Предложения")
                        st.dataframe(kp_df, use_container_width=True)

                        kp_out = io.BytesIO()
                        with pd.ExcelWriter(kp_out, engine='openpyxl') as w:
                            kp_df.to_excel(w, index=False, sheet_name='КП_ЮТэйр')
                        st.download_button(
                            "📥 Скачать готовое КП для ЮТэйр (Excel)",
                            kp_out.getvalue(),
                            f"KP_UTair_{kp_req}.xlsx"
                        )
            else:
                st.info("Нет запросов для КП.")
        except Exception as e:
            st.error(f"Ошибка КП: {e}")

    # 4. Формирование заказов поставщикам (Разделение сводного файла по исполнителям)
    with sub_tab4:
        st.markdown("### Шаг 4. Размещение заказов поставщикам")
        try:
            with engine.connect() as conn:
                reqs_for_order = pd.read_sql_query(
                    text("SELECT DISTINCT request_number FROM customer_requests ORDER BY created_at DESC"),
                    conn
                )
            if not reqs_for_order.empty:
                ord_req = st.selectbox(
                    "Сформировать заказы на основании запроса:",
                    reqs_for_order['request_number'],
                    key="ord_box"
                )

                if st.button("📦 Разбить сводный заказ по поставщикам"):
                    with engine.connect() as conn:
                        req_items = pd.read_sql_query(
                            text("""
                                SELECT article, qty_required
                                FROM customer_requests
                                WHERE request_number = :r
                            """),
                            conn,
                            params={"r": ord_req}
                        )
                        offers = pd.read_sql_query(
                            text("""
                                SELECT supplier, article, price_offered
                                FROM supplier_offers
                                WHERE request_number = :r
                            """),
                            conn,
                            params={"r": ord_req}
                        )

                    if offers.empty:
                        st.warning("Нет предложений от поставщиков для закупки.")
                    else:
                        best_offers = (
                            offers
                            .sort_values('price_offered')
                            .groupby('article', as_index=False)
                            .first()
                        )
                        final_orders = pd.merge(req_items, best_offers, on='article', how='inner')
                        unique_suppliers = final_orders['supplier'].unique()

                        st.success(
                            f"Сводный заказ успешно распределен между {len(unique_suppliers)} поставщиками!"
                        )

                        for supplier in unique_suppliers:
                            sup_items = final_orders[final_orders['supplier'] == supplier][
                                ['article', 'qty_required', 'price_offered']
                            ]
                            sup_items.columns = ['P/N (Парт-номер)', 'Количество', 'Цена за единицу (USD)']
                            sup_items['Итоговая стоимость (USD)'] = (
                                sup_items['Количество'] * sup_items['Цена за единицу (USD)']
                            )

                            st.write(
                                f"Заказ для поставщика: {supplier} "
                                f"(Внутренний номер: ORD-{ord_req}-{supplier})"
                            )
                            st.dataframe(sup_items, use_container_width=True)

                            sup_out = io.BytesIO()
                            with pd.ExcelWriter(sup_out, engine='openpyxl') as w:
                                sup_items.to_excel(w, index=False)
                            st.download_button(
                                f"📥 Скачать Excel заказа для {supplier}",
                                sup_out.getvalue(),
                                f"Order_{supplier}_{ord_req}.xlsx"
                            )
            else:
                st.info("Нет данных для заказов.")
        except Exception as e:
            st.error(f"Ошибка формирования заказов: {e}")
