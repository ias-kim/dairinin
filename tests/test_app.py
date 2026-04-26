"""
FastAPI 앱 + 폴링 루프 테스트.

poll_gmail_loop의 핵심 로직:
    1. fetch_emails → 이메일 리스트
    2. 각 이메일에 대해 graph.invoke(state)
    3. 에러 발생해도 루프 계속 (시스템 죽으면 안 됨)
    4. 동시 실행 방지 (processing lock)
"""

import hashlib
import hmac
import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app, process_single_email
from utils.models import EventJSON


class TestProcessSingleEmail:
    """이메일 1건 처리 테스트."""

    def test_processes_event_email(self):
        """이벤트 이메일 → graph 실행 → 결과 반환."""
        mock_event = EventJSON(
            title="Lunch",
            event_datetime=datetime(2026, 3, 31, 12, 0),
        )

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "email_id": "msg_1",
            "parsed_event": mock_event,
            "confidence": 0.8,
        }

        with patch("app.get_graph", return_value=mock_graph):
            result = process_single_email({
                "id": "msg_1",
                "from": "kim@example.com",
                "subject": "Lunch?",
                "snippet": "Let's grab lunch at noon",
            })

        assert result is not None
        assert result["confidence"] == 0.8

    def test_skips_non_event_email(self):
        """일정 아닌 이메일 → graph 실행 → None 결과."""
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "email_id": "msg_2",
            "parsed_event": None,
            "confidence": 0.0,
        }

        with patch("app.get_graph", return_value=mock_graph):
            result = process_single_email({
                "id": "msg_2",
                "from": "boss@example.com",
                "subject": "Report",
                "snippet": "Please review the attached",
            })

        assert result["parsed_event"] is None

    def test_thread_id_is_passed_in_state(self):
        """graph.invoke() 호출 시 initial state에 _thread_id가 포함되어야 한다.

        버그: _thread_id가 config에는 있지만 state에 없으면
        notifier._handle_hitl()이 새 uuid를 생성 → hitl_pending에 잘못된
        thread_id 저장 → Slack ✅ 클릭 시 resume이 원래 그래프를 못 찾음.
        """
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {}

        with patch("app.get_graph", return_value=mock_graph):
            process_single_email({
                "id": "msg_thread",
                "from": "test@example.com",
                "subject": "Meeting",
                "snippet": "Let's meet",
            })

        invoke_call = mock_graph.invoke.call_args
        initial_state = invoke_call[0][0]  # 첫 번째 positional 인자
        config = invoke_call[1]["config"]  # keyword 인자

        config_thread_id = config["configurable"]["thread_id"]
        state_thread_id = initial_state.get("_thread_id")

        assert state_thread_id is not None, "_thread_id가 state에 없습니다"
        assert state_thread_id == config_thread_id, (
            f"thread_id 불일치: state={state_thread_id!r}, config={config_thread_id!r}\n"
            "HITL resume이 잘못된 그래프를 찾게 됩니다."
        )

    def test_handles_graph_error(self):
        """graph.invoke 실패 → None 반환, 시스템 계속."""
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = Exception("LLM timeout")

        with patch("app.get_graph", return_value=mock_graph):
            result = process_single_email({
                "id": "msg_3",
                "from": "test@example.com",
                "subject": "Test",
                "snippet": "test",
            })

        assert result is None


def _make_slack_signature(body: bytes, secret: str, ts: str) -> str:
    """테스트용 유효한 Slack 서명 생성."""
    sig_base = f"v0:{ts}:{body.decode()}".encode()
    return "v0=" + hmac.new(secret.encode(), sig_base, hashlib.sha256).hexdigest()


class TestSlackSigningSecretWarning:
    """앱 시작 시 SLACK_SIGNING_SECRET 미설정 경고."""

    def test_warns_on_startup_when_slack_secret_missing(self):
        """SLACK_SIGNING_SECRET 없이 앱 시작 시 WARNING 로그가 찍혀야 한다.

        prod에서 미설정 시 누구나 webhook을 호출할 수 있는 보안 위험.
        로그가 없으면 배포 후 뒤늦게 발견.
        """
        import logging

        with patch.dict("os.environ", {}, clear=True):
            with patch("app.logger") as mock_logger:
                # lifespan startup 로직 직접 호출 대신, 경고 함수를 직접 테스트
                from app import warn_if_slack_secret_missing
                warn_if_slack_secret_missing()

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "SLACK_SIGNING_SECRET" in warning_msg

    def test_no_warning_when_slack_secret_set(self):
        """SLACK_SIGNING_SECRET 설정 시 경고 없음."""
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "secret123"}):
            with patch("app.logger") as mock_logger:
                from app import warn_if_slack_secret_missing
                warn_if_slack_secret_missing()

        mock_logger.warning.assert_not_called()


