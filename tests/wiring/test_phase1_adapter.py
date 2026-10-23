import asyncio
import pytest
from agent.adapters.phase1 import Phase1Model, EvidenceTools, make_phase1_classifier
from agent.state import FactsOutput, ExplanationOutput
from agent.execution import Execution, current_execution
from agent.ports import AgentError
from shared.schemas import ToolResponse

# Existing test citation; the following are contract responses, not new DART case labels.
E={'source':'stored-source','quote':'stored-quote','rcept_no':'20250321802001','location':'stored-location'}


@pytest.mark.parametrize('title,kind',[('부도발생','DEFAULT'),('회생절차개시신청','REHABILITATION'),
    ('영업정지','BUSINESS_SUSPENSION'),('소송등의제기','LAWSUIT'),('주요사항보고서(교환사채권발행결정)','CB_BW')])
def test_new_classification_reaches_phase1(title,kind):
    assert make_phase1_classifier()(title)==kind


def test_new_facts_are_allowed_only_for_the_matching_type():
    class Model:
        async def generate(self,prompt,schema,payload):
            assert 'phase1_signal_contract' in payload
            return schema.model_validate({'facts':[{'signal_type':'DEFAULT','values':{},'evidence':[E]},
                                                   {'signal_type':'PLEDGE','values':{},'evidence':[E]}]})
    result=asyncio.run(Phase1Model(Model()).generate('extract_facts',FactsOutput,
                       {'disclosure_type':'DEFAULT','chunks':[{**E,'text':E['quote']}]}))
    assert [f.signal_type for f in result.facts]==['DEFAULT']


def test_explanation_fallback_has_no_score():
    class Model:
        async def generate(self,prompt,schema,payload):
            return schema.model_validate({'items':[{'breakdown_index':0,'explanation':'설명','evidence':[E]}]})
    row={'item':'항목','points':5,'weight':1,'evidence':[E]}
    result=asyncio.run(Phase1Model(Model()).generate('explain_risk',ExplanationOutput,{'score_result':{'breakdown':[row,row]}}))
    assert '점' not in result.items[1].explanation and '반영률' not in result.items[1].explanation


class Harness:
    async def recover_tool(self,operation): return await operation()


@pytest.mark.parametrize('quote_exists',[True,False])
def test_historical_sources_are_retrieved_and_verified(quote_exists):
    class Tools:
        def __init__(self):self.calls=[]
        async def call(self,name,arguments):
            self.calls.append(name)
            if name=='calculate_risk_score':return ToolResponse(ok=True,data={'breakdown':[{'evidence':[E]}]})
            return ToolResponse(ok=True,data={'chunks':[{**E,'text':('prefix '+E['quote']+' suffix') if quote_exists else 'unrelated'}]})
    tools=Tools()
    async def run():
        token=current_execution.set(Execution(seconds=10,max_model_turns=8))
        try:return await EvidenceTools(tools,Harness()).call('calculate_risk_score',{'new_signals':[]})
        finally:current_execution.reset(token)
    if not quote_exists:
        with pytest.raises(AgentError,match='확인되지 않은 인용'):asyncio.run(run())
    else:
        result=asyncio.run(run())
        chunk=result.data['breakdown'][0]['source_chunks'][0]
        assert chunk['text']==E['quote'] and chunk['verified_chunk_sha256']
        assert 'prefix' not in str(result.data)  # no full raw RAG chunk in checkpoint-bound result
    assert tools.calls==['calculate_risk_score','search_disclosure_text']


def test_overseas_delisting_is_not_domestic_red():
    assert make_phase1_classifier()('해외증권시장주권등상장폐지결정')=='OVERSEAS_DELISTING'


def test_computed_financial_ratios_are_not_extracted_by_ai():
    from shared.rule_catalog import load_catalog
    allowed=load_catalog()['allowed']['PERIODIC_REPORT']
    assert not set(allowed) & {'OPERATING_CASH_FLOW','INTEREST_BURDEN','SHORT_TERM_DEBT'}
