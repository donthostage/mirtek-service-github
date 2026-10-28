"""Read-only outbound source flows, shared by the message and structured API."""
import re
from datetime import datetime, timedelta
from contracts.inventory import InventoryQuery
from contracts.models import ParseResult, PolicyQuery
from contracts.reference import DeliveryQuery, HoursQuery, PromotionQuery
from contracts.workflow import decide
from .classifier import normalized
from .models import Source
from .semantic_models import resolve_mentioned_date


def stock_fields(client, query, clock):
    result=client.inventory(query,clock)
    reasons={'product_unknown':'unknown_product','warehouse_unknown':'unknown_warehouse',
             'stock_unknown':'missing_stock','unavailable':'source_unavailable'}
    if result.status!='found':
        return {'status':'handoff','reason_code':reasons[result.status],
                'answer':'Достоверного остатка нет. Нужен оператор; сообщение сотруднику не отправлено.'}
    item=result.item
    quantity=format(item.available_quantity,'f')
    reserve=''
    if item.reserved_quantity > 0:
        reserve=f' В резерве {format(item.reserved_quantity,"f")} {item.unit}; это количество недоступно.'
    return {'status':'answered','reason_code':'source_answer',
            'answer':f'Учебный остаток: {item.product_name} (артикул {item.sku}), {item.warehouse_name} ({item.warehouse_id}): {quantity} {item.unit}.{reserve} Это доступный остаток на складе, не резерв и не подтверждение самовывоза из магазина.',
            'sources':[Source(**result.source.model_dump(mode='json'))]}


def source_stamp(document):
    return Source(id=document.id, version=document.version, as_of=document.as_of.isoformat(),
                  valid_until=document.valid_until.isoformat(), synthetic=document.synthetic)


def match_stores(stores, value):
    value=normalized(value)
    return [store for store in stores if any(
        re.search(r'(?<!\w)'+re.escape(normalized(name))+r'(?!\w)', value) for name in store.aliases+[store.id])]


def resolve_day(query, semantic, now):
    dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b', query)
    if len(set(dates))>1 or ('завтра' in query and 'сегодня' in query):
        return None, 'ambiguous_date'
    day=now.date()+timedelta(days=1 if 'завтра' in query else 0)
    if semantic and semantic.slots.requested_date:
        try:
            day=resolve_mentioned_date(semantic.slots.requested_date.text, now.date())
        except ValueError:
            return None, 'unsupported_date'
    elif dates:
        try:
            day=datetime.strptime(dates[0], '%Y-%m-%d').date()
        except ValueError:
            return None, 'unsupported_date'
    elif re.search(r'понедель|вторник|сред[ау]|четверг|пятниц|суббот|воскрес|праздник|\b\d{1,2}[./]\d{1,2}\b|послезавтра|вчера|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр', query):
        return None, 'unsupported_date'
    return day, None


def remote_hours(client, *, text, query, state, semantic, clock, intent, result, handoff, clarify):
    now=clock()
    day, reason=resolve_day(query, semantic, now)
    if reason: return handoff(reason, intent)
    catalog=client.stores(now)
    if catalog.status!='found': return handoff('source_unavailable', intent)
    store_text=normalized(semantic.slots.store.text) if semantic and semantic.slots.store else text
    selected=state.get('store_id') if state else None
    hits=match_stores(catalog.stores, store_text) or (
        [store for store in catalog.stores if store.id==selected] if selected else match_stores(catalog.stores, query))
    if len(hits)>1:
        return clarify('Укажите один магазин: Учебная, 1 или Примерная, 2.', 'ambiguous_store')
    if not hits:
        return clarify('Какой учебный магазин: Учебная, 1 (S1) или Примерная, 2 (S2)?', 'missing_store')
    store=hits[0]
    if day<store.schedule_from or day>store.schedule_until:
        return handoff('schedule_unconfirmed', intent)
    found=client.hours(HoursQuery(store_id=store.id, on_date=day), now)
    if found.status=='date_unconfirmed': return handoff('schedule_unconfirmed', intent)
    if found.status!='found': return handoff('source_unavailable', intent)
    schedule=f'{found.hours}.' if found.hours else 'закрыт.'
    answer=f'Учебный магазин {found.store_name}, {found.on_date.isoformat()}: {schedule}'
    return result('answered', answer, 'source_answer', intent=intent, sources=[source_stamp(found.source)])


