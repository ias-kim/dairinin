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

        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {
                "email_id": "msg_1",
                "parsed_event": mock_event,
                "confidence": 0.8,
            }
            mock_build.return_value = mock_graph

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
        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {
                "email_id": "msg_2",
                "parsed_event": None,
                "confidence": 0.0,
            }
            mock_build.return_value = mock_graph

            result = process_single_email({
                "id": "msg_2",
                "from": "boss@example.com",
                "subject": "Report",
                "snippet": "Please review the attached",
            })

        assert result["parsed_event"] is None

    def test_handles_graph_error(self):
        """graph.invoke 실패 → None 반환, 시스템 계속."""
        with patch("app.build_graph") as mock_build:
            mock_graph = MagicMock()
            mock_graph.invoke.side_effect = Exception("LLM timeout")
            mock_build.return_value = mock_graph

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


class TestRouteEmail:
    """route_email() — EmailClassifier 기반 분기 테스트."""

    def test_spam_archived(self):
        """spam → archive_email_logic 호출."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="spam"),
            patch("app.archive_email_logic") as mock_archive,
            patch("app.build_gmail_service", return_value=MagicMock()),
        ):
            route_email({"id": "msg_1", "from": "spam@evil.com", "subject": "WIN!", "snippet": "click now"})

        mock_archive.assert_called_once()

    def test_newsletter_labeled_and_skipped(self):
        """newsletter → add_label_logic 호출, LangGraph 미실행."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="newsletter"),
            patch("app.add_label_logic") as mock_label,
            patch("app.build_gmail_service", return_value=MagicMock()),
            patch("app.process_single_email") as mock_pipeline,
        ):
            route_email({"id": "msg_2", "from": "news@example.com", "subject": "Weekly", "snippet": "..."})

        mock_label.assert_called_once()
        mock_pipeline.assert_not_called()

    def test_important_sends_slack_notification(self):
        """important → Slack 알림 전송."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="important"),
            patch("app.send_reply_notification") as mock_slack,
            patch("app.build_slack_client", return_value=MagicMock()),
        ):
            route_email({"id": "msg_3", "from": "boss@example.com", "subject": "URGENT", "snippet": "..."})

        mock_slack.assert_called_once()

    def test_calendar_runs_pipeline(self):
        """calendar → process_single_email 실행."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="calendar"),
            patch("app.process_single_email") as mock_pipeline,
        ):
            route_email({"id": "msg_4", "from": "team@example.com", "subject": "Meeting", "snippet": "Let's meet"})

        mock_pipeline.assert_called_once()

    def test_other_skipped(self):
        """other → 아무것도 하지 않음."""
        from app import route_email

        with (
            patch("app.classify_email", return_value="other"),
            patch("app.process_single_email") as mock_pipeline,
        ):
            route_email({"id": "msg_5", "from": "x@x.com", "subject": "Thanks", "snippet": "ty"})

        mock_pipeline.assert_not_called()

    def test_classifier_error_falls_back_to_pipeline(self):
        """classifier 오류 → 안전하게 LangGraph 파이프라인으로 처리."""
        from app import route_email

        with (
            patch("app.classify_email", side_effect=Exception("LLM error")),
            patch("app.process_single_email") as mock_pipeline,
        ):
            route_email({"id": "msg_6", "from": "x@x.com", "subject": "Hi", "snippet": "hello"})

        mock_pipeline.assert_called_once()


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
