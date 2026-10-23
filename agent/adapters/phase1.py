"""R4 bridge for the current R2 ports. No graph/prompt/source ownership changes."""
import hashlib
from agent.evidence import validate_evidence, chunks_from
from agent.execution import invoke_tool
from agent.model_adapter import best_chunk
from shared.presentation import public_explanation
from shared.rule_catalog import load_catalog
from shared.schemas import ToolResponse


def make_phase1_classifier():
    entries = load_catalog()['classification']
    def classify(title):
        title = ''.join(title.split())
        if title.startswith('[첨부추가]'):
            return None
        return next((entry['type'] for entry in entries if any(word in title for word in entry['keywords']) or (entry.get('all_keywords') and all(word in title for word in entry['all_keywords']))), None)
    return classify


def signal_input(extracted):
    return [{'signal_type': str(fact['signal_type']).strip().upper(), 'detail': dict(fact.get('values') or {}),
             'evidence': list(fact.get('evidence') or []),
             'rcept_no': fact['evidence'][0]['rcept_no'] if fact.get('evidence') else None}
            for fact in extracted.get('facts', [])]


class Phase1Model:
    def __init__(self, inner, harness=None):
        self.inner, self.harness = inner, harness

    def __getattr__(self, key):
        return getattr(self.inner, key)

    async def generate(self, prompt, schema, payload):
        catalog = load_catalog()
        if prompt == 'extract_facts':
            permitted = catalog['allowed'].get(payload.get('disclosure_type'), [])
            payload = {**payload, 'phase1_signal_contract': {k: catalog['signals'][k] for k in permitted if k in catalog['signals']}}
            if self.harness is not None:
                payload = await self.harness.prepare_input(payload)
        result = await self.inner.generate(prompt, schema, payload)
        if prompt == 'extract_facts':
            facts = []
            permitted = set(catalog['allowed'].get(payload.get('disclosure_type'), []))
            for fact in result.facts:
                data = fact.model_dump()
                data['signal_type'] = str(data['signal_type']).strip().upper()
                if data['signal_type'] not in permitted or data['signal_type'] not in catalog['signals']:
                    continue
                for evidence in data['evidence']:
                    chunk, quote = best_chunk(evidence, payload.get('chunks', []))
                    if chunk is not None:
                        evidence.update({k: chunk[k] for k in ('source','rcept_no','location')})
                        evidence['quote'] = quote
                facts.append(data)
            return schema.model_validate({'facts': facts})
        if prompt == 'explain_risk':
            rows = payload['score_result']['breakdown']
            by_index = {item.breakdown_index: item.model_dump() for item in result.items}
            items = []
            for index, row in enumerate(rows):
                item = by_index.get(index) or {'breakdown_index': index,
                    'explanation': f"[규칙 요약] {row.get('item') or row.get('signal_type')} 관련 근거를 확인해 주세요.",
                    'evidence': row.get('evidence', [])}
                item['explanation'] = public_explanation(item['explanation'])
                if row.get('evidence'):
                    item['evidence'] = row['evidence']
                items.append(item)
            return schema.model_validate({'items': items})
        return result


class EvidenceTools:
    """Historical citations must be re-fetched; never fabricate source_chunks from stored claims."""
    def __init__(self, inner, harness):
        self.inner, self.harness = inner, harness

    async def call(self, name, arguments):
        response = ToolResponse.model_validate(await self.inner.call(name, arguments))
        if name != 'calculate_risk_score' or not response.ok:
            return response
        current = {(e['source'], e['rcept_no'], e['location'], e['quote'])
                   for signal in arguments.get('new_signals', []) for e in signal.get('evidence', [])}
        fetched = {}
        for row in response.data.get('breakdown', []):
            excerpts = []
            for evidence in row.get('evidence', []):
                identity = tuple(evidence[k] for k in ('source','rcept_no','location','quote'))
                if identity in current:
                    continue
                key = (evidence['rcept_no'], evidence['quote'])
                if key not in fetched:
                    raw = await invoke_tool(self.inner, self.harness, 'search_disclosure_text',
                                            {'rcept_no': key[0], 'query': key[1], 'k': 5})
                    fetched[key] = chunks_from(raw)
                chunks = fetched[key]
                validate_evidence([evidence], chunks)
                chunk = next(c for c in chunks if c['source'] == evidence['source'] and c['rcept_no'] == evidence['rcept_no']
                             and c['location'] == evidence['location'] and evidence['quote'] in c['text'])
                start = chunk['text'].index(evidence['quote'])
                excerpts.append({k: chunk[k] for k in ('source','rcept_no','location')} | {
                    'text': chunk['text'][start:start+len(evidence['quote'])],
                    'verified_chunk_sha256': hashlib.sha256(chunk['text'].encode()).hexdigest()})
            # A row can have both a current and historical citation; all must be verifiable together.
            if excerpts:
                excerpts.extend({**e, 'text': e['quote']} for e in row['evidence']
                                if tuple(e[k] for k in ('source','rcept_no','location','quote')) in current)
                row['source_chunks'] = excerpts
        return response
