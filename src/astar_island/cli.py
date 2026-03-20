from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from astar_island.backtest import run_backtests
from astar_island.client import AstarIslandClient
from astar_island.cloud import detect_gcloud_status
from astar_island.config import load_settings
from astar_island.delivery import collect_two_phase_observations, deliver_round, validate_prediction_bundle
from astar_island.models import DeliveryRunManifest, SeedSubmissionReceipt, SubmissionBundle
from astar_island.predictor import BaselinePredictor
from astar_island.storage import (
    load_observations,
    load_predictions,
    load_round_detail,
    load_run_manifest,
    load_submission_receipts,
    round_artifact_dir,
    save_predictions,
    save_round_artifacts,
    save_run_manifest,
    save_submission_receipts,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _artifact_dir_from_args(explicit_dir: str | None, *, round_id: int) -> Path:
    if explicit_dir:
        return Path(explicit_dir)
    settings = load_settings()
    return round_artifact_dir(settings.data_dir, round_id, _timestamp())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Astar Island solver toolkit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-round", help="Fetch the active round or a specific round.")
    fetch.add_argument("--round-id", type=int)
    fetch.add_argument("--artifact-dir")

    collect = subparsers.add_parser("collect-observations", help="Execute the phased observation workflow.")
    collect.add_argument("--artifact-dir", required=True)
    collect.add_argument("--force-resume", action="store_true")

    predict = subparsers.add_parser("predict", help="Generate predictions from stored round artifacts.")
    predict.add_argument("--artifact-dir", required=True)

    submit = subparsers.add_parser("submit", help="Submit saved predictions for all seeds.")
    submit.add_argument("--artifact-dir", required=True)
    submit.add_argument("--force", action="store_true")

    deliver = subparsers.add_parser("deliver-round", help="Run the full fetch/observe/predict workflow, optionally submitting.")
    deliver.add_argument("--artifact-dir")
    deliver.add_argument("--submit", action="store_true")
    deliver.add_argument("--force-resume", action="store_true")

    backtest = subparsers.add_parser("backtest", help="Evaluate the empirical model on archived or completed rounds.")
    backtest.add_argument("--round-id")
    backtest.add_argument("--all-completed", action="store_true")
    backtest.add_argument("--artifact-dir")
    backtest.add_argument("--use-gcp", action="store_true")

    cloud = subparsers.add_parser("cloud-status", help="Inspect optional GCP integration readiness.")
    cloud.add_argument("--json", action="store_true")

    return parser


def _command_fetch_round(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = AstarIslandClient(settings=settings)
    if args.round_id is None:
        summary = client.get_active_round()
    else:
        summary = next((round_item for round_item in client.list_rounds() if round_item.id == args.round_id), None)
        if summary is None:
            raise RuntimeError(f"Round {args.round_id} not found.")
    detail = client.get_round(summary.id)
    artifact_dir = _artifact_dir_from_args(args.artifact_dir, round_id=detail.id)
    save_round_artifacts(artifact_dir, summary, detail)
    manifest = DeliveryRunManifest(
        round_id=detail.id,
        round_number=summary.round_number,
        active_round_status=summary.status,
        artifact_dir=str(artifact_dir),
        status="round_fetched",
    )
    save_run_manifest(artifact_dir, manifest)
    print(f"Saved round {detail.id} to {artifact_dir}")
    return 0


def _command_collect_observations(args: argparse.Namespace) -> int:
    settings = load_settings()
    artifact_dir = Path(args.artifact_dir)
    round_detail = load_round_detail(artifact_dir)
    manifest = load_run_manifest(artifact_dir) or DeliveryRunManifest(
        round_id=round_detail.id,
        round_number=round_detail.round_number,
        active_round_status=round_detail.status or "active",
        artifact_dir=str(artifact_dir),
    )
    client = AstarIslandClient(settings=settings)
    predictor = BaselinePredictor()
    _, observations, updated_manifest = collect_two_phase_observations(
        client=client,
        artifact_dir=artifact_dir,
        manifest=manifest,
        predictor=predictor,
        force_resume=args.force_resume,
    )
    print(f"Saved {observations.total_queries} phased observation samples to {artifact_dir}")
    save_run_manifest(artifact_dir, updated_manifest)
    return 0


def _command_predict(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir)
    round_detail = load_round_detail(artifact_dir)
    observations = load_observations(artifact_dir)
    predictor = BaselinePredictor()
    predictions, diagnostics = predictor.predict_with_diagnostics(round_detail, observations)
    validation = validate_prediction_bundle(
        predictions,
        expected_seed_count=round_detail.seeds_count,
        map_height=round_detail.map_height,
        map_width=round_detail.map_width,
    )
    save_predictions(artifact_dir, predictions)
    manifest = load_run_manifest(artifact_dir) or DeliveryRunManifest(
        round_id=round_detail.id,
        round_number=round_detail.round_number,
        active_round_status=round_detail.status or "active",
        artifact_dir=str(artifact_dir),
    )
    manifest = manifest.model_copy(
        update={
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": "predictions_ready",
            "latent_proxies": diagnostics.latent_proxies,
            "uncertainty_summaries": diagnostics.uncertainty_summaries,
            "prediction_model_name": predictions.model_name,
            "prediction_created_at": predictions.created_at,
            "prediction_validation": validation,
        }
    )
    save_run_manifest(artifact_dir, manifest)
    print(f"Saved predictions for {len(predictions.seeds)} seeds to {artifact_dir}")
    return 0


def _command_submit(args: argparse.Namespace) -> int:
    settings = load_settings()
    artifact_dir = Path(args.artifact_dir)
    round_detail = load_round_detail(artifact_dir)
    predictions = load_predictions(artifact_dir)
    validate_prediction_bundle(
        predictions,
        expected_seed_count=round_detail.seeds_count,
        map_height=round_detail.map_height,
        map_width=round_detail.map_width,
    )
    previous = load_submission_receipts(artifact_dir)
    completed_seeds = (
        {
            receipt.seed_index
            for receipt in previous.receipts
            if receipt.status_code < 400 and not receipt.skipped
        }
        if previous is not None
        else set()
    )
    client = AstarIslandClient(settings=settings)
    receipts: list[SeedSubmissionReceipt] = []
    for seed in predictions.seeds:
        if not args.force and seed.seed_index in completed_seeds:
            receipts.append(
                SeedSubmissionReceipt(
                    seed_index=seed.seed_index,
                    status_code=200,
                    response_body={"status": "skipped_existing_success"},
                    skipped=True,
                )
            )
            continue
        response = client.submit_prediction(
            round_id=predictions.round_id,
            seed_index=seed.seed_index,
            prediction=seed.prediction,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        receipts.append(
            SeedSubmissionReceipt(
                seed_index=seed.seed_index,
                status_code=response.status_code,
                response_body=body,
            )
        )
    submission_bundle = SubmissionBundle(round_id=predictions.round_id, receipts=receipts)
    save_submission_receipts(artifact_dir, submission_bundle)
    manifest = load_run_manifest(artifact_dir)
    if manifest is not None:
        manifest = manifest.model_copy(
            update={
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "status": "submitted" if all(receipt.status_code < 400 or receipt.skipped for receipt in receipts) else "submit_failed",
                "submission": submission_bundle,
            }
        )
        save_run_manifest(artifact_dir, manifest)
    successful = sum(1 for receipt in receipts if receipt.status_code < 400 or receipt.skipped)
    print(f"Processed {len(receipts)} seeds, {successful} successful or skipped.")
    return 0 if all(receipt.status_code < 400 or receipt.skipped for receipt in receipts) else 1


def _command_deliver_round(args: argparse.Namespace) -> int:
    settings = load_settings()
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else None
    resolved_dir, manifest = deliver_round(
        settings=settings,
        artifact_dir=artifact_dir,
        submit=args.submit,
        force_resume=args.force_resume,
    )
    print(f"Delivery status: {manifest.status}")
    print(f"Artifacts: {resolved_dir}")
    return 0 if manifest.status in {"predictions_ready", "submitted"} else 1


def _command_backtest(args: argparse.Namespace) -> int:
    settings = load_settings()
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else None
    round_id = args.round_id
    if round_id is not None:
        try:
            round_id = int(round_id)
        except ValueError:
            pass
    resolved_dir, summary = run_backtests(
        settings=settings,
        round_id=round_id,
        all_completed=args.all_completed,
        artifact_dir=artifact_dir,
        use_gcp=args.use_gcp,
    )
    print(f"Backtest artifacts: {resolved_dir}")
    print(f"Reports: {len(summary.reports)}")
    if summary.aggregate_metrics:
        print(json.dumps(summary.aggregate_metrics, indent=2, sort_keys=True))
    return 0 if summary.reports else 1


def _command_cloud_status(args: argparse.Namespace) -> int:
    status = detect_gcloud_status()
    if args.json:
        print(
            json.dumps(
                {
                    "active_account": status.active_account,
                    "active_project": status.active_project,
                    "has_adc": status.has_adc,
                },
                indent=2,
            )
        )
    else:
        print(f"active_account={status.active_account or 'none'}")
        print(f"active_project={status.active_project or 'none'}")
        print(f"has_adc={status.has_adc}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "fetch-round":
        return _command_fetch_round(args)
    if args.command == "collect-observations":
        return _command_collect_observations(args)
    if args.command == "predict":
        return _command_predict(args)
    if args.command == "submit":
        return _command_submit(args)
    if args.command == "deliver-round":
        return _command_deliver_round(args)
    if args.command == "backtest":
        return _command_backtest(args)
    if args.command == "cloud-status":
        return _command_cloud_status(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
