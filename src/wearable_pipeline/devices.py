"""Participant-device layer and ``gold_participant_devices`` (contract: specs/INC-011.md)."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

TABLE_NAME = "gold_participant_devices"
SILVER_TABLE = "silver_device_metadata"
SOURCE = "device_metadata"
PARTICIPANTS_PATH = "participants/participants.csv"
METADATA_PATH = "health_summaries/device_metadata.csv"

DEVICE_ID_PATTERN = re.compile(r"FBT-\d{4}-[A-Z0-9]{8}")
FIRMWARE_PATTERN = re.compile(r"(?<!\w)fw(\d+(?:\.\d+)+)(?!\w|\.\d)")

SOURCE_METADATA = "metadata"
SOURCE_LABEL = "device_label"
SOURCE_NONE = "none"

DEVICE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("participant_id", "VARCHAR"),
    ("device_id", "VARCHAR"),
    ("firmware_version", "VARCHAR"),
)
SILVER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("participant_id", "VARCHAR"),
    ("device_id", "VARCHAR"),
    ("firmware_version_raw", "VARCHAR"),
    ("device_label", "VARCHAR"),
    ("firmware_version_parsed", "VARCHAR"),
    ("firmware_version", "VARCHAR"),
    ("firmware_source", "VARCHAR"),
    ("quality_flags", "VARCHAR"),
)

MISMATCH = "DEVICE_ID_MISMATCH"
SHARED = "DEVICE_SHARED"
MALFORMED = "DEVICE_ID_MALFORMED"
RECOVERED = "FIRMWARE_RECOVERED_FROM_LABEL"
UNRECOVERABLE = "FIRMWARE_UNRECOVERABLE"

# rule_id -> (source, handling, severity, reason); merged into pipeline.RULES by the implementer
DEVICE_RULES: dict[str, tuple[str, str, str, str]] = {
    MISMATCH: (
        SOURCE, "flag", "high",
        "device_id in participants.csv has no matching row in device_metadata.csv"),
    SHARED: (
        SOURCE, "flag", "high",
        "device_id in participants.csv is used by more than one participant_id"),
    MALFORMED: (
        SOURCE, "flag", "medium",
        "device_id in participants.csv does not match FBT-NNNN-XXXXXXXX"),
    RECOVERED: (
        SOURCE, "convert", "low",
        "firmware_version is blank and was parsed from device_label"),
    UNRECOVERABLE: (
        SOURCE, "flag", "medium",
        "firmware_version is blank and device_label carries no version, so it stays NULL"),
}


@dataclass(frozen=True)
class DeviceLayer:
    """Silver rows, gold rows and quality outcomes of the participant-device derivation."""

    silver: list[tuple[Any, ...]]  # SILVER_COLUMNS, one row per device_metadata.csv row, file order
    gold: list[tuple[Any, ...]]  # DEVICE_COLUMNS, one row per participant_id, sorted by it
    rejects: list[tuple[str, str, int, dict[str, str]]]  # (rule_id, path, 1-based row, raw row)
    failed: Counter[str]  # rule_id -> number of participants failing it
    checked: int  # distinct participant_id values in participants.csv


def parse_firmware_label(label: str | None) -> str | None:
    """First ``fw<dotted version>`` token of a device label, or ``None``."""
    match = FIRMWARE_PATTERN.search(label or "")
    return match.group(1) if match else None


def resolve_firmware(firmware_version: str | None,
                     device_label: str | None) -> tuple[str | None, str]:
    """Final firmware version and its source (``SOURCE_*``): the stripped column value when it
    is not blank, else the version parsed from the label, else ``(None, SOURCE_NONE)``."""
    stripped = (firmware_version or "").strip()
    if stripped:
        return stripped, SOURCE_METADATA
    parsed = parse_firmware_label(device_label)
    if parsed is not None:
        return parsed, SOURCE_LABEL
    return None, SOURCE_NONE


def build_device_layer(
    participants: Sequence[Mapping[str, str]],
    metadata: Sequence[Mapping[str, str]],
) -> DeviceLayer:
    """Derive silver and gold device rows plus quality outcomes from the two CSV files' rows."""
    silver: list[tuple[Any, ...]] = []
    resolved: list[tuple[str | None, str]] = []
    by_participant: dict[str, list[int]] = {}
    for index, row in enumerate(metadata):
        raw = row.get("firmware_version") or ""
        label = row.get("device_label") or ""
        value, source = resolve_firmware(raw, label)
        resolved.append((value, source))
        flag = {SOURCE_LABEL: RECOVERED, SOURCE_NONE: UNRECOVERABLE}.get(source)
        silver.append((row["participant_id"], row["device_id"], raw, label,
                       parse_firmware_label(label), value, source, flag))
        by_participant.setdefault(row["participant_id"], []).append(index)

    first: dict[str, int] = {}
    users: dict[str, set[str]] = {}
    for index, row in enumerate(participants):
        first.setdefault(row["participant_id"], index)
        users.setdefault(row["device_id"], set()).add(row["participant_id"])

    gold: list[tuple[Any, ...]] = []
    rejects: list[tuple[str, str, int, dict[str, str]]] = []
    failed: Counter[str] = Counter()
    for pid in sorted(first):
        p_index = first[pid]
        device_id = participants[p_index]["device_id"]
        rows = by_participant.get(pid, [])
        matching = [i for i in rows if metadata[i]["device_id"] == device_id]
        chosen = matching[0] if matching else (rows[0] if rows else None)
        firmware = resolved[chosen][0] if chosen is not None else None
        source = resolved[chosen][1] if chosen is not None else SOURCE_NONE
        gold.append((pid, device_id, firmware))

        def reject(rule: str, on_metadata: bool) -> None:
            failed[rule] += 1
            if on_metadata and chosen is not None:
                rejects.append((rule, METADATA_PATH, chosen + 1, dict(metadata[chosen])))
            else:
                rejects.append((rule, PARTICIPANTS_PATH, p_index + 1, dict(participants[p_index])))

        if not matching:
            reject(MISMATCH, True)
        if len(users[device_id]) > 1:
            reject(SHARED, False)
        if DEVICE_ID_PATTERN.fullmatch(device_id) is None:
            reject(MALFORMED, False)
        if source == SOURCE_LABEL:
            reject(RECOVERED, True)
        if firmware is None:
            reject(UNRECOVERABLE, True)
    return DeviceLayer(silver, gold, rejects, failed, len(first))
