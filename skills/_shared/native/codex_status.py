"""Read-only, content-free projection of Codex rollout observations.

Rollouts are an unstable local format (observed with codex-cli 0.157.1), not
an API. The caller must associate the file with its live harness first; this
module never searches sessions or guesses from modification time. Timestamps
describe observations, not freshness guarantees. Token accounting cannot prove
current context remaining, especially across compaction or an active turn.
"""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
from collections.abc import Iterable


_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
               "output_tokens", "reasoning_output_tokens", "total_tokens")


def _text(value):
    return value if isinstance(value, str) and value and len(value) <= 4096 else None


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _tokens(value):
    if not isinstance(value, dict):
        return None
    result = {key: _integer(value.get(key)) for key in _TOKEN_KEYS}
    return result if result["total_tokens"] is not None else None


def _window(value, observed_at, now):
    if not isinstance(value, dict):
        return None
    used = value.get("used_percent")
    if type(used) not in (float, int) or not 0 <= used <= 100 or not math.isfinite(used):
        used = None
    minutes = _integer(value.get("window_minutes"))
    reset = _integer(value.get("resets_at"))
    if minutes == 0:
        minutes = None
    if reset is not None and not 0 < reset <= 253402300799:  # latest UTC datetime
        reset = None
    observed = (datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
                if observed_at is not None else None)
    state = "unavailable"
    if (used is not None and minutes is not None and reset is not None
            and observed is not None and observed <= now and observed < reset):
        state = "expired" if reset <= now else "observed"
    return {"state": state, "historical_used_percent": used,
            "used_percent": used if state == "observed" else None,
            "remaining_percent": 100 - used if state == "observed" else None,
            "window_minutes": minutes, "resets_at": reset}


def parse_rollout(lines: Iterable[str | None], expected_session_id: str | None = None, *,
                  expected_source: str | None = None, now: datetime | None = None) -> dict:
    """Project complete JSONL records; ignore an unfinished trailing record.

    Requires an authoritative session_meta record first. A CLI fork may retain
    later inherited session_meta rows; they never replace that first identity.
    Malformed complete records and a
    mismatched expected UUID fail closed with no partial identity/status data.
    Unknown record types and all conversation content are discarded. A missing
    quota update preserves the last observation, not an invented fresh value.
    Expired/invalid quota windows expose historical usage separately and never
    expose remaining quota. ``now`` permits deterministic offline evaluation.
    The reader uses None for a skipped record: invalidate token accounting,
    without inventing a compaction event or changing the authoritative identity.
    """
    def failure(reason):
        return {"state": "unsupported", "source": "codex_rollout", "reason": reason}

    if expected_session_id is not None and (
            not isinstance(expected_session_id, str) or not _ID.fullmatch(expected_session_id)):
        return failure("invalid_expected_session_id")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        return failure("invalid_observation_time")
    evaluated_at = now.timestamp()
    result = {"state": "observed", "source": "codex_rollout", "session_id": None,
              "cwd": None, "model": None, "model_provider": None,
              "identity_observed_at": None, "model_observed_at": None,
              "token_usage": None, "quota_buckets": {},
              "context_remaining": None, "context_remaining_state": "unsupported",
              "compaction": {"event_count": 0, "last_timestamp": None}}
    incomplete = False
    initial_source = None
    for line in lines:
        if line is None:
            result["token_usage"] = None
            continue
        if incomplete:
            return failure("incomplete_nonfinal_record")
        if not line.endswith("\n"):
            incomplete = True
            continue  # the writer has not committed this JSONL record yet
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            return failure("malformed_record")
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            return failure("malformed_record")
        kind, payload = record.get("type"), record["payload"]
        stamp = _timestamp(record.get("timestamp"))
        if kind == "session_meta":
            if result["session_id"] is not None:
                if initial_source != "cli":
                    return failure("inherited_metadata_without_cli_root")
                continue
            sid = payload.get("id")
            if not isinstance(sid, str) or not _ID.fullmatch(sid):
                return failure("invalid_session_meta")
            if expected_session_id is not None and sid.lower() != expected_session_id.lower():
                return failure("session_id_mismatch")
            if expected_source is not None and payload.get("source") != expected_source:
                return failure("session_source_mismatch")
            initial_source = payload.get("source")
            result.update(session_id=sid.lower(), cwd=_text(payload.get("cwd")),
                          model_provider=_text(payload.get("model_provider")),
                          identity_observed_at=stamp)
        elif result["session_id"] is None:
            return failure("missing_session_meta")
        elif kind == "turn_context":
            result.update(model=_text(payload.get("model")), model_observed_at=stamp,
                          cwd=_text(payload.get("cwd")))
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                result["token_usage"] = {"observed_at": stamp,
                    "total": _tokens(info.get("total_token_usage")),
                    "last": _tokens(info.get("last_token_usage")),
                    "context_window": _integer(info.get("model_context_window"))}
            quota = payload.get("rate_limits")
            if isinstance(quota, dict):
                bucket = _text(quota.get("limit_id"))
                # A legacy snapshot without a bucket ID has explicitly unknown scope.
                result["quota_buckets"][bucket or "unknown"] = {
                    "limit_id": bucket, "limit_name": _text(quota.get("limit_name")),
                    "scope": "account", "provider": result["model_provider"],
                    "observed_at": stamp, "evaluated_at": now.isoformat(),
                    "primary": _window(quota.get("primary"), stamp, evaluated_at),
                    "secondary": _window(quota.get("secondary"), stamp, evaluated_at)}
        elif kind == "compacted":
            result["compaction"]["event_count"] += 1
            result["compaction"]["last_timestamp"] = stamp
            # Pre-compaction accounting cannot describe the new context.
            result["token_usage"] = None
    if result["session_id"] is None:
        return failure("missing_session_meta")
    return result


