from iceman.presentation import _explain_permission_stat_line


def test_explains_775_file_in_plain_english():
    lines = _explain_permission_stat_line(
        "-rwxrwxr-x 775 divine:divine /tmp/README.md"
    )

    text = "\n".join(lines)

    assert "Owner (divine)" in text
    assert "read the file" in text
    assert "modify the file" in text
    assert "run/execute the file" in text
    assert "Group (divine)" in text
    assert "Everyone else" in text
    assert "Numeric mode: 775" in text
    assert "everyone else cannot" in text
    assert "everyone else can also run" in text


def test_explains_644_file_without_execute():
    lines = _explain_permission_stat_line(
        "-rw-r--r-- 644 divine:divine /tmp/config.txt"
    )

    text = "\n".join(lines)

    assert "Numeric mode: 644" in text
    assert "modify the file" in text
    assert "run/execute the file" not in text


def test_warns_about_world_writable_file():
    lines = _explain_permission_stat_line(
        "-rwxrwxrwx 777 divine:divine /tmp/script.sh"
    )

    text = "\n".join(lines)

    assert "broadly writable" in text
    assert "Numeric mode: 777" in text


def test_explains_directory_permissions_as_directory_actions():
    lines = _explain_permission_stat_line(
        "drwxr-x--- 750 divine:developers /tmp/project"
    )

    text = "\n".join(lines)

    assert "Type: directory" in text
    assert "list its contents" in text
    assert "enter/traverse it" in text


def test_invalid_stat_output_is_ignored():
    assert _explain_permission_stat_line("garbled output") == []
