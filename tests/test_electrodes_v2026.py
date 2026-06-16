from __future__ import annotations

import csv
import logging
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

import recon_scripts.electrodes_v2026 as formatter
from recon_scripts.electrodes_v2026 import (
    ElectrodeBuildError,
    LabelVolume,
    MicroBundle,
    MontageRow,
    NSxChannel,
    apply_bolt_rules,
    assign_nsx_metadata,
    build_electrodes_v2026,
    canonicalize_label,
    classify_matter,
    compute_manufacturer,
    load_montage,
    output_row_from_macro,
    resolve_subject_paths,
    sample_label_volume,
    synthesize_micro_row,
)


def test_canonicalize_label_handles_case_and_zero_padding() -> None:
    assert canonicalize_label("LF1aIa1") == canonicalize_label("lf1AIA01")
    assert canonicalize_label("mLT2aA01") != canonicalize_label("LT2aA01")
    assert canonicalize_label("empty") == "empty"
    assert canonicalize_label("C") == "c"


def test_compute_manufacturer_and_matter_mapping() -> None:
    assert compute_manufacturer("sEEG") == "Ad-Tech"
    assert compute_manufacturer("sEEG-micro") == "Ad-Tech"
    assert compute_manufacturer("microwires") == "Ad-Tech"
    assert compute_manufacturer("REF") == "NA"
    assert classify_matter("Left-Cerebral-White-Matter") == "White"
    assert classify_matter("Left-Hippocampus") == "Subcortical"
    assert classify_matter("ctx-rh-rostralanteriorcingulate") == "Grey"


def test_load_montage_groups_micro_bundles(tmp_path: Path) -> None:
    montage = tmp_path / "montage.xlsx"
    write_simple_xlsx(
        montage,
        {
            "Sheet1": [
                ["TBarPinID", "ElectrodeID", "ChannelLabel"],
                ["A01", "1", "LT2aA01"],
                ["A02", "2", "LT2aA02"],
            ],
            "Sheet2": [
                ["TBarPinID", "ElectrodeID", "ChannelLabel"],
                ["A01", "1", "mLT2aA01"],
                ["A02", "2", "mLT2aA02"],
                ["A03", "3", "mLT2aA03"],
                ["A04", "4", "mLT2aA04"],
                ["A05", "5", "mLT2aA05"],
                ["A06", "6", "mLT2aA06"],
                ["A07", "7", "mLT2aA07"],
                ["A08", "8", "mLT2aA08"],
            ],
        },
    )
    macros, bundles = load_montage(montage)
    assert len(macros) == 2
    assert len(bundles) == 1
    assert bundles[0].contact_range == "mLT2aA01-mLT2aA08"


def test_output_row_from_macro_maps_rave_atlas_fields() -> None:
    row = output_row_from_macro(
        MontageRow(electrode_id="33", channel_label="LT2aA01", sheet="Sheet1"),
        {
            "Electrode": "99",
            "Label": "LT2AA1",
            "Coord_x": "1",
            "Coord_y": "2",
            "Coord_z": "3",
            "LocationType": "iEEG",
            "Hemisphere": "Left",
            "FSLabel_aparc_a2009s_aseg": "Left-Hippocampus",
            "FSLabel_aparc_aseg": "Left-Cerebral-White-Matter",
            "FSLabel_aparc_DKTatlas_aseg": "ctx-lh-insula",
            "T1R": "10",
            "T1A": "11",
            "T1S": "12",
            "MRVoxel_I": "101",
            "MRVoxel_J": "102",
            "MRVoxel_K": "103",
            "MNI152_x": "20",
            "MNI152_y": "21",
            "MNI152_z": "22",
        },
    )
    assert row["ElectrodeID"] == "33"
    assert row["Label"] == "LT2aA01"
    assert row["ROI_D2009_3mm"] == "Left-Hippocampus"
    assert row["ROI_DK2005_3mm"] == "Left-Cerebral-White-Matter"
    assert row["Area_fs_vox"] == "ctx-lh-insula"
    assert row["Matter_3mm"] == "Subcortical"
    assert row["Matter_fs_vox"] == "Grey"
    assert row["Scanner_R"] == "10"
    assert row["x_vox"] == "101"


