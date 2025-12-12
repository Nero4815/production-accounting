import streamlit as st
import pandas as pd
import psycopg2
from datetime import date
from collections import defaultdict
from decimal import Decimal

# Настройки подключения к БД
DB_CONFIG = {
    "host": "db",
    "database": "production_db",
    "user": "nero",
    "password": "secure_password_123"
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def classify_recipe_group(name: str) -> str:
    n = name.lower().strip()
    if 'х/к' in n or 'холодного копчения' in n:
        return "Копчёнка"
    dixie_keywords = [
        'nord fjord', 'magellan', 'spar', 'мореслав', 'красная цена',
        'fish house', 'кд/', 'кп/', 'пр!ст'
    ]
    if any(kw in n for kw in dixie_keywords):
        return "Дикси"
    return "Регионы"

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
    with st.spinner("Обработка файла..."):
        try:
            df = pd.read_excel(uploaded_file)
            df = df.dropna(how='all')
            if df.empty:
                st.warning("Файл не содержит данных.")
                st.stop()

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
                st.stop()

            dates_to_clear = set()
            parsed_rows = []
            row_errors = []

            for idx, row in df.iterrows():
                try:
                    name_val = row[name_col]
                    qty_val = row[qty_col]
                    if pd.isna(name_val) and pd.isna(qty_val):
                        continue

                    full_name = str(name_val).strip() if pd.notna(name_val) else ""
                    if not full_name:
                        raise ValueError("Пустое наименование")

                    if pd.isna(qty_val):
                        raise ValueError("Отсутствует объём")
                    qty_kg = float(qty_val)
                    if qty_kg <= 0:
                        raise ValueError("Объём должен быть > 0")

                    date_val = row[date_col]
                    if pd.isna(date_val):
                        raise ValueError("Отсутствует дата")
                    date_str = str(date_val).strip()
                    if ':' in date_str:
                        date_part = date_str.split(':')[0].strip()
                    else:
                        date_part = date_str.strip()
                    prod_date = pd.to_datetime(date_part, format='%d.%m.%Y').date()

                    dates_to_clear.add(prod_date)
                    parsed_rows.append((prod_date, full_name, qty_kg))

                except Exception as e:
                    row_errors.append(f"Строка {idx + 2}: {str(e)}")

            if row_errors:
                st.warning(f"Пропущено строк с ошибками: {len(row_errors)}")
                with st.expander("Подробности ошибок"):
                    for msg in row_errors:
                        st.write(msg)

            if not parsed_rows:
                st.error("Нет корректных данных для импорта.")
                st.stop()

            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()

                for d in dates_to_clear:
                    cur.execute("""
                        DELETE FROM write_offs
                        WHERE finished_good_id IN (
                            SELECT id FROM finished_goods WHERE production_date = %s
                        )
                    """, (d,))
                    cur.execute("DELETE FROM finished_goods WHERE production_date = %s", (d,))

                not_found = []
                for prod_date, full_name, qty_kg in parsed_rows:
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
                total_ok = len(parsed_rows) - len(not_found)
                st.success(f"✅ Успешно обработано записей: {total_ok}")
                if not_found:
                    with st.expander(f"⚠️ {len(not_found)} продуктов не найдено в справочнике"):
                        for name in not_found:
                            st.write(f"- {name}")

            finally:
                if conn:
                    conn.close()

        except Exception as e:
            st.error(f"❌ Ошибка при обработке файла: {str(e)}")

# === ОТЧЁТ ПО ДАТЕ С КОРРЕКТНЫМИ НОРМАМИ ===
st.subheader("📅 Отчёт по дате выработки")
selected_date = st.date_input("Выберите дату", value=date.today())

try:
    conn = get_db_connection()
    cur = conn.cursor()

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

        grouped = defaultdict(list)
        group_totals = {"Регионы": 0.0, "Дикси": 0.0, "Копчёнка": 0.0}

        for name, total_kg, pkg_kg, product_id in releases:
            total_kg = float(total_kg) if isinstance(total_kg, Decimal) else float(total_kg)
            pkg_kg = float(pkg_kg) if isinstance(pkg_kg, Decimal) else float(pkg_kg)
            group = classify_recipe_group(name)
            grouped[group].append((name, total_kg, pkg_kg))
            group_totals[group] += total_kg

        # === Коэффициенты на 1 кг готового продукта ===
        # Рассчитаны из ваших эталонных данных
        norms = {
            "Регионы": {
                "Охлаждённая форель": 2010 / 1884,
                "Вода": 1913.47 / 1884,
                "Соль": 260.79 / 1884,
                "Фиш PN": 7.73 / 1884,
                "Консерв \"Специальный\"": 0.6 / 1884,
                "Краситель": 0.23 / 1884,
                "Бактостоп": 3.01 / 1884,
            },
            "Дикси": {
                "Охлаждённая форель": 3434 / 3150,
                "Вода": 3046.01 / 3150,
                "Соль": 548.17 / 3150,
                "Консерв \"Специальный\"": 2.4678 / 3150,
            },
            "Копчёнка": {
                "Охлаждённая форель": 15 / 14,
                "Вода": 14.25 / 14,
                "Соль": 2.45 / 14,
                "Бактостоп": 0.05904 / 14,
            }
        }

        for group_name in ["Регионы", "Дикси", "Копчёнка"]:
            total_kg_group = group_totals[group_name]
            if group_name in grouped and total_kg_group > 0:
                st.markdown(f"#### 📌 {group_name}")

                # Таблица выпуска
                table_data = []
                for name, total_kg, pkg_kg in grouped[group_name]:
                    pieces = int(total_kg / pkg_kg) if pkg_kg > 0 else 0
                    table_data.append({
                        "Наименование продукции": name,
                        "Объём (кг)": f"{total_kg:.3f}",
                        "Штук": pieces
                    })
                st.table(table_data)

                # Суммарные компоненты по нормам
                st.markdown("**Суммарные компоненты по рецептуре:**")

                components = []
                for comp_name, coef in norms.get(group_name, {}).items():
                    qty = total_kg_group * coef
                    if qty > 0.0001:
                        components.append({
                            "Компонент": comp_name,
                            "Количество (кг)": f"{qty:.4f}"
                        })

                if components:
                    st.table(components)
                else:
                    st.write("Нет компонентов для отображения.")

                st.markdown("---")
    else:
        st.info("Нет данных за выбранную дату.")

except Exception as e:
    st.error(f"Ошибка при загрузке отчёта: {str(e)}")
finally:
    if 'conn' in locals() and conn:
        conn.close()