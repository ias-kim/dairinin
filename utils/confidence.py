"""
이벤트 파싱 결과의 신뢰도를 계산한다.

전체 시스템에서의 역할:
    Parser Agent가 이메일 → EventJSON 변환 후 이 함수를 호출.
    반환된 점수가 시스템의 분기점을 결정:

        compute_confidence(event) ≥ 0.8  →  auto_register (자동 등록)
        compute_confidence(event) < 0.8  →  hitl_required (사람에게 확인)

의존성:
    utils.models.EventJSON → 이 함수의 입력
    이 함수 → agents/parser.py에서 호출됨
    이 함수 → graph/orchestrator.py에서 분기 조건으로 사용됨

점수 계산 방식:
    1. 만점 1.0에서 시작
    2. REQUIRED_FIELDS 누락 시 큰 감점 (0.6 가중치)
    3. OPTIONAL_FIELDS 누락 시 작은 감점 (0.2 가중치)
    4. 최소 0.0

왜 LLM 자기 평가를 안 쓰는가:
    LLM이 "이거 잘 파싱한 것 같아"라고 하는 건 miscalibrated.
    대신 "필수 필드가 채워졌느냐"를 규칙 기반으로 측정.
    이게 더 예측 가능하고 테스트 가능.
"""

from utils.models import EventJSON

# 필수: 이벤트의 핵심. 이것 없이는 캘린더에 등록 불가.
REQUIRED_FIELDS = ["title", "event_datetime"]

# 선택: 있으면 좋지만 없어도 등록 가능.
# attendees는 여기 — 솔로 이벤트(치과, 마감일)도 auto_register 되어야 하니까.
OPTIONAL_FIELDS = ["attendees", "location", "duration", "description"]

# 가중치: required 누락이 optional 누락보다 3배 무거움
REQUIRED_WEIGHT = 0.6
OPTIONAL_WEIGHT = 0.2


def compute_confidence(event: EventJSON) -> float:
    """이벤트 파싱 결과의 신뢰도를 0.0~1.0 사이로 계산.

    Args:
        event: Parser Agent가 이메일에서 추출한 EventJSON

    Returns:
        0.0~1.0 사이의 float.
        ≥ 0.8이면 자동 등록, < 0.8이면 사람에게 확인.

    사용 예:
        >>> event = EventJSON(title="회의", datetime=datetime(2026, 3, 29, 14, 0))
        >>> compute_confidence(event)
        0.8
    """
    # bool() 체크가 핵심 — None뿐 아니라 "", [], 0도 "없음"으로 처리
    # eng review issue #4에서 발견: getattr만으로는 Pydantic 빈 문자열 통과
    missing_required = sum(
        1 for f in REQUIRED_FIELDS if not bool(getattr(event, f, None))
    )
    missing_optional = sum(
        1 for f in OPTIONAL_FIELDS if not bool(getattr(event, f, None))
    )

    required_penalty = (missing_required / len(REQUIRED_FIELDS)) * REQUIRED_WEIGHT
    optional_penalty = (missing_optional / len(OPTIONAL_FIELDS)) * OPTIONAL_WEIGHT

    return max(0.0, round(1.0 - required_penalty - optional_penalty, 2))