def remote_promotions(client, *, query, semantic, clock, intent, result, handoff):
    now=clock()
    if semantic and semantic.slots.requested_date:
        try:
            requested=resolve_mentioned_date(semantic.slots.requested_date.text, now.date())
        except ValueError:
            return handoff('unsupported_date', intent)
        if requested!=now.date(): return handoff('unsupported_date', intent)
    if re.search(r'завтра|вчера|недел|\d{4}-\d{2}-\d{2}', query):
        return handoff('unsupported_date', intent)
    if re.search(r'\d|хлопок|лен|ткан|магазин|учебн|примерн', query):
        return handoff('promotion_scope_unknown', intent)
    found=client.promotions(PromotionQuery(on_date=now.date()), now)
    if found.status!='found': return handoff('source_unavailable', intent)
    if any(line.scope!='all' for line in found.promotions):
        return handoff('promotion_scope_unknown', intent)
    answer='\n'.join(line.text for line in found.promotions) if found.promotions else 'В учебном источнике нет действующих акций.'
    return result('answered', answer, 'source_answer', intent=intent, sources=[source_stamp(found.source)])


def remote_delivery(client, *, query, semantic, clock, intent, result, handoff):
    if re.search(r'\d|мой|моего|мою|мне|могу|успе|сегодня|завтра|цен|стоим|сколько|бесплат|срок|когда|адрес|\bв\s+[а-я]+', query):
        return handoff('individual_policy_question', intent)
    topic='pickup_terms' if re.search(r'самовывоз|забрать', query) else 'delivery_terms'
    if semantic and semantic.topic in ('delivery_terms', 'pickup_terms'):
        topic=semantic.topic
    found=client.delivery(DeliveryQuery(topic=topic), clock())
    if found.status!='found': return handoff('source_unavailable', intent)
    return result('answered', found.text, 'source_answer', intent=intent, sources=[source_stamp(found.source)])


