"""Recovery for an OpenCode turn whose JSON stream ends before the terminal
``step_finish``/``error`` event lands on stdout (a truncated/racy exit). Two
layers: ``opencode export <thread>`` first, falling back to a direct read of
the local sqlite session database if the export's JSON is malformed.
Extracted verbatim from ``agent_cli_runner.py``.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path


class OpenCodeRecoveryMixin:
    """Reconstructs the current-turn events an interrupted OpenCode CLI missed."""

    def _recover_opencode_events(
        self,
        *,
        thread_id: str,
        observed_events: list[dict],
        options,
    ) -> tuple[list[dict], str | None]:
        """Recover a completed OpenCode turn when its JSON stream ends early."""
        message_id = ""
        for event in reversed(observed_events):
            if event.get("type") != "step_start":
                continue
            part = event.get("part")
            if not isinstance(part, dict):
                continue
            candidate = part.get("messageID")
            if isinstance(candidate, str) and candidate.strip():
                message_id = candidate.strip()
                break
        if not message_id:
            return [], "OpenCode stream ended before exposing the current message identity."

        command = [self._resolve_executable(self.agent_bin), "export", thread_id]
        try:
            exported = subprocess.run(
                command,
                cwd=options.working_dir or None,
                env=self._child_env(options),
                text=True,
                # UTF-8 avoids a cp1252 decode crash on Windows when the exported
                # session JSON contains non-Latin-1 model output.
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            return [], f"OpenCode session export failed: {exc}"
        if exported.returncode != 0:
            return (
                [],
                f"OpenCode session export exited with code {exported.returncode} "
                "after an incomplete event stream.",
            )
        try:
            payload = json.loads(exported.stdout)
        except json.JSONDecodeError:
            payload, database_error = self._recover_opencode_payload_from_database(
                thread_id=thread_id,
                message_id=message_id,
                options=options,
            )
            if database_error is not None:
                return (
                    [],
                    "OpenCode session export returned invalid JSON; "
                    f"database recovery failed: {database_error}",
                )
        if not isinstance(payload, dict):
            return [], "OpenCode session export returned an invalid payload."
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return [], "OpenCode session export did not contain messages."

        start_index: int | None = None
        for index, item in enumerate(messages):
            if not isinstance(item, dict):
                continue
            info = item.get("info")
            if (
                isinstance(info, dict)
                and info.get("role") == "assistant"
                and info.get("id") == message_id
            ):
                start_index = index
                break
        if start_index is None:
            return (
                [],
                "OpenCode session export did not contain the current assistant message.",
            )

        observed_part_ids = {
            part_id
            for event in observed_events
            if isinstance(event.get("part"), dict)
            for part_id in [event["part"].get("id")]
            if isinstance(part_id, str) and part_id
        }
        recovered: list[dict] = []
        for item in messages[start_index:]:
            if not isinstance(item, dict):
                continue
            info = item.get("info")
            if not isinstance(info, dict):
                continue
            role = info.get("role")
            if role == "user":
                break
            if role != "assistant":
                continue

            session_id = str(info.get("sessionID") or thread_id)
            parts = item.get("parts")
            parts = parts if isinstance(parts, list) else []
            has_finish_part = False
            for part in parts:
                if not isinstance(part, dict):
                    continue
                part_id = part.get("id")
                if isinstance(part_id, str) and part_id in observed_part_ids:
                    continue
                part_type = str(part.get("type") or "").strip()
                if part_type not in {"text", "step-finish", "step_finish"}:
                    continue
                if part_type in {"step-finish", "step_finish"}:
                    has_finish_part = True
                recovered.append(
                    {
                        "type": part_type.replace("-", "_"),
                        "sessionID": session_id,
                        "part": part,
                    }
                )

            error = info.get("error")
            if isinstance(error, dict) and error:
                recovered.append(
                    {
                        "type": "error",
                        "sessionID": session_id,
                        "error": error,
                    }
                )
                continue

            finish = str(info.get("finish") or "").strip()
            if finish and not has_finish_part:
                finish_part = {
                    "type": "step-finish",
                    "sessionID": session_id,
                    "messageID": info.get("id"),
                    "reason": finish,
                    "cost": info.get("cost"),
                    "tokens": info.get("tokens"),
                }
                recovered.append(
                    {
                        "type": "step_finish",
                        "sessionID": session_id,
                        "part": finish_part,
                    }
                )

        if not recovered:
            return [], "OpenCode session export contained no recoverable current-turn events."
        return recovered, None

    def _recover_opencode_payload_from_database(
        self,
        *,
        thread_id: str,
        message_id: str,
        options,
    ) -> tuple[dict | None, str | None]:
        """Read one interrupted turn when ``opencode export`` truncates stdout."""
        command = [self._resolve_executable(self.agent_bin), "db", "path"]
        try:
            located = subprocess.run(
                command,
                cwd=options.working_dir or None,
                env=self._child_env(options),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=10.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "OpenCode database path lookup timed out."
        except OSError as exc:
            return None, f"OpenCode database path lookup failed: {exc}"
        if located.returncode != 0:
            return None, (
                "OpenCode database path lookup exited with code "
                f"{located.returncode}."
            )
        path_lines = [line.strip() for line in located.stdout.splitlines() if line.strip()]
        if not path_lines:
            return None, "OpenCode database path lookup returned no path."
        database_path = Path(path_lines[-1]).expanduser()
        if not database_path.is_file():
            return None, f"OpenCode database does not exist at {database_path}."

        try:
            connection = sqlite3.connect(
                f"{database_path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            with connection:
                message_rows = connection.execute(
                    """
                    SELECT id, session_id, data
                    FROM message
                    WHERE session_id = ?
                    ORDER BY time_created, id
                    """,
                    (thread_id,),
                ).fetchall()
                start_index = next(
                    (
                        index
                        for index, row in enumerate(message_rows)
                        if row[0] == message_id
                    ),
                    None,
                )
                if start_index is None:
                    return None, (
                        "OpenCode database did not contain the current assistant "
                        "message."
                    )

                messages: list[dict] = []
                for stored_id, stored_session_id, raw_info in message_rows[start_index:]:
                    try:
                        info = json.loads(raw_info)
                    except (TypeError, json.JSONDecodeError):
                        return None, (
                            "OpenCode database contained invalid message metadata."
                        )
                    if not isinstance(info, dict):
                        return None, (
                            "OpenCode database contained invalid message metadata."
                        )
                    role = info.get("role")
                    if role == "user":
                        break
                    if role != "assistant":
                        continue

                    part_rows = connection.execute(
                        """
                        SELECT id, data
                        FROM part
                        WHERE session_id = ?
                          AND message_id = ?
                          AND json_extract(data, '$.type') IN (
                              'text',
                              'step-finish',
                              'step_finish'
                          )
                        ORDER BY time_created, id
                        """,
                        (stored_session_id, stored_id),
                    ).fetchall()
                    parts: list[dict] = []
                    for part_id, raw_part in part_rows:
                        try:
                            part = json.loads(raw_part)
                        except (TypeError, json.JSONDecodeError):
                            return None, (
                                "OpenCode database contained invalid message parts."
                            )
                        if not isinstance(part, dict):
                            return None, (
                                "OpenCode database contained invalid message parts."
                            )
                        part = dict(part)
                        part.setdefault("id", part_id)
                        part.setdefault("messageID", stored_id)
                        part.setdefault("sessionID", stored_session_id)
                        parts.append(part)

                    info = dict(info)
                    info.setdefault("id", stored_id)
                    info.setdefault("sessionID", stored_session_id)
                    messages.append({"info": info, "parts": parts})
        except sqlite3.Error as exc:
            return None, f"OpenCode database query failed: {exc}"
        finally:
            if "connection" in locals():
                connection.close()

        if not messages:
            return None, "OpenCode database contained no recoverable assistant messages."
        return {"messages": messages}, None
