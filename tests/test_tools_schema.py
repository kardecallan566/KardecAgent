import pytest
from kardecagent.agent.tools_schema import ToolCallError, parse_tool_call
def test_valid(): assert parse_tool_call('{"tool":"read_file","arguments":{"path":"a.py"}}').tool == 'read_file'
def test_unknown_tool():
    with pytest.raises(ToolCallError, match='unknown tool'): parse_tool_call('{"tool":"bad","arguments":{}}')
def test_missing_arg():
    with pytest.raises(ToolCallError, match='missing required'): parse_tool_call('{"tool":"read_file","arguments":{}}')
def test_wrong_type():
    with pytest.raises(ToolCallError, match='must be str'): parse_tool_call('{"tool":"read_file","arguments":{"path":1}}')
def test_unknown_arg():
    with pytest.raises(ToolCallError, match='unknown argument'): parse_tool_call('{"tool":"finish","arguments":{"reason":"ok","x":1}}')
def test_invalid_json():
    with pytest.raises(ToolCallError, match='invalid JSON'): parse_tool_call('not json')
def test_fence_rejected():
    with pytest.raises(ToolCallError, match='code fences'): parse_tool_call('```json\\n{}\\n```')

def test_validate_kind():
    assert parse_tool_call(
        '{"tool":"run_checks","arguments":{"kind":"validate"}}'
    ).arguments["kind"] == "validate"


def test_invalid_check_kind():
    with pytest.raises(ToolCallError, match="must be one of"):
        parse_tool_call('{"tool":"run_checks","arguments":{"kind":"deploy"}}')


def test_mutating_tool_requires_plan_step():
    with pytest.raises(ToolCallError, match="plan_step is required"):
        parse_tool_call('{"tool":"write_file","arguments":{"path":"a.txt","content":"x"}}')


def test_plan_step_is_parsed():
    action = parse_tool_call(
        '{"tool":"write_file","arguments":{"path":"a.txt","content":"x"},"plan_step":2}'
    )
    assert action.plan_step == 2


def test_invalid_plan_step():
    with pytest.raises(ToolCallError, match="positive integer"):
        parse_tool_call(
            '{"tool":"write_file","arguments":{"path":"a.txt","content":"x"},"plan_step":0}'
        )


def test_complete_step_schema():
    action = parse_tool_call(
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"tests pass"},"plan_step":1}'
    )
    assert action.arguments["step"] == 1


def test_plan_change_tool():
    action = parse_tool_call(
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"new","steps":["one"],"validation":["test"],"risks":[],"completion_criteria":["done"]}}}'
    )
    assert action.tool == "request_plan_change"
    assert action.arguments["plan"]["steps"] == ["one"]


def test_finish_requires_completion_evidence():
    with pytest.raises(ToolCallError, match="criteria_evidence"):
        parse_tool_call('{"tool":"finish","arguments":{"reason":"ok"}}')


def test_finish_accepts_completion_evidence():
    action = parse_tool_call('{"tool":"finish","arguments":{"reason":"ok","criteria_evidence":["tests passed"]}}')
    assert action.arguments["criteria_evidence"] == ["tests passed"]