def remote_message(client, *, intent, text, query, state, customer_text, semantic=None, clock, result, handoff, clarify):
    if intent=='hours':
        return remote_hours(client, text=text, query=query, state=state, semantic=semantic, clock=clock,
                            intent=intent, result=result, handoff=handoff, clarify=clarify)
    if intent=='promotions':
        return remote_promotions(client, query=query, semantic=semantic, clock=clock, intent=intent,
                                 result=result, handoff=handoff)
    if intent=='delivery':
        return remote_delivery(client, query=query, semantic=semantic, clock=clock, intent=intent,
                               result=result, handoff=handoff)
    if intent=='stock':
        if semantic and semantic.slots.requested_date:
            try:
                requested=resolve_mentioned_date(semantic.slots.requested_date.text,clock().date())
            except ValueError: return handoff('unsupported_date',intent)
            if requested!=clock().date(): return handoff('future_stock_unknown',intent)
        if re.search(r'завтра|послезавтра|будет|недел|\d{4}-\d{2}-\d{2}',query):
            return handoff('future_stock_unknown',intent)
        def skus(value):
            explicit=re.findall(r'(?:артикул|\bsku)\s*[:№#]?\s*([a-z0-9][a-z0-9_.-]{0,63})',value)
            return list(dict.fromkeys(s.upper() for s in (explicit or re.findall(r'\b\d{5}\b',value))))
        def warehouses(value):
            return list(dict.fromkeys(s.upper() for s in re.findall(r'\bwh-[a-z0-9-]+\b',value)))
        ids=skus(text) or ([state['remote_sku']] if state and state.get('remote_sku') else skus(query))
        whs=warehouses(text) or ([state['warehouse_id']] if state and state.get('warehouse_id') else warehouses(query))
        if semantic:
            ids=[semantic.slots.sku.text] if semantic.slots.sku else []
            whs=[semantic.slots.warehouse_id.text] if semantic.slots.warehouse_id else []
        extra={'remote_sku':ids[0] if len(ids)==1 else None,'warehouse_id':whs[0] if len(whs)==1 else None}
        if len(ids)!=1:
            return clarify('Укажите один артикул товара, например 10001 для учебного примера.',
                           'ambiguous_product' if ids else 'missing_product',**extra)
        if len(whs)!=1:
            return clarify('Укажите один идентификатор склада. В демо: WH-DEMO-1 или WH-DEMO-2. Адрес магазина не определяет склад автоматически.',
                           'ambiguous_warehouse' if whs else 'missing_warehouse',**extra)
        try:
            lookup=InventoryQuery(sku=ids[0],warehouse_id=whs[0])
        except ValueError:
            return handoff('invalid_parameters',intent)
        fields=stock_fields(client,lookup,clock)
        if fields['status']=='handoff': return handoff(fields['reason_code'],intent)
        return result(fields.pop('status'),fields.pop('answer'),fields.pop('reason_code'),intent=intent,**fields)

    if semantic and semantic.slots.requested_date:
        try:
            requested=resolve_mentioned_date(semantic.slots.requested_date.text,clock().date())
        except ValueError: return handoff('unsupported_date',intent)
        if requested!=clock().date(): return handoff('unsupported_date',intent)

    # Only a generic policy question may be auto-answered. This conservative
    # parameter extraction is explicitly a baseline, not a new semantic parser.
    personal=bool(re.search(r'\bхочу\b|\bмогу\b|\bмне\b|\bмой\b|\bмо[яю]\b|верните|компенсац|отказ|\bбрак|отрезан|где.*деньг|когда.*деньг|статус',query))
    if intent=='complaints' and not re.search(r'как|куда|порядок|срок|правил',query):
        personal=True
    if intent=='returns' and not re.search(r'как|услов|правил|документ|порядок|что нужно|какие',query):
        personal=True
    if intent=='returns' and re.search(r'можно ли',query) and re.search(r'ткан|хлоп|лен|отрез|купил|заказ|\d',query):
        personal=True
    if semantic:
        personal=semantic.request_kind=='individual_case'
        if re.search(r'верните.*деньг|компенсац|\bбрак|отрезан|где.*деньг|когда.*деньг',query):
            personal=True
    channels=[]
    for name,pattern in [('store',r'магазин'),('website',r'сайт|интернет'),('marketplace',r'маркетплейс|ozon|wildberries|озон')]:
        if re.search(pattern,text): channels.append(name)
    if len(channels)>1: return handoff('ambiguous_purchase_channel',intent)
    channel=channels[0] if channels else (state.get('purchase_channel') if state else None)
    if semantic:
        channel=semantic.slots.purchase_channel.value if semantic.slots.purchase_channel else None
    if intent=='returns':
        topic='documents' if re.search(r'документ|чек',query) else 'conditions' if re.search(r'услов|подлеж|можно ли',query) else 'steps'
    else:
        topic='complaint_review' if re.search(r'срок|рассмотр|ответят',query) else 'complaint_procedure'
    if semantic:
        topic=semantic.topic
    parsed=ParseResult(scenarios=[intent],request_kind='individual_case' if personal else 'policy_question',
                       topic=topic,purchase_channel=channel,uncertain=False)
    source=None if personal else client.policy(PolicyQuery(scenario=intent,topic=topic,purchase_channel=channel))
    decision=decide(parsed,source,customer_text=customer_text,now=clock(),
                    clarification_count=state.get('clarification_count',0) if state else 0)
    if decision['status']=='needs_clarification':
        return clarify(decision['answer'],decision['reason_code'],purchase_channel=channel)
    if decision['status']=='handoff':
        reply=handoff(decision['reason_code'],intent)
        # Keep customer assertions separate from facts; no ticket is created.
        decision['handoff_payload']['request_id']=reply.request_id
        reply.handoff_payload=decision['handoff_payload']
        return reply
    p=source.policy
    return result('answered',decision['answer'],'source_answer',intent=intent,
                  sources=[Source(id=p.id,version=p.version,as_of=p.as_of.isoformat(),
                                  valid_until=p.valid_until.isoformat(),synthetic=True)])
