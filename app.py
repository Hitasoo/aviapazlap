import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io
import requests

st.set_page_config(page_title="AviaPazlAP", layout="wide")

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
        conn.commit()


init_db()

st.title("📦 AviaPazlAP — Общая база поставщиков")

tab1, tab2, tab3 = st.tabs(["Загрузка", "Поиск", "Экспорт"])

# ===================== ЗАГРУЗКА =====================
with tab1:
    supplier_name = st.text_input("Название поставщика *")
    uploaded_file = st.file_uploader("Excel файл", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("💾 Сохранить в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)

            # Безопасное переименование
            rename_map = {
                'PN': 'article', 'P/N': 'article', 'TERM': 'article',
                'DES': 'name', 'Description': 'name', 'Наименование': 'name',
                'UNIT/USD': 'price', 'Price': 'price',
                'QTY': 'stock', 'Quantity': 'stock'
            }

            for old, new in rename_map.items():
                if old in df.columns and new not in df.columns:
                    df = df.rename(columns={old: new})

            # Добавляем отсутствующие колонки
            if 'article' not in df.columns:
                df['article'] = None
            if 'name' not in df.columns:
                df['name'] = None
            if 'price' not in df.columns:
                df['price'] = None
            if 'stock' not in df.columns:
                df['stock'] = None

            df['supplier'] = supplier_name.strip()
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str), axis=1)

            save_cols = ['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']
            df[save_cols].to_sql('parts', engine, if_exists='append', index=False)

            st.success(f"✅ Сохранено {len(df)} позиций от {supplier_name}")

        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")

# ===================== ПОИСК =====================
with tab2:
    search = st.text_input("Поиск по артикулу или названию")
    if st.button("🔍 Искать", type="primary"):
        with engine.connect() as conn:
            df = pd.read_sql("""
                SELECT supplier, article, name, price, stock, uploaded_at, raw_data
                FROM parts 
                WHERE article LIKE :s OR name LIKE :s OR raw_data LIKE :s
            """, conn, params={"s": f"%{search}%"})

        if len(df) > 0:
            rows = []
            for _, row in df.iterrows():
                item = json.loads(row['raw_data'])
                item['_supplier'] = row['supplier']
                rows.append(item)
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

                all_rows = [json.loads(r['raw_data']) for _, r in df_raw.iterrows()]
                full_df = pd.DataFrame(all_rows)

                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    full_df.to_excel(writer, index=False)
                output.seek(0)

                filename = f"AviaPazlAP_Full_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

                st.success(f"Файл готов ({len(full_df)} строк)")
                st.download_button(
                    label="⬇ Скачать Excel",
                    data=output,
                    file_name=filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"Ошибка: {e}")

st.caption("Данные теперь должны сохраняться после перезапуска")