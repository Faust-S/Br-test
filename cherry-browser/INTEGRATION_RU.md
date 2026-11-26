# Cherry Browser: подготовка заимствования B067.1

Источник: CherryHQ/cherry-studio v2.1.7, commit 6e08bf9b8aa2acd05a0d40cc5a4560b6153760ba.
Исходная лицензия AGPL-3.0. Никакого закрытого кода Agent в этом репозитории.
source-lock.json фиксирует 100 оригинальных файлов и Git blob IDs. Это первичный набор, не полное транзитивное замыкание зависимостей.

## Получение оригинала
git clone https://github.com/CherryHQ/cherry-studio.git upstream/cherry-studio
git -C upstream/cherry-studio checkout --detach 6e08bf9b8aa2acd05a0d40cc5a4560b6153760ba
python3 cherry-browser/verify_upstream.py

## Адаптация
Сохранить оригинальные actions, snapshot, session, mcp, automation и исходные тесты.
GuestSession зависит от Electron.WebContents, Debugger, Session, DownloadItem, logger и lifecycle.
WebviewHost зависит от Electron WebviewTag. Не считать исходники готовым плагином Tauri.
Следующий шаг: выделить минимальную границу транспорта CDP, событий, навигации, геометрии,
фокуса и жизненного цикла guest; проверить фактические возможности WebView2.
Сохранить оригинальные document-bound AX refs, snapshot diff, действия, отмену, курсор,
повторное наблюдение и диалоги. Не переносить всю платформу, агентов, persistence, cookie import,
произвольный evaluate или WebMCP без отдельного решения.

## Правовой рубеж
Портирование AGPL-кода остаётся AGPL; отдельный процесс сам по себе не гарантирует допустимость
совместного распространения с закрытым приложением. До согласования лицензионной модели
не включать AGPL-код в закрытый распространяемый продукт. Не удалять оригинальные notices.
Исследовательская подготовка не означает готовность сборки или релиза.
