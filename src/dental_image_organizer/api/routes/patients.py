"""The library listing, and a patient's name and note (spec §4.11, §4.12)."""

from __future__ import annotations

from fastapi import APIRouter

from dental_image_organizer.api.deps import Ctx
from dental_image_organizer.api.models import PatientPatch, PatientsView, PatientView
from dental_image_organizer.services import cases

router = APIRouter()


@router.get("/patients")
def list_patients(ctx: Ctx) -> PatientsView:
    # A plain def: the listing checks every patient and case file on disk, so it runs in the
    # thread pool, off the event loop.
    return PatientsView.of(ctx.library().listing())


@router.patch("/patients/{patient_id}")
def patch_patient(patient_id: str, body: PatientPatch, ctx: Ctx) -> PatientView:
    library = ctx.library()
    current = library.patient_record(patient_id)
    # PatientPatch refuses a null name, so None here means the name was not sent.
    name = body.name if body.name is not None else current.name
    note = body.note if "note" in body.model_fields_set else current.note
    cases.update_patient(ctx, patient_id, name=name, note=note)
    return PatientView.of(library.patient(patient_id))
