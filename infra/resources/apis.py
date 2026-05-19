"""Enable required GCP APIs before any resources are created."""

import pulumi_gcp as gcp

REQUIRED_APIS = [
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "drive.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
]


def enable(project: str) -> list[gcp.projects.Service]:
    """Enable all required GCP APIs. Returns list for depends_on usage."""
    return [
        gcp.projects.Service(
            f"api-{api.split('.')[0]}",
            service=api,
            project=project,
            disable_on_destroy=False,
        )
        for api in REQUIRED_APIS
    ]
