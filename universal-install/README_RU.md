# IWXA — универсальные установочные backend (открытый компонент)

**PCB 0.1.0-dev.1, source-only.** Разработка в публичном `Faust-S/Br-test`. Первым устанавливаемым пакетом служит **доработанный IWXA Obscura Worker**. Разработка графического IWXA Browser прекращена: этот компонент **не устанавливает и не тестирует IWXA Browser**.

Модуль предназначен для встраивания в разные продукты, включая закрытый IWXA Personal Agent 0.3. **Исходники Agent сюда не копируются.** Устанавливаемые пакеты и потребители модуля — разные сущности. Это общий backend, а не отдельное приложение, менеджер программ, реестр, Core, Store, система разрешений или Setup.

## Публичный контракт

`iwxa_universal_install.ProgramInstallBackend`: `inspect`, `prepare`, `apply`, `verify`, `recover`. Реализован первый вариант `ObscuraManagedBackend` (`IWXA_MANAGED_ARTIFACT_V1`). Он использует существующий `iwxa_obscura.preparer` для строго закреплённых архивов Obscura. WinGet и pinned Goose пока остаются действующими backend внутри приватного Agent; их перенос **не объявлен выполненным**.

Ответственность приложения-хозяина: выбор программы и каталога, план и его digest, подлинное пользовательское согласие, запись `STARTED` до `apply`, сериализация операций, итоговая квитанция, подключение к провайдеру и выполнение. `ApprovedInstall` — переданный хозяином параметр, **не самостоятельное доказательство согласия**. Модуль не хранит квитанций и не делает сетевых обращений без явного вызова `prepare(..., archive_path=None)`.

Маршрут: `ArtifactSpec` с закреплёнными digest → `prepare(archive)` = `STAGED_NOT_ACTIVATED` → хост подтверждает действие и выдаёт `ApprovedInstall` → `apply` публикует ровно один проверенный бинарник в новый versioned directory → `verify` читает SHA-256 с диска и возвращает `INSTALLED_NOT_CONNECTED` → подключение и запуск решает продукт. `recover` — только чтение, **никогда** повторная установка.

## Тесты

В корне `Br-test`:

```sh
python -m unittest discover -s universal-install/tests -v
python -m unittest discover -s agent-headless/tests -v
```

Публичный Actions workflow проверяет это на Windows и Linux в выделенных ephemeral runner. Никакие установщики не запускаются в реальных пользовательских профилях. Тестовые пакеты создаются локально из искусственных байтов, не считаются настоящим Obscura Worker.

**Граница доказательств:** перед реальным выпуском требуется native-сборка patched Obscura, закреплённые archive/binary SHA-256, проверка origin-gate, настоящий readback и сквозное использование через Agent 0.3. Ни `STAGED`, ни `INSTALLED_NOT_CONNECTED` не означают `READY/CONNECTED/EXECUTED`. Отчёт открытого модуля не подменяет закрытую приёмку Agent.
