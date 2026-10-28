import json
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from .models import ParseResult, PolicyQuery, PolicyResult
from .mock_source import resolve
from .mock_api import app
from .workflow import decide
from .inventory import InventoryQuery, InventoryResult
from .reference import (
    DeliveryQuery, DeliveryResult, HoursQuery, HoursResult, PromotionQuery, PromotionResult,
    StoreCatalogQuery, StoreCatalogResult,
)
from app.semantic_models import SemanticResult

ROOT=Path(__file__).resolve().parent


def write(name,data):
    (ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def run():
    for name,model in [('parse-result',ParseResult),('policy-query',PolicyQuery),('policy-result',PolicyResult)]:
        write(name+'.schema.json',model.model_json_schema())
    for name,model in [('inventory-query',InventoryQuery),('inventory-result',InventoryResult),
                       ('store-catalog-query',StoreCatalogQuery),('store-catalog-result',StoreCatalogResult),
                       ('hours-query',HoursQuery),('hours-result',HoursResult),
                       ('promotion-query',PromotionQuery),('promotion-result',PromotionResult),
                       ('delivery-query',DeliveryQuery),('delivery-result',DeliveryResult)]:
        write(name+'.schema.json',model.model_json_schema())
    write('source-openapi.json',app.openapi())
    write('semantic-result.schema.json',SemanticResult.model_json_schema())
    examples=[]
    for text,kind,scenarios,topic,channel in [
        ('Как оформить возврат?','policy_question',['returns'],'steps',None),
        ('В магазине','policy_question',['returns'],'steps','store'),
        ('Какие документы нужны?','policy_question',['returns'],'documents',None),
        ('Хочу вернуть отрезанную ткань','individual_case',['returns'],None,'store'),
        ('Как оставить жалобу?','policy_question',['complaints'],'complaint_procedure',None),
        ('Мне нагрубили, хочу пожаловаться','individual_case',['complaints'],None,None),
        ('Привезли брак, хочу возврат и жалобу','individual_case',['returns','complaints'],None,'website'),
        ('Когда ответят на жалобу?','policy_question',['complaints'],'complaint_review',None),
        ('Хочу человека','operator_request',[],None,None),
    ]:
        parsed=ParseResult(scenarios=scenarios,request_kind=kind,topic=topic,purchase_channel=channel,uncertain=False)
        query=PolicyQuery(scenario=scenarios[0],topic=topic,purchase_channel=channel) if kind=='policy_question' else None
        source=resolve(query) if query else None
        result=decide(parsed,source,customer_text=text,now=datetime.fromisoformat('2026-10-23T12:00:00+07:00'))
        # Exported fixtures are deterministic; runtime request IDs remain random UUIDs.
        result['request_id']=str(uuid5(NAMESPACE_URL,'mirtek-synthetic-example:'+text))
        if result.get('handoff_payload'):
            result['handoff_payload']['request_id']=result['request_id']
        examples.append({'customer_text':text,'parse_fixture':parsed.model_dump(),
                         'source_request':query.model_dump() if query else None,
                         'source_response':source.model_dump(mode='json') if source else None,
                         'expected_decision':result})
    write('examples.json',{'label':'SYNTHETIC DESIGN EXAMPLES; NOT LLM OUTPUT OR COMPANY POLICY',
                           'fixture_time':'2026-10-23T12:00:00+07:00','examples':examples})


if __name__=='__main__': run()
