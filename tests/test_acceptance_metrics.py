from scripts.measure_acceptance import metric, decision_match, facts_match


def test_percentages_keep_difficult_cases_in_automation_denominator():
    assert metric(38,48,80)['passed'] is False
    assert metric(39,48,80)['passed'] is True
    assert metric(9,10,90)['percent']==90
    assert metric(0,0,90)['percent'] is None
    assert metric(0,0,90)['passed'] is False
    interval=metric(40,48,80)['ci95']
    assert interval[0]<80<interval[1]


def test_network_handoff_is_not_counted_as_correct_refund_understanding():
    case={'expected_status':'handoff','reasons':['individual_review_required']}
    actual={'status':'handoff','reason_code':'model_unavailable','operator_required':True,'handoff_delivered':False}
    assert not decision_match(case,actual,False)
    actual['reason_code']='individual_review_required'
    assert decision_match(case,actual,False)


def test_source_text_must_apply_to_question_and_be_fresh():
    case={'scenario':'delivery','reference':'terms'}
    truth={'terms':{'answer':'Учебная доставка от 300 ₽.','version':'1','source_id':'demo'}}
    actual={'status':'answered','intent':'delivery','answer':'Учебная доставка от 300 ₽.',
        'sources':[{'id':'demo','version':'1','synthetic':True,
                    'as_of':'2026-10-29T00:00:00+00:00','valid_until':'2026-10-30T00:00:00+00:00'}]}
    assert facts_match(case,actual,truth,'2026-10-29T12:00:00+00:00')
    assert not facts_match(case,actual,truth,'2026-10-31T00:00:00+00:00')
    actual['answer']+=' Доставка точно бесплатна.'
    assert not facts_match(case,actual,truth,'2026-10-29T12:00:00+00:00')
