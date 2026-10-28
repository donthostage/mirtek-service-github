import re
import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4
from .classifier import RuleClassifier, normalized
from .models import Reply
from .sources import SourceFailure
from .semantic_models import SemanticResult, Slots, resolve_mentioned_date

# Fixed offset avoids requiring an external tzdata package on Windows.
from datetime import timezone
KUZBASS = timezone(timedelta(hours=7))


class Contexts:
    """Opaque, bounded, short-lived pending dialogues; no client-supplied state."""
    def __init__(self, ttl=900, capacity=1000):
        self.ttl, self.capacity = ttl, capacity
        self.entries = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > time.monotonic():
                return entry[1].copy()
            self.entries.pop(key, None)
        return None

    def put(self, state):
        with self.lock:
            now = time.monotonic()
            self.entries = {k: v for k, v in self.entries.items() if v[0] > now}
            if len(self.entries) >= self.capacity:
                self.entries.pop(next(iter(self.entries)))
            key = str(uuid4())
            self.entries[key] = (now + self.ttl, state.copy())
            return key


class Service:
    def __init__(self, repository, classifier=None, clock=None, source_client=None):
        self.repository = repository
        self.classifier = classifier or RuleClassifier()
        self.clock = clock or (lambda: datetime.now(KUZBASS))
        self.contexts = Contexts()
        self.source_client = source_client

    @property
    def recognizer_version(self):
        return getattr(self.classifier,'prompt_version',
                       'lexical-1' if self.classifier.mode=='synthetic_rule_baseline' else 'intent-legacy-1')

    def process(self, message):
        started = time.perf_counter()
        now = self.clock()
        base = dict(request_id=str(uuid4()), evaluated_at=now.isoformat(), mode=self.classifier.mode,
                    data_backend='http_pull_mock' if self.source_client else 'snapshot',
                    recognizer_version=self.recognizer_version)

        def result(status, answer, reason, **kwargs):
            return Reply(**base, status=status, answer=answer, reason_code=reason,
                         operator_required=status == 'handoff',
                         elapsed_ms=round((time.perf_counter()-started)*1000, 3), **kwargs)

        def handoff(reason, intent=None):
            return result('handoff', 'Для достоверного ответа нужен оператор. В демоверсии обращение отмечено для передачи; сотруднику оно не отправлено.', reason, intent=intent)

        state = self.contexts.get(message.context_id) if message.context_id else None
        if message.context_id and state is None:
            return handoff('context_expired')
        # Reject oversized accumulated context before any paid external request.
        if len((state['query'] + ' ' if state else '') + message.text) > 6000:
            return handoff('context_limit', state['intent'] if state else None)
        pending_context=None
        if state:
            pending_context={'pending_field':state.get('pending_field'),
                             'topic':state.get('semantic_state',{}).get('topic'),
                             'known_slots':state.get('semantic_state',{}).get('slots',{})}
        parsed = self.classifier.classify(message.text, pending_intent=state['intent'] if state else None,
                                         pending_context=pending_context)
        if parsed.reason:
            return handoff(parsed.reason)
        if len(parsed.intents) > 1:
            reply=handoff('multiple_intents')
            reply.handoff_payload={'request_id':reply.request_id,'scenarios':parsed.intents,
                                   'customer_text':message.text,'reason_code':'multiple_intents'}
            return reply
        if parsed.intents:
            intent = parsed.intents[0]
            if state and state['intent'] != intent:
                state = None
            elif state and parsed.semantic and parsed.semantic.request_kind!='followup':
                # A complete new question must not silently inherit parameters from an older one.
                state = None
        elif state:
            intent = state['intent']
        else:
            return handoff('unsupported_intent')

        semantic=parsed.semantic
        if semantic is not None:
            values=semantic.model_dump()
            if semantic.request_kind=='followup':
                previous=state.get('semantic_state') if state else None
                if not previous:
                    return handoff('context_incompatible',intent)
                values['request_kind']=previous['request_kind']
                values['topic']=semantic.topic or previous['topic']
                values['slots']={k:v if v is not None else previous['slots'].get(k)
                                 for k,v in values['slots'].items()}
            values['intents']=[intent]
            try:
                semantic=SemanticResult.model_validate(values)
            except ValueError:
                return handoff('model_invalid_response',intent)
            if semantic.request_kind=='individual_case':
                reply=handoff('individual_review_required',intent)
                reply.handoff_payload={'request_id':reply.request_id,'scenarios':[intent],
                                       'customer_text':message.text,'reason_code':reply.reason_code}
                return reply

        text = normalized(message.text)
        # Carry forward only the pending question, not arbitrary earlier dialogue.
        query = normalized((state['query'] + ' ' if state else '') + message.text)
        original_query=(state.get('original_query',state['query'])+' ' if state else '')+message.text
        if len(query) > 6000:
            return handoff('context_limit', intent)
        selected_store = state.get('store_id') if state else None

        def clarify(answer, reason, **extra):
            count=state.get('clarification_count',0) if state else 0
            if self.source_client and count>=2:
                return handoff('clarification_limit',intent)
            context_id = self.contexts.put(dict(intent=intent, query=query, store_id=selected_store,
                                               original_query=original_query,clarification_count=count+1,
                                               pending_field=reason,semantic_state=semantic.model_dump() if semantic else {},**extra))
            return result('needs_clarification', answer, reason, intent=intent, context_id=context_id)

        try:
            if self.source_client and intent in ('stock','returns','complaints','hours','promotions','delivery'):
                from .pull_flow import remote_message
                return remote_message(self.source_client,intent=intent,text=text,query=query,state=state,
                                      customer_text=original_query,semantic=semantic,
                                      clock=self.clock,result=result,handoff=handoff,clarify=clarify)
            if intent=='complaints':
                return handoff('requires_operator')
            if intent in ('hours', 'stock'):
                stores, stores_meta = self.repository.read('stores', now)
                def find_stores(value):
                    return [s for s in stores if any(re.search(r'(?<!\w)' + re.escape(normalized(alias)) + r'(?!\w)', value) for alias in s['aliases'] + [s['id']])]
                store_text=normalized(semantic.slots.store.text) if semantic and semantic.slots.store else text
                hits = find_stores(store_text) or ([s for s in stores if s['id'] == selected_store] if selected_store else find_stores(query))
                if len(hits) > 1:
                    selected_store = None
                    return clarify('Укажите один магазин: Учебная, 1 или Примерная, 2.', 'ambiguous_store')
                if not hits:
                    return clarify('Какой учебный магазин: Учебная, 1 (S1) или Примерная, 2 (S2)?', 'missing_store')
                store = hits[0]
                selected_store = store['id']
                if intent == 'hours':
                    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', query)
                    if len(set(dates)) > 1 or ('завтра' in query and 'сегодня' in query):
                        return handoff('ambiguous_date', intent)
                    day = now.date() + timedelta(days=1 if 'завтра' in query else 0)
                    if semantic and semantic.slots.requested_date:
                        try:
                            day=resolve_mentioned_date(semantic.slots.requested_date.text,now.date())
                        except ValueError:
                            return handoff('unsupported_date',intent)
                    elif dates:
                        try:
                            day = datetime.strptime(dates[0], '%Y-%m-%d').date()
                        except ValueError:
                            return handoff('unsupported_date', intent)
                    elif re.search(r'понедель|вторник|сред[ау]|четверг|пятниц|суббот|воскрес|праздник|\b\d{1,2}[./]\d{1,2}\b|послезавтра|вчера|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр', query):
                        return handoff('unsupported_date', intent)
                    if day.isoformat() > store['schedule_until'] or day.isoformat() < store['schedule_from']:
                        return handoff('schedule_unconfirmed', intent)
                    hours = store['exceptions'].get(day.isoformat(), store['weekly'][day.weekday()])
                    answer = f"Учебный магазин {store['name']}, {day.isoformat()}: " + (f'{hours}.' if hours else 'закрыт.')
                    return result('answered', answer, 'source_answer', intent=intent, sources=[stores_meta])
                products, product_meta = self.repository.read('products', now)
                def find_products(value):
                    ids = re.findall(r'\b\d{5}\b', value)
                    return [p for p in products if p['sku'] in ids or any(re.search(r'(?<!\w)' + re.escape(normalized(a)) + r'(?!\w)', value) for a in p['aliases'])]
                skus = re.findall(r'\b\d{5}\b', text) or re.findall(r'\b\d{5}\b', query)
                products_found = find_products(text) or find_products(query)
                if any(sku not in {p['sku'] for p in products} for sku in skus):
                    return handoff('unknown_product', intent)
                if len(products_found) != 1:
                    return clarify('Укажите один учебный артикул: 10001 (хлопок) или 10002 (лён).', 'ambiguous_product' if products_found else 'missing_product')
                stock, stock_meta = self.repository.read('stock', now)
                product = products_found[0]
                row = next((r for r in stock if r['sku'] == product['sku'] and r['store_id'] == store['id']), None)
                if row is None:
                    return handoff('missing_stock', intent)
                if re.search(r'завтра|послезавтра|\d{4}-\d{2}-\d{2}|будет|недел', query):
                    return handoff('future_stock_unknown', intent)
                answer = f"Учебный остаток: {product['name']} (артикул {product['sku']}), {store['name']}: {row['quantity']} {product['unit']}. Это снимок данных, не резервирование."
                return result('answered', answer, 'source_answer', intent=intent, sources=[stores_meta, product_meta, stock_meta])
            records, meta = self.repository.read(intent, now)
            if intent == 'promotions':
                if semantic and semantic.slots.requested_date:
                    try:
                        requested=resolve_mentioned_date(semantic.slots.requested_date.text,now.date())
                    except ValueError:
                        return handoff('unsupported_date',intent)
                    if requested!=now.date(): return handoff('unsupported_date',intent)
                if re.search(r'завтра|вчера|недел|\d{4}-\d{2}-\d{2}', query):
                    return handoff('unsupported_date', intent)
                active = [r for r in records if r['start'] <= now.date().isoformat() <= r['end']]
                if re.search(r'\d|хлопок|лен|ткан|магазин|учебн|примерн', query):
                    return handoff('promotion_scope_unknown', intent)
                answer = '\n'.join(r['text'] for r in active) if active else 'В учебном источнике нет действующих акций.'
            else:
                # Only generic approved policy text: no individual promises or calculations.
                if re.search(r'\d|мой|моего|мою|мне|могу|успе|сегодня|завтра|цен|стоим|сколько|бесплат|срок|когда|адрес|\bв\s+[а-я]+', query):
                    return handoff('individual_policy_question', intent)
                answer = records['text']
            return result('answered', answer, 'source_answer', intent=intent, sources=[meta])
        except SourceFailure as exc:
            return handoff(exc.reason, intent)

    def availability(self, query):
        from .pull_flow import stock_fields
        start=time.perf_counter()
        try:
            if self.source_client is None: raise SourceFailure('source_unavailable')
            fields=stock_fields(self.source_client,query,self.clock)
        except SourceFailure as exc:
            fields=dict(status='handoff',reason_code=exc.reason,
                        answer='Не удалось подтвердить остаток. Нужен оператор; сообщение сотруднику не отправлено.')
        return Reply(request_id=str(uuid4()),intent='stock',evaluated_at=self.clock().isoformat(),
                     mode=self.classifier.mode,data_backend='http_pull_mock',
                     recognizer_version='not_used',
                     operator_required=fields['status']=='handoff',
                     elapsed_ms=round((time.perf_counter()-start)*1000,3),**fields)
