"""UNIT suite: classified retry policy + health/config validation."""

from odar.health import liveness, validate_config
from odar.retry import FailureClass, RetryPolicy, classify_exception, classify_status
from odar.url_safety import UnsafeURLError


class TestFailureClassification:
    def test_status_classes(self):
        assert classify_status(429) is FailureClass.RATE_LIMITED
        assert classify_status(500) is FailureClass.TRANSIENT
        assert classify_status(503) is FailureClass.TRANSIENT
        assert classify_status(404) is FailureClass.PERMANENT
        assert classify_status(403) is FailureClass.PERMANENT
        assert classify_status(200) is FailureClass.UNKNOWN  # not a failure

    def test_exception_classes(self):
        assert classify_exception(TimeoutError("slow")) is FailureClass.TIMEOUT
        assert classify_exception(ConnectionError("down")) is FailureClass.TRANSIENT
        assert classify_exception(UnsafeURLError("ssrf")) is FailureClass.SECURITY


class TestRetryPolicy:
    def test_rate_limited_retries_with_backoff_and_jitter(self):
        policy = RetryPolicy(max_retries=3, base_delay=1.0)
        decision = policy.decide(FailureClass.RATE_LIMITED, attempt=0)
        assert decision.should_retry is True
        assert decision.delay_s >= 1.0  # base backoff (jitter only adds)

    def test_honors_retry_after_hint(self):
        policy = RetryPolicy(max_retries=3)
        decision = policy.decide(FailureClass.RATE_LIMITED, attempt=0, retry_after=7.5)
        assert decision.should_retry is True
        assert decision.delay_s >= 7.5

    def test_transient_bounded(self):
        policy = RetryPolicy(max_retries=2)
        assert policy.decide(FailureClass.TRANSIENT, attempt=0).should_retry is True
        assert policy.decide(FailureClass.TRANSIENT, attempt=2).should_retry is False

    def test_permanent_never_retried(self):
        policy = RetryPolicy(max_retries=9)
        assert policy.decide(FailureClass.PERMANENT, attempt=0).should_retry is False

    def test_security_failures_never_retried(self):
        policy = RetryPolicy(max_retries=9)
        assert policy.decide(FailureClass.SECURITY, attempt=0).should_retry is False


class TestHealthAndConfig:
    def test_liveness_ok(self):
        assert liveness().status == "OK"

    def test_config_validation_catches_bad_values(self):
        problems = validate_config({"budget": {"max_iterations": -1, "max_wall_clock_s": 0}, "model": "gpt"})
        assert len(problems) == 3
        assert any("max_iterations" in p for p in problems)

    def test_config_validation_accepts_good_config(self):
        problems = validate_config(
            {"budget": {"max_iterations": 10, "max_wall_clock_s": 60}, "model": "scripted"}
        )
        assert problems == []

    def test_llm_model_requires_key_in_config_check(self):
        import os

        if os.environ.get("ANTHROPIC_API_KEY"):
            return  # cannot test the negative branch with a key present
        problems = validate_config({"model": "llm"})
        assert any("ANTHROPIC_API_KEY" in p for p in problems)
