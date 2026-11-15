# Agent 0.6: U01 / U04, документы и установленное ПО

Дата: 2026-11-15. Публичная ветка разработки: `work/2026-11-15-agent06-office-document-integration`.

Исходная U01: `Faust-S/Br-test@work/2026-11-13-u01-agent05-lazy-inventory` HEAD `722f2f701ec3451e0f45a9f454175e1e21e42653`, TREE `eda74d01c3c52173b6dd77f2ec28449a0650005c`.

U04, только чтение: `Faust-S/Br-test@work/2026-11-04-u04-presentation-bridge` HEAD `1548cf607616904f78085e1e6f32a984022efa5b`, TREE `8d844fb385f896b0eead3c95b9a546153f12b4fb`.

Agent 0.6, только чтение: `Faust-S/iwxa-personal-agent@work/2026-11-15-agent-0-6-development` HEAD `978b28d6690aeee7f17cc263a563ec1a5d025cbe`, TREE `c6ba3d5bb0f9a9c558dec7d8799e722b1f3823c3` на момент начальной ревизии. Общая ветка не изменяется.

## Матрица фактических функций

Условные статусы: `EXISTING` уже реализовано в исходном Agent (без новой приёмки), `U01_UNIT` проверен отдельный модуль с файлами и тестами, `OPTIONAL` выполняется лишь после подтверждения установленной программы и U01 connect, `NOT_DONE` не выполнен полный Agent E2E. Отдельные тесты не подтверждают установленный Windows Agent.

| Формат | Чтение | Создание/изменение | Конвертация | Извлечение/OCR |
|---|---|---|---|---|
| TXT / Markdown | EXISTING: UTF-8 через LocalFilesV1 | EXISTING: `files.write_text` | NOT_DONE: PDF | EXISTING: текст |
| DOCX | EXISTING: `local_document_text_v1` | U01_UNIT: добавить один простой абзац в **новую копию** | OPTIONAL: установленный LibreOffice через `CliConnection` | EXISTING: текст / таблицы, с оговорками |
| XLSX | NOT_DONE: существующий `files.read` не извлекает листы | NOT_DONE | OPTIONAL: LibreOffice CLI template / NOT_DONE E2E | NOT_DONE |
| PPTX | NOT_DONE: существующий `files.read` не извлекает слайды | NOT_DONE | OPTIONAL: LibreOffice CLI template / NOT_DONE E2E | NOT_DONE |
| PDF | EXISTING: текстовый PDF с pypdf, отчёт по страницам без текста | NOT_DONE: редактор | OPTIONAL: результат DOCX->PDF | U01_UNIT: оценка наличия текстового слоя; OCRmyPDF OPTIONAL/NOT_DONE E2E |
| PNG/JPEG/TIFF | NOT_DONE: нет общего Agent image-read маршрута `LocalFilesV1` | NOT_DONE | NOT_DONE | OPTIONAL: Tesseract CLI; реальный Tesseract отдельно проверен локально, но Agent E2E NOT_DONE |

## Выбор исполнителя

1. `select_document_executor` прежде всего выбирает `IWXA_NATIVE_DOCX_V1` для поддерживаемого простого DOCX-изменения, без проверки наличия Office.
2. `discover_document_programs` без запуска и установки обнаруживает типовые пути Microsoft Word/Excel/PowerPoint, LibreOffice, ONLYOFFICE, Tesseract, OCRmyPDF. На Windows читает HKCU `UserChoice` ProgId для расширений. `installed_detected` **не** значит наличие поддерживаемого API, CLI или пользовательской лицензии; `cli_usable_verified=false` до реального U01 connect.
3. `make_document_cli_spec` передаёт утверждённые argv-шаблоны в существующий `CliConnection`: исполняемый файл pin по SHA-256, версия, статус, ограниченное время, stdout/stderr, без shell. Вывод формируется во временном каталоге; исходник не меняется.
4. Только host, после Human Review и проверки конкретного Task/Workspace/плана, может предоставить `approved_plan_digest`, пути в существующем Files root и записать утверждённую копию через свои полномочия; `publish_approved` проверяет исходник и записанный SHA-256, но **не** создаёт новую систему прав, durable receipt, ToolRuntime, Result или файл-Store. `result_persisted=false` — осознанная граница.
5. Если требуется настоящее окно, существующий U04 `createPresentationBridge(...).open/activate` должен получать `hostRef`, `appRef`, `launchSpec` от Agent host и подтверждённый исполняемый файл. Нельзя выдавать GUI launch за машинно проверенное редактирование. **U04 в приватном Agent пока не подключён данным срезом.**

