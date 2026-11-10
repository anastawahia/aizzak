"""Guard for the SQL template in the live message-status migration test (ث-2).

The live test renders ``_INSERT_MESSAGE`` with ``str.format``; an unescaped JSON
brace once made it raise ``KeyError`` only in CI. This renders it locally.
"""

from __future__ import annotations

from tests.integration.test_message_status_migration_live import _INSERT_MESSAGE


def test_insert_template_formats_without_extra_columns() -> None:
    sql = _INSERT_MESSAGE.format(extra_cols="", extra_vals="")
    assert '{"text": "t", "attachments": []}' in sql
    assert "{" not in sql.replace('{"text": "t", "attachments": []}', "")


def test_insert_template_formats_with_extra_columns() -> None:
    sql = _INSERT_MESSAGE.format(extra_cols=", status", extra_vals=", :status")
    assert "seq, status)" in sql
    assert ":seq, :status)" in sql
