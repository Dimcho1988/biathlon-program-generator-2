# Обновяване до версия 0.4

Подмени `app.py` и следните файлове **вътре в папката `biathlon/`**:

- `biathlon/charts.py`
- `biathlon/explanations.py`
- `biathlon/planning.py`
- `biathlon/preferences.py`
- `biathlon/service.py`

След commit отвори Streamlit Cloud и избери `Manage app → Reboot app`.

## Как да провериш поправката

1. Отвори `Календар и цели`.
2. Задай 500 часа и запази.
3. Запиши планирания обем за следващите 7 дни.
4. Задай 600 или 700 часа и запази.
5. Историческата линия трябва да остане същата, а плановата линия да се повиши.
6. Ако целта изисква прекомерно увеличение, ще се покаже предупреждение, че е достигнат защитният лимит.

Структурата в GitHub трябва да бъде:

```text
app.py
biathlon/
    __init__.py
    charts.py
    constants.py
    demo_data.py
    explanations.py
    monitoring.py
    physiology.py
    planning.py
    preferences.py
    service.py
    testing.py
    ui_helpers.py
```
