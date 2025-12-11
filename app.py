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

# === ОПРЕДЕЛЕНИЕ РЕЦЕПТУРНОЙ ГРУППЫ ===
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
            # st.exception(e)  # для отладки

# === ОТЧЁТ ПО ДАТЕ ===
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
        for row in releases:
            name, total_kg, pkg_kg, product_id = row
            # 🔥 Преобразуем Decimal → float
            total_kg = float(total_kg) if isinstance(total_kg, Decimal) else total_kg
            pkg_kg = float(pkg_kg) if isinstance(pkg_kg, Decimal) else pkg_kg
            group = classify_recipe_group(name)
            grouped[group].append((name, total_kg, pkg_kg, product_id))

        for group_name in ["Регионы", "Дикси", "Копчёнка"]:
            if group_name in grouped:
                st.markdown(f"#### 📌 {group_name}")
                for name, total_kg, pkg_kg, product_id in grouped[group_name]:
                    pieces = total_kg / pkg_kg if pkg_kg > 0 else 0
                    st.markdown(f"**{name}**")
                    st.write(f"Объём: {total_kg:.3f} кг | Штук: {int(pieces)}")

                    # Расчёт компонентов
                    if group_name == "Регионы":
                        comps = [
                            ("Вода", total_kg * (0.7375 + 0.89746)),
                            ("Соль", total_kg * (0.24 + 0.10)),
                            ("Фиш PN", total_kg * (0.01 + 0.0025)),
                            ("Консерв \"Специальный\"", total_kg * 0.002),
                            ("Краситель", total_kg * (0.0005 + 0.00004)),
                            ("Бактостоп", total_kg * 0.01),
                        ]
                    elif group_name == "Дикси":
                        comps = [
                            ("Вода", total_kg * (0.758 + 0.8995)),
                            ("Соль", total_kg * (0.24 + 0.14)),
                            ("Консерв \"Специальный\"", total_kg * (0.002 + 0.0005)),
                        ]
                    elif group_name == "Копчёнка":
                        comps = [
                            ("Вода", total_kg * (0.80 + 0.8575)),
                            ("Соль", total_kg * (0.19 + 0.14)),
                            ("Бактостоп", total_kg * (0.01 + 0.0025)),
                        ]
                    else:
                        comps = []

                    for comp_name, qty in comps:
                        if qty > 0.0001:
                            st.write(f"- {comp_name}: {qty:.4f} кг")
                    st.markdown("---")
    else:
        st.info("Нет данных за выбранную дату.")

except Exception as e:
    st.error(f"Ошибка при загрузке отчёта: {str(e)}")
finally:
    if 'conn' in locals() and conn:
        conn.close()