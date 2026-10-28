"""Synthetic policy source. It does not read company systems or create tickets."""
from datetime import datetime
from .models import PolicyDocument, PolicyQuery, PolicyResult


def policy(scenario, topic, channel, text):
    return PolicyDocument(id=f'DEMO-{scenario}-{topic}-{channel}', version='draft-1',
                          scenario=scenario, topic=topic, channel=channel, text=text,
                          as_of=datetime.fromisoformat('2026-10-23T00:00:00+07:00'),
                          valid_until=datetime.fromisoformat('2026-11-23T00:00:00+07:00'),
                          approval_state='synthetic_demo')


POLICIES = [
    policy('returns','steps','store', 'Учебный пример для покупки в магазине: подготовьте описание покупки и обратитесь к сотруднику для рассмотрения возврата. Это вымышленная инструкция, не правила Миртека.'),
    policy('returns','steps','website', 'Учебный пример для покупки на сайте: подготовьте описание заказа для сотрудника. Решение о возврате в демо не принимается. Это вымышленная инструкция, не правила Миртека.'),
    policy('returns','steps','marketplace', 'Учебный пример для покупки на маркетплейсе: порядок возврата задаёт площадка, в этом источнике его нет. Подготовьте номер заказа площадки и обратитесь к сотруднику. Решение о возврате в демо не принимается. Это вымышленная инструкция, не правила Миртека.'),
    policy('returns','documents','all', 'Учебный пример: сотруднику понадобится описание покупки и причины обращения. Конкретный перечень документов для реального возврата должен предоставить Миртек.'),
    policy('returns','conditions','all', 'Учебный пример: подтверждённого перечня условий возврата нет. Нельзя называть срок, исключения для отреза и обязанность принять товар. Индивидуальный случай передаётся сотруднику. Это вымышленная инструкция, не правила Миртека.'),
    policy('complaints','complaint_procedure','all', 'Учебный пример подачи жалобы: опишите, что произошло и какого решения ожидаете. Для рассмотрения нужен сотрудник. Жалоба этим сообщением не регистрируется. Это вымышленная инструкция.'),
    policy('complaints','complaint_review','all', 'Учебный пример: срок рассмотрения жалобы в этом источнике не указан. Называть количество дней нельзя. Для конкретного срока нужен сотрудник. Это вымышленная инструкция, не порядок Миртека.'),
]


def resolve(query: PolicyQuery) -> PolicyResult:
    matches=[p for p in POLICIES if p.scenario == query.scenario and p.topic == query.topic]
    generic=[p for p in matches if p.channel == 'all']
    specific=[p for p in matches if p.channel == query.purchase_channel]
    # A specific and a generic rule at once would need explicit precedence.
    if len(specific) + len(generic) > 1:
        return PolicyResult(status='unavailable')
    candidates=specific or generic
    if candidates:
        return PolicyResult(status='found', policy=candidates[0])
    if matches and query.purchase_channel is None:
        return PolicyResult(status='needs_context', missing_fields=['purchase_channel'])
    return PolicyResult(status='not_found')
