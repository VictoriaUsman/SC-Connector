"""Kalilos Connector — Pulumi infrastructure entry point.

Resource creation order:
  1. GCP APIs
  2. Service accounts + project-level IAM
  3. Firestore, Secret Manager, GCS bucket  (independent)
  4. Cloud Functions  (needs bucket + SA)
  5. Cloud Workflow   (needs function URLs + SA)
  6. Cloud Scheduler  (needs scheduler function URL + SA)
  7. Function-level IAM bindings  (needs functions + SAs)
"""

import pulumi

from resources import apis, iam, firestore, secrets, storage, bigquery, functions, workflow, scheduler, mcp_server, monitoring

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
env = pulumi.get_stack()
gcp_config = pulumi.Config("gcp")
kalilos_config = pulumi.Config("kalilos")

project = gcp_config.require("project")
region = gcp_config.get("region") or "us-central1"

# ---------------------------------------------------------------------------
# 1. Enable GCP APIs
# ---------------------------------------------------------------------------
enabled_apis = apis.enable(project)

# ---------------------------------------------------------------------------
# 2. Service accounts + project-level IAM
# ---------------------------------------------------------------------------
sas = iam.create_service_accounts(env, project, enabled_apis)

# ---------------------------------------------------------------------------
# 3. Foundation resources (independent of each other)
# ---------------------------------------------------------------------------
db = firestore.create(env, project, enabled_apis)
secret_resources = secrets.create(env, project, enabled_apis)
source_bucket = storage.create_source_bucket(env, project, region, enabled_apis)

# ---------------------------------------------------------------------------
# 3b. BigQuery dataset + tables
# ---------------------------------------------------------------------------
bq_tables = bigquery.create(env, project, region, enabled_apis)

# ---------------------------------------------------------------------------
# 4. Cloud Functions
# ---------------------------------------------------------------------------
fns = functions.create(env, project, region, source_bucket, sas["functions"], enabled_apis)

# ---------------------------------------------------------------------------
# 5. Cloud Workflow (needs function URLs for templating)
# ---------------------------------------------------------------------------
wf = workflow.create(env, project, region, sas["workflow"], fns, enabled_apis)

# ---------------------------------------------------------------------------
# 6. Cloud Scheduler (triggers the scheduler function on a cron)
# ---------------------------------------------------------------------------
scheduler_jobs = scheduler.create(
    env, project, region, fns["scheduler"], sas["scheduler"], kalilos_config, enabled_apis,
    cloud_functions=fns,
)

# ---------------------------------------------------------------------------
# 7. Function-level IAM (who can invoke what)
# ---------------------------------------------------------------------------
iam.bind_invokers(env, project, region, fns, sas)

# ---------------------------------------------------------------------------
# 8. MCP Server (Cloud Run)
# ---------------------------------------------------------------------------
mcp_service = mcp_server.create(env, project, region, fns["api"], enabled_apis)

# ---------------------------------------------------------------------------
# 9. Cloud Monitoring (log-based error metric + alerts + /health uptime check)
# ---------------------------------------------------------------------------
monitoring_resources = monitoring.create(env, project, fns["api"], kalilos_config, enabled_apis)

# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
pulumi.export("project", project)
pulumi.export("region", region)
pulumi.export("environment", env)
pulumi.export("api_url", fns["api"].url)
pulumi.export("workflow_name", wf.name)
pulumi.export("source_bucket", source_bucket.name)
