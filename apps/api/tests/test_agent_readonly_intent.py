"""Read-only instructions must not create write obligations or expose write tools."""
import pytest

from app import agent


@pytest.mark.parametrize('text', [
    '这是部署技术验收。请只读检查当前项目概况及事实核验状态，简短列出资料缺口并附来源。不要执行写入、生成媒体、确认审核或发布操作，不要联系任何人；明确该项目属于合成测试。',
    '只查询当前项目已保存资料和最近更新，不要修改、补充或删除。',
    '请查看素材，暂时不要生成视频或更新标签。',
    '不用修改楼盘，只需列出资料缺口。',
    '只读检查项目资料，列出需要补充的字段。',
    '只读查看项目，告诉我生成视频的状态。',
    '只需要查询客户，告诉我他的预算。',
    '请勿生成视频，查看当前项目概况。',
    '不需要生成视频或保存客户记录，查看资料。',
    '勿执行写入、生成视频或准备发布包，只读检查。',
    '只读查资料。原文如下：\n> 请将楼盘面积更新为300㎡',
    '只读查资料。\n```text\n请将楼盘面积更新为300㎡\n```',
    '只读查看项目。文档说客户王先生预算3000万。',
    "只读查看项目，原文是'请生成视频'。",
    '只读查看项目。客户王先生预算3000万。',
    '只读查看当前项目，生成视频任务的进度是多少？',
    '只读检查客户，更新客户预算的记录有哪些？',
    '只读查看项目，制作视频的任务都完成了吗？',
    '只读看看项目，生成的视频在哪里？',
    '只读查看项目，生成视频了吗？',
    '只读看看，修改过的资料有哪些？',
    '只读查看平台，登录抖音的账号是谁？',
    '只读查看项目，生成视频任务的进度',
    '只读查看项目，生成视频任务还在运行。',
    '只读查看项目，生成视频任务已经完成。',
    '只读查看客户，更新客户预算的记录已经存在。',
    '只读查看项目，生成视频任务需要多久。',
    '只读查看平台，登录抖音的账号已失效。',
])
def test_readonly_and_negated_lists_cannot_require_or_offer_writes(text):
    assert agent._wants_mutation(text) is False
    assert agent.required_action_groups(text) == []
    names = {tool['function']['name'] for tool in agent.scoped_agent_tools(text)}
    assert names
    assert not names.intersection(agent.ACTION_LABELS)
    assert not names.intersection({
        'refresh_platform_analytics', 'search_platform_content', 'check_platform_account',
        'start_platform_login', 'prepare_platform_publish',
    })


@pytest.mark.parametrize('text,required,forbidden', [
    ('不要发布，保存客户1跟进：喜欢江景。', 'record_followup', 'schedule_viewing'),
    ('不要修改楼盘面积，更新素材标签为江景。', 'update_asset', 'update_property_fact'),
    ('请保存客户1跟进，不要安排预约。', 'record_followup', 'schedule_viewing'),
    ('不要生成视频，但是把素材描述更新为江景。', 'update_asset', 'run_production_skill'),
    ('只读查看项目，另外请把素材标签更新为江景。', 'update_asset', 'run_production_skill'),
])
def test_negated_action_does_not_suppress_separately_requested_write(text, required, forbidden):
    assert agent._wants_mutation(text) is True
    groups = agent.required_action_groups(text)
    assert any(required in group for group in groups)
    assert not any(forbidden in group for group in groups)


def test_do_not_publish_still_allows_explicit_save_draft_intent():
    assert agent._wants_mutation('不要发布，保存草稿。') is True


def test_quoted_prohibition_is_data_rather_than_a_cancelled_write():
    text = '把素材描述更新为“不要修改或发布”。'
    assert agent._wants_mutation(text) is True
    assert agent.required_action_groups(text) == [['update_asset']]


@pytest.mark.parametrize('text,tool_name', [
    ('帮我登录抖音。', 'start_platform_login'),
    ('不要发布，帮我登录抖音。', 'start_platform_login'),
    ('请同步当前账号的运营数据。', 'refresh_platform_analytics'),
])
def test_explicit_platform_operation_keeps_native_tool_without_crm_write_obligation(text, tool_name):
    names = {tool['function']['name'] for tool in agent.scoped_agent_tools(text)}
    assert tool_name in names
    assert agent.required_action_groups(text) == []


