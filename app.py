import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime, date

DB_CONFIG = {
    "host": "db",
    "database": "production_db",
    "user": "nero",
    "password": "secure_password_123"
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

# === Аутентификация ===
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Вход для аудита")
    with st.form("auth"):
        pwd = st.text_input("Пароль", type="password")
        if st.form_submit_button("Войти"):
            if pwd == "audit2025":
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Неверный пароль")
    st.stop()

# === Основной UI ===
st.title("🐟 Система прослеживаемости производства")

# === Импорт Excel ===
st.subheader("📥 Импорт выпуска из Меркурия")
uploaded_file = st.file_uploader("Загрузите Excel-файл", type=["xlsx"])

if uploaded_file:
    try:
        # Читаем все листы (по умолчанию первый)
        df = pd.read_excel(uploaded_file)

        # Функция поиска колонки по точному имени (без учёта регистра и пробелов)
        def find_col(cols, expected):
            for col in cols:
                if str(col).strip().lower() == expected.lower():
                    return col
            return None

        date_col = find_col(df.columns, "Дата выработки")
        name_col = find_col(df.columns, "Наименование продукции")
        qty_col = find_col(df.columns, "Объём")

        if not all([date_col, name_col, qty_col]):
            st.error("❌ Не найдены обязательные колонки: 'Дата выработки', 'Наименование продукции', 'Объём'")
            st.write("Доступные колонки:", list(df.columns))
        else:
            conn = get_db_connection()
            cur = conn.cursor()

            processed = 0
            for _, row in df.iterrows():
                full_name = str(row[name_col]).strip()
                qty_kg = float(row[qty_col])
                # Обработка даты в формате "06.11.2025:00"
                date_str = str(row[date_col]).strip()
                if ':' in date_str and '.' in date_str:
                    date_part = date_str.split(':')[0]  # Берём только "06.11.2025"
                    prod_date = pd.to_datetime(date_part, format='%d.%m.%Y').date()
                else:
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
                processed += 1

            conn.commit()
            cur.close()
            conn.close()
            st.success(f"✅ Успешно обработано записей: {processed}")

    except Exception as e:
        st.error(f"Ошибка при обработке файла: {str(e)}")

# === Отчёт по дате ===
st.subheader("📅 Отчёт по дате выработки")
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

        # Списанные компоненты (реальные списания)
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
        water_row = cur.fetchone()
        if water_row:
            write_offs.append(water_row)

        for comp_name, qty in write_offs:
            st.write(f"- {comp_name}: {qty:.4f} кг")
else:
    st.info("Нет данных за выбранную дату.")

cur.close()
conn.close()