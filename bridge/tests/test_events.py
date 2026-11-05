from agent_controller import Agent, AgentEvent, Session, SessionState, rank_queue


def make(session_id: str, state: SessionState, since: float) -> Session:
    return Session(Agent.CLAUDE_CODE, session_id, state=state, since=since)


def test_needs_input_comes_first_then_oldest():
    queue = rank_queue(
        [
            make("working", SessionState.WORKING, since=1),
            make("new-question", SessionState.NEEDS_INPUT, since=20),
            make("done", SessionState.DONE, since=5),
            make("old-question", SessionState.NEEDS_INPUT, since=10),
            make("error", SessionState.ERROR, since=30),
        ]
    )
    assert [s.session_id for s in queue] == [
        "old-question",
        "new-question",
        "error",
        "done",
        "working",
    ]


def test_apply_resets_wait_time_only_on_state_change():
    session = make("a", SessionState.WORKING, since=0)
    session.apply(AgentEvent(Agent.CLAUDE_CODE, "a", SessionState.WORKING, at=5))
    assert session.since == 0
    session.apply(
        AgentEvent(Agent.CLAUDE_CODE, "a", SessionState.NEEDS_INPUT, summary="Run npm test?", at=9)
    )
    assert (session.state, session.since, session.summary) == (
        SessionState.NEEDS_INPUT,
        9,
        "Run npm test?",
    )
