"""
LangGraph Orchestrator — 에이전트 노드를 그래프로 연결.

    START → parser → has_event?
                       ├── yes → scheduler → conflict → END
                       └── no  → END
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agents.conflict import conflict_decision_node
from agents.parser import parse_email_node
from agents.scheduler import schedule_check_node
from graph.state import ScheduleState


def after_parser(state: ScheduleState) -> str:
    """Parser 후 분기: 이벤트가 있으면 scheduler로, 없으면 종료."""
    if state.get("parsed_event") is not None:
        return "scheduler"
    return "end"


def build_graph() -> StateGraph:
    """StateGraph 조립."""
    graph = StateGraph(ScheduleState)

    # 노드 등록
    graph.add_node("parser", parse_email_node)
    graph.add_node("scheduler", schedule_check_node)
    graph.add_node("conflict", conflict_decision_node)

    # 엣지 연결
    graph.set_entry_point("parser")
    graph.add_conditional_edges(
        "parser",
        after_parser,
        {"scheduler": "scheduler", "end": END},
    )
    graph.add_edge("scheduler", "conflict")
    graph.add_edge("conflict", END)

    return graph.compile()
