---
name: gcp-cloud-workflows
description: GCP Cloud Workflows YAML syntax, sleep/poll patterns, HTTP calls, retry and error handling. Use when editing or creating Cloud Workflow YAML files, or when the user asks about workflow orchestration.
---

# GCP Cloud Workflows

## Basic Structure

A workflow is a YAML file with named steps executed sequentially. The `main` routine is the entry point.

```yaml
main:
  params: [args]
  steps:
    - step1:
        call: http.post
        args:
          url: https://example.com/api
          body: ${args}
        result: response
    - step2:
        return: ${response.body}
```

## HTTP Calls

### Call a Cloud Function with OIDC Authentication

Cloud Workflows automatically obtains an OIDC token for the workflow's service account.

```yaml
- callFunction:
    call: http.post
    args:
      url: ${function_url}
      auth:
        type: OIDC
      body:
        api_source: ${args.api_source}
        report_id: ${report_id}
        client_id: ${args.client_id}
    result: functionResult
```

### GET Request

```yaml
- getRequest:
    call: http.get
    args:
      url: ${"https://api.example.com/reports/" + report_id}
      headers:
        Authorization: ${"Bearer " + token}
    result: getResult
```

### HTTP Call with Retry Policy

```yaml
- callWithRetry:
    try:
      call: http.post
      args:
        url: ${function_url}
        auth:
          type: OIDC
        body: ${payload}
      result: callResult
    retry:
      predicate: ${http.default_retry_predicate}
      max_retries: 3
      backoff:
        initial_delay: 2
        max_delay: 60
        multiplier: 2
```

## Sleep (Free — No Compute Cost)

Sleep pauses the workflow without consuming compute. Ideal for polling.

```yaml
- waitStep:
    call: sys.sleep
    args:
      seconds: 30
```

Maximum sleep: 31536000 seconds (365 days). Workflows can run for up to 1 year.

## Variables and Expressions

### Assign Variables

```yaml
- assignVars:
    assign:
      - poll_count: 0
      - max_polls: 20
      - base_delay: 30
      - max_delay: 120
```

### Update Variables

```yaml
- incrementCounter:
    assign:
      - poll_count: ${poll_count + 1}
```

### Expressions

- String concatenation: `${"Hello " + name}`
- Math: `${poll_count + 1}`, `${math.min(a, b)}`, `${math.pow(2, n)}`
- Comparisons: `${status == "ready"}`, `${poll_count >= max_polls}`
- Access nested fields: `${response.body.status}`, `${args.api_source}`

## Conditional Branching (switch)

```yaml
- checkResult:
    switch:
      - condition: ${pollResult.body.status == "ready"}
        next: downloadStep
      - condition: ${pollResult.body.status == "failed"}
        next: failStep
    next: waitAndRetry
```

The `next` at the bottom of `switch` is the default (no condition matched). Use `next: end` to terminate the workflow.

## Error Handling (try/except)

```yaml
- tryStep:
    try:
      call: http.post
      args:
        url: ${function_url}
        auth:
          type: OIDC
        body: ${payload}
      result: result
    except:
      as: e
      steps:
        - logError:
            call: sys.log
            args:
              text: ${"Error " + string(e.code) + ": " + e.message}
              severity: ERROR
        - raiseError:
            raise: ${e}
```

### Known Exception Fields

- `e.code` — HTTP status code (for HTTP errors)
- `e.message` — error description
- `e.tags` — list of error tags (e.g., `["HttpError"]`)

## Logging

```yaml
- logStep:
    call: sys.log
    args:
      text: ${"Processing report " + report_id + " for " + client_id}
      severity: INFO
      json:
        report_id: ${report_id}
        client_id: ${client_id}
        api_source: ${api_source}
```

Severity levels: `DEFAULT`, `DEBUG`, `INFO`, `NOTICE`, `WARNING`, `ERROR`, `CRITICAL`, `ALERT`, `EMERGENCY`.

## Subworkflows

Define reusable routines alongside `main`:

