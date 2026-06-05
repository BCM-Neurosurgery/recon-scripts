from __future__ import annotations

import csv
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from recon_scripts.electrodes_v2026 import (
    ElectrodeBuildError,
    apply_bolt_rules,
    build_electrodes_v2026,
    canonicalize_label,
    compute_manufacturer,
    load_montage,
    synthesize_micro_row,
)


def test_canonicalize_label_handles_case_and_zero_padding() -> None:
    assert canonicalize_label("LF1aIa1") == canonicalize_label("lf1AIA01")
    assert canonicalize_label("mLT2aA01") != canonicalize_label("LT2aA01")
    assert canonicalize_label("empty") == "empty"
    assert canonicalize_label("C") == "c"


def test_compute_manufacturer_mapping() -> None:
    assert compute_manufacturer("sEEG") == "Ad-Tech"
    assert compute_manufacturer("sEEG-micro") == "Ad-Tech"
    assert compute_manufacturer("microwires") == "Ad-Tech"
    assert compute_manufacturer("REF") == "NA"


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
        "ROI_D2009_3mm": "Left-Hippocampus",
        "Matter_3mm": "Grey",
        "ROI_DK2005_3mm": "Left-Hippocampus",
        "ROI_XTRACT_3mm": "Unknown",
        "Area_fs_vox": "Left-Hippocampus",
        "Matter_fs_vox": "Grey",
        "Bolt": "0",
    }
    shaft_rows = [
        {"Label": "LT2aA01", "Coord_x": "0", "Coord_y": "0", "Coord_z": "0", "MNI305_x": "0", "MNI305_y": "0", "MNI305_z": "0", "MNI152_x": "0", "MNI152_y": "0", "MNI152_z": "0", "Scanner_R": "0", "Scanner_A": "0", "Scanner_S": "0", "x_vox": "0", "y_vox": "0", "z_vox": "0"},
        {"Label": "LT2aA02", "Coord_x": "0", "Coord_y": "0", "Coord_z": "1", "MNI305_x": "0", "MNI305_y": "0", "MNI305_z": "1", "MNI152_x": "0", "MNI152_y": "0", "MNI152_z": "1", "Scanner_R": "0", "Scanner_A": "0", "Scanner_S": "1", "x_vox": "0", "y_vox": "0", "z_vox": "1"},
        {"Label": "LT2aA03", "Coord_x": "0", "Coord_y": "0", "Coord_z": "2", "MNI305_x": "0", "MNI305_y": "0", "MNI305_z": "2", "MNI152_x": "0", "MNI152_y": "0", "MNI152_z": "2", "Scanner_R": "0", "Scanner_A": "0", "Scanner_S": "2", "x_vox": "0", "y_vox": "0", "z_vox": "2"},
    ]
    from recon_scripts.electrodes_v2026 import MicroBundle

    row = synthesize_micro_row(
        MicroBundle(
            stem="mLT2aA",
            representative_label="mLT2aA01",
            contact_labels=[f"mLT2aA0{i}" for i in range(1, 9)],
            electrode_ids=[str(i) for i in range(1, 9)],
        ),
        owner,
        shaft_rows,
    )
    assert row["Coord_z"] == "-3"
    assert row["MNI305_z"] == "-3.15"
    assert row["MicroContactRange"] == "mLT2aA01-mLT2aA08"


def test_apply_bolt_rules_marks_terminal_unknown_contact() -> None:
    rows = [
        {"Label": "LT2aA01", "Matter_3mm": "Grey", "Matter_fs_vox": "Unknown", "Bolt": "0"},
        {"Label": "LT2aA02", "Matter_3mm": "Grey", "Matter_fs_vox": "Unknown", "Bolt": "0"},
        {"Label": "LT2aA03", "Matter_3mm": "Grey", "Matter_fs_vox": "Unknown", "Bolt": "0"},
    ]
    apply_bolt_rules(rows)
    assert [row["Bolt"] for row in rows] == ["0", "0", "1"]


def test_build_electrodes_v2026_integration(tmp_path: Path) -> None:
    input_csv = tmp_path / "electrodes.csv"
    montage = tmp_path / "montage.xlsx"
    subject_root = tmp_path / "subject"
    elec_recon = subject_root / "elec_recon"
    elec_recon.mkdir(parents=True)

    write_csv(
        input_csv,
        [
            {
                "Electrode": "1",
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
                "FSLabel": "Left-Hippocampus",
                "FSLabel_aparc_a2009s_aseg": "",
                "FSLabel_aparc_aseg": "",
                "LocationType": "iEEG",
                "Hemisphere": "Left",
                "MRVoxel_I": "100",
                "MRVoxel_J": "100",
                "MRVoxel_K": "100",
                "SubjectCode": "YFW",
            },
            {
                "Electrode": "2",
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
                "SubjectCode": "YFW",
            },
        ],
    )

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

    write_simple_xlsx(
        elec_recon / "YFW_D2009Vol_ElectrodeLabelsRadius_3mm.xlsx",
        {
            "Sheet1": [
                ["Electrode", "Label_Voxel_Radius", "Matter_Voxel_Radius"],
                ["LT2aA01", "Left-Hippocampus", "Grey"],
                ["LT2aA02", "Left-Hippocampus", "Grey"],
            ]
        },
    )
    write_simple_xlsx(
        elec_recon / "YFW_DK2005Vol_ElectrodeLabelsRadius_3mm.xlsx",
        {
            "Sheet1": [
                ["Electrode", "Label_Voxel_Radius"],
                ["LT2aA01", "Left-Hippocampus"],
                ["LT2aA02", "Left-Hippocampus"],
            ]
        },
    )
    write_simple_xlsx(
        elec_recon / "YFW_xtract_ElectrodeLabelsRadius_3mm.xlsx",
        {
            "Sheet1": [
                ["Electrode", "CorrectedLabel"],
                ["LT2aA01", "Unknown"],
                ["LT2aA02", "Unknown"],
            ]
        },
    )
    write_csv(
        elec_recon / "YFW_DK_AtlasLabels.csv",
        [
            {"Label": "LT2aA01", "Area": "Left-Hippocampus"},
            {"Label": "LT2aA02", "Area": "Left-Hippocampus"},
        ],
    )

    output = build_electrodes_v2026(
        input_csv=input_csv,
        montage=montage,
        subject_root=subject_root,
    )
    rows = read_csv(output)
    assert output.name == "YFW-electrodes_v2026.csv"
    assert len(rows) == 3
    assert rows[0]["Label"] == "LT2aA01"
    assert rows[0]["Type"] == "sEEG-micro"
    assert rows[1]["Label"] == "LT2aA02"
    assert rows[2]["Label"] == "mLT2aA01"
    assert rows[2]["Type"] == "microwires"
    assert rows[2]["MicroContactRange"] == "mLT2aA01-mLT2aA08"
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


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
        workbook.append(
            f'<sheet name="{escape(sheet_name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        )
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
