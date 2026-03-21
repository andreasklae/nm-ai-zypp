from __future__ import annotations

from pathlib import Path

from astar_island.client import AstarIslandClient
from astar_island.config import AstarIslandSettings
from astar_island.models import (
    DeliveryRunManifest,
    ObservationCollection,
    ObservationPhasePlan,
    ObservationPlan,
    ObservationSample,
    PredictionBundle,
    PredictionValidationSummary,
    SeedSubmissionReceipt,
    SubmissionBundle,
    ViewportRequest,
    utc_now_iso,
)
from astar_island.planner import (
    build_observation_collection,
    build_phase1_observation_plan,
    build_phase2_observation_plan,
    build_two_phase_observation_plan,
)
from astar_island.predictor import BaselinePredictor
from astar_island.storage import (
    load_observations,
    load_optional_observation_plan,
    load_predictions,
    load_run_manifest,
    load_submission_receipts,
    round_artifact_dir,
    save_observation_plan,
    save_observations,
    save_predictions,
    save_round_artifacts,
    save_run_manifest,
    save_submission_receipts,
)


def _query_signature(sample_or_query: ObservationSample | object) -> tuple[object, ...]:
    query = sample_or_query.planned_query if isinstance(sample_or_query, ObservationSample) else sample_or_query
    return (
        query.phase,
        query.seed_index,
        query.viewport_x,
        query.viewport_y,
        query.viewport_w,
        query.viewport_h,
        query.repeat_index,
    )


def _artifact_dir(settings: AstarIslandSettings, *, round_id: int, explicit_dir: Path | None) -> Path:
    if explicit_dir is not None:
        return explicit_dir
    return round_artifact_dir(
        settings.data_dir, round_id, utc_now_iso().replace(":", "").replace("-", "").replace("+", "")
    )


def _merge_samples(existing: list[ObservationSample], new: list[ObservationSample]) -> list[ObservationSample]:
    merged = {_query_signature(sample): sample for sample in existing}
    for sample in new:
        merged[_query_signature(sample)] = sample
    return sorted(merged.values(), key=lambda item: item.planned_query.query_index)


def _filter_phase_samples(samples: list[ObservationSample], phase: str) -> list[ObservationSample]:
    return [sample for sample in samples if sample.planned_query.phase == phase]


def _execute_missing_queries(
    client: AstarIslandClient,
    round_id: int,
    phase_plan: ObservationPhasePlan,
    existing_samples: list[ObservationSample],
) -> list[ObservationSample]:
    existing_signatures = {_query_signature(sample) for sample in existing_samples}
    new_samples: list[ObservationSample] = []
    for query in phase_plan.queries:
        signature = _query_signature(query)
        if signature in existing_signatures:
            continue
        result = client.simulate(
            ViewportRequest(
                round_id=round_id,
                seed_index=query.seed_index,
                viewport_x=query.viewport_x,
                viewport_y=query.viewport_y,
                viewport_w=query.viewport_w,
                viewport_h=query.viewport_h,
            )
        )
        new_samples.append(ObservationSample(planned_query=query, result=result))
    return _merge_samples(existing_samples, new_samples)


def _persist_phase_checkpoint(
    *,
    artifact_dir: Path,
    manifest: DeliveryRunManifest,
    phase1_plan: ObservationPhasePlan | None,
    phase2_plan: ObservationPhasePlan | None,
    samples: list[ObservationSample],
    status: str,
) -> tuple[ObservationCollection, DeliveryRunManifest]:
    collection = build_observation_collection(manifest.round_id, samples)
    save_observations(artifact_dir, collection)
    updated = _update_manifest(
        manifest,
        status=status,
        phase1_plan=phase1_plan,
        phase2_plan=phase2_plan,
        observations=collection,
    )
    save_run_manifest(artifact_dir, updated)
    return collection, updated


def _update_manifest(manifest: DeliveryRunManifest, **changes: object) -> DeliveryRunManifest:
    return manifest.model_copy(update={"updated_at": utc_now_iso(), **changes})


def _empty_observations(round_id: str | int) -> ObservationCollection:
    return ObservationCollection(
        round_id=round_id,
        total_queries=0,
        samples=[],
        per_seed=[],
        phase_query_counts={},
    )


def _is_budget_exhausted_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "query budget exhausted" in message and "429" in message


