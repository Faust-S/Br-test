# IWXA 0.6.7: ограниченное испытание Tauri + WebView2 + оригинальный Google MCP

Ветка от 722f2f701ec3451e0f45a9f454175e1e21e42653, до заимствований Cherry. Не менять существующий Obscura, main или Agent. Это не новая программа для пользователя, а публичный нативный стенд подключения браузера в правой панели.

Используется Tauri 2.12.7 (unstable multi-webview), WebView2 Evergreen и оригинальный пакет Chrome DevTools MCP 1.14.0 (Apache-2.0). Google MCP использует Node.js; Puppeteer является внутренней зависимостью оригинального MCP, не отдельным тестовым браузером. Отдельный Chromium не скачивается и не запускается. Ветка не содержит исходников Cherry.

Официальная схема: https://learn.microsoft.com/en-us/microsoft-edge/web-platform/devtools-mcp-server
Google: https://github.com/ChromeDevTools/chrome-devtools-mcp/tree/chrome-devtools-mcp-v1.14.0
Tauri: https://github.com/tauri-apps/tauri/tree/tauri-v2.12.7

Два настоящих WebView2 в одном native Window. Разные каталоги shell и guest, CDP только для guest. Фикстура доступна по loopback HTTP. Порт 0 и DevToolsActivePort, без глобального реестра Windows и общих флагов процесса. Не подключать к личным аккаунтам/профилям. Присутствие CDP на loopback не является полноценной авторизацией: это испытательный, а не production режим.

G1: сборка, один HWND, два нативных WebView, ручной ввод, штатный Google MCP autoConnect, точная привязка к guest, AX snapshot, fill, click, readback, screenshot, ручное завершение; скрытие/раскрытие, изменение размера, отключение клиента без закрытия окна, повторный запуск с тем же профилем.
G2 (только если G1 подтверждён): действительные сайты, расширенный браузерный функционал, стабильность и ресурсы. Затем отдельная адаптация к существующему PreviewDock, ToolProvider и T3, без нового Task/IAM/Store. Не создавать собственный CDP-сервер, собственный Chromium и параллельный MCP.
Лимит: один исходный прогон и одна адресная инженерная итерация для основных гипотез; при фундаментальном ограничении или недоступной Windows GUI не называть архитектуру готовой. Не маскировать ошибки запуском Edge, iframe, скринкастом, скрытым браузером или синтетическими результатами.
