# Обновяване от версия 0.2 към 0.3

Промените не са само в `app.py`. За да работят календарът, ръчната история, годишната цел и седмичната структура, трябва да бъдат качени всички файлове от версия 0.3 със запазени папки.

## Най-сигурен вариант

1. Разархивирай ZIP файла.
2. В GitHub repository отвори **Add file → Upload files**.
3. Плъзни цялото съдържание на разархивираната папка.
4. Потвърди замяната на съществуващите файлове и натисни **Commit changes**.
5. Провери, че има папка `biathlon/` и вътре се вижда новият файл `preferences.py`.
6. В Streamlit Cloud избери **Manage app → Reboot app**.

## Задължителни променени файлове

```text
app.py
biathlon/preferences.py       нов файл
biathlon/planning.py
biathlon/service.py
biathlon/demo_data.py
biathlon/charts.py
biathlon/explanations.py
README.md
CHANGELOG.md
docs/MODEL_LOGIC.md
docs/DATA_CONTRACT.md
tests/test_app_smoke.py
tests/test_pipeline.py
tests/test_preferences.py     нов файл
```

Не качвай Python файловете от `biathlon/` директно до `app.py`. Те трябва да останат вътре в папката `biathlon`.
