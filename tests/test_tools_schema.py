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