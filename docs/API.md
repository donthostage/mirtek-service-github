# API 0.4

Обновление API 0.3: POST /api/v1/availability принимает sku и warehouse_id и возвращает Reply. Все шесть направлений используют HTTP-мок источника; появился intent complaints, data_backend snapshot/http_pull_mock и необязательный handoff_payload. /health содержит source_backend. Доступ к реальным системам отсутствует. Контракты источника и новые reason_code описаны в HTTP_PULL.md.

Адрес локальной демонстрации: `http://127.0.0.1:8766`. Swagger: `/docs`, машинная схема: `/openapi.json`. Внешние ресурсы Swagger могут требовать интернет; экспорт схемы включён в `docs/openapi.json`.

В 0.4 добавлено recognizer_version: lexical-1, mirtek-semantic-2 или исторический intent-legacy-1. Для структурированного availability распознаватель не вызывается, значение not_used. В /health версия относится к выбранной конфигурации, а не подтверждает качество модели.

## POST /api/v1/messages

JSON: `{"text":"Как работает магазин на Учебной?","context_id":null}`. Текст — от 1 до 2000 символов, не только пробелы. Неизвестные поля отклоняются с 422. Для продолжения уточнения передать возвращённый `context_id`; для нового диалога — null. Содержимое старого контекста клиент не передаёт. Просроченный/неизвестный токен вызывает handoff, не молчаливый новый диалог.

Ответ 200 содержит:

- `request_id`: UUID конкретного запроса.
- `status`: `answered`, `needs_clarification` или `handoff`.
- `answer`: текст для показа пользователю.
- `intent`: `hours`, `stock`, `promotions`, `delivery`, `returns`, `complaints` или null.
- `reason_code`: машиночитаемая причина.
- `sources`: id, version, as_of, valid_until, synthetic; при автоматическом ответе обязательно есть источник.
- `context_id`: токен только для ожидаемого уточнения. Срок 15 минут, перезапуск его удаляет.
- `operator_required`: true только при handoff.
- `handoff_delivered`: всегда false в прототипе; доставки оператору нет.
- `mode`: `synthetic_rule_baseline` или `synthetic_aitunnel`. Оба используют вымышленные источники; режим AITUNNEL не означает подтверждённое качество модели.
- `evaluated_at`: время проверки с часовым поясом Кузбасса.
- `elapsed_ms`: внутренняя задержка обработки без HTTP, не нагрузочный KPI.

Основные причины: `source_answer`; `missing_store`, `ambiguous_store`, `missing_product`, `ambiguous_product`; `operator_requested`, `requires_operator`, `unsupported_intent`, `multiple_intents`, `context_expired`, `context_limit`, `source_stale`, `source_unavailable`, `unknown_product`, `missing_stock`, `future_stock_unknown`, `unsupported_date`, `ambiguous_date`, `schedule_unconfirmed`, `promotion_scope_unknown`, `individual_policy_question`. Накопленный текст уточнений ограничен 6000 символами.

Handoff возвращается с 200: это результат обработки, а не ошибка HTTP. `needs_clarification` не считать решением обращения.

Дополнительные причины распознавания AITUNNEL: `model_not_configured`, `model_timeout`, `model_unavailable`, `model_invalid_response`, `model_refused`, `uncertain_intent`. Сбой модели всегда означает handoff; ответ из модели не показывается пользователю как факт.

## GET /health

Проверка доступности процесса, не проверка всех источников: `{"status":"ok","mode":"synthetic_rule_baseline","company_connected":false,"model_configured":false}`. В режиме AITUNNEL `model_configured=true` означает только наличие ключа и id модели, а не проверенную связь с провайдером. Источники проверяются при обращении.

## Ограничения среды

API предназначен для локальной разработки с одним процессом. Не публиковать напрямую: необходимы аутентификация, разграничение диалогов, TLS, ограничения размера HTTP-тела и частоты, политика хранения и мониторинг. Не передавать персональные данные в демо.

## Общий таймаут

Модель и источники делят бюджет 4,5 секунды на обработку. При его исчерпании — статус `handoff`, `reason_code=request_timeout`, без источников и неподтверждённого ответа. Отдельные пределы модели/источника сохраняются (3,5/1 секунда). Очередь HTTP-сервера не входит в этот внутренний бюджет; KPI измеряется отдельно на границе API.

При подтверждении смысла reason_code=confirm_intent, context_id содержит ожидающий контекст; «да» продолжает вопрос без новой генерации. При «нет» ответ needs_clarification/rephrase_question не содержит контекста: следующий запрос начинает новый вопрос. Структурированные ответы на предложенный канал и ожидаемый артикул/склад также не требуют повторного LLM-вызова.
