import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime, date
import re

DB_CONFIG = {
    "host": "db",
    "database": "production_db",
    "user": "nero",
    "password": "secure_password_123"
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

# Простая аутентификация
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    with st.form("auth"):
        pwd = st.text_input("Пароль для аудита", type="password")
        if st.form_submit_button("Войти"):
            if pwd == "audit2025":
                st.session_state.authenticated = True
                st.rerun()
    st.stop()

st.title("🐟 Система прослеживаемости производства")

# === ИМПОРТ EXCEL ===
st.subheader("Импорт выпуска из Меркурия")
uploaded_file = st.file_uploader("Загрузите Excel-файл", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file, skiprows=7)
        
        date_col = [c for c in df.columns if 'дата выработки' in str(c).lower() or 'выработки' in str(c).lower()][0]
        name_col = [c for c in df.columns if 'наименование продукции' in str(c).lower()][0]
        qty_col = [c for c in df.columns if 'объём' in str(c).lower()][0]

        conn = get_db_connection()
        cur = conn.cursor()

        for _, row in df.iterrows():
            full_name = str(row[name_col]).strip()
            qty_kg = float(row[qty_col])
            prod_date = pd.to_datetime(row[date_col]).date()

            cur.execute("SELECT id FROM products WHERE mercurius_name = %s", (full_name,))
            prod = cur.fetchone()
            if not prod:
                st.warning(f"Продукт не найден: {full_name}")
                continue

            cur.execute("""
                INSERT INTO finished_goods (production_date, product_id, quantity_kg)
                VALUES (%s, %s, %s)
            """, (prod_date, prod[0], qty_kg))

        conn.commit()
        cur.close()
        conn.close()
        st.success("✅ Файл обработан и данные сохранены.")
    except Exception as e:
        st.error(f"Ошибка: {str(e)}")

# === ОТЧЁТ ПО ДАТЕ ===
st.subheader("Отчёт по дате выработки")
selected_date = st.date_input("Выберите дату", value=date.today())

conn = get_db_connection()
cur = conn.cursor()

cur.execute("""
    SELECT fg.id, p.mercurius_name, fg.quantity_kg, p.package_weight_kg
    FROM finished_goods fg
    JOIN products p ON fg.product_id = p.id
    WHERE fg.production_date = %s
    ORDER BY p.mercurius_name
""", (selected_date,))
releases = cur.fetchall()

if releases:
    for fg_id, name, kg, pkg_kg in releases:
        pieces = kg / pkg_kg
        st.markdown(f"### {name}")
        st.write(f"**Объём:** {kg} кг | **Штук:** {pieces:.0f}")

        # Списания (включая воду — для отображения)
        cur.execute("""
            SELECT c.name, w.quantity
            FROM write_offs w
            JOIN components c ON w.component_id = c.id
            WHERE w.finished_good_id = %s
            ORDER BY c.name
        """, (fg_id,))
        write_offs = cur.fetchall()

        # Добавляем воду из рецептуры (только для отображения!)
        cur.execute("""
            SELECT 'Вода', ri.quantity_per_kg * %s
            FROM recipe_items ri
            JOIN components c ON ri.component_id = c.id
            JOIN products p ON ri.recipe_id = p.recipe_id
            WHERE p.id = (
                SELECT product_id FROM finished_goods WHERE id = %s
            ) AND c.name = 'Вода'
        """, (kg, fg_id))
        water = cur.fetchone()
        if water:
            write_offs.append(water)

        for comp_name, qty in write_offs:
            st.write(f"- {comp_name}: {qty:.4f} кг")
else:
    st.info("Нет данных за выбранную дату.")

cur.close()
conn.close()