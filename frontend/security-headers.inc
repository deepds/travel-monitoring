# Заголовки безопасности, общие для всех location.
#
# Вынесены в отдельный файл, потому что add_header внутри location отменяет
# наследование одноименных директив из блока server: подключать нужно явно.

add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "same-origin" always;
add_header X-XSS-Protection "0" always;

# Приложение самодостаточно: внешние скрипты, стили и шрифты не загружаются.
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'" always;
