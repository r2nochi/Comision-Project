from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .excel_exporter import export_results
from .pipeline import process_file
from .utils import sanitize_output_stem


MANIFEST_VERSION = 1
QUEUE_VERSION = 1


@dataclass(slots=True)
class JobManifest:
    manifest_path: Path
    manifest_version: int
    job_id: str
    job_name: str
    source_pdf: Path
    expected_insurer: str
    output_root: Path


@dataclass(slots=True)
class QueueManifest:
    queue_path: Path
    queue_version: int
    queue_name: str
    jobs: list[Path]


def build_job_manifests(
    *,
    input_dir: str | Path,
    manifests_dir: str | Path,
    queue_path: str | Path,
    output_root: str | Path,
    include_scans: bool = True,
    expected_insurer: str = "AUTO",
) -> tuple[list[Path], Path]:
    input_root = Path(input_dir).resolve()
    manifests_root = Path(manifests_dir).resolve()
    output_root_path = Path(output_root).resolve()
    queue_file = Path(queue_path).resolve()

    manifests_root.mkdir(parents=True, exist_ok=True)
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    output_root_path.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_root.glob("*.pdf"))
    if not include_scans:
        pdf_files = [path for path in pdf_files if not path.stem.lower().endswith("_scan")]

    created_at = _now_iso()
    manifest_paths: list[Path] = []
    jobs_payload: list[dict[str, str]] = []

    for index, pdf_path in enumerate(pdf_files, start=1):
        job_id = sanitize_output_stem(pdf_path.stem).lower()
        manifest_name = f"{index:03d}__{sanitize_output_stem(pdf_path.stem)}.job.json"
        manifest_path = manifests_root / manifest_name
        payload = {
            "manifest_version": MANIFEST_VERSION,
            "job_id": job_id,
            "job_name": pdf_path.stem,
            "source_pdf": _to_posix_relative(pdf_path.resolve(), manifest_path.parent),
            "expected_insurer": expected_insurer,
            "output_root": _to_posix_relative(output_root_path, manifest_path.parent),
            "created_at": created_at,
            "mode": "AUTO" if expected_insurer.upper() == "AUTO" else expected_insurer.upper(),
        }
        manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest_paths.append(manifest_path)
        jobs_payload.append(
            {
                "job_id": job_id,
                "job_name": pdf_path.stem,
                "manifest": _to_posix_relative(manifest_path, queue_file.parent),
            }
        )

    queue_payload = {
        "queue_version": QUEUE_VERSION,
        "queue_name": queue_file.stem,
        "created_at": created_at,
        "job_count": len(jobs_payload),
        "jobs": jobs_payload,
    }
    queue_file.write_text(json.dumps(queue_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_paths, queue_file


def load_job_manifest(manifest_path: str | Path) -> JobManifest:
    path = Path(manifest_path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return JobManifest(
        manifest_path=path,
        manifest_version=int(payload["manifest_version"]),
        job_id=str(payload["job_id"]),
        job_name=str(payload["job_name"]),
        source_pdf=_resolve_from_manifest(path, str(payload["source_pdf"])),
        expected_insurer=str(payload.get("expected_insurer", "AUTO")),
        output_root=_resolve_from_manifest(path, str(payload["output_root"])),
    )


def load_queue_manifest(queue_path: str | Path) -> QueueManifest:
    path = Path(queue_path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = [_resolve_from_manifest(path, str(job["manifest"])) for job in payload.get("jobs", [])]
    return QueueManifest(
        queue_path=path,
        queue_version=int(payload["queue_version"]),
        queue_name=str(payload["queue_name"]),
        jobs=jobs,
    )


def run_job(
    manifest_path: str | Path,
    *,
    run_root: str | Path | None = None,
) -> dict:
    manifest = load_job_manifest(manifest_path)
    timestamp = _now_stamp()
    base_run_root = Path(run_root).resolve() if run_root else manifest.output_root.resolve()
    job_run_dir = base_run_root / f"{timestamp}__{manifest.job_id}"
    job_run_dir.mkdir(parents=True, exist_ok=True)

    document = process_file(manifest.source_pdf, expected_insurer=manifest.expected_insurer)
    excel_name = f"{timestamp}__{sanitize_output_stem(manifest.source_pdf.stem)}__{sanitize_output_stem(document.detected_insurer)}.xlsx"
    excel_path = export_results([document], job_run_dir / excel_name)

    result_payload = {
        "job_id": manifest.job_id,
        "job_name": manifest.job_name,
        "manifest_path": str(manifest.manifest_path),
        "source_pdf": str(manifest.source_pdf),
        "expected_insurer": manifest.expected_insurer,
        "detected_insurer": document.detected_insurer,
        "detected_profile": document.detected_profile,
        "input_mode": document.input_mode,
        "detail_row_count": len(document.detail_rows),
        "validation_count": len(document.validations),
        "warning_count": len(document.warnings),
        "detection_score": document.detection_score,
        "excel_path": str(excel_path.resolve()),
        "run_dir": str(job_run_dir.resolve()),
        "run_at": _now_iso(),
    }
    result_path = job_run_dir / "job_result.json"
    result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return result_payload


def run_queue(
    queue_path: str | Path,
    *,
    run_root: str | Path | None = None,
    stop_on_error: bool = False,
) -> dict:
    queue = load_queue_manifest(queue_path)
    timestamp = _now_stamp()
    base_run_root = Path(run_root).resolve() if run_root else queue.queue_path.resolve().parents[1] / "results"
    queue_run_dir = base_run_root / f"{timestamp}__{sanitize_output_stem(queue.queue_name)}"
    queue_run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    failures: list[dict] = []

    for manifest_path in queue.jobs:
        try:
            result = run_job(manifest_path, run_root=queue_run_dir)
            results.append(result)
        except Exception as exc:  # pragma: no cover - queue error path
            failure = {
                "manifest_path": str(manifest_path),
                "error": str(exc),
            }
            failures.append(failure)
            if stop_on_error:
                break

    summary = {
        "queue_name": queue.queue_name,
        "queue_path": str(queue.queue_path),
        "run_dir": str(queue_run_dir.resolve()),
        "job_count": len(queue.jobs),
        "completed_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
        "run_at": _now_iso(),
    }
    summary_path = queue_run_dir / "queue_result.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def _resolve_from_manifest(manifest_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def _to_posix_relative(target: Path, base_dir: Path) -> str:
    return Path(os.path.relpath(target, start=base_dir.resolve())).as_posix()


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
