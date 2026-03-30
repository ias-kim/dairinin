"""
LangGraph Orchestrator — 에이전트 노드를 그래프로 연결.

    START → parser → has_event?
                       ├── yes → scheduler → conflict → notifier → END
                       └── no  → notifier (skip) → END
"""

from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.conflict import conflict_decision_node
from agents.notifier import notify_node
from agents.parser import parse_email_node
from agents.scheduler import schedule_check_node
from graph.state import ScheduleState


def after_parser(state: ScheduleState) -> str:
    """Parser 후 분기: 이벤트가 있으면 scheduler로, 없으면 notifier(skip)로."""
    if state.get("parsed_event") is not None:
        return "scheduler"
    return "notifier"


def build_graph() -> StateGraph:
    """StateGraph 조립."""
    graph = StateGraph(ScheduleState)

    # 노드 등록
    graph.add_node("parser", parse_email_node)
    graph.add_node("scheduler", schedule_check_node)
    graph.add_node("conflict", conflict_decision_node)
    graph.add_node("notifier", notify_node)

    # 엣지 연결
    graph.set_entry_point("parser")
    graph.add_conditional_edges(
        "parser",
        after_parser,
        {"scheduler": "scheduler", "notifier": "notifier"},
    )
    graph.add_edge("scheduler", "conflict")
    graph.add_edge("conflict", "notifier")
    graph.add_edge("notifier", END)

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            checkpointer = PostgresSaver.from_conn_string(database_url)
            checkpointer.setup()
        except Exception:
            checkpointer = MemorySaver()
    else:
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)
