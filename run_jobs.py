from __future__ import annotations

import argparse
from pathlib import Path

from commission_system.jobs import build_batch_manifest, build_job_manifests, run_batch_manifest, run_job, run_queue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Genera manifests y ejecuta jobs/queues de PDFs a Excel.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_single = subparsers.add_parser("build-manifest", help="Crea un solo manifest JSON que agrupa todos los PDFs.")
    build_single.add_argument("--input-dir", default="files", help="Carpeta con PDFs.")
    build_single.add_argument("--manifest-path", default="output/jobs/manifests/all_pdfs_auto.manifest.json", help="Ruta del manifest unico.")
    build_single.add_argument("--output-root", default="output/jobs/results", help="Carpeta raiz de resultados.")
    build_single.add_argument("--expected-insurer", default="AUTO", help="Aseguradora esperada. Usa AUTO para deteccion libre.")
    build_single.add_argument("--include-scans", action="store_true", help="Incluye tambien los PDFs *_scan.pdf.")

    build = subparsers.add_parser("build-manifests", help="Crea un manifest por PDF y una queue en modo AUTO.")
    build.add_argument("--input-dir", default="files", help="Carpeta con PDFs.")
    build.add_argument("--manifests-dir", default="output/jobs/manifests", help="Carpeta donde se guardan los manifests.")
    build.add_argument("--queue-path", default="output/jobs/queues/all_pdfs_auto.queue.json", help="Ruta del archivo queue.")
    build.add_argument("--output-root", default="output/jobs/results", help="Carpeta raiz de resultados de jobs.")
    build.add_argument("--expected-insurer", default="AUTO", help="Aseguradora esperada. Usa AUTO para deteccion libre.")
    build.add_argument("--include-scans", action="store_true", help="Incluye tambien los PDFs *_scan.pdf.")

    run_one = subparsers.add_parser("run-job", help="Ejecuta un solo manifest y genera un Excel.")
    run_one.add_argument("--manifest", required=True, help="Ruta del manifest .job.json")
    run_one.add_argument("--run-root", default=None, help="Carpeta raiz opcional donde guardar el resultado del job.")

    run_manifest = subparsers.add_parser("run-manifest", help="Ejecuta un manifest unico con todos los PDFs listados.")
    run_manifest.add_argument("--manifest", required=True, help="Ruta del archivo .manifest.json")
    run_manifest.add_argument("--run-root", default=None, help="Carpeta raiz opcional donde guardar el resultado del manifest.")
    run_manifest.add_argument("--stop-on-error", action="store_true", help="Detiene el manifest al primer error.")

    run_many = subparsers.add_parser("run-queue", help="Ejecuta una cola de manifests uno por uno.")
    run_many.add_argument("--queue", required=True, help="Ruta del archivo .queue.json")
    run_many.add_argument("--run-root", default=None, help="Carpeta raiz opcional donde guardar el resultado de la queue.")
    run_many.add_argument("--stop-on-error", action="store_true", help="Detiene la cola al primer error.")

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "build-manifest":
        manifest_path = build_batch_manifest(
            input_dir=args.input_dir,
            manifest_path=args.manifest_path,
            output_root=args.output_root,
            include_scans=args.include_scans,
            expected_insurer=args.expected_insurer,
        )
        print(f"Manifest: {Path(manifest_path).resolve()}")
        return 0

    if args.command == "build-manifests":
        manifests, queue_path = build_job_manifests(
            input_dir=args.input_dir,
            manifests_dir=args.manifests_dir,
            queue_path=args.queue_path,
            output_root=args.output_root,
            include_scans=args.include_scans,
            expected_insurer=args.expected_insurer,
        )
        print(f"Manifests creados: {len(manifests)}")
        for manifest_path in manifests:
            print(Path(manifest_path).resolve())
        print(f"Queue: {Path(queue_path).resolve()}")
        return 0

    if args.command == "run-job":
        result = run_job(args.manifest, run_root=args.run_root)
        print(f"Job: {result['job_name']}")
        print(f"PDF: {result['source_pdf']}")
        print(f"Aseguradora: {result['detected_insurer']} / {result['detected_profile']}")
        print(f"Modo: {result['input_mode']} / filas={result['detail_row_count']}")
        print(f"Excel: {result['excel_path']}")
        print(f"Run dir: {result['run_dir']}")
        return 0

    if args.command == "run-manifest":
        summary = run_batch_manifest(args.manifest, run_root=args.run_root, stop_on_error=args.stop_on_error)
        print(f"Manifest: {summary['manifest_name']}")
        print(f"Completados: {summary['completed_count']}")
        print(f"Fallidos: {summary['failed_count']}")
        print(f"Run dir: {summary['run_dir']}")
        return 0

    summary = run_queue(args.queue, run_root=args.run_root, stop_on_error=args.stop_on_error)
    print(f"Queue: {summary['queue_name']}")
    print(f"Completados: {summary['completed_count']}")
    print(f"Fallidos: {summary['failed_count']}")
    print(f"Run dir: {summary['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
