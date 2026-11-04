# IWXA Universal Program Integration — публичная инженерная передача 04.11.2026

## Разделение ответственности и источников

Разработка ведётся в публичной ветке `work/2026-11-04-universal-program-integration`, созданной из `work/2026-11-03-universal-install-module@fb4549c1efd83dbbf867cf996add42542e65be6f`. Установочный backend не переписан и исходная ветка не менялась.

Проверены read-only существующие владельцы:
- Personal Agent: `runtime/existing_software_discovery.py`, `runtime/existing_runtime_adoption.py`, `providers/agent_runtime_port.py`. Host уже владеет регистрацией/квитанциями, выбором и полномочиями. Новый коннектор не заменяет их.
- Atlas: `src/github-public.mjs` и `src/source-observation.mjs`; публичные наблюдения и изменение ревизий остаются владельцем Atlas.
- Center: `runtime/t3-admin-port.mjs` и `src/atlas-integration.mjs`; нет второго Atlas/Store.
- T3: межпродуктовые authority/reconciliation и `.iwxa` остаются в T3.
- IWXA-Admin-Center: проверены `AGENTS.md` и действующие продуктовые границы; приватный продукт не изменялся.

## Реализованные публичные компоненты

- M1: старый `ProgramInstallBackend` и `ManagedArtifactBackend` переиспользуются без изменения, включая pinned HTTPS/SHA-256, ZIP/tar.gz/binary/native formats.
- M2: `iwxa_universal_install/program_connection.py` и `host-bindings/program-connection.mjs` обнаруживают конкретный CLI-файл без запуска или установки. Неизвестная архитектура/владелец/процесс явно `UNKNOWN`, а не выдуманная готовность.
- M3: подключение, действительный version probe, `capabilities`, `invoke` с данными операции, `disconnect` и `recover`; только перечисленные манифестом CLI-команды, без shell. Дополнительный HTTP connector `host-bindings/http-program.mjs` использует штатный `fetch`, host-owned `authHeaders`, настоящий HTTP result и не требует Python.
- M4: `DECLARED` отдельно от `verified` — после реального успешного вызова функция отмечается проверенной. `INSTALLED`, `CONNECTED`, `EXECUTED` не смешиваются.
- M5: источник авторизации передаётся callback приложения-хозяина. Ни одного независимого реестра учётных данных или IAM нет. OAuth/service tokens не интегрированы ни с одним настоящим продуктовым хранилищем.
- M6: `host-bindings/github-source.mjs` оставляет Public GitHub источником существующий `observePublicRepo` Atlas (инъекция `publicObserver`). Authorized/private mode принимает host-owned credential provider и сохраняет `ACCESS_UNKNOWN` при 404, `ACCESS_DENIED` при 403, неполноту при частичных сбоях. `local-git-source.mjs` использует только read-only Git CLI, без network/fetch. Реальная приватная авторизация **ещё не квалифицирована**; изолированный сервер проверяет только контракт.
- M7: `ui-levels.mjs` различает I0–I5 и не выдаёт I3/I4/I5 без отдельного наблюдения. **Настоящий нативный UI/window bridge пока не квалифицирован.**
- M8: `host-port.mjs` — browser-neutral JS request/response port для существующего host-native исполнителя, `product-hosts.mjs` — тонкий общий factory для Agent/Admin/Atlas. `contracts/` содержит версионированные JSON Schema request/observation. Python host требует только стандартный Python, Node host — Node 22 и запускает CLI по необходимости. Tauri/Rust реального продукта ещё не подключён; не утверждается, что WebView может непосредственно импортировать `node:child_process`.
- M9: ID контракта `IWXA_PROGRAM_CONNECTION/1.0.0`, данные операции и результатов по JSON без привязки к версии Agent; динамической загрузки внутренних модулей нет.
- M10: публичные reference host ports Agent/Admin/Atlas проверяются действительным CLI jq/yq в CI; **это не приёмка закрытого Agent, Admin Center или выпущенного Atlas**.

