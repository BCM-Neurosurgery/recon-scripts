from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import re
import subprocess
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import numpy as np


LOGGER = logging.getLogger("recon_scripts.electrodes_v2026")

OUTPUT_COLUMNS = [
    "ElectrodeID",
    "Label",
    "Coord_x",
    "Coord_y",
    "Coord_z",
    "MNI305_x",
    "MNI305_y",
    "MNI305_z",
    "Scanner_R",
    "Scanner_A",
    "Scanner_S",
    "ROI_D2009_3mm",
    "Matter_3mm",
    "ROI_DK2005_3mm",
    "ROI_XTRACT_3mm",
    "Area_fs_vox",
    "Matter_fs_vox",
    "Bolt",
    "Type",
    "Hemisphere",
    "Manufacturer",
    "x_vox",
    "y_vox",
    "z_vox",
    "MNI152_x",
    "MNI152_y",
    "MNI152_z",
    "MontageElectrodeID",
    "NSxSource",
    "NSxIndex",
    "NSxElectrodeID",
]

ROW_ALIAS_MAP = {
    "Electrode": "ElectrodeID",
    "LocationType": "Type",
    "T1R": "Scanner_R",
    "T1A": "Scanner_A",
    "T1S": "Scanner_S",
    "MRVoxel_I": "x_vox",
    "MRVoxel_J": "y_vox",
    "MRVoxel_K": "z_vox",
}

RAVE_OUTPUT_MAP = {
    "ROI_D2009_3mm": "FSLabel_aparc_a2009s_aseg",
    "ROI_DK2005_3mm": "FSLabel_aparc_aseg",
    "Area_fs_vox": "FSLabel_aparc_DKTatlas_aseg",
    "MNI152_x": "MNI152_x",
    "MNI152_y": "MNI152_y",
    "MNI152_z": "MNI152_z",
    "Scanner_R": "T1R",
    "Scanner_A": "T1A",
    "Scanner_S": "T1S",
    "x_vox": "MRVoxel_I",
    "y_vox": "MRVoxel_J",
    "z_vox": "MRVoxel_K",
}

LEGACY_SIDE_CAR_FILES = {
    "ROI_D2009_3mm": "{subject}_D2009Vol_ElectrodeLabelsRadius_3mm.xlsx",
    "Matter_3mm": "{subject}_D2009Vol_ElectrodeLabelsRadius_3mm.xlsx",
    "ROI_DK2005_3mm": "{subject}_DK2005Vol_ElectrodeLabelsRadius_3mm.xlsx",
    "ROI_XTRACT_3mm": "{subject}_xtract_ElectrodeLabelsRadius_3mm.xlsx",
    "Area_fs_vox": "{subject}_DK_AtlasLabels.csv",
}

FREESURFER_LUT_CANDIDATES = (
    "FreeSurferColorLUT.txt",
    "FreeSurferColorLUTnoFormat.txt",
)

XTRACT_LUT_CANDIDATES = (
    "xtractColorLUTnoFormat_SH.txt",
    "xtractColorLUTnoFormat.txt",
    "xtract_LUT_noformat.txt",
)

WHITE_KEYWORDS = (
    "white",
    "wm",
    "uncinate",
    "cingulum",
    "fasciculus",
    "tract",
    "radiation",
    "capsule",
    "corona-radiata",
)

SUBCORTICAL_KEYWORDS = (
    "hippocampus",
    "amygdala",
    "thalamus",
    "ventraldc",
    "putamen",
    "caudate",
    "pallidum",
    "accumbens",
    "brain-stem",
    "hypothalam",
    "subthalamic",
    "substantia",
    "lat-vent",
    "ventricle",
)


@dataclass(frozen=True)
class MontageRow:
    electrode_id: str
    channel_label: str
    sheet: str


@dataclass(frozen=True)
class MicroBundle:
    stem: str
    representative_label: str
    contact_labels: list[str]
    electrode_ids: list[str]

    @property
    def owner_label(self) -> str:
        label = self.representative_label
        return label[1:] if label.startswith(("m", "M")) else label

@dataclass(frozen=True)
class NSxChannel:
    source: str
    index: int
    electrode_id: str
    label: str


@dataclass(frozen=True)
class SubjectPaths:
    subject_root: Path
    rave_root: Path | None
    localization_csv: Path | None
    transform_norig: Path | None
    transform_torig: Path | None
    fs_aparc_a2009s: Path | None
    fs_aparc_dk: Path | None
    fs_aparc_dkt: Path | None
    xtract_volume: Path | None
    freesurfer_lut: Path | None
    xtract_lut: Path | None


@dataclass(frozen=True)
class LabelVolume:
    data: np.ndarray
    affine: np.ndarray
    labels: dict[int, str]


class ElectrodeBuildError(RuntimeError):
    """Raised when the electrodes file cannot be built safely."""


def canonicalize_label(label: str) -> str:
    if label is None:
        return ""
    value = str(label).strip()
    if not value:
        return ""
    match = re.match(r"^(.*?)(\d+)$", value)
    if not match:
        return value.casefold()
    stem, suffix = match.groups()
    return f"{stem.casefold()}{int(suffix):02d}"


def split_label(label: str) -> tuple[str, int | None]:
    match = re.match(r"^(.*?)(\d+)$", str(label).strip())
    if not match:
        return str(label).strip(), None
    stem, suffix = match.groups()
    return stem, int(suffix)


