import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime, date
from collections import defaultdict

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

st.title("🐟 Система прослеживаемости производства")

# === Импорт Excel ===
st.subheader("📥 Импорт выпуска из Меркурия")
uploaded_file = st.file_uploader("Загрузите Excel-файл", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)

        def find_col(cols, expected):
            for col in cols:
                if str(col).strip().lower() == expected.lower():
                    return col
            return None

        date_col = find_col(df.columns, "Дата выработки")
        name_col = find_col(df.columns, "Наименование продукции")
        qty_col = find_col(df.columns, "Объём")

        if not all([date_col, name_col, qty_col]):
            st.error("❌ Не найдены обязательные колонки")
            st.write("Доступные колонки:", list(df.columns))
        else:
            conn = get_db_connection()
            cur = conn.cursor()
            not_found = []

            for _, row in df.iterrows():
                full_name = str(row[name_col]).strip()
                qty_kg = float(row[qty_col])
                
                # Обработка даты: "06.11.2025:00" → "06.11.2025"
                date_str = str(row[date_col]).strip()
                if ':' in date_str and '.' in date_str:
                    date_part = date_str.split(':')[0]
                    prod_date = pd.to_datetime(date_part, format='%d.%m.%Y').date()
                else:
                    prod_date = pd.to_datetime(row[date_col]).date()

                cur.execute("SELECT id FROM products WHERE mercurius_name = %s", (full_name,))
                prod = cur.fetchone()
                if not prod:
                    not_found.append(full_name)
                    continue

                cur.execute("""
                    INSERT INTO finished_goods (production_date, product_id, quantity_kg)
                    VALUES (%s, %s, %s)
                """, (prod_date, prod[0], qty_kg))

            conn.commit()
            cur.close()
            conn.close()

            st.success(f"✅ Успешно обработано записей.")
            if not_found:
                with st.expander(f"⚠️ {len(not_found)} продуктов не найдено в справочнике"):
                    for name in not_found:
                        st.write(f"- {name}")

    except Exception as e:
        st.error(f"Ошибка: {str(e)}")

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
    st.subheader(f"Выпуск за {selected_date.strftime('%d.%m.%Y')}")
    grouped = defaultdict(lambda: {"kg": 0, "pieces": 0, "write_offs": []})

    for fg_id, name, kg, pkg_kg in releases:
        grouped[name]["kg"] += kg
        grouped[name]["pieces"] += kg / pkg_kg

        # Списания
        cur.execute("""
            SELECT c.name, w.quantity
            FROM write_offs w
            JOIN components c ON w.component_id = c.id
            WHERE w.finished_good_id = %s
            ORDER BY c.name
        """, (fg_id,))
        for comp, qty in cur.fetchall():
            # Суммируем по компоненту
            found = False
            for i, (c, q) in enumerate(grouped[name]["write_offs"]):
                if c == comp:
                    grouped[name]["write_offs"][i] = (c, q + qty)
                    found = True
                    break
            if not found:
                grouped[name]["write_offs"].append((comp, qty))

        # Вода (для отображения)
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
            comp, qty = water
            found = False
            for i, (c, q) in enumerate(grouped[name]["write_offs"]):
                if c == comp:
                    grouped[name]["write_offs"][i] = (c, q + qty)
                    found = True
                    break
            if not found:
                grouped[name]["write_offs"].append((comp, qty))

    for name, data in grouped.items():
        st.markdown(f"### {name}")
        st.write(f"**Объём:** {data['kg']:.3f} кг | **Штук:** {data['pieces']:.0f}")
        for comp, qty in data["write_offs"]:
            st.write(f"- {comp}: {qty:.4f} кг")
        st.markdown("---")
else:
    st.info("Нет данных за выбранную дату.")

cur.close()
conn.close()