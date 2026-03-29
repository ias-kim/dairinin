"""
LangGraph Orchestrator — 에이전트 노드를 그래프로 연결.

현재 (Week 1):
    START → parser → should_continue 분기
                        ├── parsed_event 있음 → END (이후 Step에서 scheduler로 연결)
                        └── parsed_event 없음 → END

Week 2-3 완성형:
    START → parser → should_continue
                        ├── scheduler → conflict → notifier → END
                        └── END (이벤트 아닌 이메일)
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agents.parser import parse_email_node
from graph.state import ScheduleState


def should_continue(state: ScheduleState) -> str:
    """Parser 후 분기 결정.

    parsed_event가 있으면 다음 단계로, 없으면 종료.
    Week 2에서 scheduler 노드 추가 시 "continue" → "scheduler"로 변경.
    """
    if state.get("parsed_event") is not None:
        return "continue"
    return "end"


def build_graph() -> StateGraph:
    """StateGraph 조립.

    Returns:
        컴파일된 LangGraph (graph.invoke(state)로 실행)
    """
    graph = StateGraph(ScheduleState)

    # 노드 등록
    graph.add_node("parser", parse_email_node)

    # 엣지 연결
    graph.set_entry_point("parser")
    graph.add_conditional_edges(
        "parser",
        should_continue,
        {
            "continue": END,  # Week 2: → "scheduler"
            "end": END,
        },
    )

    return graph.compile()