## Три сценария и наблюдения

- DOCX -> предложение простого дополнения -> точный digest плана -> подтверждённая запись **новой** копии -> sha readback -> открытие копии после холодного повторного чтения: U01_UNIT PASS на реальных временных файловых объектах, `python-docx` открывает образец. Действительная Human Review/ToolRuntime/Result в установленном Agent: NOT_DONE.
- DOCX -> PDF: независимый локальный опыт через LibreOffice CLI без U01 PASS; публичный U01 qualification исполняется только при наличии установленного конвертера и реального connect. Один конвертер не даёт полной Office E2E.
- PDF без текстового слоя -> предложение OCR: обнаружение через pypdf U01_UNIT; маршрут OCRmyPDF требует отдельной установки пользователем и подключения через U01, реальный OCRmyPDF E2E NOT_DONE. Изображение -> текст проверено локальным Tesseract без интеграции в Agent.

## Лицензии и принцип установки

- LibreOffice: MPL-2.0 как основная лицензия, также компоненты с другими свободными лицензиями (https://www.libreoffice.org/licenses/). Не навязывается и не устанавливается Agent. Изолированный Linux CI может временно установить тестовую копию.
- Tesseract: Apache-2.0 (https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE). Самостоятельная поставка, без автоматической установки Agent.
- OCRmyPDF: MPL-2.0, зависимости и ресурсы могут иметь иные лицензии (https://github.com/ocrmypdf/OCRmyPDF). Не устанавливается Agent.
- ONLYOFFICE Desktop Editors: AGPL-3.0 с дополнительными условиями текущего upstream (https://github.com/ONLYOFFICE/desktop-apps/blob/master/LICENSE). Только пользовательская установка и окно/утверждённый host-интерфейс.
- Microsoft Office/365: проприетарный софт; наличие EXE и файловой ассоциации не подтверждает права пользователя и не означает автоматизацию через COM. Установка Agent не предусмотрена.

## Проверки и граница готовности

Запуск адресного теста: `python -m unittest discover -s universal-install/tests -p test_document_operations.py -v`.

Проверка исходного U01: `python -m unittest discover -s universal-install/tests -p test_program_connection.py -v`.

Реальная квалификация при наличии установленного LibreOffice: `python universal-install/qualification/office_document_u01.py artifacts/office-document`. `IWXA_REQUIRE_INSTALLED_LIBREOFFICE=1` требует PASS (Linux публичный CI). На Windows/macOS новые unit tests проверяют контракты без заявления наличия Office.

Следующий интеграционный этап для владельца Agent 0.6: добавить штатную **бинарную операцию записи новой копии** в существующий `LocalFilesV1`/ToolRuntime с разрешением Human Review и receipt; не использовать `files.write_text` для ZIP-байтов. Отобразить выбор исполнителя в существующем `document-workflow.tsx`, связать `result_persisted` с реально сохранённым Agent Result и проверить cold restart установленного Tauri Windows приложения. Объединение веток и принятие Agent 0.6 не выполнялись.

Приватность: публичный `Br-test` хранит лишь новую открытую реализацию, тесты и синтетические файлы CI. Приватные исходники, реальные пользовательские документы и токены не передавались; приватные Actions не запускались. Действует узкое разрешение на публичный Center ↔ закрытый Agent, но оно здесь **не использовано**.

## Финальное наблюдение публичного CI от 15.11.2026

GitHub Actions #43717967716: SUCCESS, все три jobs Windows 2022, macOS 15 и Ubuntu 24.04 завершились SUCCESS на commit `6698985112d35bd05e57da97b834028a1e22fbfb`. В публичном Ubuntu runner LibreOffice 24.2.7.2 был явно установлен только для квалификации; настоящий `CliConnection` выполнил DOCX -> PDF и повторное чтение. `real_cli=true`, `text_readback=true`, `source_preserved=true`, `status=PASS`.

Источник: https://github.com/Faust-S/Br-test/actions/runs/43717967716 , публичный артефакт `agent06-office-public-ubuntu-24.04` (синтетические `original.docx`, `converted.pdf`, `REPORT.json`). Исходный SHA-256: `5d179ff0eaa2687967a4fbb07707614bd566d8e45e469d06a629dfdd33571bbd`. PDF SHA-256: `669159927d78223dc325748a314e653085224cb31f70d4db20a73586b55242c2`. `task_result_persisted=false`: это не подтверждает приватный Agent Result/GUI и не изменяет его исходники.

## Реальный OCR / окончательная публичная квалификация 15.11.2026

- Run [#43719968885](https://github.com/Faust-S/Br-test/actions/runs/43719968885) на commit `793d1abe5fa12b013d5b05524b188d5b494e069c`: **SUCCESS** Windows 2022, Ubuntu 24.04, macOS 15. Новые адресные Python-тесты: 13/13 PASS на Ubuntu, также PASS на Windows/macOS. Действующие тесты U01 CliConnection: PASS.
- В отдельной Ubuntu CI среде на самом деле установлены LibreOffice 24.2.7.2, Tesseract 5.3.4, OCRmyPDF 15.2.0. Все три вызова выполняются через прежний U01 `CliConnection` с pinned SHA-256, предварительным `inspect/connect` и физической проверкой результата.
- DOCX → PDF: `real_cli=true`, PDF разобран pypdf, исходник сохранён, output readback подтверждён.
- PNG → TXT: OCR прочитал синтетическую строку `INVOICE 1500 USD`, результат сохранён и повторно проверен.
- Сканированный PDF → поисковый PDF: исходная страница без извлекаемого текста, `ocr_pdf.real_cli=true`, `ocr_pdf.searchable_after=true`, `ocr_pdf.source_preserved=true`, `ocr_pdf.status=PASS`, точный SHA-256 нового PDF `88599a29a663d05c04f3a98c79446d19cec844ad47c4debe17d89e8c7b763dfe`.
- Публичный артефакт `agent06-office-public-ubuntu-24.04`, artifact ID `14164600158`, включает только синтетические файлы (`original.docx`, `converted.pdf`, `ocr-invoice.png`, `recognized.txt`, `scanned.pdf`, `searchable.pdf`) и проверочный `REPORT.json`. Данные частного Agent не использованы.
- Сборка установленного приватного Agent, настоящая UI-приёмка U04, сохранённый Agent Result и проверка холодного перезапуска Tauri **NOT_RUN**. Публичная квалификация этого не заменяет.

В закрытом `iwxa-personal-agent` ведётся самостоятельная feature-ветка с использованием точного U01 source blob `c8c6628d67a68642f817aaff628bf82da06e0058`, без нового U01 manager и без изменений общей ветки 0.6. Одна офисная операция не означает полной поддержки DOCX/XLSX/PPTX/PDF. Приватные Actions не запускались.

## Сверка строгой схемы кандидата, 15.11.2026

В публичном U01 `files_binary_write_candidate` удалено поле `description`, отсутствующее в `schemas/action-candidate.schema.json` Agent 0.6 (`additionalProperties=false`). Возвращается только `operation,target,arguments,risk,reversible`. Адресной проверкой закреплено точное множество полей. Публичный workflow [#43720366392](https://github.com/Faust-S/Br-test/actions/runs/43720366392) завершён `SUCCESS` на commit `3ec766bee6d88070bdda6902672dd072d897456f` (Windows, macOS, Ubuntu); независимый локальный запуск 13/13 адресных U01 тестов PASS. Приватный vendor blob вновь точно совпадает с public source: `c8c6628d67a68642f817aaff628bf82da06e0058`.

Скачан реальный публичный synthetic artifact `agent06-office-public-ubuntu-24.04` из предыдущего [#43719968885](https://github.com/Faust-S/Br-test/actions/runs/43719968885), независимо проверены шесть SHA-256 содержимого; `scanned.pdf` не содержит текста, `searchable.pdf` содержит `SCAN DOCUMENT 1500 USD`; `converted.pdf` содержит текст счёта. Это проверка публичных результатов, не приватного `Task/Result`. Приватные тесты и установленная Windows-приёмка по-прежнему НЕ выполнялись.
