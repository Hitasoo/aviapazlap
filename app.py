
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io

st.set_page_config(page_title="AviaPazlAP", layout="wide")

# ===================== DATABASE =====================

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

        # Индексы для ускорения поиска
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_article ON parts(article)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_name ON parts(name)
        """))

        conn.commit()


init_db()

# ===================== UI =====================

st.title("📦 AviaPazlAP — Общая база поставщиков")

tab1, tab2, tab3 = st.tabs([
    "Загрузка",
    "Поиск",
    "Весь файл + Статистика"
])

# ===================== ЗАГРУЗКА =====================

with tab1:

    st.subheader("Загрузка Excel файла")

    supplier_name = st.text_input("Название поставщика")

    uploaded_file = st.file_uploader(
        "Excel файл",
        type=['xlsx', 'xls']
    )

    if uploaded_file and supplier_name and st.button(
        "Сохранить в базу",
        type="primary"
    ):

        try:

            # Читаем Excel
            df = pd.read_excel(uploaded_file)

            # Нормализация колонок
            rename_map = {
                'PN': 'article',
                'P/N': 'article',
                'TERM': 'article',
                'DES': 'name',
                'UNIT/USD': 'price',
                'QTY': 'stock'
            }

            for old, new in rename_map.items():
                if old in df.columns and new not in df.columns:
                    df = df.rename(columns={old: new})

            # Метаданные
            df['supplier'] = supplier_name
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name

            # Сохраняем оригинальную строку Excel
            df['raw_data'] = df.apply(
                lambda x: json.dumps(
                    x.to_dict(),
                    ensure_ascii=False,
                    default=str
                ),
                axis=1
            )

            # Сохраняем в SQL
            save_columns = [
                'supplier',
                'article',
                'name',
                'price',
                'stock',
                'uploaded_at',
                'filename',
                'raw_data'
            ]

            # Добавляем отсутствующие колонки
            for col in save_columns:
                if col not in df.columns:
                    df[col] = None

            df[save_columns].to_sql(
                'parts',
                engine,
                if_exists='append',
                index=False
            )

            st.success(f"✅ Сохранено строк: {len(df)}")

        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")

# ===================== ПОИСК =====================

with tab2:

    st.subheader("Поиск по общей базе")

    search = st.text_input(
        "Введите артикул, название или любой текст"
    )

    if st.button("Искать"):

        if len(search.strip()) < 2:
            st.warning("Введите минимум 2 символа")

        else:

            try:

                with engine.connect() as conn:

                    df = pd.read_sql("""
                        SELECT 
                            supplier,
                            uploaded_at,
                            filename,
                            raw_data
                        FROM parts 
                        WHERE article LIKE :s
                           OR name LIKE :s
                           OR raw_data LIKE :s
                        LIMIT 5000
                    """, conn, params={
                        "s": f"%{search}%"
                    })

                if len(df) == 0:

                    st.warning("Ничего не найдено")

                else:

                    rows = []

                    # Восстанавливаем оригинальные колонки Excel
                    for _, r in df.iterrows():

                        item = json.loads(r['raw_data'])

                        # Добавляем служебную информацию
                        item['_supplier'] = r['supplier']
                        item['_uploaded_at'] = r['uploaded_at']
                        item['_filename'] = r['filename']

                        rows.append(item)

                    result_df = pd.DataFrame(rows)

                    # Перемещаем важные колонки вперёд
                    priority_cols = [
                        '_supplier',
                        '_uploaded_at',
                        '_filename',
                        'article',
                        'PN',
                        'P/N',
                        'TERM',
                        'name',
                        'DES',
                        'price',
                        'UNIT/USD',
                        'stock',
                        'QTY'
                    ]

                    existing_priority = [
                        c for c in priority_cols
                        if c in result_df.columns
                    ]

                    other_cols = [
                        c for c in result_df.columns
                        if c not in existing_priority
                    ]

                    result_df = result_df[
                        existing_priority + other_cols
                    ]

                    st.success(f"✅ Найдено строк: {len(result_df)}")

                    st.dataframe(
                        result_df,
                        use_container_width=True,
                        height=700
                    )

            except Exception as e:
                st.error(f"Ошибка поиска: {e}")

# ===================== ЭКСПОРТ =====================

with tab3:

    st.subheader("Экспорт всей базы")

    if st.button(
        "📥 Экспортировать ВСЮ базу в Excel",
        type="primary"
    ):

        with st.spinner("Создаём Excel файл..."):

            try:

                with engine.connect() as conn:
                    df = pd.read_sql(
                        "SELECT raw_data FROM parts",
                        conn
                    )

                if len(df) == 0:

                    st.warning("База пустая")

                else:

                    all_rows = []

                    # Восстанавливаем оригинальные строки
                    for row in df['raw_data']:
                        all_rows.append(json.loads(row))

                    full_df = pd.DataFrame(all_rows)

                    # Создание Excel
                    output = io.BytesIO()

                    with pd.ExcelWriter(
                        output,
                        engine='openpyxl'
                    ) as writer:

                        full_df.to_excel(
                            writer,
                            index=False,
                            sheet_name='All_Data'
                        )

                    output.seek(0)

                    st.success(
                        f"✅ Готово! Всего строк: {len(full_df)}"
                    )

                    st.download_button(
                        label="⬇ Скачать полный Excel файл",
                        data=output,
                        file_name=f"Полная_база_AviaPazlAP_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

            except Exception as e:
                st.error(f"Ошибка экспорта: {e}")

    # ===================== СТАТИСТИКА =====================

    st.divider()

    st.subheader("Статистика базы")

    try:

        with engine.connect() as conn:

            total_rows = conn.execute(
                text("SELECT COUNT(*) FROM parts")
            ).scalar()

            total_suppliers = conn.execute(
                text("SELECT COUNT(DISTINCT supplier) FROM parts")
            ).scalar()

            total_files = conn.execute(
                text("SELECT COUNT(DISTINCT filename) FROM parts")
            ).scalar()

        col1, col2, col3 = st.columns(3)

        col1.metric("Всего строк", total_rows)
        col2.metric("Поставщиков", total_suppliers)
        col3.metric("Файлов", total_files)

    except Exception as e:
        st.error(f"Ошибка статистики: {e}")

    st.caption(
        "Экспорт может занять время при большом объёме данных"
    )

