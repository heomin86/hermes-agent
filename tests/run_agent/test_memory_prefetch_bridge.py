from unittest.mock import MagicMock


def _bare_agent():
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent._session_db = MagicMock()
    agent._memory_prefetch_session_search_bridge = False
    agent._memory_prefetch_session_search_limit = 3
    return agent


def test_memory_prefetch_query_bridge_disabled_keeps_original_query():
    agent = _bare_agent()

    query = agent._build_memory_prefetch_query("remember my last setup?")

    assert query == "remember my last setup?"
    agent._session_db.search_messages.assert_not_called()


def test_memory_prefetch_query_bridge_requires_recall_intent():
    agent = _bare_agent()
    agent._memory_prefetch_session_search_bridge = True

    query = agent._build_memory_prefetch_query("write a new parser")

    assert query == "write a new parser"
    agent._session_db.search_messages.assert_not_called()


def test_memory_prefetch_query_bridge_adds_bounded_session_snippets():
    agent = _bare_agent()
    agent._memory_prefetch_session_search_bridge = True
    agent._memory_prefetch_session_search_limit = 2
    agent._session_db.search_messages.return_value = [
        {"snippet": ">>>memory<<< provider failed during sync"},
        {"content": "Earlier conversation about session_search bridge"},
        {"snippet": "ignored because limit is two"},
    ]

    query = agent._build_memory_prefetch_query("do you remember the memory issue?")

    assert query.startswith("do you remember the memory issue?")
    assert "Relevant prior session snippets for memory recall:" in query
    assert "provider failed during sync" in query
    assert "Earlier conversation about session_search bridge" in query
    assert "ignored because limit is two" not in query
    agent._session_db.search_messages.assert_called_once_with(
        "do you remember the memory issue?",
        limit=2,
    )


def test_memory_prefetch_query_bridge_falls_back_on_search_error():
    agent = _bare_agent()
    agent._memory_prefetch_session_search_bridge = True
    agent._session_db.search_messages.side_effect = RuntimeError("fts unavailable")

    query = agent._build_memory_prefetch_query("remember the old bug?")

    assert query == "remember the old bug?"
