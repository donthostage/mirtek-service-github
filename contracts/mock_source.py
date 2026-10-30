"""Synthetic policy source. It does not read company systems or create tickets."""
from datetime import datetime
from .models import PolicyDocument, PolicyQuery, PolicyResult


def policy(scenario, topic, channel, text):
    return PolicyDocument(id=f'DEMO-{scenario}-{topic}-{channel}', version='draft-3',
                          scenario=scenario, topic=topic, channel=channel, text=text,
                          as_of=datetime.fromisoformat('2026-10-23T00:00:00+07:00'),
                          valid_until=datetime.fromisoformat('2026-11-23T00:00:00+07:00'),
                          approval_state='synthetic_demo')


POLICIES = [
    policy('returns','steps','store', 'Учебный порядок возврата покупки из магазина:\n1. Подготовьте название товара, дату покупки и описание причины возврата.\n2. Соберите товар и имеющееся подтверждение покупки; если его нет, укажите это в обращении.\n3. Передайте товар на проверку в магазин покупки и согласуйте способ обратной связи.\nЭти шаги описывают подачу обращения, а не одобрение возврата или обещание выплаты. Инструкция вымышленная, не правила Миртека.'),
    policy('returns','steps','website', 'Учебный порядок возврата покупки с сайта:\n1. Найдите номер заказа и укажите товар, который хотите вернуть.\n2. Опишите причину обращения; для повреждения подготовьте фотографии, если они есть.\n3. Согласуйте способ передачи товара перед его отправкой.\nВ демо заявка не регистрируется и решение о выплате не принимается. Инструкция вымышленная, не правила Миртека.'),
    policy('returns','steps','marketplace', 'Учебный порядок для маркетплейса:\n1. Откройте покупку в личном кабинете площадки и найдите раздел возврата.\n2. Выберите товар, опишите причину и приложите запрошенные площадкой материалы.\n3. Используйте способ передачи товара, который предложит площадка.\nТочный интерфейс и условия зависят от площадки. Это вымышленная инструкция, не правила Миртека; заявка в демо не создаётся.'),
    policy('returns','documents','all', 'Учебный список сведений для обращения: товар, дата и место покупки, причина возврата, номер заказа или имеющееся подтверждение покупки. При повреждении можно приложить фотографии. Если части сведений нет, укажите это; автоматического отказа из-за отсутствующего документа демо не делает. Список вымышленный и требует подтверждения Миртеком.'),
    policy('returns','conditions','all', 'Учебные условия: общий порядок относится к неиспользованному товару из учебной покупки. Для обращения укажите товар, место и дату покупки, причину возврата. Повреждённый, использованный или отрезной товар требует отдельной проверки; API не одобряет приём товара и выплату. Эти условия вымышлены и не являются правилами Миртека.'),
    policy('complaints','complaint_procedure','all', 'Учебный порядок подачи жалобы:\n1. Укажите, где и когда произошла ситуация, и кратко опишите её.\n2. Добавьте номер покупки, если жалоба связана с заказом, и материалы, если они есть.\n3. Напишите, какого решения ожидаете и каким способом хотите получить ответ.\nЭто перечень для подготовки обращения; жалоба в демо не регистрируется, расследование и решение остаются за сотрудником. Инструкция вымышленная, не правила Миртека.'),
    policy('complaints','complaint_review','all', 'Учебный срок рассмотрения жалобы — до 3 рабочих дней после её регистрации. Статус конкретной жалобы этим источником не предоставляется. Срок вымышленный и не является обязательством или порядком Миртека.'),
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
