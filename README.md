# Internal Provisioning API

Jenkins-facing FastAPI middleware that brokers validation environment provisioning across OneCloud and GTAX-style provider APIs.

Jenkins calls this service with a validation scenario. The middleware resolves the scenario, queries the correct provider API, filters eligible team-tagged machines, creates a reservation, triggers image deployment, tracks normalized status, and exposes a polling endpoint.

## Endpoints

```text
GET  /health
GET  /provider-health
GET  /scenarios
GET  /machines
POST /provision
GET  /provision/{request_id}/status
POST /reservations/{reservation_id}/release
```

## Environment Variables

```text
ONECLOUD_BASE_URL=https://dummy-onecloud-api.onrender.com
GTAX_BASE_URL=https://dummy-gtax-api.onrender.com
PROVISION_POLL_INTERVAL_SECONDS=2
PROVISION_TIMEOUT_SECONDS=300
```

## Local Run

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

## Example Request

```bash
curl -X POST http://localhost:8080/provision \
  -H "Content-Type: application/json" \
  -d '{"test_scenario":"dpcpp-adl-win11-validation","team":"oneapi","jenkins_build_id":"12345","duration_hours":4}'
```

Then poll:

```bash
curl http://localhost:8080/provision/<request_id>/status
```

## Provider Diagnostics

Check whether the middleware can reach OneCloud and GTAX:

```bash
curl http://localhost:8080/provider-health
```

If one provider is asleep or temporarily unavailable, `/machines` returns a controlled `502` with provider details instead of a raw server error. To return machines from reachable providers only:

```bash
curl "http://localhost:8080/machines?allow_partial=true"
```

## Jenkins Parameterized Pipeline

This repo includes a `Jenkinsfile` that shows a parameterized Jenkins pipeline. When you run the job, Jenkins shows options such as:

```text
TEST_OPTION
PLATFORM
OS
TEAM
PROVISION_API
DURATION_HOURS
```

The Jenkinsfile maps valid option combinations to provisioning scenarios such as:

```text
DPCPP Compiler Validation + ADL + windows-11 -> dpcpp-adl-win11-validation
Package Validation + caas + linux -> package-validation-caas
VM Smoke Validation + vm + ubuntu-24.04 -> oneapi-vm-smoke-validation
```

Unsupported combinations fail early in the `Resolve Scenario` stage.

## Render

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
Health Check Path: /health
```

Set these environment variables in Render:

```text
ONECLOUD_BASE_URL=<your OneCloud Render URL>
GTAX_BASE_URL=<your GTAX Render URL>
```

## EC2

For EC2 deployment with systemd, see [EC2_DEPLOYMENT.md](EC2_DEPLOYMENT.md).
