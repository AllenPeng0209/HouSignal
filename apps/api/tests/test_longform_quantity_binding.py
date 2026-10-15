"""Known project quantities gain precise citations; unknown or foreign ones fail."""
from copy import deepcopy
import json

import pytest

from app import provider
from test_longform_generation import generation_module,longform_input,ChapterModel,run_generation


class MissingCitationModel(ChapterModel):
    def __init__(self,*,unknown=False):
        super().__init__();self.unknown=unknown
    def __call__(self,messages,**kwargs):
        response=super().__call__(messages,**kwargs)
        request=json.loads(messages[-1]['content'])
        if request['stage']=='chapter' and request['chapter']['id']=='c1':
            draft=json.loads(response['choices'][0]['message']['content'])
            draft['shots'][0]['fact_refs']=[]
            draft['shots'][0]['start']=22
            if self.unknown:draft['shots'][0]['voiceover']=draft['shots'][0]['voiceover'].replace('420㎡','888㎡')
            response['choices'][0]['message']['content']=json.dumps(draft,ensure_ascii=False)
        return response


def test_known_quantity_receives_exact_current_fact_reference_and_image_start_zero(generation_module,longform_input,monkeypatch,tmp_path):
    monkeypatch.setattr(provider,'chat',MissingCitationModel())
    result=run_generation(generation_module,longform_input,tmp_path/'checkpoint')
    first=result['storyboard']['shots'][0]
    assert first['voiceover'].startswith('资料中的面积为420㎡')
    assert first['fact_refs']==[{'id':'f','revision':1}]
    assert first['start']==0


def test_unknown_quantity_is_hard_rejected_with_private_stage_diagnostic(generation_module,longform_input,monkeypatch,tmp_path):
    monkeypatch.setattr(provider,'chat',MissingCitationModel(unknown=True))
    directory=tmp_path/'checkpoint'
    with pytest.raises(ValueError,match='888|数字'):
        run_generation(generation_module,longform_input,directory)
    path=directory/'stage-failures.jsonl'
    assert path.is_file()
    record=json.loads(path.read_text().splitlines()[-1])
    assert record['stage']=='chapter'
    assert record['chapter_id']=='c1'
    assert record['shot_id']=='c1-shot-1'
    assert record['field']=='voiceover'
    assert record['quantity']=='888㎡'
    assert 'voiceover' not in record or not isinstance(record['voiceover'],str)


@pytest.mark.parametrize('mutation',['foreign_fact','stale_fact'])
def test_known_quantity_does_not_make_foreign_or_stale_citation_acceptable(generation_module,longform_input,monkeypatch,tmp_path,mutation):
    monkeypatch.setattr(provider,'chat',ChapterModel(mutation=mutation))
    with pytest.raises(ValueError,match='事实'):
        run_generation(generation_module,longform_input,tmp_path/'checkpoint')