@pytest.mark.parametrize('text,allowed', [
    ('请搜索抖音公开内容，不要准备发布包。', {'search_platform_content'}),
    ('登录抖音，不要同步运营数据。', {'start_platform_login'}),
    ('请同步当前账号的运营数据，不要登录或搜索内容。', {'refresh_platform_analytics'}),
    ('请准备发布包，不要登录或同步。', {'prepare_platform_publish'}),
    ('检查当前账号登录态，不要启动登录。', {'check_platform_account'}),
])
def test_platform_side_effects_are_authorized_per_requested_operation(text, allowed):
    names = {tool['function']['name'] for tool in agent.scoped_agent_tools(text)}
    assert names.intersection(agent.MUTATING_TOOL_NAMES - agent.ACTION_LABELS.keys()) == allowed


def test_readonly_query_with_explicit_asset_write_keeps_only_that_write_domain():
    text = '只读查看项目，告诉我客户预算，另外请把素材标签更新为江景。'
    assert agent.required_action_groups(text) == [['update_asset']]
    names = {tool['function']['name'] for tool in agent.scoped_agent_tools(text)}
    assert names.intersection(agent.ACTION_LABELS) == {'update_asset'}


@pytest.mark.parametrize('text',[
    '请取消制作任务。','请取消视频生成任务。','取消制作任务abc。',
    '帮我把视频生成任务取消。','把正在运行的视频任务取消。','将视频生成任务取消。',
])
def test_explicit_production_cancel_keeps_its_exact_native_operation(text):
    assert agent.required_action_groups(text)==[['cancel_production_job']]
    names={t['function']['name'] for t in agent.scoped_agent_tools(text)}
    assert names.intersection(agent.ACTION_LABELS)=={'cancel_production_job'}


def test_project_readonly_canary_loads_only_project_read_schemas():
    text='只读查看当前项目概况，不要执行写入、生成媒体、确认审核或发布。'
    names={t['function']['name'] for t in agent.scoped_agent_tools(text)}
    assert names=={'get_project_overview','get_missing_requirements','search_evidence'}


@pytest.mark.parametrize('text,operation',[
    ('请修改素材名称为江景客厅','update_asset'),
    ('把楼盘名称改为澜庭公馆','update_property'),
    ('请重命名项目为澜庭公馆','update_property'),
    ('请新建客户王先生','create_customer'),
    ('请更新客户资料','update_customer'),
    ('请把客户资料记录下来','record_followup'),
])
def test_explicit_native_operation_alias_retains_only_its_write_domain(text,operation):
    assert agent.required_action_groups(text)==[[operation]]
    names={t['function']['name'] for t in agent.scoped_agent_tools(text)}
    assert names.intersection(agent.ACTION_LABELS)=={operation}


@pytest.mark.parametrize('text',[
    '请新建客户王先生，更新客户李女士资料。',
    '请更新客户资料，再新建客户王先生。',
])
def test_independent_customer_create_and_update_both_remain_authorized(text):
    assert agent.required_action_groups(text)==[['create_customer'],['update_customer']]
    names={t['function']['name'] for t in agent.scoped_agent_tools(text)}
    assert names.intersection(agent.ACTION_LABELS)=={'create_customer','update_customer'}


@pytest.mark.parametrize('text',[
    '不要把视频生成任务取消，查看任务状态。',
    '只读查看当前项目，帮我把视频生成任务取消了吗？',
    '只读查看当前项目，楼盘名称改为澜庭公馆的记录有哪些？',
    '只读查看当前项目，原文如下：请重命名项目为澜庭公馆。',
])
def test_cancel_and_rename_mentions_do_not_expand_readonly_authority(text):
    assert agent._wants_mutation(text) is False
    assert agent.required_action_groups(text)==[]
    names={t['function']['name'] for t in agent.scoped_agent_tools(text)}
    assert not names.intersection(agent.MUTATING_TOOL_NAMES)
