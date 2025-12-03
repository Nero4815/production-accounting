if uploaded_file:
    try:
        # Читаем Excel (без пропуска строк — если заголовки на первой строке)
        df = pd.read_excel(uploaded_file)

        # Поиск колонок по точному имени
        def find_col_by_name(df, target_names):
            for col in df.columns:
                col_clean = str(col).strip().lower()
                for target in target_names:
                    if col_clean == target.lower():
                        return col
            return None

        date_col = find_col_by_name(df, ['Дата выработки'])
        name_col = find_col_by_name(df, ['Наименование продукции'])
        qty_col = find_col_by_name(df, ['Объём'])

        if not all([date_col, name_col, qty_col]):
            st.error("❌ Не найдены обязательные колонки: 'Дата выработки', 'Наименование продукции', 'Объём'.")
            st.write("Доступные колонки:", list(df.columns))
            return

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
        st.error(f"Ошибка при обработке файла: {str(e)}")