import logging

from apps.api import sync_worker


def test_worker_suppresses_transport_urls_but_keeps_failures_and_lifecycle(monkeypatch, caplog):
    transport_logger = logging.getLogger("httpx")
    monkeypatch.setattr(transport_logger, "level", logging.INFO)
    monkeypatch.setattr(sync_worker, "_repository_from_environment", lambda: object())
    monkeypatch.setattr(sync_worker, "_worker_id", lambda: "test-worker")
    monkeypatch.setattr(sync_worker.signal, "signal", lambda *args: None)
    monkeypatch.delenv("ONFLOWS_HISTORY_ARCHIVE_VERIFY", raising=False)

    def worker(*args, **kwargs):
        transport_logger.info("HTTP Request: GET https://example.invalid/?athlete_alias=private")
        transport_logger.warning("transport_warning")
        sync_worker.logger.info("sync_worker_job_finished outcome=ACTIVATED")
        return 0

    monkeypatch.setattr(sync_worker, "run_worker", worker)
    with caplog.at_level(logging.INFO):
        assert sync_worker.main(["--once"]) == 0
    assert "athlete_alias" not in caplog.text
    assert "HTTP Request" not in caplog.text
    assert "transport_warning" in caplog.text
    assert "sync_worker_job_finished" in caplog.text
