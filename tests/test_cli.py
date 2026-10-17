"""CLI parser + logging-helper smoke tests (no network)."""
import logging

import pytest

from deep_research import cli, log_config


def test_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_scope_args():
    args = cli.build_parser().parse_args(["scope", "q", "--session-id", "abc", "--json"])
    assert args.command == "scope"
    assert args.query == "q"
    assert args.session_id == "abc"
    assert args.json is True


def test_research_requires_brief():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["research"])


def test_run_defaults():
    args = cli.build_parser().parse_args(["run", "topic"])
    assert args.command == "run"
    assert args.yes is False
    assert set(cli._DISPATCH) == {"scope", "research", "run"}


def test_setup_logging_and_kv(caplog):
    log_config.setup_logging("DEBUG")
    with caplog.at_level(logging.INFO, logger="test.smoke"):
        log = logging.getLogger("test.smoke")
        with log_config.timed(log, "demo=work", n=3):
            pass
    assert "demo=work start" in caplog.text
    assert "demo=work done" in caplog.text
    assert log_config.kv(a=1, b=None) == "a=1"
