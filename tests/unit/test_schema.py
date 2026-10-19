"""The versioned case.json and patient.json records (spec §4.11)."""

import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest

from dental_image_organizer.core.color import LabAffine
from dental_image_organizer.errors import PhotoNotFoundError, StorageError
from dental_image_organizer.storage import schema
from dental_image_organizer.storage.schema import (
    CaseRecord,
    ColorRecord,
    CompositeRecord,
    EditRecord,
    PatientRecord,
    PhotoRecord,
    SequenceRecord,
    dump,
    new_id,
    parse_case,
    parse_patient,
)

NOW = datetime(2026, 10, 16, 14, 30, tzinfo=UTC)
IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def make_edit(**changes: Any) -> EditRecord:
    fields: dict[str, Any] = {
        "transform": IDENTITY,
        "model": "similarity",
        "output_size": (4032, 3024),
        "color": ColorRecord(method="identity"),
        "status": "ok",
        "algorithm_version": "align-1",
    }
    return EditRecord(**{**fields, **changes})


def make_photo(**changes: Any) -> PhotoRecord:
    sha = new_id() + new_id()
    fields: dict[str, Any] = {
        "id": new_id(),
        "sha256": sha,
        "file": f"originals/{sha[:16]}.jpg",
        "source_name": "IMG_1.jpg",
        "role": "photo",
    }
    return PhotoRecord(**{**fields, **changes})


def make_procedure() -> CaseRecord:
    ref = make_photo(role="reference", stage="pre_op", state="ready", edit=make_edit())
    other = make_photo(stage="intra_op")
    return CaseRecord(
        id=new_id(),
        type="procedure",
        patient_id=new_id(),
        title="implant",
        output_scale=1.0,
        created_at=NOW,
        updated_at=NOW,
        photos=[ref, other],
        sequence=SequenceRecord(photo_ids=[ref.id, other.id], gap_px=4),
        composite=None,
    )


def make_composite() -> CaseRecord:
    a = make_photo(role="member", slot="upper_right", edit=make_edit(flip="horizontal"))
    b = make_photo(role="member", slot="upper_left")
    c = make_photo(role="member")
    return CaseRecord(
        id=new_id(),
        type="composite",
        patient_id=new_id(),
        title="evaluation",
        output_scale=None,
        created_at=NOW,
        updated_at=NOW,
        photos=[a, b, c],
        sequence=None,
        composite=CompositeRecord(
            baseline_photo_id=a.id,
            cell_size=(1200, 800),
            gap_px=0,
            gap_color="#000000",
            slots_source="auto",
        ),
    )


def as_doc(case: CaseRecord) -> dict[str, Any]:
    return json.loads(dump(case))


def test_the_spec_example_parses() -> None:
    photo_id = new_id()
    doc = {
        "schema_version": 1,
        "id": new_id(),
        "type": "procedure",
        "patient_id": new_id(),
        "title": "implant maxillary right first molar",
        "output_scale": 1.0,
        "created_at": "2026-10-16T14:30:00+08:00",
        "updated_at": "2026-10-16T15:02:11+08:00",
        "photos": [
            {
                "id": photo_id,
                "sha256": "ab" * 32,
                "file": "originals/9b1f0c2d44aa01ee.heic",
                "source_name": "IMG_1234.HEIC",
                "width": 4032,
                "height": 3024,
                "captured_at": "2026-10-16T14:30:12+08:00",
                "captured_at_source": "exif",
                "role": "reference",
                "state": "ready",
                "error": None,
                "stage": "pre_op",
                "stage_source": "auto",
                "slot": None,
                "edit": {
                    "transform": IDENTITY,
                    "model": "similarity",
                    "output_size": [4032, 3024],
                    "detail_ratio": 1.0,
                    "color": {"method": "identity", "params": {}},
                    "source": "auto",
                    "confidence": 1.0,
                    "status": "ok",
                    "message": None,
                    "algorithm_version": "align-1",
                },
            }
        ],
        "sequence": {"photo_ids": [], "gap_px": 0},
        "composite": None,
        "exports": [
            {"file": "export/a.jpg", "kind": "single", "created_at": "2026-10-16T15:00:00+08:00"}
        ],
    }
    case = parse_case(json.dumps(doc).encode())
    photo = case.photo(photo_id)
    assert case.reference() == photo
    assert photo.role == "reference"
    assert photo.stage == "pre_op"
    assert photo.edit is not None
    assert photo.edit.color.method == "identity"
    assert photo.edit.color_transform() is None


def test_records_round_trip() -> None:
    for case in (make_procedure(), make_composite()):
        assert parse_case(dump(case)) == case
    patient = PatientRecord(id=new_id(), name="Alex Wang", created_at=NOW)
    assert parse_patient(dump(patient)) == patient


def test_future_schema_version_asks_for_an_update() -> None:
    with pytest.raises(StorageError, match="newer version"):
        parse_case(b'{"schema_version": 2, "anything": true}')
    with pytest.raises(StorageError, match="newer version"):
        parse_patient(b'{"schema_version": 2}')


