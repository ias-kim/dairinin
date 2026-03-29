"""
HITL Store 테스트.
"""

from db.hitl_store import HitlStore


class TestHitlStore:

    def test_insert_and_lookup(self):
        store = HitlStore()
        store.insert("1234.5678", "thread-1", "msg_1")

        result = store.lookup_by_slack_ts("1234.5678")
        assert result["thread_id"] == "thread-1"
        assert result["email_id"] == "msg_1"

    def test_duplicate_email_rejected(self):
        store = HitlStore()
        assert store.insert("1111", "thread-1", "msg_1") is True
        assert store.insert("2222", "thread-2", "msg_1") is False  # 같은 email_id

    def test_is_email_pending(self):
        store = HitlStore()
        store.insert("1111", "thread-1", "msg_1")

        assert store.is_email_pending("msg_1") is True
        assert store.is_email_pending("msg_2") is False

    def test_remove(self):
        store = HitlStore()
        store.insert("1111", "thread-1", "msg_1")
        store.remove("1111")

        assert store.lookup_by_slack_ts("1111") is None
        assert store.is_email_pending("msg_1") is False

    def test_cleanup_expired(self):
        store = HitlStore()
        store.insert("1111", "thread-1", "msg_1")

        # 0시간 TTL → 즉시 만료
        expired = store.cleanup_expired(ttl_hours=0)
        assert expired == 1
        assert store.lookup_by_slack_ts("1111") is None
