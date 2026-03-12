"""Tests for forge/security_scan.py."""

from pathlib import Path

from forge.security_scan import (
    Finding,
    scan_file,
    scan_project,
    should_skip_file,
    format_scan_results,
    run_security_scan,
    get_file_context,
    CRITICAL_PATTERNS,
    WARNING_PATTERNS,
)


def test_scan_file_detects_hardcoded_secret(tmp_path):
    """Finds hardcoded password in source file."""
    f = tmp_path / "config.py"
    f.write_text('DB_PASSWORD = "supersecretpassword123"\n', encoding="utf-8")
    findings = scan_file(f, tmp_path)
    critical = [x for x in findings if x.severity == "critical"]
    assert len(critical) >= 1
    assert critical[0].category == "hardcoded_secret"


def test_scan_file_detects_sql_injection(tmp_path):
    """Finds f-string in SQL query."""
    f = tmp_path / "db.py"
    f.write_text('cursor.execute("SELECT * FROM users WHERE id=" + user_id)\n',
                 encoding="utf-8")
    findings = scan_file(f, tmp_path)
    critical = [x for x in findings if x.severity == "critical"
                and x.category == "sql_injection"]
    assert len(critical) >= 1


def test_scan_file_detects_eval(tmp_path):
    """Finds eval() call."""
    f = tmp_path / "handler.py"
    f.write_text('result = eval(user_input)\n', encoding="utf-8")
    findings = scan_file(f, tmp_path)
    critical = [x for x in findings if x.severity == "critical"
                and x.category == "eval_usage"]
    assert len(critical) >= 1


def test_scan_file_returns_empty_for_clean_file(tmp_path):
    """No findings for file with no patterns."""
    f = tmp_path / "utils.py"
    f.write_text('def add(a, b):\n    return a + b\n', encoding="utf-8")
    findings = scan_file(f, tmp_path)
    assert len(findings) == 0


def test_should_skip_node_modules():
    """node_modules/ path is skipped."""
    root = Path("/project")
    p = Path("/project/node_modules/lodash/index.js")
    assert should_skip_file(p, root)


def test_should_skip_large_file(tmp_path):
    """Files over 500KB are skipped."""
    f = tmp_path / "big.js"
    f.write_bytes(b"x" * (501 * 1024))
    assert should_skip_file(f, tmp_path)


def test_should_skip_binary_file(tmp_path):
    """Non-text files are skipped."""
    f = tmp_path / "image.dat"
    f.write_bytes(b"\x00\x01\x02\xff\xfe\xfd" * 100)
    assert should_skip_file(f, tmp_path)


def test_scan_project_finds_critical(tmp_path):
    """scan_project() returns critical findings from files."""
    src = tmp_path / "src"
    src.mkdir()
    f = src / "config.ts"
    f.write_text('const API_KEY = "sk-abcdefghij1234567890abcdef";\n',
                 encoding="utf-8")
    findings = scan_project(tmp_path)
    critical = [x for x in findings if x.severity == "critical"]
    assert len(critical) >= 1


def test_scan_project_skips_test_files(tmp_path):
    """Files in tests/ directory are skipped."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    f = tests_dir / "test_config.py"
    f.write_text('PASSWORD = "testpassword12345678"\n', encoding="utf-8")
    findings = scan_project(tmp_path)
    assert len(findings) == 0


def test_format_scan_results_clean():
    """Clean scan formats as pass message."""
    result = format_scan_results([], [], [], 47)
    assert "passed" in result.lower()
    assert "47" in result


def test_format_scan_results_with_warnings():
    """Warnings formatted with count and descriptions."""
    warnings = [
        Finding("warning", "http_not_https", "src/api.ts", 14,
                'fetch("http://example.com")', "http://"),
    ]
    result = format_scan_results([], warnings, [], 10)
    assert "1 warning" in result
    assert "not blocking" in result


def test_format_scan_results_with_critical():
    """Critical findings formatted with block message."""
    critical = [
        Finding("critical", "hardcoded_secret", "src/config.ts", 22,
                'const KEY = "sk-abc123..."', "sk-pattern"),
    ]
    result = format_scan_results(critical, [], [], 10)
    assert "1 critical" in result
    assert "hardcoded_secret" in result


def test_run_security_scan_never_raises(monkeypatch, tmp_path):
    """run_security_scan catches all exceptions."""
    # Make scan_project raise
    monkeypatch.setattr(
        "forge.security_scan.scan_project",
        lambda root: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    confirmed, warnings, audit, usage = run_security_scan(tmp_path, run_audit=False)
    assert confirmed == []
    assert warnings == []
    assert audit == []


def test_get_file_context_returns_surrounding_lines(tmp_path):
    """Returns lines around the specified line number."""
    f = tmp_path / "example.py"
    lines = [f"line {i}" for i in range(1, 21)]
    f.write_text("\n".join(lines), encoding="utf-8")
    context = get_file_context(f, 10, context_lines=3)
    assert ">>> " in context  # flagged line marked
    assert "line 10" in context
    assert "line 7" in context
    assert "line 13" in context
