# IWXA Browser — парное сравнение Obscura 0.2.5 и Gecko/Firefox 158.0.1
Дата: 2026-11-01. Статус: **SOURCE/RUNTIME EXPERIMENT COMPLETED; PRODUCT QUALIFICATION NOT COMPLETED**.

## Цель
Проверить два варианта будущего **собственного** интерфейса IWXA Browser: (A) Obscura как единственный веб-движок и собственный GPU/native frontend; (B) Gecko как веб-движок и собственная оболочка IWXA. Не учитывать штатный Firefox UI как достоинство готового продукта.

Публичные протоколы: [исходный парный прогон](https://github.com/Faust-S/Br-test/actions/runs/41247729180), [нормализованный прогон](https://github.com/Faust-S/Br-test/actions/runs/41247900528). Исполнитель `comparison/paired.py`, страница `comparison/iwxa-ui-fixture.html`. Запуск Windows Server 2022 и Ubuntu 24.04 на одинаковом runner **внутри каждой ОС**, один прогон каждой пары.

## Проверенный результат (второй прогон)
| Параметр | Windows Firefox | Windows Obscura | Linux Firefox | Linux Obscura |
|---|---:|---:|---:|---:|
| Launch to protocol ready, ms | 3953 | 515 | 15740 | 304 |
| Сумма RSS процессов: 1 вкладка, МиБ | 851.8 | 50.8 | 1314.8 | 63.7 |
| Сумма RSS процессов: 5 вкладок, МиБ | 964.1 | 73.1 | 1643.6 | 84.8 |
| Рендер PNG при 1280×850 | PASS | PASS | PASS | PASS |
| Клик по координатам | PASS | PASS | PASS | PASS |
| Ввод с клавиатуры | PASS | PASS | PASS | PASS |
| Прокрутка колесом | PASS | PASS | PASS | PASS |
| Адаптивный viewport 500×844 | PASS | PASS | PASS | PASS |
| IDB: 3 stores, commit/readback | PASS | PASS | PASS | PASS |
| IDB: abort/rollback | PASS | PASS | PASS | PASS |
| `localStorage` после полного restart | PASS | PASS | PASS | PASS |
| **IndexedDB после restart** | **PASS** | **FAIL** | **PASS** | **FAIL** |
| `navigator.gpu.requestAdapter()` | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |

Эксперимент перезапуска: одно и то же хранилище браузера, полностью завершённый предыдущий процесс, новый процесс, новый браузерный контекст, та же origin. Для Obscura **оба раза** прочитаны `[null,null,null]` вместо `[7,7,7]`. `localStorage` в обеих ОС восстановился. Это подтверждает границу persistence Obscura, а **не отсутствие сохранения в T2**.

Объект UI-проверки — одинаковая контролируемая HTML/CSS-страница: шапка, панели, кнопка, текстовый ввод, checkbox, SVG, Canvas, Shadow DOM, CSS Grid, responsive breakpoint. Исключено повторное отправление побочных пользовательских действий после неизвестного результата. Проверяется живое управление кнопкой, клавиатурой, прокруткой и открытие 4 дополнительных вкладок; не достаточно статичной картинки.

## Изобразительное сравнение
Выходные PNG:
- `firefox-desktop.png`, `obscura-desktop.png`, `firefox-mobile.png`, `obscura-mobile.png`
- находятся в обоих GitHub Actions artifacts `paired-windows-2022`, `paired-ubuntu-24.04`.
- Одинаковые **desktop canvas 1280×850**, **mobile canvas 500×844** (Firefox WebDriver не уменьшил viewport ниже 500 px; поэтому 390 px из первого прогона **не сравнивали**).

Приведённые к одинаковым размерам raw pixel difference: 7.28/255 Windows и 6.53/255 Linux (средняя абсолютная разница RGB). Это **не** процент веб-совместимости и не метрика визуальной корректности. Осмотр реальных PNG выявил у Obscura читаемую общую структуру/Grid, но дефекты размещения подписи рядом с кнопкой и искажение текста внутри Canvas; у Firefox они существенно меньше. Для пользовательского браузера это реальный долг CSS/paint/typography.

WebGL: Windows Firefox сообщает WebGL context, Windows/Linux Obscura нет; Linux Firefox в hosted VM тоже не создал WebGL context. MediaSource API доступен Firefox и отсутствует в Obscura. MP4 `canPlayType` у Firefox возвращает `maybe`, Obscura — пустую строку. Это capability probe, **не реальный тест воспроизведения видео/аудио**.

В обоих движках `navigator.gpu` может существовать, но реальный `requestAdapter()` на этих CI runners вернул `UNAVAILABLE`. Не заявлять о поддержке Browser Local Inference/WebGPU.

## Ограничения измерений и тестовой среды
1. Firefox/Gecko исследован через самостоятельный штатный headless Firefox и geckodriver, **не через будущую встраиваемую Windows оболочку**. GeckoView — Android библиотека; не готовый Windows embedder. Сравнение не доказывает готовность собственного UI.
2. Obscura через штатный render-enabled non-stealth binary и CDP; **не** через полноценный собственный нативный viewport IWXA. Screencast/IME/accessibility/scroll focus/60 FPS не квалифицированы.
3. Сумма RSS/working set может двойным счётом учитывать общие страницы. GPU allocations не включены. Не измерена память Node coordinator, собственного native UI и обновлений.
4. Firefox и Obscura протестированы **на одном runner в каждом OS прогоне**. Но всего по одному полному парному прогону на ОС, не рандомизировано; нет статистически устойчивых p95.
5. Браузерный restart охватывает `records/heads/audit` по образцу механизма T2, но **не запускает T2 Kernel/Host**, не создаёт настоящую receipt, не проверяет Workspace authority, историю/revisions или `.iwxa` exchange.
6. Нет квалификации hostile-site sandbox, CORS/CSP/iframe negative auth, browser process isolation, sleep/resume, Windows real GUI/installer, Android/iOS, service worker и медиа на реальных объектах.
7. Собственные Learn/ArtBazi ранее проверялись только как public content present. Полного маршрута собственных инструментов здесь нет.

## Вывод по архитектуре (не release gate)
**Предпочтительное инженерное направление: Obscura как единственный встроенный веб-движок + собственный UI IWXA**, а Gecko оставить независимым эталоном проверки и внешним браузером по выбору пользователя, **не обязательным компонентом сборки**.

Почему:
- Obscura использует значительно меньше памяти и имеет native Windows/Linux builds, Rust API, визуальный рендеринг/CDP; события работают в одной JS-сессии.
- собственный UI нужен **в обоих вариантах**; Gecko/Firefox на Windows не имеет готового GeckoView-подобного API, поэтому требуется тяжёлое встраивание/форк или отдельное окно Firefox, не эквивалентное нашему продукту.
- Gecko зрелее в визуализации, хранении, медиа и стандартах; это сильный сравнительный эталон, который должен выявлять регрессии Obscura.

**Однако Obscura-only НЕ READY для сохранённых T2 Workspace результатов и универсального визуального браузера.** Первые обязательные работы: нормальное постоянное IndexedDB с атомарным commit/readback/restart без второго T2 storage; исправление текущих CSS/Canvas/Shadow DOM артефактов; process isolation/authenticated IPC; реальный native viewport и выборочное прохождение маршрутов собственных доменов. Не внедрять параллельный JSON как T2 source-of-truth.

До исправления IndexedDB T2 protected save нельзя выдавать за долговременное сохранение: стандартная T2 persistence существует, но движок уничтожает базу между процессами. Не считать экспорт временного JSON результатом T2.

## Технические источники
- Образец T2 хранилища: https://github.com/Faust-S/T2/blob/main/t2/current/final-successor/canonical-source/src/persistence/canonical-persistence.mjs (read-only, private repository).
- Obscura architecture: https://github.com/h4ckf0r0day/obscura/blob/v0.2.5/docs/Architecture-overview.md
- GeckoView Android: https://mozilla.github.io/geckoview/
- Публичные результаты предыдущих прогонов: https://github.com/Faust-S/Br-test/actions/runs/41242024170 и https://github.com/Faust-S/Br-test/actions/runs/41240961581