def read_csv_rows(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def read_xlsx_sheet(path: Path, sheet_name: str) -> list[dict[str, str]]:
    namespace = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("a:si", namespace):
                text = "".join(node.text or "" for node in item.iterfind(".//a:t", namespace))
                shared_strings.append(text)

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        target = None
        for sheet in workbook.find("a:sheets", namespace) or []:
            if sheet.attrib["name"] == sheet_name:
                rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                target = "xl/" + rel_map[rel_id]
                break
        if target is None:
            return []

        sheet_xml = ET.fromstring(archive.read(target))
        rows: list[list[str]] = []
        for row in sheet_xml.findall(".//a:sheetData/a:row", namespace):
            row_values: dict[int, str] = {}
            max_col = 0
            for cell in row.findall("a:c", namespace):
                ref = cell.attrib.get("r", "")
                col_letters = re.match(r"([A-Z]+)", ref)
                if not col_letters:
                    continue
                col_index = column_letter_to_index(col_letters.group(1))
                max_col = max(max_col, col_index)
                cell_type = cell.attrib.get("t")
                inline = cell.find("a:is", namespace)
                value_node = cell.find("a:v", namespace)
                if inline is not None:
                    text = "".join(node.text or "" for node in inline.iterfind(".//a:t", namespace))
                elif value_node is None:
                    text = ""
                else:
                    raw = value_node.text or ""
                    text = shared_strings[int(raw)] if cell_type == "s" else raw
                row_values[col_index] = text
            if max_col == 0:
                continue
            rows.append([row_values.get(index, "") for index in range(1, max_col + 1)])

    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:] if any(str(value).strip() for value in row)]


def column_letter_to_index(letters: str) -> int:
    result = 0
    for char in letters:
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result


def normalize_rave_row(row: dict[str, str]) -> dict[str, str]:
    normalized = {key: value for key, value in row.items()}
    for src, dst in ROW_ALIAS_MAP.items():
        if src in normalized and dst not in normalized:
            normalized[dst] = normalized[src]
    if "ElectrodeID" not in normalized:
        normalized["ElectrodeID"] = normalized.get("Electrode", "")
    if "Type" not in normalized:
        normalized["Type"] = normalized.get("LocationType", "")
    normalized.setdefault("Label", "")
    return normalized


def resolve_subject_paths(subject_root: Path, subject_code: str) -> SubjectPaths:
    rave_root = subject_root / "rave-imaging"
    localization_csv = None
    transform_norig = None
    transform_torig = None
    fs_aparc_a2009s = None
    fs_aparc_dk = None
    fs_aparc_dkt = None
    xtract_volume = None
    freesurfer_lut = None
    xtract_lut = None

    if rave_root.exists():
        localization_candidate = rave_root / "localization" / "electrodes.csv"
        localization_csv = localization_candidate if localization_candidate.exists() else None
        transform_norig = find_first_existing(
            rave_root / "derivative" / "transform-Norig.tsv",
            rave_root / "coregistration" / "transform-Norig.tsv",
        )
        transform_torig = find_first_existing(
            rave_root / "derivative" / "transform-Torig.tsv",
            rave_root / "coregistration" / "transform-Torig.tsv",
        )
        fs_aparc_a2009s = find_first_existing(
            rave_root / "fs" / "mri" / "aparc.a2009s+aseg.mgz",
            rave_root / "fs" / "mri" / "aparc.a2009s+aseg.nii.gz",
        )
        fs_aparc_dk = find_first_existing(
            rave_root / "fs" / "mri" / "aparc+aseg.mgz",
            rave_root / "fs" / "mri" / "aparc+aseg.nii.gz",
        )
        fs_aparc_dkt = find_first_existing(
            rave_root / "fs" / "mri" / "aparc.DKTatlas+aseg.mgz",
            rave_root / "fs" / "mri" / "aparc.DKTatlas+aseg.nii.gz",
        )
        xtract_volume = find_first_existing(
            rave_root / "derivative" / f"xtract_label_in_{subject_code}.mgz",
            rave_root / "derivative" / f"xtract_label_in_{subject_code}.nii.gz",
            rave_root / "fs" / "mri" / f"xtract_label_in_{subject_code}.mgz",
            rave_root / "fs" / "mri" / f"xtract_label_in_{subject_code}.nii.gz",
        )

    freesurfer_lut = discover_lut(
        subject_root,
        FREESURFER_LUT_CANDIDATES,
        env_var="FREESURFER_HOME",
    )
    xtract_lut = discover_lut(
        subject_root,
        XTRACT_LUT_CANDIDATES,
        env_var="XTRACT_LUT_FILE",
    )

    return SubjectPaths(
        subject_root=subject_root,
        rave_root=rave_root if rave_root.exists() else None,
        localization_csv=localization_csv,
        transform_norig=transform_norig,
        transform_torig=transform_torig,
        fs_aparc_a2009s=fs_aparc_a2009s,
        fs_aparc_dk=fs_aparc_dk,
        fs_aparc_dkt=fs_aparc_dkt,
        xtract_volume=xtract_volume,
        freesurfer_lut=freesurfer_lut,
        xtract_lut=xtract_lut,
    )


def discover_lut(subject_root: Path, candidate_names: tuple[str, ...], env_var: str | None = None) -> Path | None:
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            env_path = Path(env_value)
            if env_path.is_file():
                return env_path
            if env_path.is_dir():
                for candidate_name in candidate_names:
                    candidate = env_path / candidate_name
                    if candidate.exists():
                        return candidate

    candidate_dirs = [
        subject_root,
        subject_root / "rave-imaging",
        subject_root / "rave-imaging" / "derivative",
        Path("/Volumes/projectworlds/EMU-18112/ElectrodeLabelsROIs"),
        Path("/usr/local/freesurfer"),
        Path("/Applications/freesurfer"),
    ]
    for directory in candidate_dirs:
        for candidate_name in candidate_names:
            candidate = directory / candidate_name
            if candidate.exists():
                return candidate
    return None


