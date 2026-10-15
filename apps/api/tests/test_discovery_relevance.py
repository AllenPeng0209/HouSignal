"""Real-estate query matching keeps requested topics and cities observable."""

import pytest

from app.discovery import _matched_keywords


@pytest.mark.parametrize('query', ['房地产', '房地产相关的内容', '楼市', '置业'])
def test_broad_real_estate_queries_keep_housing_topics(query):
    # Removing a broad alias must not silently discard real housing references.
    assert _matched_keywords('苏州大平层户型实拍', query)


@pytest.mark.parametrize('title', ['苏州房地产市场观察', '苏州楼市成交观察', '苏州首次置业指南'])
def test_real_estate_titles_are_housing_evidence(title):
    assert _matched_keywords(title, '房地产')


@pytest.mark.parametrize('query', ['住宅 大平层', '房产 大平层', '房地产 大平层'])
def test_requested_property_type_cannot_be_replaced_by_generic_housing_word(query):
    assert _matched_keywords('苏州大平层户型实拍', query)
    assert _matched_keywords('苏州住宅装修实拍', query) == []


def test_each_requested_housing_topic_must_be_present():
    assert _matched_keywords('苏州豪宅户型讲解', '豪宅 户型')
    assert _matched_keywords('苏州豪宅花园实拍', '豪宅 户型') == []
    assert _matched_keywords('普通住宅户型讲解', '豪宅 户型') == []


def test_natural_query_filler_does_not_become_an_unintended_title_requirement():
    assert _matched_keywords('苏州大平层户型讲解', '苏州 大平层相关的内容')
    assert _matched_keywords('杭州大平层户型讲解', '苏州 大平层相关的内容') == []


@pytest.mark.parametrize('query', ['房地产', '住宅'])
@pytest.mark.parametrize('title', ['家居收纳好物推荐', '装修工具开箱', '游戏里的豪宅户型设计'])
def test_broad_property_search_rejects_decor_only_and_virtual_property(title, query):
    assert _matched_keywords(title, query) == []


def test_explicit_decor_search_can_keep_its_requested_topic():
    assert _matched_keywords('家居收纳好物推荐', '家居')
    assert _matched_keywords('装修工具开箱', '装修')


def test_city_and_audience_need_must_both_survive_query_matching():
    query = '苏州 住宅 采光'
    assert _matched_keywords('苏州大平层住宅采光实测', query)
    assert _matched_keywords('苏州大平层住宅户型实拍', query) == []
    assert _matched_keywords('杭州大平层住宅采光实测', query) == []


def test_city_and_need_query_does_not_require_the_original_spacing_in_title():
    assert _matched_keywords('苏州大平层住宅采光实测', '苏州 采光')
    assert _matched_keywords('杭州大平层住宅采光实测', '苏州 采光') == []
    assert _matched_keywords('苏州采光拍摄技巧', '苏州 采光') == []


@pytest.mark.parametrize('title', ['买房别踩这3个坑', '买房踩坑经历'])
def test_compound_buyer_pitfall_query_accepts_explicit_pitfall_wording(title):
    assert _matched_keywords(title, '买房避坑')


@pytest.mark.parametrize(('query', 'title'), [
    ('买房避坑', '买房装修灵感'),
    ('买房避坑', '汽车避坑经验'),
    ('户型动线', '大平层花园实拍'),
    ('实拍看房', '看房知识讲解'),
])
def test_compound_query_keeps_its_audience_need(query, title):
    assert _matched_keywords(title, query) == []


@pytest.mark.parametrize(('query', 'title'), [
    ('户型动线', '大平层户型动线实测'),
    ('实拍看房', '上海看房实拍'),
])
def test_compound_property_queries_accept_reordered_lexical_evidence(query, title):
    assert _matched_keywords(title, query)
