import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io
import requests
import os
import re

# ============================================================
# Настройка страницы (ОБЯЗАТЕЛЬНО на первой строчке)
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
        # Автоматически добавляем требование SSL для стабильного подключения на Render
        if "sslmode" not in url:
            url += "&sslmode=require" if "?" in url else "?sslmode=require"

        # Настраиваем пул соединений с защитой от внезапных обрывов сети
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=300
        )
    return create_engine('sqlite:///suppliers.db', echo=False)


engine = get_db_engine(DATABASE_URL)

if DATABASE_URL:
    st.sidebar.success("✅ Подключено к PostgreSQL")
else:
    st.sidebar.warning("💻 Локальный режим: SQLite")

# ============================================================
# 2. ИНИЦИАЛИЗАЦИЯ ВСЕХ ТАБЛИЦ БАЗЫ ДАННЫХ
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


init_db()

# ============================================================
# 3. ВСПЕМОГАТЕЛЬНЫЕ ФУНКЦИИ (Яндекс.Диск, Очистка цены)
# ============================================================
def upload_to_yandex_disk(file_bytes, filename, token):
    if not token:
        st.warning("Токен Яндекс.Диска не задан")
        return None

    headers = {"Authorization": f"OAuth {token}"}
    folder_path = "/AviaPazlAP_exports"

    # Проверка/создание папки (передаем обычную строку, requests закодирует её сам)
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

    # Получаем URL для загрузки файла
    upload_url = "https://yandex.net/upload"
    full_path = f"{folder_path}/{filename}"

    resp = requests.get(upload_url, headers=headers, params={"path": full_path, "overwrite": "true"})
    if resp.status_code != 200:
        st.error(f"Ошибка получения ссылки для загрузки: {resp.text}")
        return None
    upload_href = resp.json()["href"]

    # Загружаем бинарные данные файла
    with io.BytesIO(file_bytes) as f:
        upload_resp = requests.put(upload_href, data=f.read())
    if upload_resp.status_code not in (200, 201):
        st.error(f"Ошибка загрузки файла на сервер: {upload_resp.text}")
        return None

    # Делаем файл публичным
    publish_url = "https://yandex.net/publish"
    pub_resp = requests.put(publish_url, headers=headers, params={"path": full_path})
    if pub_resp.status_code != 200:
        return f"https://yandex.ru{folder_path}"

    # Запрашиваем метаданные для получения красивой публичной ссылки
    info_resp = requests.get(check_url, headers=headers, params={"path": full_path})
    if info_resp.status_code == 200:
        public_url = info_resp.json().get("public_url")
        if public_url:
            return public_url

    return f"https://yandex.ru{folder_path}"


def clean_price(value):
    if pd.isna(value):
        return None

    # Переводим в строку и заменяем запятую на точку для корректного дробного формата
    s = str(value).strip().replace(',', '.')

    # Удаляем всё, кроме цифр, точек и знака минус
    cleaned = re.sub(r'[^\d\.\-]', '', s)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# ============================================================
# 4. ШАБЛОН ИНТЕРФЕЙСА (ГЛАВНОЕ МЕНЮ)
# ============================================================
col1, col2 = st.columns(2)
with col1:
    # Защита от падения, если файла логотипа нет в папке со скриптом
    if os.path.exists("logoSitr.jpg"):
        st.image("logoSitr.jpg", width=60)
    else:
        st.title("✈️")
with col2:
    st.title("AviaPazlAP — Управление Снабжением Авиадеталей")

tab1, tab2, tab3, tab4 = st.tabs(["📥 Загрузка Прайсов", "🔎 Поиск", "📊 Статистика базы", "🗂️ Документооборот ЮТэйр"])