def find_first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def load_montage(path: Path) -> tuple[list[MontageRow], list[MicroBundle]]:
    sheet1 = read_xlsx_sheet(path, "Sheet1")
    sheet2 = read_xlsx_sheet(path, "Sheet2")

    macros: list[MontageRow] = []
    for row in sheet1:
        label = row.get("ChannelLabel", "").strip()
        if not label or label.casefold() == "empty":
            continue
        macros.append(MontageRow(
            electrode_id=str(row.get("ElectrodeID", "")).strip(),
            channel_label=label,
            sheet="Sheet1",
        ))

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in sheet2:
        label = row.get("ChannelLabel", "").strip()
        if not label or label.casefold() == "empty":
            continue
        stem, _ = split_label(label)
        grouped.setdefault(stem, []).append(row)

    bundles: list[MicroBundle] = []
    for stem, rows in grouped.items():
        rows = sorted(rows, key=lambda item: split_label(item.get("ChannelLabel", ""))[1] or 0)
        labels = [item.get("ChannelLabel", "").strip() for item in rows]
        electrode_ids = [str(item.get("ElectrodeID", "")).strip() for item in rows]
        bundles.append(MicroBundle(
            stem=stem,
            representative_label=labels[0],
            contact_labels=labels,
            electrode_ids=electrode_ids,
        ))

    return macros, sorted(bundles, key=lambda bundle: canonicalize_label(bundle.representative_label))


def build_rave_index(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    duplicates: list[str] = []
    for raw in rows:
        row = normalize_rave_row(raw)
        key = canonicalize_label(row.get("Label", ""))
        if not key:
            continue
        if key in index:
            duplicates.append(row["Label"])
        index[key] = row
    if duplicates:
        raise ElectrodeBuildError(f"Duplicate canonical labels found in RAVE CSV: {duplicates}")
    return index


def validate_macros(macros: list[MontageRow], rave_index: dict[str, dict[str, str]]) -> list[tuple[MontageRow, dict[str, str]]]:
    seen: set[str] = set()
    pairs: list[tuple[MontageRow, dict[str, str]]] = []
    missing: list[str] = []
    for montage_row in macros:
        key = canonicalize_label(montage_row.channel_label)
        if key in seen:
            raise ElectrodeBuildError(f"Duplicate montage label after canonicalization: {montage_row.channel_label}")
        seen.add(key)
        rave_row = rave_index.get(key)
        if rave_row is None:
            missing.append(montage_row.channel_label)
            continue
        pairs.append((montage_row, rave_row))
    if missing:
        raise ElectrodeBuildError(f"Montage labels missing from RAVE CSV: {missing}")
    return pairs


def create_base_output_row() -> dict[str, str]:
    row = {column: "" for column in OUTPUT_COLUMNS}
    row["Bolt"] = "0"
    return row


def derive_hemisphere(label: str) -> str:
    if not label:
        return "NA"
    if label.startswith(("mL", "Ml", "ml")):
        return "Left"
    if label.startswith(("mR", "Mr", "mr")):
        return "Right"
    if label.startswith(("L", "l")):
        return "Left"
    if label.startswith(("R", "r")):
        return "Right"
    return "NA"


def normalize_type(value: str, label: str) -> str:
    source = (value or "").strip()
    lowered = source.casefold()
    if label.startswith(("m", "M")):
        return "microwires"
    if label.upper().startswith("Z"):
        return "GND"
    if label.upper().startswith("C"):
        return "REF"
    if lowered in {"ieeg", "seeg", "seeg-micro", "microwires"}:
        return "sEEG" if lowered == "ieeg" else source
    if not source:
        return "sEEG"
    return source


def compute_manufacturer(row_type: str) -> str:
    if row_type in {"sEEG", "sEEG-micro", "microwires"}:
        return "Ad-Tech"
    if row_type in {"REF", "GND", "External", "empty"}:
        return "NA"
    if row_type == "DBS":
        return "Boston Scientific"
    return "NA"


def classify_matter(label: str) -> str:
    value = (label or "").strip()
    folded = value.casefold()
    if not value:
        return ""
    if folded in {"na"}:
        return "NA"
    if "out" in folded:
        return "Out"
    if "unknown" in folded:
        return "Unknown"
    if any(keyword in folded for keyword in WHITE_KEYWORDS):
        return "White"
    if any(keyword in folded for keyword in SUBCORTICAL_KEYWORDS):
        return "Subcortical"
    return "Grey"


def apply_rave_macro_mappings(output: dict[str, str], rave_row: dict[str, str]) -> None:
    for source, dest in ROW_ALIAS_MAP.items():
        if source in rave_row and dest in OUTPUT_COLUMNS and str(rave_row[source]).strip() and not output.get(dest):
            output[dest] = str(rave_row[source]).strip()
    for output_column, rave_column in RAVE_OUTPUT_MAP.items():
        if str(rave_row.get(rave_column, "")).strip() and not output.get(output_column):
            output[output_column] = str(rave_row[rave_column]).strip()
    if not output.get("Matter_fs_vox") and output.get("Area_fs_vox"):
        output["Matter_fs_vox"] = classify_matter(output["Area_fs_vox"])
    if not output.get("Matter_3mm") and output.get("ROI_D2009_3mm"):
        output["Matter_3mm"] = classify_matter(output["ROI_D2009_3mm"])


def output_row_from_macro(montage_row: MontageRow, rave_row: dict[str, str]) -> dict[str, str]:
    output = create_base_output_row()
    output["ElectrodeID"] = montage_row.electrode_id
    output["Label"] = montage_row.channel_label
    output["MontageElectrodeID"] = montage_row.electrode_id

    for column in OUTPUT_COLUMNS:
        if column in {"ElectrodeID", "Label", "MontageElectrodeID", "NSxSource", "NSxIndex", "NSxElectrodeID"}:
            continue
        if column in rave_row and str(rave_row[column]).strip():
            output[column] = str(rave_row[column]).strip()

    apply_rave_macro_mappings(output, rave_row)
    output["Type"] = normalize_type(output.get("Type", ""), output["Label"])
    output["Hemisphere"] = output.get("Hemisphere") or derive_hemisphere(output["Label"])
    output["Manufacturer"] = output.get("Manufacturer") or compute_manufacturer(output["Type"])
    output["Bolt"] = output.get("Bolt") or "0"
    return output


def parse_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def values_to_point(rows: list[dict[str, str]], columns: tuple[str, str, str]) -> np.ndarray | None:
    points: list[list[float]] = []
    for row in rows:
        values = [parse_float(row.get(column, "")) for column in columns]
        if any(value is None for value in values):
            continue
        points.append([float(value) for value in values])
    if len(points) < 2:
        return None
    return np.asarray(points, dtype=float)


def get_unit_vector(points: np.ndarray) -> np.ndarray:
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered)
    direction = vh[0]
    direction = direction / np.linalg.norm(direction)
    first_point = points[0]
    last_point = points[-1]
    if np.dot(direction, last_point - first_point) < 0:
        direction = -direction
    return direction


