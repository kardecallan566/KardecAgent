from kardecagent.tools.web import _validate_url, search_web


def test_search_web_rejects_empty_query():
    try:
        search_web(" ")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_search_web_rejects_invalid_result_limit():
    try:
        search_web("python", max_results=21)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_web_blocks_non_http_scheme():
    try:
        _validate_url("file:///etc/passwd")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_web_blocks_localhost():
    try:
        _validate_url("http://localhost:8080")
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")


def test_web_blocks_private_ip():
    try:
        _validate_url("http://127.0.0.1/")
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")
