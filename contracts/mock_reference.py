"""Synthetic schedule, promotions and delivery. Not company data."""
from datetime import date, datetime
from .reference import (
    CatalogStore, DeliveryQuery, DeliveryResult, HoursQuery, HoursResult,
    PromotionLine, PromotionQuery, PromotionResult, SourceStamp, StoreCatalogResult, stamp,
)

AS_OF = datetime.fromisoformat('2026-10-22T09:00:00+07:00')
UNTIL = datetime.fromisoformat('2026-11-22T09:00:00+07:00')

STORES = [
    {
        'id': 'S1', 'name': 'Учебная, 1', 'aliases': ['учебной', 'учебная'],
        'schedule_from': date(2026, 10, 22), 'schedule_until': date(2026, 10, 31),
        'weekly': ['09:00–20:00', '09:00–20:00', '09:00–20:00', '09:00–20:00', '09:00–20:00', '10:00–18:00', None],
        'exceptions': {date(2026, 10, 23): None},
    },
    {
        'id': 'S2', 'name': 'Примерная, 2', 'aliases': ['примерной', 'примерная'],
        'schedule_from': date(2026, 10, 22), 'schedule_until': date(2026, 10, 31),
        'weekly': ['10:00–19:00', '10:00–19:00', '10:00–19:00', '10:00–19:00', '10:00–19:00', '10:00–17:00', None],
        'exceptions': {},
    },
]

PROMOTION = 'Учебная акция: скидка 5% на демонстрационный набор DEMO. Не является предложением ООО «Миртек».'
DELIVERY = {
    'delivery_terms': 'Учебные условия: базовая стоимость доставки — от 300 ₽, ориентир по сроку — 1–3 рабочих дня после подтверждения заказа. Итоговую цену и дату согласуют при оформлении с учётом адреса и состава заказа; это не расчёт конкретной доставки. Самовывоз оформляется отдельно и только после подтверждения заказа сотрудником. Все тарифы и сроки вымышлены, это не правила ООО «Миртек».',
    'pickup_terms': 'Учебные условия: самовывоз возможен только после подтверждения заказа сотрудником. Наличие на складе самовывоз не подтверждает. Это не правила ООО «Миртек».',
}


def _source(source_id: str) -> SourceStamp:
    return stamp(source_id, as_of=AS_OF, valid_until=UNTIL)


def catalog() -> StoreCatalogResult:
    stores = [CatalogStore(id=row['id'], name=row['name'], aliases=row['aliases'],
                           schedule_from=row['schedule_from'], schedule_until=row['schedule_until'])
              for row in STORES]
    return StoreCatalogResult(status='found', stores=stores, source=_source('demo-store-catalog'))


def hours(query: HoursQuery) -> HoursResult:
    row = next((item for item in STORES if item['id'] == query.store_id), None)
    if row is None:
        return HoursResult(status='store_unknown')
    if query.on_date < row['schedule_from'] or query.on_date > row['schedule_until']:
        return HoursResult(status='date_unconfirmed')
    value = row['exceptions'].get(query.on_date, row['weekly'][query.on_date.weekday()])
    return HoursResult(status='found', store_id=row['id'], store_name=row['name'],
                       on_date=query.on_date, hours=value, source=_source('demo-store-hours'))


def promotions(query: PromotionQuery) -> PromotionResult:
    lines = []
    if date(2026, 10, 22) <= query.on_date <= date(2026, 10, 31):
        lines.append(PromotionLine(text=PROMOTION, scope='all'))
    return PromotionResult(status='found', on_date=query.on_date, promotions=lines,
                           source=_source('demo-promotions-api'))


def delivery(query: DeliveryQuery) -> DeliveryResult:
    return DeliveryResult(status='found', topic=query.topic, text=DELIVERY[query.topic],
                          source=_source('demo-delivery-api'))
