from decimal import Decimal
from fastapi import FastAPI, Depends
from .models import PolicyQuery, PolicyResult
from .mock_source import resolve
from .inventory import InventoryQuery, InventoryResult, InventorySource, InventoryItem
from .reference import (
    DeliveryQuery, DeliveryResult, HoursQuery, HoursResult, PromotionQuery, PromotionResult,
    StoreCatalogQuery, StoreCatalogResult,
)
from .mock_reference import catalog, delivery, hours, promotions
from datetime import datetime, timedelta, timezone

app=FastAPI(title='Миртек мок API источников', version='0.4.0',
            description='Шлюз чтения для нормализатора. Имена полей наши. Запись не поддерживается. Только моки, подключения к компании нет.')


@app.post('/source/v1/policies/resolve', response_model=PolicyResult)
def resolve_policy(query: PolicyQuery):
    return resolve(query)


@app.get('/health')
def health():
    return {'mode':'synthetic_policy_mock', 'company_connected':False}


def source_now():
    return datetime.now(timezone(timedelta(hours=7)))


@app.post('/source/v1/stores/catalog', response_model=StoreCatalogResult)
def list_stores(query: StoreCatalogQuery):
    return catalog()


@app.post('/source/v1/stores/hours', response_model=HoursResult)
def store_hours(query: HoursQuery):
    return hours(query)


@app.post('/source/v1/promotions/list', response_model=PromotionResult)
def list_promotions(query: PromotionQuery):
    return promotions(query)


@app.post('/source/v1/delivery/terms', response_model=DeliveryResult)
def delivery_terms(query: DeliveryQuery):
    return delivery(query)


@app.post('/source/v1/inventory/lookup', response_model=InventoryResult)
def lookup_inventory(query: InventoryQuery, now: datetime = Depends(source_now)):
    products={'10001':'Хлопок учебный','10002':'Лён учебный'}
    warehouses={'WH-DEMO-1':'Учебный склад 1','WH-DEMO-2':'Учебный склад 2'}
    # on_hand, reserved. Available is the difference and is the only quantity quoted to a client.
    stock={('10001','WH-DEMO-1'):('15','2.5'),('10001','WH-DEMO-2'):('0','0'),('10002','WH-DEMO-1'):('4','0')}
    if query.sku not in products: return InventoryResult(status='product_unknown')
    if query.warehouse_id not in warehouses: return InventoryResult(status='warehouse_unknown')
    pair=stock.get((query.sku,query.warehouse_id))
    if pair is None: return InventoryResult(status='stock_unknown')
    on_hand, reserved = Decimal(pair[0]), Decimal(pair[1])
    # Only synthetic fixtures get a fresh timestamp per request. This is not a stock refresh from Mirtek.
    return InventoryResult(status='found', item=InventoryItem(sku=query.sku,warehouse_id=query.warehouse_id,
        product_name=products[query.sku],warehouse_name=warehouses[query.warehouse_id],
        on_hand_quantity=on_hand,reserved_quantity=reserved,available_quantity=on_hand-reserved,unit='м'),
        source=InventorySource(id='demo-warehouse-api',version='1',
        as_of=now,valid_until=now+timedelta(minutes=5),synthetic=True))
