# Melitta Barista HA Integration — Project Rules

## Git Versioning (ОБЯЗАТЕЛЬНО!)

### Формат версии
- Используем Semantic Versioning: `MAJOR.MINOR.PATCH` (например: `0.4.6`)
- Версия хранится в `custom_components/melitta_barista/manifest.json` (поле `"version"`)

### Git Tags
- **КАЖДЫЙ мерж в main ДОЛЖЕН иметь git tag** формата `vX.Y.Z`
- Tag ставится на мерж-коммит в main (не на коммит в feature-ветке)
- После создания тега — сразу `git push origin --tags`
- `git describe --tags` должен показывать текущую версию

### Процесс при коммите
1. Определи уровень изменений (patch/minor/major)
2. Обнови `"version"` в `manifest.json`
3. Включи изменение версии в коммит
4. После мержа PR в main — создай tag: `git tag vX.Y.Z <merge-commit-hash>`
5. Запушь tag: `git push origin --tags`

### Уровни версий
- **PATCH** (0.4.6 → 0.4.7): баг-фиксы, рефакторинг, мелкие улучшения
- **MINOR** (0.4.6 → 0.5.0): новая функциональность (backwards-compatible)
- **MAJOR** (0.4.6 → 1.0.0): breaking changes, крупные архитектурные изменения

## Git Workflow

### Branching
- Основная ветка: `main`
- Feature-ветки: `feat/<name>`, `fix/<name>`, `chore/<name>`, `refactor/<name>`
- Все изменения через PR: feature branch → PR → merge to main

### Commit Messages
Формат: `тип: краткое описание`

Типы: `feat`, `fix`, `refactor`, `docs`, `chore`, `perf`, `test`

## Код

### Логирование
- Единый логгер во всех модулях: `logging.getLogger("melitta_barista")`
- НЕ использовать `__name__` для логгера

### Python
- Минимальная версия: Python 3.11 (для совместимости с HA)
- НЕ использовать PEP 695 `type` statement (требует Python 3.12+)
- BLE: библиотека `bleak`

### Комментарии
- НЕ включать ссылки на APK, Java-классы или декомпиляцию в код/комментарии
