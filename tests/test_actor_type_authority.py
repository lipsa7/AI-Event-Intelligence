"""NER, rather than generative output, owns actor types. Entirely offline."""
from unittest.mock import Mock,patch
import pytest
from agents.extraction_agent_v6 import ExtractionAgentV6, default_entity_tagger

def agent(tagger, strict=True):
    a=ExtractionAgentV6.__new__(ExtractionAgentV6)
    a.entity_tagger=tagger;a.require_ner_actor=strict
    return a

def test_ner_overrules_model_type():
    a=agent(lambda text:[(0,11,'PERSON')])
    assert a._actor_type('Alice Smith','Alice Smith signed.','government') == ('person','ner')

def test_missing_ner_never_trusts_model_even_with_opt_out():
    for model in ('organization','government','person','country'):
        assert agent(None)._actor_type('Acme','Acme signed.',model) == ('','')
        assert agent(None,False)._actor_type('Acme','Acme signed.',model) == ('unverified','unverified')

def test_overlap_or_embedded_entity_cannot_type_longer_actor():
    a=agent(lambda text:[(0,4,'ORG')])
    assert a._actor_type('Acme coalition','Acme coalition signed.','organization') == ('','')
    assert a._actor_type('Acme','AcmeCorp signed.','organization') == ('','')
    assert a._actor_type('Acme','Acme signed.','person') == ('organization','ner')

def test_ner_error_or_malformed_span_fails_closed():
    assert agent(Mock(side_effect=OSError('NER failed')))._actor_type('Acme','Acme signed.','organization') == ('','')
    for spans in ([('bad',4,'ORG')],[(0,400,'ORG')],[(0,4,'DATE')]):
        assert agent(lambda text:spans)._actor_type('Acme','Acme signed.','organization') == ('','')

def test_geography_does_not_become_a_government_by_model_assertion():
    assert agent(lambda text:[(0,9,'GPE')])._actor_type('Australia','Australia responded.','government') == ('country','ner')

def test_required_ner_stops_before_any_model_or_legacy_fallback_call():
    llm=Mock()
    with patch('agents.extraction_agent_v6.default_entity_tagger',return_value=None):
        a=ExtractionAgentV6(llm_service=llm,entity_tagger='auto',require_ner_actor=True,fallback_to_v5=True)
    with pytest.raises(RuntimeError,match='no extraction calls were made'):
        a.extract({'source_id':'SOURCE-X','title':'Acme signed.','body':'Acme signed an AI contract.'})
    llm.ask.assert_not_called()
    assert a.last_run_metadata['complete'] is False
    assert a.last_run_metadata['incomplete_reasons'] == ['required_ner_unavailable']

def test_already_installed_model_used_without_download(monkeypatch):
    import spacy
    monkeypatch.delenv('EXTRACTION_V6_NER_MODEL',raising=False)
    loaded=[]
    fake=Mock(return_value=Mock(ents=[]))
    monkeypatch.setattr(spacy,'load',lambda name:loaded.append(name) or fake)
    with patch('importlib.util.find_spec',side_effect=lambda name: None if name=='en_core_web_sm' else object()):
        assert callable(default_entity_tagger())
    assert loaded == ['en_core_web_trf']

def test_explicit_ner_selection_is_never_silently_changed(monkeypatch):
    import spacy
    monkeypatch.setenv('EXTRACTION_V6_NER_MODEL','custom_model')
    loaded=[]
    def load(name):loaded.append(name);raise OSError('missing')
    monkeypatch.setattr(spacy,'load',load)
    assert default_entity_tagger() is None
    assert loaded == ['custom_model']
