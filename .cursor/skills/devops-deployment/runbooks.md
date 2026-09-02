# Operational Runbooks

## Runbook: Add a New Client

### Prerequisites
- Amazon SP API credentials (LWA refresh token, client ID, client secret)
- Amazon Ads API credentials (same LWA + profile ID)
- Google Drive folder shared with the service account

### Steps

1. **Store SP API credentials:**
   ```bash
   make env-staging
   make secret-set NAME=kalilos-staging-sp-api-newclient VALUE='{"refresh_token":"Atzr|...","client_id":"amzn1.application-oa2-client.xxx","client_secret":"xxx"}'
   ```

2. **Store Ads API credentials:**
   ```bash
   make secret-set NAME=kalilos-staging-ads-api-newclient VALUE='{"refresh_token":"Atzr|...","client_id":"amzn1.application-oa2-client.xxx","client_secret":"xxx","profile_id":"123456"}'
   ```

3. **Add client via frontend:**
   - Navigate to the Clients page
   - Click "Add Client"
   - Enter client name, marketplace IDs, and the secret names from above
   - Save

4. **Or seed via CLI:**
   ```bash
   make seed-test-client
   ```

5. **Create schedules:**
   - Navigate to the Schedules page
   - Select the new client and configure report types and frequency
   - Save

6. **Verify:**
   - Trigger a manual report: go to On-Demand page, select the client and a report type
   - Monitor the Dashboard for job status
   - Check Drive for the uploaded report

7. **Repeat for production:**
   ```bash
   make env-prod
   make secret-set NAME=kalilos-prod-sp-api-newclient VALUE='...'
   make secret-set NAME=kalilos-prod-ads-api-newclient VALUE='...'
   ```

---

## Runbook: Rotate Amazon Credentials

### When to Use
- Amazon revokes a refresh token
- Client regenerates their API credentials
- Periodic security rotation

### Steps

1. **Get new credentials from the client** (refresh token, client ID, client secret).

2. **Update staging first:**
   ```bash
   make env-staging
   make secret-set NAME=kalilos-staging-sp-api-{client} VALUE='{"refresh_token":"NEW_TOKEN","client_id":"...","client_secret":"..."}'
   ```

3. **Test:** Trigger a manual report on staging to verify the new credentials work.

4. **Update production:**
   ```bash
   make env-prod
   make secret-set NAME=kalilos-prod-sp-api-{client} VALUE='{"refresh_token":"NEW_TOKEN","client_id":"...","client_secret":"..."}'
   ```

5. **Verify:** Check Dashboard for next scheduled report to confirm success.

---

## Runbook: Investigate a Failed Workflow

### Steps

1. **Find the failed execution:**
   ```bash
   make env-staging  # or env-prod
   make workflows-status
   ```
   Note the execution ID.

2. **Check workflow logs:**
   ```bash
   make logs-workflow
   ```
   Look for the error step and message.

3. **Check function logs for the failing step:**
   ```bash
   make logs-fn NAME=create_report    # if failed at creation
   make logs-fn NAME=poll_status      # if failed during polling
   make logs-fn NAME=download_upload  # if failed at download
   make logs-fn NAME=auth             # if failed at authentication
   ```

4. **Common failure causes:**
   - `auth` failure: Token expired (see credential rotation runbook)
   - `create_report` failure: Invalid report type for marketplace, or rate limit
   - `poll_status` timeout: Report took too long (increase max_polls in workflow)
   - `download_upload` failure: Drive permission issue or large file timeout

5. **Retry:** Use the On-Demand page to re-trigger the same report.

---

## Runbook: Deploy a New Cloud Function

### Steps

1. **Create the function directory:**
   ```
   functions/my_function/main.py
   ```

2. **Implement the handler:**
   ```python
   import flask
   import functions_framework

   @functions_framework.http
   def handler(request: flask.Request) -> tuple[dict, int]:
       data = request.get_json(silent=True) or {}
       # ... implementation ...
       return {"status": "ok"}, 200
   ```

3. **Add to Pulumi in `infra/resources/functions.py`:**
   Follow the existing pattern for function definitions (source directory, runtime, entry point, env vars, service account).

4. **Add IAM bindings if needed** in `infra/resources/iam.py`.

5. **Deploy:**
   ```bash
   make env-staging && make preview && make deploy-infra
   ```

6. **Test locally first (optional):**
   ```bash
   make local-fn NAME=my_function PORT=8080
   ```

---

## Runbook: Recover from Stuck Workflows

### Symptoms
- Multiple workflow executions in ACTIVE state for hours
- No new reports appearing in Drive
- Scheduler keeps launching new executions

### Steps

1. **List active executions:**
   ```bash
   make workflows-status
   ```

2. **Cancel all stuck executions:**
   ```bash
   make workflows-cancel ID={execution_id}
   ```
   Repeat for each stuck execution.

3. **Check for the root cause:**
   - Rate limiting: Too many concurrent executions hitting Amazon APIs
   - Function errors: A function is failing and the workflow retry loop is infinite
   - External API down: Amazon APIs returning 503

4. **If rate limiting:**
   - Reduce the number of concurrent schedules in Firestore
   - Consider adding a concurrency limiter to the scheduler function

5. **If function errors:**
   - Fix the function code
   - Deploy: `make env-staging && make deploy-infra`

6. **Resume normal operation:**
   - The next scheduler tick will pick up pending schedules
   - Or manually trigger: `make workflow-trigger ...`

---

## Runbook: Scale for More Clients

### When to Use
- Adding 10+ clients and concerned about rate limits or quotas

### Considerations

1. **Amazon API rate limits** are per-seller (SP API) or per-profile (Ads API). More clients = more rate budget, but each client's reports are independent.

2. **Cloud Scheduler** has a limit of 500 jobs per project. If you need more, batch multiple clients into a single scheduler job.

3. **Cloud Workflows** supports 10,000 concurrent executions. Unlikely to be a bottleneck.

4. **Google Drive** has generous quotas (1,000 requests per 100 seconds per user). The service account counts as one user.

5. **Firestore** has no practical limit for our data volumes.

### Recommendations
- Stagger scheduler cron times so all clients don't fire simultaneously
- Monitor Cloud Function instance counts in GCP Console
- Consider increasing function `max_instances` in Pulumi if needed

---

## Runbook: Disaster Recovery

### If Firestore Data Is Lost

1. Client configs and schedules must be re-entered via the frontend or seed script.
2. Job history is lost but reports in Google Drive are unaffected.
3. Run `make seed-test-client` if test data scripts are up to date.

### If Google Drive Files Are Deleted

1. Check Drive trash (files remain for 30 days).
2. Re-run reports for the missing date range using the On-Demand page.

### If GCP Project Is Compromised

1. Rotate all Amazon credentials immediately (see credential rotation runbook).
2. Rotate the GCP service account keys.
3. Review Cloud Audit Logs for unauthorized access.
4. Redeploy all infrastructure: `make deploy-all`.
