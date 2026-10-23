"""Meaningful state changes; no score values leave this projection."""
import json
from risk_engine.loader import load_rules


def describe_changes(old_stage, old_items, old_resolved, new_stage, new_items, new_resolved):
    if old_stage is None:return []
    changes=[]
    if old_stage != new_stage:
        order=load_rules()['stage_priority']
        delta=order[new_stage]-order[old_stage]
        kind='stage_up' if delta<0 else 'stage_down' if delta>0 else 'stage_changed'
        changes.append({'kind':kind,'from':old_stage,'to':new_stage})
    before_types={i['signal_type'] for i in old_items if i.get('active')}
    after_types={i['signal_type'] for i in new_items if i.get('active')}
    for st in sorted(after_types-before_types):changes.append({'kind':'new_type','signal_type':st})
    def key(item):return (item.get('event_id'),item.get('signal_type'))
    previous={key(i):i for i in old_items}
    def citations(items,st):
        return {json.dumps(e,ensure_ascii=False,sort_keys=True) for i in items if i.get('signal_type')==st
                for e in i.get('evidence',[])}
    for st in sorted(after_types & before_types):
        if citations(new_items,st)-citations(old_items,st):
            changes.append({'kind':'evidence_added','signal_type':st})
    for item in new_items:
        old=previous.get(key(item))
        if item.get('resolved') and old and old.get('active') and not old.get('resolved'):
            changes.append({'kind':'resolved','scope':'event','signal_type':item['signal_type'],'event_id':item.get('event_id')})
    if new_resolved and not old_resolved:
        changes.append({'kind':'resolved','scope':'stock'})
    return changes
