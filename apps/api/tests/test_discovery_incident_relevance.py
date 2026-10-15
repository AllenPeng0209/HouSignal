"""A house mentioned in a crime headline is not housing research evidence."""
import pytest

from app.discovery import _matched_keywords


@pytest.mark.parametrize('title,query',[
    ('4名枪匪闯入豪宅，中国男子4秒13枪疯狂反击！','豪宅'),
    ('住宅中的持枪劫匪被抓获','房地产'),
    ('豪宅绑架案始末','豪宅'),
])
def test_crime_headline_cannot_rank_as_a_residential_content_candidate(title,query):
    assert _matched_keywords(title,query)==[]


@pytest.mark.parametrize('title,query',[
    ('豪宅安防设计：如何防范劫匪入侵','豪宅'),
    ('住宅户型改善，家庭动线这样规划','住宅 改善'),
    ('房地产市场：买房前核对项目资料','房地产'),
])
def test_concrete_housing_decision_context_remains_searchable(title,query):
    assert _matched_keywords(title,query)
