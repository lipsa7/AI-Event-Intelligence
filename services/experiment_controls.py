"""Record code and non-secret model settings so A/B runs cannot silently change controls."""
from pathlib import Path
import hashlib,json,os

FILES = ('services/insight_evaluator.py','services/substance_audit.py','services/strategic_hypothesis_lab.py',
         'services/actor_moves.py','services/editorial_briefing.py','services/hypothesis_evidence_service.py',
         'services/gemini_llm_service.py','services/research_orchestrator_service.py','agents/extraction_agent_v6.py')
SETTINGS = ('GEMINI_STRATEGIC_MODEL','GEMINI_GROUNDING_MODEL','GEMINI_BRIEFING_MODEL',
            'EXTRACTION_V6_MODEL','EXTRACTION_V6_NER_MODEL','EXTRACTION_V6_REQUIRE_NER_ACTOR',
            'EVENT_INTELLIGENCE_HYPOTHESIS_LAB_RESEARCH')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

def snapshot(root=None, environ=None):
    root=Path(root) if root else Path(__file__).resolve().parents[1]
    environ=os.environ if environ is None else environ
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in FILES}
    # Never enumerate the environment: keys/tokens/passwords are not collected.
    settings={name:environ.get(name,'<default>') for name in SETTINGS}
    return {'signature':digest({'files':files,'settings':settings}),'code':digest(files),'settings':settings}

def problems(a,b):
    out=[]
    if a.get('control_signature') or b.get('control_signature'):
        if not a.get('control_signature') or not b.get('control_signature'):
            out.append('a run lacks the code/model control signature')
        elif a['control_signature'] != b['control_signature']:
            out.append('different code or model settings')
        if a.get('controls_stable') is not True or b.get('controls_stable') is not True:
            out.append('code/model settings changed during a run, or stability was not recorded')
    if a.get('replay_input_hash') or b.get('replay_input_hash'):
        if not a.get('replay_input_hash') or a.get('replay_input_hash') != b.get('replay_input_hash'):
            out.append('different checkpoint contents')
    return out