## Реальные испытания

`qualification/two_host_programs.py` скачивает официальные SHA-pinned jq 1.8.1 (5 ОС/архитектур) и yq 4.54.1 (6 ОС/архитектур). Через прежний backend выполняет подготовку, установку, реальный результат CLI, подключение и повторное подключение, затем host-approved удаление и readback. Независимый Node host получает те же параметры и запускает тот же бинарник без Python Core Agent. Неподдерживаемый jq Windows ARM64 помечается `UPSTREAM_UNSUPPORTED`. Прямые результаты сравниваются с ответами host. `product_host_ports.mjs` вызывает реальные команды через три тонких host port (Agent/Admin/Atlas), без изменения соответствующих продуктов.

`qualification/github_source_host.mjs` вызывает настоящую публичную реализацию Atlas на публичном репозитории; синтетически проверяет авторизованные состояния. Для избежания лимита анонимного GitHub API публичный сетевой тест вызывается только одним runner; другие runner всё равно проверяют CLI/API. Реальный приватный источник в публичном CI не подменяется фикстурой.

`qualification/http_program_host.mjs` запускает краткоживущий локальный HTTP fixture, делает реальный POST, сверяет ответ и проверяет отсутствие доступа без учётных данных. `qualification/local_git_host.mjs` читает настоящий checkout Git, сравнивает HEAD и не меняет файлы. Нет постоянных процессов/демонов или фонового опроса.

Проверенные по отдельности Actions:
- [41819146582](https://github.com/Faust-S/Br-test/actions/runs/41819146582) — две реальные программы, два независимых host, 6/6 PASS.
- [41819514636](https://github.com/Faust-S/Br-test/actions/runs/41819514636) — публичный Atlas-источник и synthetic auth, 6/6 PASS.
- [41820170959](https://github.com/Faust-S/Br-test/actions/runs/41820170959) — динамические CLI-параметры и local git, 6/6 PASS.
- [41820476620](https://github.com/Faust-S/Br-test/actions/runs/41820476620) — настоящий HTTP API fixture, 6/6 PASS.

## Размер и производительность

Исходный установочный Python backend до интеграции: ~52,8 КиБ, без сторонних обязательных Python-пакетов. Добавленные коннекторы размещены отдельно и не импортируются основным продуктом, пока host не вызвал их.

На первом Linux x64 тесте: прямой jq 1,387 мс, Python invoke jq 1,308 мс; прямой yq 2,944 мс, Python invoke yq 2,712 мс. Полный одноразовый Node процесс + серия вызовов ~41–47 мс. Это одиночные CI-измерения; **не доказано** отсутствие деградации на уровне 5%, холодный запуск Agent/Atlas/Center, память и CPU простоя ещё не измерены на установленных host. Для уже работающего Tauri WebView не требуется запускать постоянный Node/Python процесс; host может реализовать стандартный envelope через штатный Rust command.

## Незавершённые обязательные критерии

1. Реальный OAuth/private GitHub с согласованным host-owned secret store; допускается только изолированная проверка с подходящими правами, без пользовательских секретов в публичных логах.
2. Прямой Rust/Tauri host и нативная Windows/macOS GUI проверка I3; I4/I5 только по официальному интерфейсу владельца программы.
3. Проверка реального Agent 0.3, Center, Atlas в их отдельных интеграционных ветках с сохранением продуктовых владельцев.
4. Многократный benchmark на одной машине по cold start, warm invoke, памяти, CPU и размеру необязательных зависимостей. При изменении типового маршрута >5% — исследование.
5. Остальные transport adapters (MCP, SDK, IPC, file) — только при наличии действительного интерфейса и реального вызывающего host.

Это публичная квалификация технических частей, **не объявление всей системы Universal Program Integration завершённой**. Выпущенные версии и приватные данные не затрагивались.
