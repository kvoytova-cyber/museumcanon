# museumcanon

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/kvoytova-cyber/museumcanon/blob/main/demo.ipynb)

Инструмент для анализа того, как в коллекции и выставках музея представлена выбранная группа художников. Группа задаётся по стране рождения и национальности. Демо-кейс — русские, советские и восточноевропейские художники в MoMA и Tate.

**На входе:**
- файлы музея (csv, tsv, xlsx, json, jsonl);
- YAML-маппинг: в каком столбце искать каждое поле;
- YAML с группами художников.

**На выходе:**
- сводная таблица (художники, эмигранты, работы, выставки);
- графики поступлений по периодам, в штуках и в доле от всех поступлений музея, с выделением крупных даров;
- графики даров и покупок, таблица крупнейших дарителей;
- выставки по годам, самые «групповые» выставки;
- сеть совместных выставок (PNG и интерактивный HTML) и таблица «посредников».

## Установка и запуск

**Google Colab:** нажмите бейдж выше (или откройте `demo.ipynb` по ссылке `colab.research.google.com/github/kvoytova-cyber/museumcanon/blob/main/demo.ipynb`) и выполните Runtime → Run all — первая ячейка сама клонирует репозиторий и ставит зависимости.

**Локально:**

```bash
pip install -r requirements.txt
python -m museumcanon --download --museums configs/moma.yaml configs/tate.yaml
```

Коллекция MoMA хранится в Git LFS и может не скачаться автоматически. Тогда скачайте `Artists.csv` и `Artworks.csv` из github.com/MuseumofModernArt/collection и положите в `data/raw/` как `moma_artists.csv` и `moma_artworks.csv`.

Дальше есть два варианта:

- **Ноутбук** `demo.ipynb` — пошаговая демонстрация всех трёх блоков (запускать из корня проекта).
- **Консоль** — все таблицы и графики сразу в папку `output/`:

```bash
python -m museumcanon --museums configs/moma.yaml configs/tate.yaml \
    --groups configs/groups_russia.yaml --highlight Rothschild --out output
```

Параметры: `--start`, `--end` (период), `--bin` (шаг в годах), `--highlight` (регулярка по credit line).

## Как подключить свой датасет

Код менять не нужно, достаточно написать конфиг. Слева каноническое поле, справа название колонки в вашем файле. Если значение нужно извлечь из строки, указывается парсер:

```yaml
name: Мой музей
tables:
  artists:
    path: data/raw/my_artists.xlsx
    read_options: {sheet_name: Авторы}        # передаётся в pandas
    fields:
      artist_id: Код
      name: ФИО
      birth_year: {column: Годы жизни, parser: year}
      birth_country: {column: Место рождения, parser: last_part}
  artworks:
    path: data/raw/my_works.csv
    fields:
      artwork_id: Инв. №
      artist_ids: {column: Код автора, parser: split_list}
      acquisition_year: {column: Дата поступления, parser: year}
      credit_line: Источник поступления
```

Готовый пример лежит в `examples/excel_example.yaml`. Если колонка указана с ошибкой, загрузчик назовёт похожие колонки из файла.

### Канонические поля

| Класс | Обязательные | Необязательные |
|---|---|---|
| `Artist` | `artist_id`, `name` | `nationality`, `birth_year`, `death_year`, `birth_place`, `birth_country`, `gender`, `wikidata_id` |
| `Artwork` | `artwork_id`, `artist_ids` | `title`, `creation_year`, `acquisition_year`, `medium`, `classification`, `department`, `credit_line` |
| `Exhibition` | `exhibition_id`, `artist_id` | `title`, `start_year`; `artist_fields` — поля художника, если отдельного файла с художниками нет |

Если необязательного поля нет, соответствующий график пропускается, а не падает.

### Парсеры

