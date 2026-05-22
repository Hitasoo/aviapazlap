import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import json
import io

st.set_page_config(page_title="AviaPazlAP", layout="wide")

engine = create_engine('sqlite:///suppliers.db', echo=False)


# ===================== ИНИЦИАЛИЗАЦИЯ БАЗЫ =====================
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
        conn.commit()  # ← Правильный commit


init_db()

st.title("📦 AviaPazlAP — Общая база поставщиков")

tab1, tab2 = st.tabs(["Загрузка", "Поиск"])

# ===================== ЗАГРУЗКА =====================
with tab1:
    supplier_name = st.text_input("Название поставщика *", placeholder="Tina")
    uploaded_file = st.file_uploader("Excel файл", type=['xlsx', 'xls'])

    if uploaded_file and supplier_name and st.button("💾 Сохранить в базу", type="primary"):
        try:
            df = pd.read_excel(uploaded_file)

            # Переименование колонок
            rename_map = {
                'PN': 'article', 'P/N': 'article', 'TERM': 'article',
                'DES': 'name', 'Description': 'name',
                'UNIT/USD': 'price', 'Price': 'price',
                'QTY': 'stock', 'Quantity': 'stock'
            }
            for old, new in rename_map.items():
                if old in df.columns and new not in df.columns:
                    df = df.rename(columns={old: new})

            # Защита от отсутствующих колонок
            if 'article' not in df.columns: df['article'] = None
            if 'name' not in df.columns: df['name'] = None
            if 'price' not in df.columns: df['price'] = None
            if 'stock' not in df.columns: df['stock'] = None

            df['supplier'] = supplier_name.strip()
            df['uploaded_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
            df['filename'] = uploaded_file.name
            df['raw_data'] = df.apply(lambda x: json.dumps(x.to_dict(), ensure_ascii=False, default=str), axis=1)

            save_cols = ['supplier', 'article', 'name', 'price', 'stock', 'uploaded_at', 'filename', 'raw_data']
            df[save_cols].to_sql('parts', engine, if_exists='append', index=False)

            st.success(f"✅ Сохранено {len(df)} позиций")

        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")

# ===================== ПОИСК =====================
with tab2:
    search = st.text_input("Поиск по артикулу или названию")
    if st.button("🔍 Искать", type="primary"):
        with engine.connect() as conn:
            df = pd.read_sql("""
                SELECT supplier, article, name, price, stock, uploaded_at
                FROM parts 
                WHERE article LIKE :s OR name LIKE :s OR raw_data LIKE :s
            """, conn, params={"s": f"%{search}%"})

        if len(df) > 0:
            st.success(f"Найдено: {len(df)} позиций")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Ничего не найдено")

st.caption("AviaPazlAP © 2026")