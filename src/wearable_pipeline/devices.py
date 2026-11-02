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
    raise NotImplementedError


def resolve_firmware(firmware_version: str | None,
                     device_label: str | None) -> tuple[str | None, str]:
    """Final firmware version and its source (``SOURCE_*``): the stripped column value when it
    is not blank, else the version parsed from the label, else ``(None, SOURCE_NONE)``."""
    raise NotImplementedError


def build_device_layer(
    participants: Sequence[Mapping[str, str]],
    metadata: Sequence[Mapping[str, str]],
) -> DeviceLayer:
    """Derive silver and gold device rows plus quality outcomes from the two CSV files' rows."""
    raise NotImplementedError