def format_decimal(value: float | None, digits: int = 4) -> str:
    if value is None or math.isnan(value):
        return ""
    rounded = round(float(value), digits)
    text = f"{rounded:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def range_or_scalar(values: list[str]) -> str:
    compact = [str(value).strip() for value in values if str(value).strip()]
    if not compact:
        return ""
    if len(compact) == 1:
        return compact[0]
    return f"{compact[0]}-{compact[-1]}"


def next_synthetic_electrode_id(output_rows: list[dict[str, str]]) -> int:
    numeric_ids = [
        int(row["ElectrodeID"])
        for row in output_rows
        if str(row.get("ElectrodeID", "")).isdigit()
    ]
    return (max(numeric_ids) if numeric_ids else 0) + 1


def point_from_row(row: dict[str, str], columns: tuple[str, str, str]) -> np.ndarray | None:
    values = [parse_float(row.get(column, "")) for column in columns]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def synthesize_micro_row(
    bundle: MicroBundle,
    owner_row: dict[str, str],
    shaft_rows: list[dict[str, str]],
    synthetic_electrode_id: int,
) -> dict[str, str]:
    output = create_base_output_row()
    output["ElectrodeID"] = str(synthetic_electrode_id)
    output["Label"] = bundle.representative_label
    output["MontageElectrodeID"] = range_or_scalar(bundle.electrode_ids)
    output["Type"] = "microwires"
    output["Hemisphere"] = derive_hemisphere(bundle.representative_label)
    output["Manufacturer"] = compute_manufacturer(output["Type"])
    output["Bolt"] = owner_row.get("Bolt", "0")
    output["__owner_label"] = owner_row["Label"]

    coordinate_sets = {
        ("Coord_x", "Coord_y", "Coord_z"): 3.0,
        ("MNI305_x", "MNI305_y", "MNI305_z"): 3.15,
        ("MNI152_x", "MNI152_y", "MNI152_z"): 3.15,
    }
    for columns, distance in coordinate_sets.items():
        owner_point = [parse_float(owner_row.get(column, "")) for column in columns]
        if any(value is None for value in owner_point):
            continue
        points = values_to_point(shaft_rows, columns)
        if points is None:
            continue
        direction = get_unit_vector(points)
        projected = np.asarray(owner_point, dtype=float) - direction * distance
        for column, value in zip(columns, projected):
            output[column] = format_decimal(value)

    return output


def collect_shaft_rows(output_rows: list[dict[str, str]], owner_label: str) -> list[dict[str, str]]:
    stem, _ = split_label(owner_label)
    return [
        row for row in output_rows
        if split_label(row["Label"])[0].casefold() == stem.casefold() and not row["Label"].startswith(("m", "M"))
    ]


def discover_subject_code(rows: list[dict[str, str]], input_csv: Path) -> str:
    for row in rows:
        subject = (row.get("SubjectCode") or "").strip()
        if subject:
            return subject
    stem = input_csv.stem
    if stem.endswith("-electrodes"):
        return stem[:-11]
    return stem