class TestSlackWebhookHMAC:
    """Slack webhook HMAC 서명 검증 테스트."""

    def test_valid_signature_accepted(self):
        """유효한 Slack 서명 → 200 처리."""
        client = TestClient(app)
        secret = "test_slack_signing_secret"
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, secret, ts)

        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}):
            response = client.post(
                "/webhook/slack",
                content=body,
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": sig,
                    "Content-Type": "application/json",
                },
            )

        assert response.status_code == 200

    def test_invalid_signature_rejected(self):
        """잘못된 서명 → 403 반환."""
        client = TestClient(app)
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
        ts = str(int(time.time()))

        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "correct_secret"}):
            response = client.post(
                "/webhook/slack",
                content=body,
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": "v0=invalidsignature",
                    "Content-Type": "application/json",
                },
            )

        assert response.status_code == 403

    def test_missing_signature_rejected(self):
        """서명 헤더 없음 → 403 반환."""
        client = TestClient(app)
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()

        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "correct_secret"}):
            response = client.post(
                "/webhook/slack",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 403

    def test_replay_attack_rejected(self):
        """5분 이상 오래된 타임스탬프 → 403 반환 (리플레이 공격 방어)."""
        client = TestClient(app)
        secret = "test_secret"
        body = json.dumps({"type": "url_verification", "challenge": "xyz"}).encode()
        old_ts = str(int(time.time()) - 400)  # 6분 40초 전
        sig = _make_slack_signature(body, secret, old_ts)

        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}):
            response = client.post(
                "/webhook/slack",
                content=body,
                headers={
                    "X-Slack-Request-Timestamp": old_ts,
                    "X-Slack-Signature": sig,
                    "Content-Type": "application/json",
                },
            )

        assert response.status_code == 403

    def test_no_signing_secret_env_skips_check(self):
        """SLACK_SIGNING_SECRET 미설정 → 검증 스킵 (개발 환경 편의)."""
        client = TestClient(app)
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()

        with patch.dict("os.environ", {}, clear=True):
            # SLACK_SIGNING_SECRET 없으면 검증 스킵
            response = client.post(
                "/webhook/slack",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 200


class TestSlackInteract:
    """Slack 버튼 클릭 인터랙션 처리."""

    def test_interact_approve_schedules_resume(self):
        """approve 버튼 클릭 시 resume 작업이 스케줄되어야 한다."""
        client = TestClient(app)
        payload = {
            "actions": [
                {
                    "action_id": "hitl_approve",
                    "value": json.dumps({"email_id": "msg_approve"}),
                }
            ],
            "message": {"ts": "1234.5678"},
        }
        body = f"payload={json.dumps(payload)}".encode()
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, "test_secret", ts)

        mock_hitl = MagicMock()
        mock_hitl.lookup_by_slack_ts.return_value = {
            "thread_id": "thread-1",
            "email_id": "msg_approve",
        }

        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "test_secret"}),
            patch("agents.notifier.get_hitl_store", return_value=mock_hitl),
            patch("app._schedule_hitl_resume") as mock_schedule,
        ):
            response = client.post(
                "/webhook/slack/interact",
                content=body,
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": sig,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        assert response.status_code == 200
        mock_schedule.assert_called_once_with("1234.5678", "approve")

    def test_interact_invalid_signature_rejected(self):
        """잘못된 서명 → 403 반환."""
        client = TestClient(app)
        body = b"payload=%7B%7D"
        ts = str(int(time.time()))

        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "correct_secret"}):
            response = client.post(
                "/webhook/slack/interact",
                content=body,
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": "v0=invalidsignature",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        assert response.status_code == 403

    def test_interact_reject_schedules_resume(self):
        """reject 버튼 클릭 시 '무시됨' 응답 + resume 스케줄."""
        client = TestClient(app)
        payload = {
            "actions": [
                {
                    "action_id": "hitl_reject",
                    "value": json.dumps({"email_id": "msg_reject"}),
                }
            ],
            "message": {"ts": "9876.5432"},
        }
        body = f"payload={json.dumps(payload)}".encode()
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, "test_secret", ts)

        mock_hitl = MagicMock()
        mock_hitl.lookup_by_slack_ts.return_value = {
            "thread_id": "thread-2",
            "email_id": "msg_reject",
        }

        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "test_secret"}),
            patch("agents.notifier.get_hitl_store", return_value=mock_hitl),
            patch("app._schedule_hitl_resume") as mock_schedule,
        ):
            response = client.post(
                "/webhook/slack/interact",
                content=body,
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": sig,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        assert response.status_code == 200
        assert "무시됨" in response.json()["text"]
        mock_schedule.assert_called_once_with("9876.5432", "reject")


class TestResumeHitl:
    """HITL resume 경로."""

    def test_resume_uses_fresh_graph_instance(self):
        """resume은 싱글톤 대신 새 그래프 인스턴스로 수행해야 한다."""
        from app import _resume_hitl

        mock_hitl = MagicMock()
        mock_hitl.lookup_by_slack_ts.return_value = {
            "thread_id": "thread-resume",
            "email_id": "msg_resume",
        }
        mock_graph = MagicMock()

        with (
            patch("agents.notifier.get_hitl_store", return_value=mock_hitl),
            patch("app.build_graph", return_value=mock_graph) as mock_build,
        ):
            _resume_hitl("1234.5678", "approve")

        mock_build.assert_called_once()
        mock_graph.invoke.assert_called_once()
        mock_hitl.remove.assert_called_once_with("1234.5678")

    def test_resume_hitl_no_mapping_returns_early(self):
        """mapping 없으면 build_graph를 호출하지 않아야 한다."""
        from app import _resume_hitl

        mock_hitl = MagicMock()
        mock_hitl.lookup_by_slack_ts.return_value = None

        with (
            patch("agents.notifier.get_hitl_store", return_value=mock_hitl),
            patch("app.build_graph") as mock_build,
        ):
            _resume_hitl("no_such_ts", "approve")

        mock_build.assert_not_called()
        mock_hitl.remove.assert_not_called()


class TestRouteEmail:
    """route_email() — EmailClassifier 기반 분기 테스트."""

    @pytest.mark.asyncio
    async def test_spam_archived(self):
        """spam → archive_email MCP 툴 호출."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="spam"),
            patch("mcp_servers.gmail_mcp.archive_email_logic") as mock_archive,
            patch("mcp_servers.gmail_mcp.build_gmail_service", return_value=MagicMock()),
        ):
            mock_archive.return_value = True
            await route_email({"id": "msg_1", "from": "spam@evil.com", "subject": "WIN!", "snippet": "click now"})

        mock_archive.assert_called_once()

    @pytest.mark.asyncio
    async def test_newsletter_labeled_and_skipped(self):
        """newsletter → add_label MCP 툴 호출, LangGraph 미실행."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="newsletter"),
            patch("mcp_servers.gmail_mcp.add_label_logic") as mock_label,
            patch("mcp_servers.gmail_mcp.build_gmail_service", return_value=MagicMock()),
            patch("app.process_single_email") as mock_pipeline,
        ):
            mock_label.return_value = True
            await route_email({"id": "msg_2", "from": "news@example.com", "subject": "Weekly", "snippet": "..."})

        mock_label.assert_called_once()
        mock_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_important_sends_slack_notification(self):
        """important → Slack send_reply_notification_tool MCP 툴 호출."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="important"),
            patch("mcp_servers.slack_mcp.send_reply_notification") as mock_slack,
            patch("mcp_servers.slack_mcp.build_slack_client", return_value=MagicMock()),
            patch.dict("os.environ", {"SLACK_CHANNEL_ID": "C0123"}),
        ):
            mock_slack.return_value = True
            await route_email({"id": "msg_3", "from": "boss@example.com", "subject": "URGENT", "snippet": "..."})

        mock_slack.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_runs_pipeline(self):
        """calendar → process_single_email 실행."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="calendar"),
            patch("app.process_single_email") as mock_pipeline,
        ):
            await route_email({"id": "msg_4", "from": "team@example.com", "subject": "Meeting", "snippet": "Let's meet"})

        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_skipped(self):
        """other → 아무것도 하지 않음."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="other"),
            patch("app.process_single_email") as mock_pipeline,
        ):
            await route_email({"id": "msg_5", "from": "x@x.com", "subject": "Thanks", "snippet": "ty"})

        mock_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_classifier_error_with_schedule_content_runs_pipeline(self):
        """classifier 오류 + 일정 관련 내용 → 파이프라인 실행."""
        from app import route_email

        with (
            patch("app.classify_email", side_effect=Exception("LLM error")),
            patch("app.should_process_email", return_value=True),
            patch("app.process_single_email") as mock_pipeline,
        ):
            await route_email({
                "id": "msg_6",
                "from": "team@co.com",
                "subject": "3월 31일 팀 미팅",
                "snippet": "14:00에 회의실에서 만납시다",
            })

        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_classifier_error_with_no_schedule_content_skips_pipeline(self):
        """classifier 오류 + 일정 없는 내용 → 파이프라인 스킵.

        버그: 현재는 스팸/뉴스레터도 폴백 시 파이프라인을 탐.
        should_process_email()로 1차 필터링해야 함.
        """
        from app import route_email

        with (
            patch("app.classify_email", side_effect=Exception("LLM error")),
            patch("app.should_process_email", return_value=False),
            patch("app.process_single_email") as mock_pipeline,
        ):
            await route_email({
                "id": "msg_7",
                "from": "spam@evil.com",
                "subject": "당신이 당첨되었습니다!",
                "snippet": "지금 바로 클릭하세요",
            })

        mock_pipeline.assert_not_called()


class TestGraphSingleton:
    """build_graph()는 프로세스당 1회만 호출되어야 한다.

    매 요청마다 호출하면 PostgreSQL 연결이 요청 수만큼 열림.
    Railway 무료 플랜 연결 한도(~25개)를 초과할 수 있음.
    """

    def test_build_graph_called_once_for_multiple_emails(self):
        """이메일 3개 처리 시 build_graph는 1회만 호출되어야 한다."""
        import app as app_module
        from app import process_single_email

        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {}
            mock_build.return_value = mock_graph

            # 이전 싱글톤 캐시 초기화 (테스트 격리)
            original = getattr(app_module, "_graph_instance", None)
            app_module._graph_instance = None

            try:
                process_single_email({"id": "e1", "from": "a@a.com", "subject": "s1", "snippet": "t"})
                process_single_email({"id": "e2", "from": "b@b.com", "subject": "s2", "snippet": "t"})
                process_single_email({"id": "e3", "from": "c@c.com", "subject": "s3", "snippet": "t"})
            finally:
                app_module._graph_instance = original

        assert mock_build.call_count == 1, (
            f"build_graph()가 {mock_build.call_count}회 호출됨. "
            "이메일마다 새 DB 연결이 열립니다."
        )


class TestEmailDedup:
    """이메일 중복 처리 방지 테스트."""

    def test_same_email_not_processed_twice(self):
        """같은 email_id → 두 번째는 skip."""
        from app import is_already_processed, mark_processed, reset_processed_emails

        reset_processed_emails()
        assert not is_already_processed("msg_dup")
        mark_processed("msg_dup")
        assert is_already_processed("msg_dup")

    def test_different_emails_both_processed(self):
        """다른 email_id → 모두 처리."""
        from app import is_already_processed, mark_processed, reset_processed_emails

        reset_processed_emails()
        mark_processed("msg_a")
        assert not is_already_processed("msg_b")

    def test_reset_clears_all(self):
        """reset_processed_emails → 모두 초기화."""
        from app import is_already_processed, mark_processed, reset_processed_emails

        mark_processed("msg_x")
        reset_processed_emails()
        assert not is_already_processed("msg_x")

    def test_already_processed_falls_back_to_db_after_restart(self):
        """인메모리 set이 비워진 후에도 DB 로그가 있으면 True를 반환해야 한다.

        시나리오: 컨테이너 재시작 → _processed_emails 소실
        → 이미 처리된 이메일이 다시 처리되면 안 됨.
        """
        from app import is_already_processed, reset_processed_emails
        from db.email_log import EmailLogStore

        # DB에 이메일 처리 기록이 있는 상황
        mock_log = EmailLogStore()
        mock_log.log("msg_restart", action="auto_register")

        # 재시작 시뮬레이션: 인메모리 set 초기화
        reset_processed_emails()

        with patch("app.get_email_log_store", return_value=mock_log):
            result = is_already_processed("msg_restart")

        assert result is True, (
            "재시작 후 DB 로그를 확인하지 않아 이미 처리된 이메일이 재처리됩니다."
        )
