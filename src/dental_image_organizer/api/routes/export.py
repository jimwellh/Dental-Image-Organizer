"""Case and patient export jobs, and their zip downloads (spec §3.2 step 7, §3.4, §4.9)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from dental_image_organizer.api.deps import ZIP_RESPONSE, Ctx, run_cpu, zip_download
from dental_image_organizer.api.models import ExportRequest, JobRef
from dental_image_organizer.services import export

router = APIRouter()


@router.post("/cases/{case_id}/export")
def export_case(case_id: str, body: ExportRequest, ctx: Ctx) -> JobRef:
    # Plain defs: starting an export only reads the records and queues the job.
    return JobRef(job_id=export.start_case_export(ctx, case_id, body.include, body.conflict))


@router.get(
    "/cases/{case_id}/export/{job_id}/zip", response_class=FileResponse, responses=ZIP_RESPONSE
)
async def case_export_zip(case_id: str, job_id: str, ctx: Ctx) -> FileResponse:
    path, name = await run_cpu(ctx, export.export_zip, ctx, job_id, case_id=case_id)
    return zip_download(path, name)


@router.post("/patients/{patient_id}/export")
def export_patient(patient_id: str, body: ExportRequest, ctx: Ctx) -> JobRef:
    job_id = export.start_patient_export(ctx, patient_id, body.include, body.conflict)
    return JobRef(job_id=job_id)


@router.get(
    "/patients/{patient_id}/export/{job_id}/zip",
    response_class=FileResponse,
    responses=ZIP_RESPONSE,
)
async def patient_export_zip(patient_id: str, job_id: str, ctx: Ctx) -> FileResponse:
    path, name = await run_cpu(ctx, export.export_zip, ctx, job_id, patient_id=patient_id)
    return zip_download(path, name)