```yaml
main:
  params: [args]
  steps:
    - callSub:
        call: poll_until_ready
        args:
          report_id: ${args.report_id}
          function_url: ${args.poll_url}
        result: finalStatus
    - returnResult:
        return: ${finalStatus}

poll_until_ready:
  params: [report_id, function_url]
  steps:
    - init:
        assign:
          - poll_count: 0
    - pollLoop:
        # ... polling logic ...
```

## Our Poll Loop Pattern

This is the central pattern for `mode="report"`. The unified workflow polls both SP API and Ads API reports using exponential backoff.

For synchronous API operations (`mode="api_call"`), do not enter the create/poll/download loop. Route near input validation to `fetch_api`, then run the same best-effort BigQuery ingestion step. Current API-call operations are registered in `functions/shared/api_operations.py` and include Replenishment / Subscribe & Save (`SNS_OFFER_METRICS`, `SNS_SP_METRICS`, `SNS_OFFERS`).

```yaml
- initPolling:
    assign:
      - poll_count: 0
      - max_polls: 20
      - base_delay: 30
      - max_delay: 120

- pollLoop:
    steps:
      - calculateDelay:
          assign:
            - delay: ${int(math.min(base_delay * math.pow(2, poll_count), max_delay))}
      - wait:
          call: sys.sleep
          args:
            seconds: ${delay}
      - poll:
          call: http.post
          args:
            url: ${poll_function_url}
            auth:
              type: OIDC
            body:
              report_id: ${report_id}
              api_source: ${api_source}
              client_id: ${client_id}
          result: pollResult
      - logPoll:
          call: sys.log
          args:
            text: ${"Poll " + string(poll_count) + ": status=" + pollResult.body.status}
            severity: INFO
      - increment:
          assign:
            - poll_count: ${poll_count + 1}
      - check:
          switch:
            - condition: ${pollResult.body.status == "ready"}
              next: download
            - condition: ${pollResult.body.status == "failed"}
              next: handleFailure
            - condition: ${poll_count >= max_polls}
              next: timeout
          next: pollLoop
```

### Backoff Schedule

| Poll # | Delay (seconds) | Cumulative Wait |
|--------|----------------|-----------------|
| 0      | 30             | 0:30            |
| 1      | 60             | 1:30            |
| 2      | 120            | 3:30            |
| 3+     | 120 (capped)   | 5:30, 7:30...   |

With `max_polls: 20`, the maximum total wait is ~38 minutes.

## Complete Workflow Skeleton

This matches the project's `workflows/report_flow.yaml` structure:

```yaml
main:
  params: [args]
  steps:
    - validateInput:
        switch:
          - condition: ${args.api_source != "sp_api" and args.api_source != "ads_api"}
            raise: "Invalid api_source. Must be sp_api or ads_api."

    - authenticate:
        call: http.post
        args:
          url: ${args.auth_function_url}
          auth:
            type: OIDC
          body:
            api_source: ${args.api_source}
            client_id: ${args.client_id}
        result: authResult

    - createReport:
        call: http.post
        args:
          url: ${args.create_report_function_url}
          auth:
            type: OIDC
          body:
            api_source: ${args.api_source}
            client_id: ${args.client_id}
            report_type: ${args.report_type}
            marketplace_id: ${args.marketplace_id}
            access_token: ${authResult.body.access_token}
        result: createResult

    - initPolling:
        assign:
          - report_id: ${createResult.body.report_id}
          - poll_count: 0
          - max_polls: 20

    # ... poll loop (see pattern above) ...

    - download:
        call: http.post
        args:
          url: ${args.download_function_url}
          auth:
            type: OIDC
          body:
            api_source: ${args.api_source}
            report_id: ${report_id}
            client_id: ${args.client_id}
        result: downloadResult

    - done:
        return:
          status: "completed"
          report_id: ${report_id}
          drive_file_id: ${downloadResult.body.drive_file_id}
```

## Limits

| Limit | Value |
|-------|-------|
| Max execution duration | 1 year |
| Max memory per execution | 512 KB of variables |
| Max steps per execution | 1,000,000 |
| Max concurrent executions | 10,000 |
| Max request size (HTTP) | 2 MB |
| Max response size (HTTP) | 2 MB |
| Max sleep duration | 31,536,000 sec (1 year) |