# ============================================================
# ВКЛАДКА 1: ЗАГРУЗКА ПРАЙСОВ ПОСТАВЩИКОВ
# ============================================================
with tab1:
    st.subheader("Загрузка прайс-листов поставщиков в общую базу")
    supplier_name = st.text_input("Название поставщика").strip()
    uploaded_file = st.file_uploader("Выберите Excel файл прайса", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("Сохранить прайс в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)
            df.columns = [str(col).strip() for col in df.columns]

            # 1. Интеллектуальный поиск колонки с ценой
            price_col = None
            price_candidates = ['price', 'unit/usd', 'total/usd', 'цена', 'стоимость', 'стоимость usd']

            for col in df.columns:
                if col.lower() in price_candidates:
                    price_col = col
                    break

            if price_col:
                df[price_col] = df[price_col].apply(clean_price)
                df = df.rename(columns={price_col: 'price'})

            # 2. Интеллектуальный маппинг остальных ключевых колонок
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

            # 3. Добавляем метаданные
            df['supplier'] = supplier_name
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(
                lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str),
                axis=1
            )

            # 4. Гарантируем наличие всех целевых колонок, чтобы избежать сбоя KeyError
            save_columns = ['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']
            for col in save_columns:
                if col not in df.columns:
                    df[col] = None

            # Оптимизированная пакетная запись в БД
            df[save_columns].to_sql(
                'parts',
                engine,
                if_exists='append',
                index=False,
                chunksize=1000,
                method='multi'
            )
            st.success(f"✅ Успешно импортировано строк: {len(df)}")
        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")

# ============================================================
# ВКЛАДКА 2: ОБЩИЙ ПОИСК ПО БАЗЕ ПРАЙСОВ
# ============================================================
with tab2:
    st.subheader("Поиск по загруженным прайсам")
    search = st.text_input("Введите артикул (P/N) или наименование")
    if st.button("Найти детали"):
        if len(search.strip()) < 2:
            st.warning("Введите не менее 2-х символов для поиска.")
        else:
            try:
                query = text("""
                    SELECT supplier, uploaded_at, filename, raw_data
                    FROM parts
                    WHERE article LIKE :s OR name LIKE :s
                    LIMIT 500
                """)
                search_param = f"%{search.strip()}%"
                with engine.connect() as conn:
                    res_df = pd.read_sql_query(query, conn, params={"s": search_param})

                if res_df.empty:
                    st.info("По вашему запросу ничего не найдено.")
                else:
                    def safe_loads(val):
                        try:
                            return json.loads(val) if val else {}
                        except:
                            return {}

                    parsed_rows = res_df['raw_data'].apply(safe_loads).tolist()
                    for idx, item in enumerate(parsed_rows):
                        item['_supplier'] = res_df['supplier'].iloc[idx]
                        item['_uploaded_at'] = res_df['uploaded_at'].iloc[idx]
                        item['_filename'] = res_df['filename'].iloc[idx]

                    result_table = pd.DataFrame(parsed_rows)
                    st.dataframe(result_table, use_container_width=True, height=500)
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")

# ============================================================
# ВКЛАДКА 3: ВЕСЬ ФАЙЛ И СТАТИСТИКА БАЗЫ
# ============================================================
with tab3:
    st.subheader("Состояние базы данных")
    try:
        with engine.connect() as conn:
            total_rows = conn.execute(text("SELECT COUNT(*) FROM parts")).scalar() or 0
            total_suppliers = conn.execute(text("SELECT COUNT(DISTINCT supplier) FROM parts")).scalar() or 0
            total_files = conn.execute(text("SELECT COUNT(DISTINCT filename) FROM parts")).scalar() or 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Всего строк", f"{total_rows:,}".replace(",", " "))
        c2.metric("Поставщиков", total_suppliers)
        c3.metric("Файлов", total_files)
    except:
        st.write("База пока пуста.")

    st.write("---")
    st.subheader("Экспорт всей базы")
    if st.button("📥 Экспортировать ВСЮ базу в Excel", type="primary"):
        with st.spinner("Создаём Excel файл..."):
            try:
                with engine.connect() as conn:
                    df_all = pd.read_sql_query(text("SELECT raw_data FROM parts"), conn)

                if df_all.empty:
                    st.warning("База пуста, экспортировать нечего.")
                else:
                    def safe_loads(val):
                        try:
                            return json.loads(val) if val else {}
                        except:
                            return {}

                    all_rows = df_all['raw_data'].apply(safe_loads).tolist()
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
# ВКЛАДКА 4: ДОКУМЕНТООБОРОТ ЮТЭЙР (УПРАВЛЕНИЕ ЗАКУПКАМИ)
# ============================================================
with tab4:
    st.subheader("✈️ Модуль автоматизации снабжения (ЮТэйр)")
    sub_tab1, sub_tab2, sub_tab3, sub_tab4 = st.tabs([
        "1. Импорт запроса Ютов и Сводный RFQ",
        "2. Сбор предложений поставщиков",
        "3. Формирование КП для Ютов",
        "4. Формирование заказов"
    ])

    # --- ПОД-ВКЛАДКА 1: Разбор запроса Ютов и генерация сводного файла ---
    with sub_tab1:
        st.markdown("### Шаг 1. Разбор ежедневного файла запроса покупателя")
        req_date = datetime.now().strftime("%Y%m%d")
        generated_req_num = st.text_input(
            "Внутренний номер запроса (Признак)",
            value=f"REQ-{req_date}-01"
        )
        ut_file = st.file_uploader(
            "Загрузите Excel-файл запроса от Ютов",
            type=['xlsx', 'xls'],
            key="ut_req_uploader"
        )

        if ut_file and st.button("Разобрать и проверить файл Ютов", type="primary"):
            try:
                ut_df = pd.read_excel(ut_file)
                ut_df.columns = [str(c).strip() for c in ut_df.columns]

                art_synonyms = ['P/N', 'PN', 'Part Number', 'Артикул', 'Номер детали']
                qty_synonyms = ['Qty', 'QTY', 'Кол-во', 'Количество', 'Кол']

                found_art = next((c for c in ut_df.columns if c.lower() in [s.lower() for s in art_synonyms]), None)
                found_qty = next((c for c in ut_df.columns if c.lower() in [s.lower() for s in qty_synonyms]), None)

                if not found_art or not found_qty:
                    st.error("❌ ФАЙЛ НЕ РАЗОБРАЛСЯ! Не найдены обязательные колонки с Артикулом (P/N) или Количеством (Qty).")
                    st.info("Доступные заголовки в вашем файле (исправьте их в Excel под стандарт):")
                    st.write(list(ut_df.columns))
                else:
                    ut_df = ut_df.rename(columns={found_art: 'article', found_qty: 'qty'})

                    # Подсветка и валидация ошибок в строках
                    ut_df['Ошибка'] = ""
                    ut_df.loc[ut_df['article'].isna() | (ut_df['article'].astype(str).str.strip() == ""), 'Ошибка'] += "Пустой P/N; "
                    ut_df.loc[ut_df['qty'].isna() | (pd.to_numeric(ut_df['qty'], errors='coerce').isna()), 'Ошибка'] += "Некорректное/пустое кол-во; "

                    errors_df = ut_df[ut_df['Ошибка'] != ""]
                    if not errors_df.empty:
                        st.error(f"⚠️ Внимание! Найдено строк с ошибками: {len(errors_df)}. Они исключены из импорта:")
                        st.dataframe(errors_df[['article', 'qty', 'Ошибка']], use_container_width=True)

                    clean_df = ut_df[ut_df['Ошибка'] == ""].copy()
                    if clean_df.empty:
                        st.warning("В файле нет корректных данных для сохранения.")
                    else:
                        clean_df['qty'] = clean_df['qty'].astype(int)

                        with engine.begin() as conn:
                            for _, row in clean_df.iterrows():
                                desc_val = str(row.get('name', row.get('Description', row.get('Наименование', 'Авиадеталь'))))
                                conn.execute(
                                    text("""
                                        INSERT INTO customer_requests
                                        (request_number, customer_name, article, name, qty_required, created_at)
                                        VALUES (:r, 'ЮТэйр', :art, :name, :q, :dt)
                                    """),
                                    {
                                        "r": generated_req_num,
                                        "art": str(row['article']).strip(),
                                        "name": desc_val,
                                        "q": int(row['qty']),
                                        "dt": datetime.now().strftime("%Y-%m-%d %H:%M")
                                    }
                                )

                        st.success(f"🎉 Файл успешно импортирован! Сохранено позиций: {len(clean_df)}. Признак: {generated_req_num}")

                        # Группируем (схлопываем дубли деталей за день)
                        rfq_grouped = clean_df.groupby('article', as_index=False)['qty'].sum()
                        rfq_out = io.BytesIO()
                        with pd.ExcelWriter(rfq_out, engine='openpyxl') as writer:
                            rfq_grouped.to_excel(writer, index=False, sheet_name='RFQ_Suppliers')

                        st.write("---")
                        st.markdown("#### 📥 Скачать файл для рассылки поставщикам")
                        st.download_button(
                            label="🔥 Скачать Сводное RFQ для Поставщиков (Excel)",
                            data=rfq_out.getvalue(),
                            file_name=f"RFQ_Suppliers_{generated_req_num}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
            except Exception as e:
                st.error(f"Ошибка выполнения разбора: {e}")

    # --- ПОД-ВКЛАДКА 2: Сбор ответов поставщиков с привязкой по признаку ---
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
                    "Выберите признак (номер запроса), к которому относится ответ поставщика",
                    active_reqs['request_number'],
                    key="offer_req_box"
                )

                sup_name = st.text_input("Название ответившего поставщика").strip()
                sup_file = st.file_uploader(
                    "Загрузите Excel-ответ поставщика с ценами",
                    type=['xlsx', 'xls'],
                    key="sup_file_uploader"
                )

                if sup_file and sup_name and st.button("Импортировать цены поставщика в запрос", type="primary"):
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
                        st.error("❌ Файл поставщика не распознан. Нужны колонки P/N (Артикул) и Цена.")
                    else:
                        s_df = s_df.rename(columns={s_art: 'article', s_prc: 'price'})
                        s_df['price'] = s_df['price'].apply(clean_price)

                        c_sdf = s_df[s_df['article'].notna() & s_df['price'].notna()].copy()

                        with engine.begin() as conn:
                            for _, r in c_sdf.iterrows():
                                q_val = int(r[s_qty]) if s_qty and pd.notna(r[s_qty]) and str(r[s_qty]).isdigit() else 1
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

                        st.success(f"✅ Успешно добавлено позиций от поставщика {sup_name}: {len(c_sdf)}")
            else:
                st.info("Нет активных запросов в базе.")
        except Exception as e:
            st.error(f"Ошибка сбора предложений: {e}")

    # --- ПОД-ВКЛАДКА 3: Автоматический расчет КП для Ютов по лучшей стоимости ---
    with sub_tab3:
        st.markdown("### Шаг 3. Расчет КП для ЮТэйр (Поиск минимальной закупочной цены)")
        margin_pct = st.number_input(
            "Ваша маржинальная наценка на стоимость детали (%)",
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
                    "Сформировать КП на основании запроса:",
                    reqs_for_kp['request_number'],
                    key="kp_box"
                )

                if st.button("📊 Сгенерировать Коммерческое Предложение"):
                    with engine.connect() as conn:
                        req_items = pd.read_sql_query(
                            text("SELECT article, name, qty_required FROM customer_requests WHERE request_number = :r"),
                            conn,
                            params={"r": kp_req}
                        )
                        offers = pd.read_sql_query(
                            text("SELECT supplier, article, price_offered FROM supplier_offers WHERE request_number = :r"),
                            conn,
                            params={"r": kp_req}
                        )

                    if req_items.empty:
                        st.warning("Запрос пустой")
                    elif offers.empty:
                        st.warning("От поставщиков пока нет ответов по этому признаку. Сначала загрузите их файлы на Шаге 2.")
                    else:
                        # Сортируем по цене и берем первую строчку — это автоматически минимальная цена для каждого P/N
                        best_offers = offers.sort_values('price_offered').groupby('article', as_index=False).first()

                        # Объединяем запрос Ютов и лучшие цены
                        kp_df = pd.merge(req_items, best_offers, on='article', how='left')

                        # Расчет итоговой цены для покупателя
                        kp_df['Цена для ЮТэйр (USD)'] = kp_df['price_offered'].apply(
                            lambda x: round(x * (1 + margin_pct / 100), 2) if pd.notna(x) else "Нет предложения"
                        )
                        kp_df = kp_df.rename(columns={
                            'article': 'P/N',
                            'name': 'Наименование',
                            'qty_required': 'Кол-во',
                            'supplier': 'Лучший Поставщик',
                            'price_offered': 'Базовая цена поставщика (USD)'
                        })

                        st.write("### Превью результирующего КП")
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
                st.info("Нет запросов для генерации КП.")
        except Exception as e:
            st.error(f"Ошибка КП: {e}")

    # --- ПОД-ВКЛАДКА 4: Авто-разбивка общего заказа по поставщикам-победителям ---
    with sub_tab4:
        st.markdown("### Шаг 4. Автоматическое распределение и формирование заказов")
        try:
            with engine.connect() as conn:
                reqs_for_order = pd.read_sql_query(
                    text("SELECT DISTINCT request_number FROM customer_requests ORDER BY created_at DESC"),
                    conn
                )

            if not reqs_for_order.empty:
                ord_req = st.selectbox(
                    "Сформировать индивидуальные заказы по запросу:",
                    reqs_for_order['request_number'],
                    key="ord_box"
                )

                if st.button("📦 Разбить сводный заказ по поставщикам"):
                    with engine.connect() as conn:
                        req_items = pd.read_sql_query(
                            text("SELECT article, qty_required FROM customer_requests WHERE request_number = :r"),
                            conn,
                            params={"r": ord_req}
                        )
                        offers = pd.read_sql_query(
                            text("SELECT supplier, article, price_offered FROM supplier_offers WHERE request_number = :r"),
                            conn,
                            params={"r": ord_req}
                        )

                    if offers.empty:
                        st.warning("Не найдено ценовых предложений от поставщиков для закупки.")
                    else:
                        best_offers = offers.sort_values('price_offered').groupby('article', as_index=False).first()
                        final_orders = pd.merge(req_items, best_offers, on='article', how='inner')
                        unique_suppliers = final_orders['supplier'].unique()

                        st.success(f"Сводный заказ успешно разделен между {len(unique_suppliers)} поставщиками-победителями!")

                        for supplier in unique_suppliers:
                            sup_items = final_orders[final_orders['supplier'] == supplier][
                                ['article', 'qty_required', 'price_offered']
                            ]
                            sup_items.columns = ['P/N (Парт-номер)', 'Количество заказа', 'Цена за единицу (USD)']
                            sup_items['Итоговая стоимость закупки (USD)'] = (
                                sup_items['Количество заказа'] * sup_items['Цена за единицу (USD)']
                            )

                            st.write(f"Заказ для контрагента: {supplier} (Номер: ORD-{ord_req}-{supplier})")
                            st.dataframe(sup_items, use_container_width=True)
            else:
                st.info("Нет запросов для формирования заказов.")
        except Exception as e:
            st.error(f"Ошибка формирования заказов: {e}")