def test_resolve_subject_paths_prefers_rave_layout(tmp_path: Path) -> None:
    subject_root = tmp_path / "YFW"
    rave = subject_root / "rave-imaging"
    (rave / "localization").mkdir(parents=True)
    (rave / "derivative").mkdir(parents=True)
    (rave / "fs" / "mri").mkdir(parents=True)
    (rave / "localization" / "electrodes.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (rave / "derivative" / "transform-Norig.tsv").write_text(identity_matrix_text(), encoding="utf-8")
    (rave / "derivative" / "transform-Torig.tsv").write_text(identity_matrix_text(), encoding="utf-8")
    (rave / "fs" / "mri" / "aparc.a2009s+aseg.mgz").write_text("stub", encoding="utf-8")
    (rave / "fs" / "mri" / "aparc+aseg.mgz").write_text("stub", encoding="utf-8")

    paths = resolve_subject_paths(subject_root, "YFW")
    assert paths.rave_root == rave
    assert paths.localization_csv == rave / "localization" / "electrodes.csv"
    assert paths.transform_norig == rave / "derivative" / "transform-Norig.tsv"
    assert paths.fs_aparc_a2009s == rave / "fs" / "mri" / "aparc.a2009s+aseg.mgz"


def test_synthesize_micro_row_projects_three_mm_deeper() -> None:
    owner = {
        "Label": "LT2aA01",
        "Coord_x": "0",
        "Coord_y": "0",
        "Coord_z": "0",
        "MNI305_x": "0",
        "MNI305_y": "0",
        "MNI305_z": "0",
        "MNI152_x": "0",
        "MNI152_y": "0",
        "MNI152_z": "0",
        "Scanner_R": "0",
        "Scanner_A": "0",
        "Scanner_S": "0",
        "x_vox": "0",
        "y_vox": "0",
        "z_vox": "0",
        "Bolt": "0",
    }
    shaft_rows = [
        {"Label": "LT2aA01", "Coord_x": "0", "Coord_y": "0", "Coord_z": "0", "MNI305_x": "0", "MNI305_y": "0", "MNI305_z": "0", "MNI152_x": "0", "MNI152_y": "0", "MNI152_z": "0"},
        {"Label": "LT2aA02", "Coord_x": "0", "Coord_y": "0", "Coord_z": "1", "MNI305_x": "0", "MNI305_y": "0", "MNI305_z": "1", "MNI152_x": "0", "MNI152_y": "0", "MNI152_z": "1"},
        {"Label": "LT2aA03", "Coord_x": "0", "Coord_y": "0", "Coord_z": "2", "MNI305_x": "0", "MNI305_y": "0", "MNI305_z": "2", "MNI152_x": "0", "MNI152_y": "0", "MNI152_z": "2"},
    ]
    row = synthesize_micro_row(
        MicroBundle(
            stem="mLT2aA",
            representative_label="mLT2aA01",
            contact_labels=[f"mLT2aA0{i}" for i in range(1, 9)],
            electrode_ids=[str(i) for i in range(1, 9)],
        ),
        owner,
        shaft_rows,
        synthetic_electrode_id=257,
    )
    assert row["ElectrodeID"] == "257"
    assert row["Coord_z"] == "-3"
    assert row["MNI305_z"] == "-3.15"
    assert row["MicroContactRange"] == "mLT2aA01-mLT2aA08"
    assert row["MontageElectrodeIDRange"] == "1-8"


def test_sample_label_volume_returns_majority_label() -> None:
    data = np.zeros((7, 7, 7), dtype=float)
    data[3, 3, 3] = 17
    data[3, 3, 4] = 17
    data[3, 4, 3] = 5
    volume = LabelVolume(
        data=data,
        affine=np.eye(4),
        labels={17: "Left-Hippocampus", 5: "Left-Cerebral-White-Matter"},
    )
    label = sample_label_volume(volume, np.array([3.0, 3.0, 3.0]), radius_mm=1.5)
    assert label == "Left-Hippocampus"


def test_assign_nsx_metadata_falls_back_to_electrode_id_range() -> None:
    output_rows = [
        {"Label": "LT2aA01", "ElectrodeID": "33", "Type": "sEEG", "NSxSource": "", "NSxIndex": "", "NSxElectrodeID": ""},
        {"Label": "mLT2aA01", "ElectrodeID": "257", "Type": "microwires", "NSxSource": "", "NSxIndex": "", "NSxElectrodeID": ""},
    ]
    bundles = [
        MicroBundle(
            stem="mLT2aA",
            representative_label="mLT2aA01",
            contact_labels=[f"mLT2aA0{i}" for i in range(1, 9)],
            electrode_ids=[str(i) for i in range(89, 97)],
        )
    ]
    ns3 = [NSxChannel(source="ns3", index=33, electrode_id="33", label="LT2aA01")]
    ns5 = [NSxChannel(source="ns5", index=index, electrode_id=str(electrode_id), label="") for index, electrode_id in enumerate(range(89, 97), start=1)]

    assign_nsx_metadata(output_rows, bundles, ns3, ns5, logging.getLogger("test"))

    assert output_rows[0]["NSxSource"] == "ns3"
    assert output_rows[0]["NSxIndex"] == "33"
    assert output_rows[1]["NSxSource"] == "ns5"
    assert output_rows[1]["NSxIndex"] == "1-8"
    assert output_rows[1]["NSxElectrodeID"] == "89-96"


def test_assign_nsx_metadata_matches_ns5_labels_with_suffixes() -> None:
    output_rows = [
        {"Label": "mLT2aA01", "ElectrodeID": "257", "Type": "microwires", "NSxSource": "", "NSxIndex": "", "NSxElectrodeID": ""},
    ]
    bundles = [
        MicroBundle(
            stem="mLT2aA",
            representative_label="mLT2aA01",
            contact_labels=[f"mLT2aA0{i}" for i in range(1, 9)],
            electrode_ids=[str(i) for i in range(89, 97)],
        )
    ]
    ns5 = [
        NSxChannel(source="ns5", index=index, electrode_id=str(372 + index), label=f"mLT2aA0{i}-{index:03d}")
        for index, i in enumerate(range(1, 9), start=1)
    ]

    assign_nsx_metadata(output_rows, bundles, [], ns5, logging.getLogger("test"))

    assert output_rows[0]["NSxSource"] == "ns5"
    assert output_rows[0]["NSxIndex"] == "1-8"
    assert output_rows[0]["NSxElectrodeID"] == "373-380"


def test_apply_bolt_rules_marks_terminal_unknown_contact() -> None:
    rows = [
        {"Label": "LT2aA01", "Matter_3mm": "Grey", "Matter_fs_vox": "Unknown", "Bolt": "0"},
        {"Label": "LT2aA02", "Matter_3mm": "Grey", "Matter_fs_vox": "Unknown", "Bolt": "0"},
        {"Label": "LT2aA03", "Matter_3mm": "Grey", "Matter_fs_vox": "Unknown", "Bolt": "0"},
    ]
    apply_bolt_rules(rows)
    assert [row["Bolt"] for row in rows] == ["0", "0", "1"]


def test_build_electrodes_v2026_rave_layout_integration(tmp_path: Path) -> None:
    input_csv = tmp_path / "electrodes.csv"
    montage = tmp_path / "montage.xlsx"
    subject_root = tmp_path / "YFW"
    rave = subject_root / "rave-imaging"
    (rave / "derivative").mkdir(parents=True)
    (rave / "localization").mkdir(parents=True)
    (rave / "fs" / "mri").mkdir(parents=True)
    (rave / "derivative" / "transform-Norig.tsv").write_text(identity_matrix_text(), encoding="utf-8")
    (rave / "derivative" / "transform-Torig.tsv").write_text(identity_matrix_text(), encoding="utf-8")
    (rave / "localization" / "electrodes.csv").write_text("placeholder\n", encoding="utf-8")

    write_csv(
        input_csv,
        [
            {
                "Electrode": "33",
                "Label": "LT2AA1",
                "Coord_x": "0",
                "Coord_y": "0",
                "Coord_z": "0",
                "MNI305_x": "10",
                "MNI305_y": "0",
                "MNI305_z": "0",
                "MNI152_x": "20",
                "MNI152_y": "0",
                "MNI152_z": "0",
                "LocationType": "iEEG",
                "Hemisphere": "Left",
                "MRVoxel_I": "100",
                "MRVoxel_J": "100",
                "MRVoxel_K": "100",
                "T1R": "0",
                "T1A": "0",
                "T1S": "0",
                "FSLabel_aparc_a2009s_aseg": "Left-Hippocampus",
                "FSLabel_aparc_aseg": "Left-Cerebral-White-Matter",
                "FSLabel_aparc_DKTatlas_aseg": "ctx-lh-insula",
                "SubjectCode": "YFW",
            },
            {
                "Electrode": "34",
                "Label": "LT2AA2",
                "Coord_x": "0",
                "Coord_y": "0",
                "Coord_z": "1",
                "MNI305_x": "10",
                "MNI305_y": "0",
                "MNI305_z": "1",
                "MNI152_x": "20",
                "MNI152_y": "0",
                "MNI152_z": "1",
                "LocationType": "iEEG",
                "Hemisphere": "Left",
                "MRVoxel_I": "100",
                "MRVoxel_J": "100",
                "MRVoxel_K": "101",
                "T1R": "0",
                "T1A": "0",
                "T1S": "1",
                "FSLabel_aparc_a2009s_aseg": "Left-Hippocampus",
                "FSLabel_aparc_aseg": "Left-Cerebral-White-Matter",
                "FSLabel_aparc_DKTatlas_aseg": "ctx-lh-insula",
                "SubjectCode": "YFW",
            },
        ],
    )

    write_simple_xlsx(
        montage,
        {
            "Sheet1": [
                ["TBarPinID", "ElectrodeID", "ChannelLabel"],
                ["A01", "33", "LT2aA01"],
                ["A02", "34", "LT2aA02"],
            ],
            "Sheet2": [
                ["TBarPinID", "ElectrodeID", "ChannelLabel"],
                ["A01", "89", "mLT2aA01"],
                ["A02", "90", "mLT2aA02"],
                ["A03", "91", "mLT2aA03"],
                ["A04", "92", "mLT2aA04"],
                ["A05", "93", "mLT2aA05"],
                ["A06", "94", "mLT2aA06"],
                ["A07", "95", "mLT2aA07"],
                ["A08", "96", "mLT2aA08"],
            ],
        },
    )

    original_parser = formatter.parse_nsx_channels
    formatter.parse_nsx_channels = lambda path, source, logger: (
        [NSxChannel(source="ns3", index=33, electrode_id="33", label="LT2aA01"), NSxChannel(source="ns3", index=34, electrode_id="34", label="LT2aA02")]
        if source == "ns3"
        else [NSxChannel(source="ns5", index=index, electrode_id=str(electrode_id), label="") for index, electrode_id in enumerate(range(89, 97), start=1)]
    )
    try:
        output = build_electrodes_v2026(
            input_csv=input_csv,
            montage=montage,
            subject_root=subject_root,
            ns3=tmp_path / "fake.ns3",
            ns5=tmp_path / "fake.ns5",
        )
    finally:
        formatter.parse_nsx_channels = original_parser

    rows = read_csv(output)
    assert output.name == "YFW-electrodes_v2026.csv"
    assert len(rows) == 3
    assert rows[0]["Label"] == "LT2aA01"
    assert rows[0]["Type"] == "sEEG-micro"
    assert rows[0]["ROI_D2009_3mm"] == "Left-Hippocampus"
    assert rows[0]["Area_fs_vox"] == "ctx-lh-insula"
    assert rows[0]["Matter_fs_vox"] == "Grey"
    assert rows[2]["Label"] == "mLT2aA01"
    assert rows[2]["Type"] == "microwires"
    assert rows[2]["ElectrodeID"] == "35"
    assert rows[2]["MicroContactRange"] == "mLT2aA01-mLT2aA08"
    assert rows[2]["MontageElectrodeIDRange"] == "89-96"
    assert rows[2]["NSxSource"] == "ns5"
    assert rows[2]["NSxIndex"] == "1-8"
    assert rows[2]["NSxElectrodeID"] == "89-96"
    assert rows[2]["ROI_D2009_3mm"] == "Left-Hippocampus"


def test_build_electrodes_v2026_raises_for_missing_macro(tmp_path: Path) -> None:
    input_csv = tmp_path / "electrodes.csv"
    montage = tmp_path / "montage.xlsx"
    subject_root = tmp_path / "subject"
    subject_root.mkdir()

    write_csv(input_csv, [{"Electrode": "1", "Label": "LT2AA1", "SubjectCode": "YFW"}])
    write_simple_xlsx(
        montage,
        {"Sheet1": [["TBarPinID", "ElectrodeID", "ChannelLabel"], ["A01", "1", "LT2aA01"], ["A02", "2", "LT2aA02"]], "Sheet2": [["TBarPinID", "ElectrodeID", "ChannelLabel"]]},
    )

    try:
        build_electrodes_v2026(input_csv, montage, subject_root)
    except ElectrodeBuildError as exc:
        assert "missing" in str(exc).lower()
    else:
        raise AssertionError("Expected missing macro mismatch to raise ElectrodeBuildError")


def test_xtract_helper_script_exists() -> None:
    path = Path("scripts/run_xtract_rave_subject.sh")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "rave-imaging" in text
    assert "derivative_root" in text
    assert "RECON_XTRACT_TOOL_VERBOSE" in text


def test_arg_parser_accepts_xtract_flags() -> None:
    parser = formatter.build_arg_parser()
    args = parser.parse_args([
        "build",
        "--input-csv", "in.csv",
        "--montage", "montage.xlsx",
        "--subject-root", "subject",
        "--run-xtract",
        "--xtract-assets-root", "assets",
        "--suppress-xtract-tool-output",
    ])
    assert args.run_xtract is True
    assert str(args.xtract_assets_root) == "assets"
    assert args.suppress_xtract_tool_output is True


def test_arg_parser_accepts_suppress_output_alias() -> None:
    parser = formatter.build_arg_parser()
    args = parser.parse_args([
        "build",
        "--input-csv", "in.csv",
        "--montage", "montage.xlsx",
        "--subject-root", "subject",
        "--suppress-output",
    ])
    assert args.suppress_xtract_tool_output is True


def test_run_xtract_helper_sets_tool_verbosity_env() -> None:
    calls: list[dict[str, object]] = []
    original_run = formatter.subprocess.run

    def fake_run(command: list[str], check: bool, env: dict[str, str]) -> None:
        calls.append({"command": command, "check": check, "env": env})

    formatter.subprocess.run = fake_run
    try:
        formatter.run_xtract_helper(
            Path("subject"),
            Path("assets"),
            logging.getLogger("test"),
            suppress_xtract_tool_output=True,
        )
    finally:
        formatter.subprocess.run = original_run

    assert calls
    assert calls[0]["command"][0] == "bash"
    assert calls[0]["check"] is True
    assert calls[0]["env"]["RECON_XTRACT_TOOL_VERBOSE"] == "0"


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def identity_matrix_text() -> str:
    return "\n".join([
        "1 0 0 0",
        "0 1 0 0",
        "0 0 1 0",
        "0 0 0 1",
    ]) + "\n"


def write_simple_xlsx(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    shared: list[str] = []
    shared_index: dict[str, int] = {}

    def shared_id(value: str) -> int:
        if value not in shared_index:
            shared_index[value] = len(shared)
            shared.append(value)
        return shared_index[value]

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>',
    ]
    for idx in range(1, len(sheets) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    workbook = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>',
    ]
    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]

    sheet_xmls: dict[str, str] = {}
    for idx, (sheet_name, rows) in enumerate(sheets.items(), start=1):
        workbook.append(f'<sheet name="{escape(sheet_name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        workbook_rels.append(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        )
        xml_rows = []
        for row_idx, row in enumerate(rows, start=1):
            cells = []
            for col_idx, value in enumerate(row, start=1):
                col = column_letters(col_idx)
                sid = shared_id(str(value))
                cells.append(f'<c r="{col}{row_idx}" t="s"><v>{sid}</v></c>')
            xml_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
        sheet_xmls[f"xl/worksheets/sheet{idx}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(xml_rows)
            + "</sheetData></worksheet>"
        )

    workbook.append("</sheets></workbook>")
    workbook_rels.append(
        '<Relationship Id="rIdShared" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
    )
    workbook_rels.append("</Relationships>")

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    shared_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared)}" uniqueCount="{len(shared)}">',
    ]
    for value in shared:
        shared_xml.append(f"<si><t>{escape(value)}</t></si>")
    shared_xml.append("</sst>")

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", "".join(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        archive.writestr("xl/sharedStrings.xml", "".join(shared_xml))
        for name, xml in sheet_xmls.items():
            archive.writestr(name, xml)


def column_letters(index: int) -> str:
    letters = []
    while index:
        index, rem = divmod(index - 1, 26)
        letters.append(chr(ord("A") + rem))
    return "".join(reversed(letters))