def find_recon_file(subject_root: Path, subject_code: str, filename: str) -> Path | None:
    candidates = [
        subject_root / filename,
        subject_root / "elec_recon" / filename,
        subject_root / subject_code / "elec_recon" / filename,
        subject_root / "IMG" / subject_code / "elec_recon" / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for candidate in subject_root.rglob(filename):
        return candidate
    return None


def read_label_value_map_from_radius_xlsx(path: Path, value_column: str, label_column: str = "Electrode") -> dict[str, str]:
    rows = read_xlsx_sheet(path, "Sheet1")
    result: dict[str, str] = {}
    for row in rows:
        label = (row.get(label_column) or row.get("elecs_labels") or "").strip()
        value = (row.get(value_column) or "").strip()
        if label and value:
            result[canonicalize_label(label)] = value
    return result


def read_fs_atlas_csv(path: Path) -> dict[str, str]:
    rows = read_csv_rows(path)
    result: dict[str, str] = {}
    for row in rows:
        key = ""
        value = ""
        for label_key in ("Electrode", "Label", "ChannelLabel"):
            if row.get(label_key):
                key = row[label_key].strip()
                break
        for value_key in ("Area", "FSLabel", "AtlasLabel", "Region"):
            if row.get(value_key):
                value = row[value_key].strip()
                break
        if not key or not value:
            values = [item.strip() for item in row.values() if str(item).strip()]
            if len(values) >= 2:
                key, value = values[0], values[1]
        if key and value:
            result[canonicalize_label(key)] = value
    return result


def read_ascii_matrix(path: Path) -> np.ndarray:
    rows: list[list[float]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            rows.append([float(item) for item in stripped.split()])
    return np.asarray(rows, dtype=float)


def parse_electrode_names(path: Path) -> list[str]:
    labels: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < 2:
                continue
            stripped = line.strip()
            if not stripped:
                continue
            labels.append(stripped.split()[0])
    return labels


def build_coordinate_sidecar_map(
    pial_path: Path,
    pialvox_path: Path,
    fsaverage_path: Path,
    names_path: Path,
) -> dict[str, dict[str, str]]:
    pial = read_ascii_matrix(pial_path)
    pialvox = read_ascii_matrix(pialvox_path)
    fsaverage = read_ascii_matrix(fsaverage_path)
    names = parse_electrode_names(names_path)
    count = min(len(names), len(pial), len(pialvox), len(fsaverage))
    mapping: dict[str, dict[str, str]] = {}
    for index in range(count):
        key = canonicalize_label(names[index])
        mapping[key] = {
            "Coord_x": format_decimal(pial[index][0]),
            "Coord_y": format_decimal(pial[index][1]),
            "Coord_z": format_decimal(pial[index][2]),
            "x_vox": format_decimal(pialvox[index][0]),
            "y_vox": format_decimal(pialvox[index][1]),
            "z_vox": format_decimal(pialvox[index][2]),
            "MNI305_x": format_decimal(fsaverage[index][0]),
            "MNI305_y": format_decimal(fsaverage[index][1]),
            "MNI305_z": format_decimal(fsaverage[index][2]),
        }
    return mapping


def read_transform_matrix(path: Path | None) -> np.ndarray | None:
    if path is None or not path.exists():
        return None
    return np.loadtxt(path)


def tkr_to_scanner_ras(coord: np.ndarray, norig: np.ndarray, torig: np.ndarray) -> np.ndarray:
    hom = np.append(coord, 1.0)
    return (norig @ np.linalg.inv(torig) @ hom)[:3]


def scanner_ras_to_vox(scanner_ras: np.ndarray, norig: np.ndarray) -> np.ndarray:
    hom = np.append(scanner_ras, 1.0)
    return (np.linalg.inv(norig) @ hom)[:3]


def ensure_scanner_and_vox_coords(output_rows: list[dict[str, str]], subject_paths: SubjectPaths) -> None:
    norig = read_transform_matrix(subject_paths.transform_norig)
    torig = read_transform_matrix(subject_paths.transform_torig)
    if norig is None or torig is None:
        return
    for row in output_rows:
        coord = point_from_row(row, ("Coord_x", "Coord_y", "Coord_z"))
        if coord is None:
            continue
        needs_scanner = row["Type"] == "microwires" or not point_from_row(row, ("Scanner_R", "Scanner_A", "Scanner_S")) is not None
        if needs_scanner:
            scanner = tkr_to_scanner_ras(coord, norig, torig)
            row["Scanner_R"] = format_decimal(scanner[0])
            row["Scanner_A"] = format_decimal(scanner[1])
            row["Scanner_S"] = format_decimal(scanner[2])
        scanner_ras = point_from_row(row, ("Scanner_R", "Scanner_A", "Scanner_S"))
        if scanner_ras is None:
            continue
        needs_vox = row["Type"] == "microwires" or point_from_row(row, ("x_vox", "y_vox", "z_vox")) is None
        if needs_vox:
            vox = scanner_ras_to_vox(scanner_ras, norig)
            row["x_vox"] = format_decimal(vox[0])
            row["y_vox"] = format_decimal(vox[1])
            row["z_vox"] = format_decimal(vox[2])


def load_label_lookup(path: Path | None) -> dict[int, str]:
    if path is None or not path.exists():
        return {}
    lookup: dict[int, str] = {}
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            try:
                code = int(parts[0])
            except ValueError:
                continue
            lookup[code] = parts[1]
    return lookup


def load_label_volume(path: Path | None, lookup: dict[int, str], logger: logging.Logger) -> LabelVolume | None:
    if path is None or not path.exists() or not lookup:
        return None
    try:
        import nibabel as nib
    except Exception as exc:  # pragma: no cover - import depends on local env
        logger.warning("nibabel is unavailable for atlas sampling: %s", exc)
        return None

    try:
        image = nib.load(str(path))
    except Exception as exc:  # pragma: no cover - runtime data dependent
        logger.warning("Unable to load atlas volume %s: %s", path, exc)
        return None

    data = np.asarray(image.get_fdata())
    return LabelVolume(data=data, affine=np.asarray(image.affine), labels=lookup)


def sample_label_volume(volume: LabelVolume | None, scanner_ras: np.ndarray | None, radius_mm: float = 3.0) -> str:
    if volume is None or scanner_ras is None:
        return ""
    inv_affine = np.linalg.inv(volume.affine)
    center_vox = (inv_affine @ np.append(scanner_ras, 1.0))[:3]
    voxel_sizes = np.linalg.norm(volume.affine[:3, :3], axis=0)
    voxel_sizes = np.where(voxel_sizes == 0, 1.0, voxel_sizes)
    radius_vox = np.ceil(radius_mm / voxel_sizes).astype(int)
    lo = np.maximum(np.floor(center_vox - radius_vox).astype(int), 0)
    hi = np.minimum(np.ceil(center_vox + radius_vox).astype(int), np.array(volume.data.shape[:3]) - 1)

    samples: list[int] = []
    for i in range(lo[0], hi[0] + 1):
        for j in range(lo[1], hi[1] + 1):
            for k in range(lo[2], hi[2] + 1):
                world = (volume.affine @ np.array([i, j, k, 1.0]))[:3]
                if np.linalg.norm(world - scanner_ras) > radius_mm:
                    continue
                code = int(round(float(volume.data[i, j, k])))
                if code != 0:
                    samples.append(code)
    if not samples:
        return ""
    code, _ = Counter(samples).most_common(1)[0]
    return volume.labels.get(code, "")


def sample_subject_atlas_volumes(output_rows: list[dict[str, str]], subject_paths: SubjectPaths, logger: logging.Logger) -> None:
    freesurfer_lookup = load_label_lookup(subject_paths.freesurfer_lut)
    xtract_lookup = load_label_lookup(subject_paths.xtract_lut)
    d2009_volume = load_label_volume(subject_paths.fs_aparc_a2009s, freesurfer_lookup, logger)
    dk_volume = load_label_volume(subject_paths.fs_aparc_dk, freesurfer_lookup, logger)
    dkt_volume = load_label_volume(subject_paths.fs_aparc_dkt, freesurfer_lookup, logger)
    xtract_volume = load_label_volume(subject_paths.xtract_volume, xtract_lookup, logger)

    for row in output_rows:
        scanner_ras = point_from_row(row, ("Scanner_R", "Scanner_A", "Scanner_S"))
        if not row.get("ROI_D2009_3mm"):
            row["ROI_D2009_3mm"] = sample_label_volume(d2009_volume, scanner_ras)
        if not row.get("ROI_DK2005_3mm"):
            row["ROI_DK2005_3mm"] = sample_label_volume(dk_volume, scanner_ras)
        if not row.get("Area_fs_vox"):
            row["Area_fs_vox"] = sample_label_volume(dkt_volume, scanner_ras, radius_mm=0.5)
        if not row.get("ROI_XTRACT_3mm"):
            row["ROI_XTRACT_3mm"] = sample_label_volume(xtract_volume, scanner_ras)
        if not row.get("Matter_3mm") and row.get("ROI_D2009_3mm"):
            row["Matter_3mm"] = classify_matter(row["ROI_D2009_3mm"])
        if not row.get("Matter_fs_vox") and row.get("Area_fs_vox"):
            row["Matter_fs_vox"] = classify_matter(row["Area_fs_vox"])


def apply_micro_owner_fallbacks(output_rows: list[dict[str, str]]) -> None:
    row_by_label = {canonicalize_label(row["Label"]): row for row in output_rows}
    for row in output_rows:
        if row.get("Type") != "microwires":
            continue
        owner_label = row.get("__owner_label", "")
        owner_row = row_by_label.get(canonicalize_label(owner_label))
        if owner_row is None:
            continue
        for column in ("ROI_D2009_3mm", "Matter_3mm", "ROI_DK2005_3mm", "ROI_XTRACT_3mm", "Area_fs_vox", "Matter_fs_vox"):
            if not row.get(column):
                row[column] = owner_row.get(column, "")


def backfill_from_legacy_sidecars(output_rows: list[dict[str, str]], subject_root: Path, subject_code: str, logger: logging.Logger) -> None:
    sidecar_maps: dict[str, dict[str, str]] = {}

    d2009 = find_recon_file(subject_root, subject_code, LEGACY_SIDE_CAR_FILES["ROI_D2009_3mm"].format(subject=subject_code))
    if d2009:
        try:
            sidecar_maps["ROI_D2009_3mm"] = read_label_value_map_from_radius_xlsx(d2009, "Label_Voxel_Radius")
            sidecar_maps["Matter_3mm"] = read_label_value_map_from_radius_xlsx(d2009, "Matter_Voxel_Radius")
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to read legacy D2009 sidecar %s: %s", d2009, exc)

    dk2005 = find_recon_file(subject_root, subject_code, LEGACY_SIDE_CAR_FILES["ROI_DK2005_3mm"].format(subject=subject_code))
    if dk2005:
        try:
            sidecar_maps["ROI_DK2005_3mm"] = read_label_value_map_from_radius_xlsx(dk2005, "Label_Voxel_Radius")
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to read legacy DK2005 sidecar %s: %s", dk2005, exc)

    xtract = find_recon_file(subject_root, subject_code, LEGACY_SIDE_CAR_FILES["ROI_XTRACT_3mm"].format(subject=subject_code))
    if xtract:
        try:
            sidecar_maps["ROI_XTRACT_3mm"] = read_label_value_map_from_radius_xlsx(xtract, "CorrectedLabel")
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to read legacy xtract sidecar %s: %s", xtract, exc)

    fs_atlas = find_recon_file(subject_root, subject_code, LEGACY_SIDE_CAR_FILES["Area_fs_vox"].format(subject=subject_code))
    if fs_atlas:
        try:
            sidecar_maps["Area_fs_vox"] = read_fs_atlas_csv(fs_atlas)
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to read legacy FS atlas csv %s: %s", fs_atlas, exc)

    pial = find_recon_file(subject_root, subject_code, f"{subject_code}.PIAL")
    pialvox = find_recon_file(subject_root, subject_code, f"{subject_code}.PIALVOX")
    fsaverage = find_recon_file(subject_root, subject_code, f"{subject_code}.FSAVERAGE")
    names_file = find_recon_file(subject_root, subject_code, f"{subject_code}.electrodeNames")
    if pial and pialvox and fsaverage and names_file:
        try:
            label_map = build_coordinate_sidecar_map(pial, pialvox, fsaverage, names_file)
            for row in output_rows:
                sidecar = label_map.get(canonicalize_label(row["Label"]))
                if sidecar is None:
                    continue
                for column, value in sidecar.items():
                    if not row.get(column):
                        row[column] = value
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to read legacy coordinate sidecars under %s: %s", subject_root, exc)

    for row in output_rows:
        key = canonicalize_label(row["Label"])
        for column, mapping in sidecar_maps.items():
            if not row.get(column):
                row[column] = mapping.get(key, "")


def enrich_from_subject_data(
    output_rows: list[dict[str, str]],
    subject_paths: SubjectPaths,
    subject_code: str,
    logger: logging.Logger,
) -> None:
    ensure_scanner_and_vox_coords(output_rows, subject_paths)
    sample_subject_atlas_volumes(output_rows, subject_paths, logger)
    apply_micro_owner_fallbacks(output_rows)
    backfill_from_legacy_sidecars(output_rows, subject_paths.subject_root, subject_code, logger)


def finalize_row_metadata(output_rows: list[dict[str, str]]) -> None:
    mark_micro_owners(output_rows)
    for row in output_rows:
        row["Type"] = normalize_type(row.get("Type", ""), row["Label"])
        row["Hemisphere"] = row.get("Hemisphere") or derive_hemisphere(row["Label"])
        row["Manufacturer"] = compute_manufacturer(row["Type"])
        if row.get("Area_fs_vox") and not row.get("Matter_fs_vox"):
            row["Matter_fs_vox"] = classify_matter(row["Area_fs_vox"])
        if row.get("ROI_D2009_3mm") and not row.get("Matter_3mm"):
            row["Matter_3mm"] = classify_matter(row["ROI_D2009_3mm"])
    apply_bolt_rules(output_rows)


def mark_micro_owners(output_rows: list[dict[str, str]]) -> None:
    owner_labels = {
        canonicalize_label(row["Label"][1:])
        for row in output_rows
        if row["Type"] == "microwires" and row["Label"].startswith(("m", "M"))
    }
    for row in output_rows:
        if canonicalize_label(row["Label"]) in owner_labels and row["Type"] == "sEEG":
            row["Type"] = "sEEG-micro"


def apply_bolt_rules(output_rows: list[dict[str, str]]) -> None:
    by_stem: dict[str, list[dict[str, str]]] = {}
    for row in output_rows:
        stem, suffix = split_label(row["Label"])
        if suffix is None:
            continue
        by_stem.setdefault(stem.casefold(), []).append(row)

    for row in output_rows:
        matter = row.get("Matter_3mm", "")
        if matter == "Out":
            row["Bolt"] = "1"
            continue
        if row.get("Bolt") == "1":
            continue
        stem, suffix = split_label(row["Label"])
        if suffix is None:
            row["Bolt"] = row.get("Bolt") or "0"
            continue
        if row.get("Matter_fs_vox") != "Unknown":
            row["Bolt"] = row.get("Bolt") or "0"
            continue
        contacts = [item for item in by_stem.get(stem.casefold(), []) if split_label(item["Label"])[1] is not None]
        if not contacts:
            row["Bolt"] = row.get("Bolt") or "0"
            continue
        terminal = max(split_label(item["Label"])[1] or 0 for item in contacts)
        row["Bolt"] = "1" if suffix == terminal else (row.get("Bolt") or "0")


def parse_nsx_channels(path: Path, source: str, logger: logging.Logger) -> list[NSxChannel]:
    try:
        from neo.rawio.blackrockrawio import BlackrockRawIO
    except Exception as exc:
        logger.warning("neo is unavailable for %s parsing: %s", path, exc)
        return []

    try:
        rawio = BlackrockRawIO(filename=str(path))
        rawio.parse_header()
    except Exception as exc:
        logger.warning("Unable to parse %s: %s", path, exc)
        return []

    channels: list[NSxChannel] = []
    signal_channels = rawio.header.get("signal_channels")
    if signal_channels is None:
        return channels

    for index, channel in enumerate(signal_channels, start=1):
        fields = getattr(signal_channels, "dtype", None)
        name = None
        chan_id = None
        if fields is not None and fields.names:
            if "name" in fields.names:
                name = channel["name"]
            if "id" in fields.names:
                chan_id = channel["id"]
        label = decode_nsx_field(name)
        electrode_id = decode_nsx_field(chan_id) or str(index)
        channels.append(NSxChannel(
            source=source,
            index=index,
            electrode_id=electrode_id,
            label=label,
        ))
    return channels


def decode_nsx_field(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip("\x00 ").strip()
    return str(value).strip()


def canonicalize_nsx_label(label: str) -> str:
    normalized = re.sub(r"-\d+$", "", str(label).strip())
    return canonicalize_label(normalized)


def build_ns5_bundle_map(ns5_channels: list[NSxChannel]) -> dict[str, list[NSxChannel]]:
    grouped: dict[str, list[NSxChannel]] = {}
    for channel in ns5_channels:
        if not channel.label:
            continue
        normalized = re.sub(r"-\d+$", "", channel.label)
        stem, suffix = split_label(normalized)
        if suffix is None or not normalized.startswith(("m", "M")):
            continue
        grouped.setdefault(stem.casefold(), []).append(
            NSxChannel(
                source=channel.source,
                index=channel.index,
                electrode_id=channel.electrode_id,
                label=normalized,
            )
        )

    representative_map: dict[str, list[NSxChannel]] = {}
    for _, channels in grouped.items():
        ordered = sorted(channels, key=lambda item: split_label(item.label)[1] or 0)
        representative_map[canonicalize_label(ordered[0].label)] = ordered
    return representative_map


def run_xtract_helper(
    subject_root: Path,
    xtract_assets_root: Path,
    logger: logging.Logger,
    suppress_xtract_tool_output: bool = False,
) -> None:
    helper = Path(__file__).resolve().parents[2] / "scripts" / "run_xtract_rave_subject.sh"
    if not helper.exists():
        raise ElectrodeBuildError(f"Xtract helper script not found: {helper}")
    command = ["bash", str(helper), str(subject_root), str(xtract_assets_root)]
    env = os.environ.copy()
    env["RECON_XTRACT_TOOL_VERBOSE"] = "0" if suppress_xtract_tool_output else "1"
    logger.info(
        "Running xtract helper: %s (tool output %s)",
        " ".join(command),
        "suppressed" if suppress_xtract_tool_output else "verbose",
    )
    subprocess.run(command, check=True, env=env)


def assign_nsx_metadata(
    output_rows: list[dict[str, str]],
    bundles: list[MicroBundle],
    ns3_channels: list[NSxChannel],
    ns5_channels: list[NSxChannel],
    logger: logging.Logger,
) -> None:
    ns3_by_label = {canonicalize_label(channel.label): channel for channel in ns3_channels if channel.label}
    ns3_by_id = {str(channel.electrode_id): channel for channel in ns3_channels if str(channel.electrode_id)}
    for row in output_rows:
        if row["Type"] == "microwires":
            continue
        channel = ns3_by_label.get(canonicalize_label(row["Label"])) or ns3_by_id.get(str(row["ElectrodeID"]))
        if channel is None:
            continue
        row["NSxSource"] = channel.source
        row["NSxIndex"] = str(channel.index)
        row["NSxElectrodeID"] = channel.electrode_id

    ns5_by_label = {canonicalize_nsx_label(channel.label): channel for channel in ns5_channels if channel.label}
    ns5_by_id = {str(channel.electrode_id): channel for channel in ns5_channels if str(channel.electrode_id)}
    ns5_bundle_map = build_ns5_bundle_map(ns5_channels)
    row_by_label = {canonicalize_label(row["Label"]): row for row in output_rows}
    for bundle in bundles:
        micro_row = row_by_label.get(canonicalize_label(bundle.representative_label))
        if micro_row is None:
            continue

        label_matches = [ns5_by_label.get(canonicalize_label(label)) for label in bundle.contact_labels]
        label_matches = [channel for channel in label_matches if channel is not None]
        id_matches = [ns5_by_id.get(str(electrode_id)) for electrode_id in bundle.electrode_ids]
        id_matches = [channel for channel in id_matches if channel is not None]
        bundle_matches = ns5_bundle_map.get(canonicalize_label(bundle.representative_label), [])

        channels = label_matches if len(label_matches) == len(bundle.contact_labels) else []
        if not channels and len(bundle_matches) == len(bundle.contact_labels):
            channels = bundle_matches
        if not channels and id_matches:
            channels = id_matches
        if not channels and label_matches:
            channels = label_matches

        if not channels:
            logger.warning("No ns5 channels resolved for micro bundle %s", bundle.representative_label)
            continue
        if len(channels) != len(bundle.contact_labels):
            logger.warning(
                "Partial ns5 mapping for %s: matched %d of %d contacts",
                bundle.representative_label,
                len(channels),
                len(bundle.contact_labels),
            )

        micro_row["NSxSource"] = "ns5"
        micro_row["NSxIndex"] = range_or_scalar([str(channel.index) for channel in channels])
        micro_row["NSxElectrodeID"] = range_or_scalar([channel.electrode_id for channel in channels])


def write_output_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in OUTPUT_COLUMNS})


def default_output_path(input_csv: Path, subject_code: str) -> Path:
    return input_csv.with_name(f"{subject_code}-electrodes_v2026.csv")


def build_electrodes_v2026(
    input_csv: Path,
    montage: Path,
    subject_root: Path,
    output_csv: Path | None = None,
    ns3: Path | None = None,
    ns5: Path | None = None,
    strict: bool = False,
    run_xtract: bool = False,
    xtract_assets_root: Path | None = None,
    suppress_xtract_tool_output: bool = False,
    logger: logging.Logger | None = None,
) -> Path:
    logger = logger or LOGGER
    rave_rows = read_csv_rows(input_csv)
    subject_code = discover_subject_code(rave_rows, input_csv)
    subject_paths = resolve_subject_paths(subject_root, subject_code)
    if run_xtract and subject_paths.xtract_volume is None:
        if xtract_assets_root is None:
            raise ElectrodeBuildError("--run-xtract requires --xtract-assets-root")
        run_xtract_helper(
            subject_root,
            xtract_assets_root,
            logger,
            suppress_xtract_tool_output=suppress_xtract_tool_output,
        )
        subject_paths = resolve_subject_paths(subject_root, subject_code)
    macros, bundles = load_montage(montage)
    rave_index = build_rave_index(rave_rows)
    pairs = validate_macros(macros, rave_index)

    output_rows = [output_row_from_macro(montage_row, rave_row) for montage_row, rave_row in pairs]
    row_by_label = {canonicalize_label(row["Label"]): row for row in output_rows}

    for bundle in bundles:
        owner_key = canonicalize_label(bundle.owner_label)
        owner_row = row_by_label.get(owner_key)
        if owner_row is None:
            message = f"Micro bundle {bundle.representative_label} has no matching macro owner {bundle.owner_label}"
            if strict:
                raise ElectrodeBuildError(message)
            logger.warning(message)
            continue
        shaft_rows = collect_shaft_rows(output_rows, owner_row["Label"])
        if len(shaft_rows) < 2:
            message = f"Not enough macro contacts to compute micro projection for {bundle.representative_label}"
            if strict:
                raise ElectrodeBuildError(message)
            logger.warning(message)
            continue
        output_rows.append(
            synthesize_micro_row(
                bundle,
                owner_row,
                shaft_rows,
                synthetic_electrode_id=next_synthetic_electrode_id(output_rows),
            )
        )

    enrich_from_subject_data(output_rows, subject_paths, subject_code, logger)
    finalize_row_metadata(output_rows)

    ns3_channels = parse_nsx_channels(ns3, "ns3", logger) if ns3 else []
    ns5_channels = parse_nsx_channels(ns5, "ns5", logger) if ns5 else []
    assign_nsx_metadata(output_rows, bundles, ns3_channels, ns5_channels, logger)

    destination = output_csv or default_output_path(input_csv, subject_code)
    write_output_csv(destination, output_rows)
    return destination


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build lab-ready electrodes_v2026 CSVs from RAVE output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build an electrodes_v2026 CSV.")
    build.add_argument("--input-csv", required=True, type=Path, help="Path to RAVE electrodes.csv")
    build.add_argument("--montage", required=True, type=Path, help="Path to montage xlsx")
    build.add_argument("--subject-root", required=True, type=Path, help="RAVE raw_dir subject root; legacy elec_recon roots remain supported as fallback")
    build.add_argument("--ns3", type=Path, help="Optional NS3 file for macro metadata")
    build.add_argument("--ns5", type=Path, help="Optional NS5 file for micro metadata")
    build.add_argument("--output", type=Path, help="Optional output CSV path")
    build.add_argument("--strict", action="store_true", help="Fail on recoverable warnings such as missing micro owners")
    build.add_argument("--run-xtract", action="store_true", help="Run the repo xtract helper if the subject-space xtract volume is missing")
    build.add_argument("--xtract-assets-root", type=Path, help="Root containing MNI152 and xtract atlas assets for --run-xtract")
    build.add_argument(
        "--suppress-xtract-tool-output",
        "--suppress-output",
        action="store_true",
        dest="suppress_xtract_tool_output",
        help="Keep Python xtract step logs, but run BET/FLIRT/FNIRT/APPLYWARP without their verbose terminal output",
    )
    build.add_argument("--verbose", action="store_true", help="Enable info-level logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "build":
        build_electrodes_v2026(
            input_csv=args.input_csv,
            montage=args.montage,
            subject_root=args.subject_root,
            output_csv=args.output,
            ns3=args.ns3,
            ns5=args.ns5,
            strict=args.strict,
            run_xtract=args.run_xtract,
            xtract_assets_root=args.xtract_assets_root,
            suppress_xtract_tool_output=args.suppress_xtract_tool_output,
            logger=LOGGER,
        )
        return 0
    parser.error(f"Unsupported command {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