def validate_prediction_bundle(
    bundle: PredictionBundle, *, expected_seed_count: int, map_height: int, map_width: int
) -> PredictionValidationSummary:
    if len(bundle.seeds) != expected_seed_count:
        raise RuntimeError(f"Expected {expected_seed_count} seed predictions, got {len(bundle.seeds)}.")
    min_probability = 1.0
    max_sum_error = 0.0
    for seed in bundle.seeds:
        if seed.height != map_height or seed.width != map_width:
            raise RuntimeError("Prediction tensor shape does not match round dimensions.")
        for row in seed.prediction:
            for cell in row:
                if len(cell) != 6:
                    raise RuntimeError("Each prediction cell must contain exactly 6 classes.")
                cell_sum = sum(cell)
                max_sum_error = max(max_sum_error, abs(cell_sum - 1.0))
                min_probability = min(min_probability, min(cell))
    valid = min_probability >= bundle.floor and max_sum_error <= 1e-6
    if not valid:
        raise RuntimeError("Prediction validation failed: probabilities do not respect floor or normalization.")
    return PredictionValidationSummary(
        valid=True,
        seed_count=len(bundle.seeds),
        map_height=map_height,
        map_width=map_width,
        class_count=6,
        min_probability=round(min_probability, 6),
        max_sum_error=round(max_sum_error, 10),
    )


def _submit_with_resume(
    client: AstarIslandClient,
    artifact_dir: Path,
    predictions: PredictionBundle,
) -> SubmissionBundle:
    previous = load_submission_receipts(artifact_dir)
    completed = (
        {receipt.seed_index for receipt in previous.receipts if receipt.status_code < 400 and not receipt.skipped}
        if previous is not None
        else set()
    )
    receipts: list[SeedSubmissionReceipt] = []
    for seed in predictions.seeds:
        if seed.seed_index in completed:
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
    bundle = SubmissionBundle(round_id=predictions.round_id, receipts=receipts)
    save_submission_receipts(artifact_dir, bundle)
    return bundle


def collect_two_phase_observations(
    *,
    client: AstarIslandClient,
    artifact_dir: Path,
    manifest: DeliveryRunManifest,
    predictor: BaselinePredictor,
    force_resume: bool = False,
) -> tuple[ObservationPlan, ObservationCollection, DeliveryRunManifest]:
    round_detail = client.get_round(manifest.round_id)
    existing_plan = load_optional_observation_plan(artifact_dir) if force_resume else None
    existing_observations = load_observations(artifact_dir) if force_resume else None
    existing_samples = existing_observations.samples if existing_observations is not None else []

    phase1_plan = manifest.phase1_plan or (existing_plan.phases[0] if existing_plan and existing_plan.phases else None)
    if phase1_plan is None:
        phase1_plan = build_phase1_observation_plan(round_detail)

    phase1_samples = _execute_missing_queries(
        client, round_detail.id, phase1_plan, _filter_phase_samples(existing_samples, "phase1")
    )
    phase1_collection, manifest = _persist_phase_checkpoint(
        artifact_dir=artifact_dir,
        manifest=manifest,
        phase1_plan=phase1_plan,
        phase2_plan=None,
        samples=phase1_samples,
        status="phase1_complete",
    )

    provisional_bundle, provisional_diagnostics = predictor.predict_with_diagnostics(round_detail, phase1_collection)
    phase2_plan = manifest.phase2_plan
    if phase2_plan is None and existing_plan is not None and len(existing_plan.phases) > 1:
        phase2_plan = existing_plan.phases[1]
    if phase2_plan is None:
        phase2_plan = build_phase2_observation_plan(
            round_detail,
            phase1_observations=phase1_collection,
            provisional_predictions=provisional_bundle,
            uncertainty_summaries=provisional_diagnostics.uncertainty_summaries,
            total_queries=max(0, 50 - phase1_plan.budget),
        )

    combined_plan = build_two_phase_observation_plan(phase1_plan, phase2_plan, round_id=round_detail.id)
    save_observation_plan(artifact_dir, combined_plan)
    manifest = _update_manifest(
        manifest,
        phase1_plan=phase1_plan,
        phase2_plan=phase2_plan,
        observations=phase1_collection,
        uncertainty_summaries=provisional_diagnostics.uncertainty_summaries,
        latent_proxies=provisional_diagnostics.latent_proxies,
    )
    save_run_manifest(artifact_dir, manifest)

    phase2_existing = _filter_phase_samples(existing_samples, "phase2")
    phase2_samples = _execute_missing_queries(client, round_detail.id, phase2_plan, phase2_existing)

    combined_samples = _merge_samples(phase1_samples, phase2_samples)
    combined_collection, manifest = _persist_phase_checkpoint(
        artifact_dir=artifact_dir,
        manifest=manifest,
        phase1_plan=phase1_plan,
        phase2_plan=phase2_plan,
        samples=combined_samples,
        status="observations_collected",
    )

    updated = _update_manifest(
        manifest,
        phase1_plan=phase1_plan,
        phase2_plan=phase2_plan,
        observations=combined_collection,
        uncertainty_summaries=provisional_diagnostics.uncertainty_summaries,
        latent_proxies=provisional_diagnostics.latent_proxies,
    )
    save_run_manifest(artifact_dir, updated)
    return combined_plan, combined_collection, updated


