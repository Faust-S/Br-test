# IWXA — открытые универсальные компоненты

**Редакция 04.11.2026; proposal, не release.** Этот документ размещён рядом с реальным `iwxa_universal_install`. Он не превращает `Br-test` в обязательный монорепозиторий всех универсальных модулей. Новые исходники размещаются у назначенного публичного owner; здесь сохраняется только описание сопряжённой инфраструктурной семьи и путь её квалификации. Обсуждаемая карта: центральный `Faust-S/iwxa/knowledge/system/modularity/MODULE_MAP_RU.md`.

## U01 — IWXA Universal Program Integration

**Назначение:** общий backend для обнаружения программы, установки только при необходимости, проверки, подключения поддерживаемых API/CLI/SDK, получения доступных функций, вызова реальной операции, readback и завершения работы. Использовать в Agent 0.3, Atlas, Admin Center и других продуктах через тонкие host adapters.

**Код уже есть:** `iwxa_universal_install/{contract,managed,manifest,native,preparer,obscura}.py` — install/remove/inspect/verify. `agent-headless/iwxa_obscura` — отдельная проверенная связка с Obscura Worker. В закрытом Agent есть собственные tool/provider ports и установочные интеграции; они остаются owner code. Нельзя вводить новый дублирующий `Universal Connection Runtime`.

**Кандидат публичного контракта:** `ProgramDescriptor {programId, version?, platform, interfaces[]}`, `CapabilityDescriptor {id, inputSchema, outputSchema, transport}`, `ConnectionObservation {programId, state, interfaceVersion, capabilities[]}`, `ExecutionObservation {operationId, status, outputRef?, errorCode?}`. Методы независимого backend `discover`, `inspect`, `prepare`, `apply`, `verify`, `connect`, `describeCapabilities`, `invoke`, `disconnect`. Старый `ProgramInstallBackend` **не переименовывать и не ломать**: новые функции — отдельные composable adapters под одной публичной продуктовой семьёй. Подключение требует реально поддерживаемого интерфейса, не обещания управления произвольным EXE.

**Граница:** host владеет выбором установки, пользовательским решением, credentials, записью квитанций, конкурентным доступом, приватными данными, разрешением вызова и Result. Общий backend не создаёт второй Store/Registry, модель или ToolRuntime.

**Этапы и PASS:** (1) сохранить install native regression/верификацию; (2) одна реальная CLI программа с execute/readback (например jq) на минимум двух ОС; (3) одна независимая программа с настоящим API/handshake (Obscura), negative unsupported; (4) два разных host adapter consumers, демонстрирующих inspect → use → result; (5) закрытый Agent 0.3 end-to-end отдельно. Install PASS **не** означает connected/invoked PASS.

## U02 — IWXA Source & Program Connector Adapters

**Назначение:** независимые адаптеры к Git/GitHub, CLI, API, локальным файлам и другому программному интерфейсу, которые поставляют объект с source/ref/version/provenance и capabilities в U01 и в Knowledge/Atlas.

**Существующее:** `Faust-S/Atlas/src/github-public.mjs` и наблюдение публичных ревизий; приватные провайдеры Agent; Obscura worker protocol. Разрабатывать публичные source adapters у соответствующего owner (для Atlas GitHub — Atlas), а общую форму описания — в нейтральном модуле. Не переносить чужой credential manager.

**Кандидат контракта:** `SourceDescriptor {providerId, sourceRef, revision, observationType}`, `SourceObservation {contentRef?, observedAt?, revision?, provenance, status}`; `SourceConnectorPort.describe/read/refresh` c host-provided authorized credential handles. Различать observation и write/apply authority.

**Первый PASS:** один публичный GitHub source и один локальный файл, pinned revision, изменения источника видны; приватный GitHub — отдельный шаг с user-provided credential только в изолированной интеграционной среде. Публичному runner секреты не нужны.

## U03 — IWXA Cross-Product Capability & Host SDK

**Назначение:** переносимая машинная декларация возможностей и версий модулей между Python Agent, TypeScript/Node Center, Rust/JS Atlas и доменным T2. Не общий Core и не единый runtime процесса.

**Существующее:** Agent modular composition, Model/Agent/ToolProviderPort; T2 workspace-module-manifest; контракт `ProgramInstallBackend`; Atlas public model. Это **разные** owning runtimes; не подменять их схемы новым унифицированным JSON без сравнения.

**Кандидат контракта:** `ModuleDescriptor {id, version, provides[], requires[], hostCompatibility[]}`, `CapabilityStatus {id, available, reason, observationRef}`, `ModuleAdapterPort.describe/attach/detach` без права выдавать grants. Семантика `install` отдельно от `connect` и `invoke`. Стандарт IWXA может принять совместимые поля только отдельным нормативным изменением.

**Первый PASS:** один JS и один Python consumer используют одну проверенную публичную спецификацию и честно отказывают на несовместимой версии; для Rust только когда нуждается реальный Atlas scenario. Публичный contract PASS не означает модификацию закрытых hosts.

## U04 — IWXA External Application Presentation Bridge

**Назначение:** открывать существующую программу, переводить фокус, отображать статус и, если движок поддерживает, безопасно связывать/встраивать визуальную поверхность в приложение-хозяин. Это отдельная **UI-capability** от установки и ToolRuntime; подключение программы всё равно идёт через U01.

**База:** Tauri/OS shells в Agent/Atlas/Center, исследования linked window modes. Не писать второй browser engine, OS compositor или новый Tauri framework.

**Кандидат контракта:** `PresentationCapability {mode: LAUNCH|FOCUS|LINKED_WINDOW|EMBEDDED_VIEW, supported, reason?}`, `PresentationBinding {appRef, windowRef?, hostRef}`, `open/activate/closeBinding` через платформенный adapter. Для I3 linked window требуются явный реальный процесс/окно и возможность повторного фокуса. I4 embedded допускается только когда API toolkit/vendor предоставляет поддержку.

**Первый PASS:** реальная программа запускается и повторно активируется из независимого GUI, host закрывается без принудительного уничтожения пользовательской работы, unsupported embed явно виден; Windows Linux macOS квалифицировать отдельно по фактам.

## Общие правила выполнения

1. У новых модулей сначала **REUSE/ADAPT** уже существующие API и код, только после этого писать недостающее. Две разные host реализации не требуют общей базы.
2. Не менять Agent 0.2, T2 main, T3 или лицензионный/нормативный Standard этим документом. Код закрытых репозиториев нельзя копировать в публичный `Br-test` или его Actions.
3. Для каждого публичного reusable package определить собственную лицензию и metadata при отдельном product decision; открытый GitHub repo не гарантирует право перепубликовать любой код.
4. После каждого реального рабочего среза тестировать новую capability, а не плодить общую test infrastructure. Итог: программатически доступные операции + readback, не только «подключено» в UI.