@pytest.mark.parametrize(
    "data",
    [
        b"{",
        b"[]",
        json.dumps({k: v for k, v in as_doc(make_procedure()).items() if k != "title"}).encode(),
    ],
    ids=["truncated", "not-an-object", "missing-title"],
)
def test_damaged_json_is_a_storage_error(data: bytes) -> None:
    with pytest.raises(StorageError, match="damaged"):
        parse_case(data)


def test_unknown_fields_are_rejected() -> None:
    doc = as_doc(make_procedure())
    doc["notes"] = "x"
    with pytest.raises(StorageError):
        parse_case(json.dumps(doc).encode())


def _edit_photo(doc: dict[str, Any], index: int, **changes: Any) -> dict[str, Any]:
    doc["photos"][index].update(changes)
    return doc


def _invalid_cases() -> dict[str, dict[str, Any]]:
    proc_case = make_procedure()
    proc, comp = as_doc(proc_case), as_doc(make_composite())
    cases: dict[str, dict[str, Any]] = {}

    def add(name: str, base: dict[str, Any], **top: Any) -> dict[str, Any]:
        doc = json.loads(json.dumps(base))
        doc.update(top)
        cases[name] = doc
        return doc

    add("procedure_with_composite", proc, composite=comp["composite"])
    add("composite_with_scale", comp, output_scale=1.0)
    _edit_photo(add("two_references", proc), 1, role="reference")
    _edit_photo(add("duplicate_ids", proc), 1, id=proc["photos"][0]["id"])
    add("sequence_id_is_no_photo", proc, sequence={"photo_ids": [new_id()], "gap_px": 0})
    add(
        "unknown_baseline",
        comp,
        composite={**comp["composite"], "baseline_photo_id": new_id()},
    )
    _edit_photo(add("two_photos_in_one_slot", comp), 1, slot="upper_right")
    _edit_photo(add("parent_traversal", proc), 1, file="../x.jpg")
    _edit_photo(add("nested_traversal", proc), 1, file="originals/../../a.jpg")
    add("scale_3", proc, output_scale=3)
    _edit_photo(add("slot_in_procedure", proc), 1, slot="upper_left")
    _edit_photo(add("stage_in_composite", comp), 1, stage="pre_op")
    _edit_photo(add("member_in_procedure", proc), 1, role="member")
    _edit_photo(add("flip_in_procedure", proc), 0, edit=make_edit(flip="none").model_dump())
    add("procedure_without_sequence", proc, sequence=None)
    for label, cell in (("small", [50, 800]), ("large", [5000, 800])):
        add(f"cell_size_{label}", comp, composite={**comp["composite"], "cell_size": cell})
    for label, gap in (("negative", -1), ("large", 201)):
        add(f"gap_px_{label}", comp, composite={**comp["composite"], "gap_px": gap})
    add("gap_color_name", comp, composite={**comp["composite"], "gap_color": "red"})
    for label, size in (("side", [70000, 10]), ("pixels", [20000, 20000])):
        edit = as_doc(proc_case)["photos"][0]["edit"] | {"output_size": size}
        _edit_photo(add(f"output_size_{label}", proc), 0, edit=edit)
    return cases


@pytest.mark.parametrize("name", list(_invalid_cases()))
def test_invalid_cases_are_rejected(name: str) -> None:
    with pytest.raises(StorageError):
        parse_case(json.dumps(_invalid_cases()[name], default=str).encode())


def test_render_key_ignores_status_and_message() -> None:
    base = make_edit()
    other = make_edit(status="needs_review", message="check", confidence=0.2, source="manual")
    assert base.render_key() == other.render_key()
    moved = make_edit(transform=[1.0, 0.0, 5.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
    assert moved.render_key() != base.render_key()


def test_edit_color_transform_round_trips() -> None:
    affine = LabAffine(gains=(1.1, 0.9, 1.0), offsets=(2.0, -1.0, 0.5))
    edit = make_edit(color=ColorRecord(method="lab_reinhard_v1", params=affine.to_params()))
    lab = np.random.default_rng(0).uniform(-50, 100, (4, 5, 3)).astype(np.float32)
    transform = edit.color_transform()
    assert transform is not None
    np.testing.assert_allclose(transform.apply_lab(lab), affine.apply_lab(lab))


def test_case_helpers() -> None:
    case = make_procedure()
    with pytest.raises(PhotoNotFoundError):
        case.photo(new_id())
    target = case.photos[1]
    replaced = case.replace_photo(target.model_copy(update={"stage": "post_op"}))
    assert replaced.photo(target.id).stage == "post_op"
    assert replaced.validated() == replaced
    assert schema.OUTPUT_SCALES == (1.0, 1.5, 2.0)


def test_composite_layout_at_the_bounds_is_accepted() -> None:
    doc = as_doc(make_composite())
    doc["composite"].update(cell_size=[100, 4000], gap_px=200, gap_color="#FFaa00")
    assert parse_case(json.dumps(doc).encode()).composite is not None
