"""Conservative lexical baseline, NOT a semantic AI model."""
import re
from dataclasses import dataclass
from typing import Protocol
from .models import Intent
from .semantic_models import SemanticResult


def normalized(text):
    return text.lower().replace('ё', 'е')


@dataclass
class Classification:
    intents: list[Intent]
    reason: str | None = None
    semantic: SemanticResult | None = None


class Classifier(Protocol):
    mode: str
    def classify(self, text: str, *, pending_intent: Intent | None = None, pending_context=None) -> Classification: ...


class RuleClassifier:
    mode = 'synthetic_rule_baseline'
    patterns = {
        'hours': r'режим|расписан|работа[ею]|работает|работают|открыва|закрыва|до скольки|до восьми|во сколько',
        'stock': r'налич|остат|артикул|\bесть\b|\bsku\b',
        'promotions': r'акци|скидк|спецпредлож',
        'delivery': r'достав|самовывоз|привез|забрать',
        'returns': r'возврат|вернуть|обмен',
        'complaints': r'жалоб|пожалова',
    }

    def classify(self, text, *, pending_intent=None, pending_context=None):
        text = normalized(text)
        if re.search(r'оператор|сотрудник|человек|менеджер', text):
            return Classification([], 'operator_requested')
        if re.search(r'заказ\s*№|статус заказ|трек|оплат|списа[лн]|брак|суд|персональн|игнорируй|system|prompt', text):
            return Classification([], 'requires_operator')
        intents = [intent for intent, pattern in self.patterns.items() if re.search(pattern, text)]
        # Generic "есть акции" or "есть доставка" does not assert a stock intent.
        if 'stock' in intents and not re.search(r'налич|остат|артикул|\bsku\b|\b\d{5}\b|ткан|лен|хлопок', text):
            if len(intents) > 1:
                intents.remove('stock')
        return Classification(intents)