def read_codex_status(pid: int, home: Path | None = None, *, proc: Path = Path("/proc"),
                      max_bytes: int = 128 * 1024 * 1024,
                      max_line_bytes: int = 4 * 1024 * 1024) -> dict:
    """Read the unique writable CLI rollout held by the pinned native process.

    CODEX_HOME comes only from that process's environment, falling back to its
    HOME/.codex, then ``home/.codex``. Read-only picker descriptors do not establish ownership. An
    empty writable descriptor is unclassified, so identity remains temporarily
    unavailable rather than risking selection of an old root during a switch.

    Read the identity header plus at most max_bytes from the tail. Oversized
    non-header records are skipped and invalidate earlier token accounting;
    fields absent from the scanned window stay
    unknown. ``history_complete`` and ``compaction.count_scope`` distinguish
    scanned-window counts from complete-file counts. No whole-file scan is
    required for a large session. rollout_path is private integration metadata.
    """
    def failure(reason):
        return {"state": "unsupported", "source": "codex_rollout", "reason": reason}

    if type(pid) is not int or pid <= 0:
        return failure("invalid_pid")
    if (type(max_bytes) is not int or max_bytes <= 0
            or type(max_line_bytes) is not int or max_line_bytes <= 0):
        return failure("invalid_read_limits")
    candidates = {}
    try:
        process_dir = proc / str(pid)
        environment = dict(item.split(b"=", 1) for item in
                           (process_dir / "environ").read_bytes().split(b"\0") if b"=" in item)
        configured = environment.get(b"CODEX_HOME")
        if configured:
            codex_home = Path(configured.decode("utf-8", "surrogateescape"))
        else:
            pinned_home = environment.get(b"HOME")
            if pinned_home:
                home = Path(pinned_home.decode("utf-8", "surrogateescape"))
            else:
                home = Path.home() if home is None else home
            codex_home = home / ".codex"
        if not codex_home.is_absolute():
            return failure("relative_codex_home_unsupported")
        sessions = (codex_home / "sessions").resolve()
        for fd in (process_dir / "fd").iterdir():
            try:
                path = Path(os.readlink(fd))
            except FileNotFoundError:
                continue
            if not path.name.startswith("rollout-") or path.suffix != ".jsonl":
                continue
            if not path.resolve().is_relative_to(sessions):
                continue
            try:
                fields = dict(line.split(":", 1) for line in
                              (process_dir / "fdinfo" / fd.name).read_text().splitlines() if ":" in line)
                flags = int(fields["flags"].strip(), 8)
                if flags & os.O_ACCMODE == os.O_RDONLY:
                    continue
                descriptor_stat = fd.stat()
                if not stat.S_ISREG(descriptor_stat.st_mode):
                    return failure("invalid_rollout_file")
                with fd.open("rb") as stream:
                    descriptor_stat = os.fstat(stream.fileno())
                    if not stat.S_ISREG(descriptor_stat.st_mode):
                        return failure("invalid_rollout_file")
                    raw = stream.readline(max_line_bytes + 1)
            except FileNotFoundError:
                continue  # this descriptor demonstrably closed during discovery
            if not raw:
                return failure("unclassified_empty_writable_rollout")
            if len(raw) > max_line_bytes or not raw.endswith(b"\n"):
                return failure("invalid_session_meta")
            meta = json.loads(raw)
            if not isinstance(meta, dict) or meta.get("type") != "session_meta":
                return failure("invalid_session_meta")
            payload = meta.get("payload")
            if not isinstance(payload, dict):
                return failure("invalid_session_meta")
            if payload.get("source") != "cli":
                continue
            sid = payload.get("id")
            if not isinstance(sid, str) or not _ID.fullmatch(sid):
                return failure("invalid_session_meta")
            candidates[(descriptor_stat.st_dev, descriptor_stat.st_ino)] = (
                fd, path, sid, descriptor_stat.st_size)
        if len(candidates) != 1:
            return failure("ambiguous_or_missing_live_rollout")
        selected_identity, candidate = next(iter(candidates.items()))
        fd, path, sid, initial_size = candidate
        with fd.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != selected_identity:
                return failure("rollout_descriptor_changed")
            header = stream.readline(max_line_bytes + 1)
            if len(header) > max_line_bytes or not header.endswith(b"\n"):
                return failure("invalid_session_meta")
            if len(header) > initial_size:
                return failure("rollout_changed_during_read")
            start = max(len(header), initial_size - max_bytes)
            complete_history = start == len(header)
            stream.seek(start - 1)
            skipping = stream.read(1) != b"\n"
            skipped_records = False

            def lines():
                nonlocal skipping, skipped_records
                yield header.decode("utf-8")
                remaining = initial_size - start
                while remaining > 0:
                    raw = stream.readline(min(max_line_bytes + 1, remaining))
                    if not raw:
                        raise ValueError("truncated")
                    remaining -= len(raw)
                    if skipping or len(raw) > max_line_bytes:
                        skipping = not raw.endswith(b"\n")
                        skipped_records = True
                        yield None  # the skipped record may have been compaction
                        continue
                    if not raw.endswith(b"\n"):
                        break  # partial trailing UTF-8 is not yet a JSONL record
                    yield raw.decode("utf-8")
            result = parse_rollout(lines(), expected_session_id=sid, expected_source="cli")
            if os.fstat(stream.fileno()).st_size < initial_size:
                return failure("rollout_changed_during_read")
        if result["state"] == "observed":
            result["rollout_path"] = str(path)
            result["history_complete"] = complete_history and not skipped_records
            result["read_window"] = {"snapshot_bytes": initial_size, "tail_start": start,
                                     "tail_bytes": initial_size - start,
                                     "oversized_or_partial_leading_records_skipped": skipped_records}
            result["compaction"]["count_scope"] = (
                "complete_file" if result["history_complete"] else "scanned_window")
        return result
    except (OSError, ValueError, UnicodeError, KeyError, RuntimeError):
        return failure("unreadable_or_invalid_rollout")