- `year` — первый правдоподобный год: `c. 1915–20` → 1915.
- `split_list` — список через запятую или точку с запятой.
- `born_country` — страна из биографии MoMA в обоих форматах: «American, born Russia» и «Born 1866, Russian Empire (now Russia)» (берётся современная страна).
- `last_part` и `first_part` — последняя или первая часть адреса через запятую.
- `clean_nationality` — национальность без скобок и «unknown».

Можно добавить свой парсер:

```python
from museumcanon.parsers import parser

@parser("my_parser")
def my_parser(value): ...
```

Можно также указать запасную колонку: `{column: A, parser: year, fallback: B}`.

### Очистка (секция `cleaning` конфига)

- `drop_names_matching` — регулярка: удалить художников с такими именами и их работы (например, «Unidentified»).
- `drop_artists` — удалить по точному имени (в Tate так убран Тёрнер: его наследие перекрывает все остальные поступления).
- `exclude_from_groups` — оставить в коллекции, но не включать ни в одну группу (ложные срабатывания по месту рождения).
- `overrides` — ручные поправки: `исходный_id: {поле: значение}`.

### Группы

```yaml
groups:
  - name: Россия
    countries: [Russia, USSR]          # страна рождения после нормализации
    nationalities: [Russian, Soviet]   # национальность в записи музея
    born_from: 1900                    # необязательно
```

Художник попадает в первую подходящую группу. **Эмигрант** — тот, кто родился в стране группы, но записан музеем с другой национальностью. Разные написания стран (`Rossiya`, `Polska`, `SSSR`) сводятся к одному через `COUNTRY_SYNONYMS` в `preprocessing.py`.

## Устройство

```
museumcanon/
  models.py         Artist, Artwork, Exhibition, Collection
  parsers.py        парсеры значений и реестр на декораторе @parser
  readers.py        блок 1: TableReader → CsvReader / ExcelReader / JsonReader; MuseumLoader
  preprocessing.py  блок 2: Cleaner (шаги с декоратором @step), Selector, Grouping
  visualizer.py     блок 3: Visualizer — таблицы и графики, знает только Collection и Grouping
  download.py       скачивание по url из конфигов
  __main__.py       консольный запуск
configs/            MoMA, Tate, группы (Россия; Япония — пример другой группы)
examples/           Excel с «чужими» названиями колонок + его маппинг
tests/              pytest
```

### Требования курса

| Требование | Где |
|---|---|
| 3+ классов, композиция и агрегация | `Collection` агрегирует `Artist`, `Artwork`, `Exhibition`; `Grouping` — `Selector`; `Visualizer` держит `Collection` и `Grouping` |
| Наследование | `CsvReader`, `ExcelReader`, `JsonReader` ← абстрактный `TableReader` |
| Данные из файлов | `data/raw/*.csv`, `examples/*.xlsx` |
| Визуализации с параметрами | поступления (штуки и доля, выделение дарителя), дары и покупки, выставки по годам, сеть совместных выставок (PNG и HTML). Период, шаг, группы и музеи задаются параметрами |
| Переопределённые методы | `Artist.__eq__` и `__hash__`, `Artwork.__lt__`, `Collection.__add__`, `__len__`, `__iter__` и `__contains__`, `Exhibition.__len__`, `__contains__` и `__iter__`, `Selector.__contains__` и `__or__`, `Grouping.__len__` и `__iter__` |
| Декораторы | `@parser` (реестр парсеров), `@step` (логирование шагов очистки), `@dataclass`, `@cached_property` |

## Ограничения

- Группа определяется по записи музея. Национальность и место рождения бывают неполными или спорными. В Tate национальности нет, поэтому эмигрантов там не определить.
- Художники из разных музеев сопоставляются по имени и году рождения: «Natalia» и «Natalya» не совпадут.
- Данные Tate — срез на 2014 год (с тех пор не обновлялись), выставки MoMA — только 1929–1989.
- Годы извлекаются по первому найденному году в строке.
- Дар и покупка определяются по ключевым словам в credit line (`Artwork.acquisition_type`), это приближение.

Данные: MoMA (CC0), Tate (CC0).
