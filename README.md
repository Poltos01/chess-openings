# Винрейты дебютов (Lichess)

Статический сайт с реальной статистикой дебютов по рейтинговым бэндам, посчитанной
по официальным дампам Lichess. Без бэкенда, без API-токенов, без хранения PGN.

## Как это устроено

```
database.lichess.org (PGN.zst, CC0)
        │  stream + zstd decode, без записи на диск
        ▼
   ingest.py  ──►  db/openings.sqlite   (агрегаты, десятки МБ)
        │
        ▼
   export.py  ──►  site/data/**/*.json  (срезы по месяц×контроль×бэнд)
        │
        ▼
   site/index.html  (статический UI, GitHub Pages)
```

## Команды

```bash
pip install zstandard

# полный месяц (≈31 ГБ трафика, ~40 мин; на диске ничего не остаётся)
python3 ingest.py \
  --source https://database.lichess.org/standard/lichess_db_standard_rated_2026-10.pgn.zst \
  --month 2026-10 --db db/openings.sqlite

# срез для проверки пайплайна (100 МБ вместо 31 ГБ)
python3 ingest.py --source dump.zst --month 2026-10 --db db/test.sqlite --max-bytes 100000000

# JSON для сайта + копия index.html в site/
python3 export.py --db db/openings.sqlite --out site --min-n 20

# локальный просмотр
cd site && python3 -m http.server 8765
```

## Автоматика

`.github/workflows/ingest.yml` — 3-го числа каждого месяца: скачивает новый дамп,
дописывает месяц в `db/openings.sqlite`, пересобирает JSON, коммитит и деплоит Pages.
Ручной запуск: Actions → ingest → Run workflow (можно указать `YYYY-MM`).

## Метрики

- `score = (W + ½D) / N` — очки белых (у чёрных дополнение до 100%)
- дебютная пара = первый ход белых + первый ответ чёрных (`1.e4 c5`)
- ECO-разрез = `[ECO]` + `[Opening]` из заголовков PGN (готовый, не считается нами)
- партии с `|Δelo| > 80` отброшены, кроме игр, где `max(elo) ≥ 2400`
- бэнд = 100 очков: по среднему рейтингу партии либо строго оба игрока в бэнде
