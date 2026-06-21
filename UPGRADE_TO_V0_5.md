# Обновяване до версия 0.5

Версия 0.5 добавя четири вида силова работа и изисква едновременно обновяване на `app.py` и свързаните модули в папката `biathlon/`.

## Файлове за замяна

```text
app.py
biathlon/charts.py
biathlon/constants.py
biathlon/demo_data.py
biathlon/explanations.py
biathlon/physiology.py
biathlon/planning.py
biathlon/preferences.py
biathlon/service.py
```

Най-сигурният вариант е да качиш съдържанието на пълния ZIP върху repository-то, като запазиш папката `biathlon/`.

## Нови силови полета

```text
STR_STAB   стабилизация / кор, k = 0.8
STR_END    обща силова издръжливост, k = 1.0
STR_MAX    максимална сила, k = 1.2
STR_PLY    плиометрия, k = 1.4
```

Във входните таблици стойностите са **реални минути**. Системата изчислява еквивалентните минути автоматично и ги сумира в общия компонент `STR`. Стар CSV с една колона `STR` остава валиден и се интерпретира като обща силова издръжливост.

## След качването

1. Направи `Commit changes`.
2. В Streamlit Cloud отвори `Manage app`.
3. Избери `Reboot app`; при нужда първо `Clear cache`.
