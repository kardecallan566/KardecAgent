from kardecagent.tools.web import search_web


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
