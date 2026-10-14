from typer.testing import CliRunner

from iceman import cli


runner = CliRunner()

FOOTER = (
    "Iceman can make mistakes and is under continuous development. "
    "Feedback, suggestions, and opinions are always welcome."
)


def normalized(text: str) -> str:
    """Ignore harmless terminal line wrapping and whitespace differences."""
    return " ".join(text.split())


def assert_footer_once(output: str) -> None:
    clean = normalized(output)
    assert FOOTER in clean
    assert clean.count(FOOTER) == 1


def test_version_subcommand_has_footer_once():
    result = runner.invoke(cli.app, ["version"])

    assert result.exit_code == 0, result.output
    assert "Iceman " in result.output
    assert_footer_once(result.output)


def test_one_shot_unsupported_reply_has_footer_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli.app,
        ["ask", "bake", "me", "a", "cake"],
    )

    assert result.exit_code == 1, result.output
    assert "not programmed" in result.output
    assert_footer_once(result.output)


def test_version_option_has_footer_once():
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0, result.output
    assert "Iceman " in result.output
    assert_footer_once(result.output)
