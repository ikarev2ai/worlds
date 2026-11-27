# Статический сайт UWI

Генератор преобразует текущие `README.md`, `ontology/` и `worlds/` в автономный статический сайт. Markdown-файлы остаются единственным источником содержимого; `dist/html/` не коммитится.

## Локальная сборка

Требуется Python 3.9 или новее. Из корня репозитория:

```bash
python3 -m venv .venv-html
.venv-html/bin/python -m pip install -r web/requirements.txt
.venv-html/bin/python web/build.py
```

Результат появится в `dist/html/`. Сборка автоматически включает все миры и произведения, проверяет полноту, ссылки и якоря. Для другого корня исходников или каталога результата доступны `--root` и `--output`; `--repository-url` задаёт базовый URL ссылок на непубликуемые файлы проекта.

## Локальный показ

```bash
python3 -m http.server 8765 --directory dist/html
```

Откройте <http://localhost:8765/>. Внешняя сеть не нужна: стили, скрипты и поисковый индекс находятся в сборке. Основные страницы и переходы работают также без JavaScript.

## GitHub Pages

Workflow `.github/workflows/html-pages.yml` запускается при push в `main` и вручную через **Actions → Build and deploy HTML → Run workflow**. Он повторяет локальную сборку и публикует `dist/html/`.

Перед первым запуском в настройках репозитория выберите **Settings → Pages → Build and deployment → Source → GitHub Actions**. После успешного deploy project site будет доступен по адресу `https://<owner>.github.io/<repository>/`; относительные ссылки уже рассчитаны на такой подкаталог.
