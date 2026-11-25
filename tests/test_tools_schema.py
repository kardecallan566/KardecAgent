import pytest
from kardecagent.llm.cleaner import extract_json_payload
from kardecagent.agent.tools_schema import ToolCallError, parse_tool_call
def test_valid(): assert parse_tool_call('{"tool":"read_file","arguments":{"path":"a.py"}}').tool == 'read_file'
def test_unknown_tool():
    with pytest.raises(ToolCallError, match='unknown tool'): parse_tool_call('{"tool":"bad","arguments":{}}')
def test_missing_arg():
    with pytest.raises(ToolCallError, match='missing required'): parse_tool_call('{"tool":"read_file","arguments":{}}')
def test_wrong_type():
    with pytest.raises(ToolCallError, match='must be str'): parse_tool_call('{"tool":"read_file","arguments":{"path":1}}')
def test_unknown_arg():
    with pytest.raises(ToolCallError, match='unknown argument'): parse_tool_call('{"tool":"finish","arguments":{"reason":"ok","criteria_evidence":["done"],"x":1}}')
def test_invalid_json():
    with pytest.raises(ToolCallError, match='invalid JSON'): parse_tool_call('not json')
def test_fence_accepted():
    action = parse_tool_call('```json\n{"tool":"read_file","arguments":{"path":"a.py"}}\n```')
    assert action.tool == 'read_file'

def test_validate_kind():
    assert parse_tool_call(
        '{"tool":"run_checks","arguments":{"kind":"validate"},"plan_step":1}'
    ).arguments["kind"] == "validate"


def test_invalid_check_kind():
    with pytest.raises(ToolCallError, match="must be one of"):
        parse_tool_call('{"tool":"run_checks","arguments":{"kind":"deploy"},"plan_step":1}')


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


def test_tool_json_with_literal_newlines_inside_file_content():
    raw = '{"tool":"write_file","plan_step":1,"arguments":{"path":"loucura.py","content":"def main():\n    print(\'ola\')\n"}}'
    action = parse_tool_call(raw.replace("\\n", "\n"))
    assert action.arguments["content"] == "def main():\n    print('ola')\n"


def test_tool_json_with_literal_tabs_inside_file_content():
    raw = '{"tool":"write_file","plan_step":1,"arguments":{"path":"a.py","content":"def main():\tpass"}}'
    action = parse_tool_call(raw.replace("\\t", "\t"))
    assert action.arguments["content"] == "def main():\tpass"



def test_open_file_requires_approved_plan_step():
    with pytest.raises(ToolCallError, match="plan_step is required"):
        parse_tool_call('{"tool":"open_file","arguments":{"path":"src/main.py"}}')
    action = parse_tool_call('{"tool":"open_file","arguments":{"path":"src/main.py"},"plan_step":1}')
    assert action.tool == "open_file"
    assert action.arguments["path"] == "src/main.py"


def test_arguments_json_string_is_normalized_to_object():
    import json
    encoded = json.dumps({"path": "a.txt", "content": "hello"})
    raw = json.dumps({"tool": "write_file", "arguments": encoded, "plan_step": 1})
    action = parse_tool_call(raw)
    assert action.arguments == {"path": "a.txt", "content": "hello"}


def test_arguments_nested_object_is_unwrapped():
    action = parse_tool_call('{"tool":"read_file","arguments":{"arguments":{"path":"a.py"}}}')
    assert action.arguments == {"path": "a.py"}


def test_non_object_arguments_reports_actual_type():
    with pytest.raises(ToolCallError, match="received list"):
        parse_tool_call('{"tool":"read_file","arguments":[]}')


def test_new_filesystem_tools_require_plan_step_and_validate_types():
    with pytest.raises(ToolCallError, match="plan_step is required"):
        parse_tool_call('{"tool":"create_directory","arguments":{"path":"src/new"}}')
    action = parse_tool_call('{"tool":"replace_in_file","arguments":{"path":"a.py","find":"x","replace":"y","replace_all":true},"plan_step":1}')
    assert action.arguments["replace_all"] is True
