import streamlit as st
import pandas as pd
import psycopg2
from datetime import date
import re

# Настройки подключения к БД
DB_CONFIG = {
    "host": "db",
    "database": "production_db",
    "user": "nero",
    "password": "secure_password_123"
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

# Аутентификация
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

# === ИМПОРТ EXCEL ===
st.subheader("📥 Импорт выпуска из Меркурия")
uploaded_file = st.file_uploader("Загрузите Excel-файл", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)

        # Поиск колонок по точному совпадению (без учёта регистра и пробелов)
        def find_col(cols, target):
            target_clean = target.strip().lower()
            for col in cols:
                if str(col).strip().lower() == target_clean:
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
            not_found = []

            for _, row in df.iterrows():
                full_name = str(row[name_col]).strip()
                qty_kg = float(row[qty_col])
                
                # Обработка даты: "06.11.2025:00" → "06.11.2025"
                date_str = str(row[date_col]).strip()
                if ':' in date_str:
                    date_part = date_str.split(':')[0].strip()
                else:
                    date_part = date_str.strip()
                # Парсинг строго по формату DD.MM.YYYY
                prod_date = pd.to_datetime(date_part, format='%d.%m.%Y').date()

                # Поиск продукта
                cur.execute("SELECT id FROM products WHERE mercurius_name = %s", (full_name,))
                prod = cur.fetchone()
                if not prod:
                    not_found.append(full_name)
                    continue

                # Вставка выпуска → триггер автоматически спишет сырьё
                cur.execute("""
                    INSERT INTO finished_goods (production_date, product_id, quantity_kg)
                    VALUES (%s, %s, %s)
                """, (prod_date, prod[0], qty_kg))

            conn.commit()
            cur.close()
            conn.close()

            st.success(f"✅ Успешно обработано записей: {len(df) - len(not_found)}")
            if not_found:
                with st.expander(f"⚠️ {len(not_found)} продуктов не найдено в справочнике"):
                    for name in not_found:
                        st.write(f"- {name}")

    except Exception as e:
        st.error(f"Ошибка при обработке файла: {str(e)}")

# === ОТЧЁТ ПО ДАТЕ ===
st.subheader("📅 Отчёт по дате выработки")
selected_date = st.date_input("Выберите дату", value=date.today())

conn = get_db_connection()
cur = conn.cursor()

# Агрегированный запрос: одна строка на продукт
cur.execute("""
    SELECT 
        p.mercurius_name,
        SUM(fg.quantity_kg) AS total_kg,
        p.package_weight_kg,
        p.id AS product_id
    FROM finished_goods fg
    JOIN products p ON fg.product_id = p.id
    WHERE fg.production_date = %s
    GROUP BY p.id, p.mercurius_name, p.package_weight_kg
    ORDER BY p.mercurius_name
""", (selected_date,))
releases = cur.fetchall()

if releases:
    st.subheader(f"Выпуск за {selected_date.strftime('%d.%m.%Y')}")
    for name, total_kg, pkg_kg, product_id in releases:
        pieces = total_kg / pkg_kg if pkg_kg > 0 else 0
        st.markdown(f"### {name}")
        st.write(f"**Объём:** {total_kg:.3f} кг | **Штук:** {int(pieces)}")

        # Суммарные списания по компонентам для всех записей этого продукта за дату
        cur.execute("""
            SELECT c.name, SUM(w.quantity) AS total_qty
            FROM finished_goods fg
            JOIN write_offs w ON w.finished_good_id = fg.id
            JOIN components c ON w.component_id = c.id
            WHERE fg.product_id = %s AND fg.production_date = %s
            GROUP BY c.id, c.name
            ORDER BY c.name
        """, (product_id, selected_date))
        write_offs = cur.fetchall()

        # Получаем воду из рецептуры (если есть)
        cur.execute("""
            SELECT ri.quantity_per_kg * %s
            FROM recipe_items ri
            JOIN components c ON ri.component_id = c.id
            WHERE ri.recipe_id = (SELECT recipe_id FROM products WHERE id = %s)
              AND c.name = 'Вода'
        """, (total_kg, product_id))
        water_row = cur.fetchone()
        water_qty = water_row[0] if water_row else 0

        # Объединяем списания и воду
        comp_dict = {name: qty for name, qty in write_offs}
        if water_qty > 0:
            comp_dict['Вода'] = comp_dict.get('Вода', 0) + water_qty

        # Вывод компонентов
        for comp_name in sorted(comp_dict.keys()):
            qty = comp_dict[comp_name]
            st.write(f"- {comp_name}: {qty:.4f} кг")
        st.markdown("---")
else:
    st.info("Нет данных за выбранную дату.")

cur.close()
conn.close()