"""Cloud Functions v2 definitions."""

from __future__ import annotations

import pathlib

import pulumi
import pulumi_gcp as gcp

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

FUNCTION_DEFS = [
    {"name": "auth",            "source_dir": "auth",            "memory": "256Mi", "timeout": 60,  "max_instances": 5},
    {"name": "create-report",   "source_dir": "create_report",   "memory": "256Mi", "timeout": 120, "max_instances": 20},
    {"name": "poll-status",     "source_dir": "poll_status",     "memory": "256Mi", "timeout": 60,  "max_instances": 20},
    {"name": "download-upload", "source_dir": "download_upload", "memory": "512Mi", "timeout": 540, "max_instances": 10},
    {"name": "scheduler",       "source_dir": "scheduler",       "memory": "256Mi", "timeout": 120, "max_instances": 1},
    {"name": "api",             "source_dir": "api",             "memory": "256Mi", "timeout": 60,  "max_instances": 10},
    {"name": "ingest-bigquery",          "source_dir": "ingest_bigquery",          "memory": "512Mi", "timeout": 300, "max_instances": 10},
    {"name": "event-report-scheduler",  "source_dir": "event_report_scheduler",  "memory": "256Mi", "timeout": 120, "max_instances": 1},
    {"name": "slack-bot",               "source_dir": "slack_bot",               "memory": "512Mi", "timeout": 300, "max_instances": 1},
]


def _build_source_archive(source_dir: str) -> pulumi.AssetArchive:
    """Bundle function source + shared/ into a single archive for GCS upload."""
    fn_path = PROJECT_ROOT / "functions" / source_dir
    shared_path = PROJECT_ROOT / "functions" / "shared"

    assets: dict[str, pulumi.Asset | pulumi.Archive] = {}
    for f in fn_path.iterdir():
        if f.is_file() and not f.name.startswith("."):
            assets[f.name] = pulumi.FileAsset(str(f))

    if shared_path.exists() and shared_path.is_dir():
        assets["shared"] = pulumi.FileArchive(str(shared_path))

    return pulumi.AssetArchive(assets)


def create(
    env: str,
    project: str,
    region: str,
    source_bucket: gcp.storage.Bucket,
    functions_sa: gcp.serviceaccount.Account,
    depends_on: list[pulumi.Resource],
) -> dict[str, gcp.cloudfunctionsv2.Function]:
    """Deploy all Cloud Functions v2. Returns dict keyed by function name."""
    opts = pulumi.ResourceOptions(depends_on=depends_on)
    results: dict[str, gcp.cloudfunctionsv2.Function] = {}

    config = pulumi.Config("kalilos")

    for fn_def in FUNCTION_DEFS:
        fn_name = fn_def["name"]
        resource_name = f"kalilos-{env}-{fn_name}"

        source_object = gcp.storage.BucketObject(
            f"{resource_name}-source",
            bucket=source_bucket.name,
            source=_build_source_archive(fn_def["source_dir"]),
            opts=opts,
        )

        env_vars: dict[str, str] = {
            "GCP_PROJECT": project,
            "ENVIRONMENT": env,
        }
        if fn_name in ("scheduler", "api"):
            env_vars["WORKFLOW_NAME"] = f"kalilos-{env}-report-flow"
            env_vars["WORKFLOW_LOCATION"] = region
        if fn_name == "api":
            api_url = f"https://{region}-{project}.cloudfunctions.net/kalilos-{env}-api"
            env_vars["OAUTH_REDIRECT_URI"] = f"{api_url}/oauth/callback"
            env_vars["FRONTEND_URL"] = config.get("frontend-url") or "http://localhost:5173"
            api_key = config.get_secret("api-key")
            if api_key:
                env_vars["API_KEY"] = api_key
        if fn_name == "download-upload":
            env_vars["GDRIVE_ROOT_FOLDER_NAME"] = config.get("gdrive-root-folder") or "Kalilos Reports"
            gdrive_folder_id = config.get("gdrive-root-folder-id")
            if gdrive_folder_id:
                env_vars["GDRIVE_ROOT_FOLDER_ID"] = gdrive_folder_id
        if fn_name == "ingest-bigquery":
            env_vars["BQ_DATASET"] = f"kalilos_reports_{env}"
        if fn_name == "event-report-scheduler":
            env_vars["WORKFLOW_NAME"] = f"kalilos-{env}-report-flow"
            env_vars["WORKFLOW_LOCATION"] = region
        if fn_name == "slack-bot":
            env_vars["BQ_DATASET"] = f"kalilos_reports_{env}"

        fn = gcp.cloudfunctionsv2.Function(
            resource_name,
            name=resource_name,
            location=region,
            project=project,
            build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
                runtime="python312",
                entry_point="handler",
                source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                    storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                        bucket=source_bucket.name,
                        object=source_object.name,
                    ),
                ),
            ),
            service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
                available_memory=fn_def["memory"],
                timeout_seconds=fn_def["timeout"],
                max_instance_count=fn_def["max_instances"],
                min_instance_count=0,
                service_account_email=functions_sa.email,
                ingress_settings="ALLOW_ALL" if fn_name == "api" else "ALLOW_INTERNAL_ONLY",
                environment_variables=env_vars,
            ),
            opts=opts,
        )

        results[fn_name] = fn

    return results
