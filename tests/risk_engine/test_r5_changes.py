from risk_engine.changes import describe_changes


def item(kind,event,quote,**extra):
    return {'signal_type':kind,'event_id':event,'active':True,'resolved':False,
            'evidence':[{'source':'contract','quote':quote,'rcept_no':'20250321802001','location':'contract'}],**extra}


def test_same_type_new_evidence_is_not_a_new_risk_type():
    old=[item('CB_BW','a','first')]
    new=[*old,item('CB_BW','b','second'),item('LAWSUIT','c','third')]
    changes=describe_changes('ORANGE',old,False,'ORANGE',new,False)
    assert {'kind':'evidence_added','signal_type':'CB_BW'} in changes
    assert {'kind':'new_type','signal_type':'LAWSUIT'} in changes
    assert {'kind':'new_type','signal_type':'CB_BW'} not in changes


def test_one_resolved_event_does_not_resolve_the_entire_stock():
    old=[item('CB_BW','a','first'),item('CB_BW','b','second')]
    new=[item('CB_BW','a','first',active=False,resolved=True),old[1]]
    changes=describe_changes('ORANGE',old,False,'ORANGE',new,False)
    assert changes==[{'kind':'resolved','scope':'event','signal_type':'CB_BW','event_id':'a'}]
