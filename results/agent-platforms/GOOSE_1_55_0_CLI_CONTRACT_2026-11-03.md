# Goose 1.55.0 — реальный CLI и синтетический модельный endpoint

**Дата:** 2026-11-03. **Публичный тестируемый commit:** 8192ed35867f75e349bb76328455d30236dd16ad.
**Реальный workflow:** https://github.com/Faust-S/Br-test/actions/runs/41648642960
**JSON artifact:** https://github.com/Faust-S/Br-test/actions/runs/41648642960/artifacts/13236686493

## Факты

- Точный upstream release: v1.55.0, Linux x86_64 GNU CLI tar.bz2; скачанный архив проверен по официальному release digest SHA-256 `d9afefe7b8f4e372ebefd8b465ab6eea1f297304d058e8e9379ed627e0329708`.
- GitHub Actions Ubuntu 24.04, Python 3.12.15; `goose --version`: 1.55.0, exit 0.
- `goose run --help`: exit 0, обнаружен `--output-format`.
- Реальный `goose run --provider openai --model gpt-4o --no-session --output-format json -t ...`: exit 0, время одного исполнения 0.429 секунды.
- Независимый тестовый HTTP-сервер получил один POST `/v1/chat/completions` с `model: gpt-4o`, `stream: true`, двумя сообщениями и описаниями доступных инструментов.
- Синтетический локальный endpoint вернул `IWXA-GOOSE-FIXTURE-REPLY-20261103`; настоящий Goose включил ровно этот текст в вывод.
- ПРИМЕЧАНИЕ: stdout содержит вводный banner перед JSON. Нельзя без проверки предполагать, что `--output-format json` = только JSON-объект без префикса. Для автоматического импорта требуется учитывать реальные строки stdout либо использовать иной подтверждённый vendor режим.

## Что не подтверждено

- **Модель не выполнялась:** endpoint был детерминированным заглушечным HTTP-сервисом. Время 0.429 с не отражает скорость LLM.
- Не запускалось ни одного shell/file/MCP/tool action. Goose лишь передал tool descriptions в запрос; это не подтверждение real tool execution.
- Не проверены рабочий локальный Ollama model, качество решения, использование PRIVATE, Windows GUI, восстановление и фактический `AgentRuntimePort` IWXA.
- Тестовой версией была 1.55.0, а не актуальная upstream 1.57.0, чтобы сопоставить с pin продукта.

## Инженерный вывод

Настоящий Goose 1.55.0 подтверждён как рабочий CLI и потребитель OpenAI-compatible HTTP streaming; его входы и выходы измерены. Не объявлять этот результат статусом полноценной многошаговой автономии, работающей внутри IWXA. Следующий шаг — реальная ограниченная задача с фактической генеративной моделью, материальным результатом и независимым readback. Затем — адресный адаптер через существующие порты; не создавать дополнительный Agent Core.