def deliver_round(
    *,
    settings: AstarIslandSettings,
    artifact_dir: Path | None = None,
    submit: bool = False,
    force_resume: bool = False,
    client: AstarIslandClient | None = None,
) -> tuple[Path, DeliveryRunManifest]:
    if not settings.access_token:
        raise RuntimeError("ASTAR_ISLAND_ACCESS_TOKEN is required before running deliver-round.")

    client = client or AstarIslandClient(settings=settings)
    active_round = client.get_active_round()
    if active_round.status != "active":
        raise RuntimeError(f"Active round endpoint returned non-active status: {active_round.status}")
    round_detail = client.get_round(active_round.id)

    resolved_artifact_dir = _artifact_dir(settings, round_id=round_detail.id, explicit_dir=artifact_dir)
    if resolved_artifact_dir.exists() and any(resolved_artifact_dir.iterdir()) and not force_resume:
        raise RuntimeError("Artifact directory already exists. Use --force-resume to reuse it.")

    save_round_artifacts(resolved_artifact_dir, active_round, round_detail)

    existing_manifest = load_run_manifest(resolved_artifact_dir) if force_resume else None
    if existing_manifest is not None and existing_manifest.round_id != round_detail.id:
        raise RuntimeError("Existing artifact directory belongs to a different round.")

    manifest = existing_manifest or DeliveryRunManifest(
        round_id=round_detail.id,
        round_number=active_round.round_number,
        active_round_status=active_round.status,
        artifact_dir=str(resolved_artifact_dir),
    )
    manifest = _update_manifest(
        manifest,
        round_number=active_round.round_number,
        active_round_status=active_round.status,
        status="round_fetched",
    )
    save_run_manifest(resolved_artifact_dir, manifest)

    from astar_island.backtest import (
        _archive_rounds_from_artifacts,
        _load_local_round_artifacts,
        fit_predictor_parameters,
    )

    local_artifacts = _load_local_round_artifacts(settings.data_dir)
    calibrated_parameters = fit_predictor_parameters(local_artifacts)
    archive_rounds = _archive_rounds_from_artifacts(local_artifacts)
    predictor = BaselinePredictor(parameters=calibrated_parameters, archive_rounds=archive_rounds)
    try:
        _, observations, manifest = collect_two_phase_observations(
            client=client,
            artifact_dir=resolved_artifact_dir,
            manifest=manifest,
            predictor=predictor,
            force_resume=force_resume,
        )
    except RuntimeError as exc:
        if not _is_budget_exhausted_error(exc):
            raise
        observations = load_observations(resolved_artifact_dir) or _empty_observations(round_detail.id)
        persisted_manifest = load_run_manifest(resolved_artifact_dir) or manifest
        warning = "Observation budget exhausted; proceeding with prediction-only fallback."
        save_observations(resolved_artifact_dir, observations)
        manifest = _update_manifest(
            persisted_manifest,
            status="observations_skipped_budget_exhausted",
            observations=observations,
            warnings=[*persisted_manifest.warnings, warning],
        )
        save_run_manifest(resolved_artifact_dir, manifest)

    final_predictions, diagnostics = predictor.predict_with_diagnostics(round_detail, observations)
    save_predictions(resolved_artifact_dir, final_predictions)
    validation = validate_prediction_bundle(
        final_predictions,
        expected_seed_count=round_detail.seeds_count,
        map_height=round_detail.map_height,
        map_width=round_detail.map_width,
    )
    manifest = _update_manifest(
        manifest,
        status="predictions_ready",
        latent_proxies=diagnostics.latent_proxies,
        uncertainty_summaries=diagnostics.uncertainty_summaries,
        prediction_model_name=final_predictions.model_name,
        prediction_created_at=final_predictions.created_at,
        prediction_validation=validation,
    )
    save_run_manifest(resolved_artifact_dir, manifest)

    if submit:
        submission = _submit_with_resume(client, resolved_artifact_dir, final_predictions)
        manifest = _update_manifest(
            manifest,
            status="submitted"
            if all(receipt.status_code < 400 or receipt.skipped for receipt in submission.receipts)
            else "submit_failed",
            submission=submission,
        )
        save_run_manifest(resolved_artifact_dir, manifest)

    return resolved_artifact_dir, manifest


def predict_from_artifacts(artifact_dir: Path) -> tuple[PredictionBundle, PredictionValidationSummary]:
    predictions = load_predictions(artifact_dir)
    first_seed = predictions.seeds[0]
    validation = validate_prediction_bundle(
        predictions,
        expected_seed_count=len(predictions.seeds),
        map_height=first_seed.height,
        map_width=first_seed.width,
    )
    return predictions, validation
