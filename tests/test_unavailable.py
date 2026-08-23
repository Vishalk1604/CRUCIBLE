"""An outage must not be able to impersonate an abstention.

This is the sharpest failure mode in the project, and it was found the hard way: Ollama
died mid-run, every extraction call failed with a connection error, `propose()` caught the
exception and returned an empty list, and the pipeline carried on producing perfectly
well-formed rows full of blank cells.

Those blanks are indistinguishable, after the fact, from the blanks the system produces on
purpose. The entire product claim is that an empty cell means "we looked and could not
establish this value". If an outage yields the same artifact, the claim is false whenever
the infrastructure hiccups, and nobody downstream can tell which kind of blank they have.
"""

from __future__ import annotations

import httpx
import pytest

from crucible.enrich import UNREACHABLE_LIMIT, ExtractionUnavailable, _refuse_if_unreachable
from crucible.extract.llm import ExtractionStats, _is_transport_failure


class TestTransportDetection:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
            httpx.RemoteProtocolError("closed"),
            ConnectionRefusedError(61, "refused"),
            ConnectionResetError("reset"),
        ],
    )
    def test_transport_failures_are_recognised(self, exc):
        assert _is_transport_failure(exc)

    @pytest.mark.parametrize(
        "exc",
        [ValueError("bad json"), KeyError("message"), RuntimeError("model exploded")],
    )
    def test_other_failures_are_not_transport(self, exc):
        assert not _is_transport_failure(exc)

    def test_wrapped_transport_failure_is_found_through_the_chain(self):
        # The ollama client wraps httpx errors, so the cause chain has to be walked.
        try:
            try:
                raise httpx.ConnectError("refused")
            except httpx.ConnectError as inner:
                raise RuntimeError("extraction failed") from inner
        except RuntimeError as outer:
            assert _is_transport_failure(outer)

    def test_cycle_in_the_cause_chain_terminates(self):
        first = RuntimeError("a")
        second = RuntimeError("b")
        first.__cause__ = second
        second.__cause__ = first
        assert _is_transport_failure(first) is False


class TestStats:
    def test_transport_failures_are_counted_apart_from_other_errors(self):
        stats = ExtractionStats(calls=10, transport_failures=3, call_errors=1)
        assert stats.transport_failures == 3
        assert stats.call_errors == 1

    def test_summary_reports_unreachable_calls(self):
        stats = ExtractionStats(calls=10, transport_failures=3)
        assert "unreachable" in stats.summary()

    def test_reached_the_model_is_false_when_every_call_failed(self):
        assert not ExtractionStats(calls=10, transport_failures=10).reached_the_model
        assert ExtractionStats(calls=10, transport_failures=1).reached_the_model
        assert not ExtractionStats().reached_the_model


class TestRefusal:
    def test_total_outage_stops_the_run(self):
        with pytest.raises(ExtractionUnavailable, match="never reached the model"):
            _refuse_if_unreachable(ExtractionStats(calls=42, transport_failures=42))

    def test_the_message_explains_why_blanks_would_lie(self):
        with pytest.raises(ExtractionUnavailable) as excinfo:
            _refuse_if_unreachable(ExtractionStats(calls=42, transport_failures=42))
        message = str(excinfo.value)
        assert "outage, not an assay" in message
        assert "ollama ps" in message  # tells the operator what to actually do

    def test_a_single_blip_is_survivable(self):
        # One lost product on a large catalog is not a reason to discard the run.
        _refuse_if_unreachable(ExtractionStats(calls=1000, transport_failures=1))

    def test_clean_run_passes(self):
        _refuse_if_unreachable(ExtractionStats(calls=100, transport_failures=0))

    def test_no_calls_is_not_an_outage(self):
        # Rules-only runs make no calls at all; that is a configuration, not a fault.
        _refuse_if_unreachable(ExtractionStats(calls=0))

    def test_threshold_boundary(self):
        at_limit = int(1000 * UNREACHABLE_LIMIT)
        _refuse_if_unreachable(ExtractionStats(calls=1000, transport_failures=at_limit))
        with pytest.raises(ExtractionUnavailable):
            _refuse_if_unreachable(ExtractionStats(calls=1000, transport_failures=at_limit + 1))
