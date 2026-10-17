"""ds — сканер дизайн-системы: stories/исходники библиотеки → факты (задача 2.2, спека §5.3).

Основной путь — чистый Python без node: лексер JS/TS/JSX (`lex`), статический вычислитель выражений
(`value`), разбор stories CSF1/2/3 + storiesOf (`csf`), деклараций и исходников — interface/type/enum/
компоненты (`decl`), MDX (`mdx`). Все они отдают одну схему фактов (`facts`); сборку документов
требований из фактов делает `daiver/ds_requirements.py`.
"""
from __future__ import annotations
