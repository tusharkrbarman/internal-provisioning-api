from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field


class Provider(str, Enum):
    ONECLOUD = "onecloud"
    GTAX = "gtax"


class ProvisionStatus(str, Enum):
    REQUESTED = "REQUESTED"
    RESERVATION_PENDING = "RESERVATION_PENDING"
    IMAGE_DEPLOYING = "IMAGE_DEPLOYING"
    READY = "READY"
    NO_ELIGIBLE_MACHINE = "NO_ELIGIBLE_MACHINE"
    RESERVATION_FAILED = "RESERVATION_FAILED"
    IMAGE_DEPLOY_FAILED = "IMAGE_DEPLOY_FAILED"
    PROVISIONING_TIMEOUT = "PROVISIONING_TIMEOUT"
    FAILED = "FAILED"
    RELEASED = "RELEASED"


class ProviderDeploymentStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    READY = "READY"
    FAILED = "FAILED"


class ProvisionRequest(BaseModel):
    test_scenario: str = Field(..., min_length=3)
    team: str = Field(default="oneapi", min_length=1)
    jenkins_build_id: str = Field(..., min_length=1)
    duration_hours: int = Field(default=4, ge=1, le=24)


class ScenarioConfig(BaseModel):
    provider: Provider
    platform: str
    os: str
    image: str
    workload_type: Literal["hardware", "caas", "vm"]


class Machine(BaseModel):
    provider: str
    machine_id: str
    platform: str
    os: str
    status: str
    team_tags: list[str]
    supported_images: list[str]
    dirty: bool = False


class ProvisionRecord(BaseModel):
    request_id: str
    test_scenario: str
    team: str
    jenkins_build_id: str
    status: ProvisionStatus
    message: str
    provider: Provider | None = None
    reservation_id: str | None = None
    machine_id: str | None = None
    image: str | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReleaseResponse(BaseModel):
    reservation_id: str
    status: ProvisionStatus
    message: str


class ProviderHealth(BaseModel):
    provider: Provider
    base_url: str
    reachable: bool
    status_code: int | None = None
    error: str | None = None


class ProviderApiError(RuntimeError):
    def __init__(self, provider: Provider, operation: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation
        self.message = message


SCENARIOS: dict[str, ScenarioConfig] = {
    "dpcpp-adl-win11-validation": ScenarioConfig(
        provider=Provider.ONECLOUD,
        platform="ADL",
        os="windows-11",
        image="compiler-validation-win11",
        workload_type="hardware",
    ),
    "ide-extension-win11-validation": ScenarioConfig(
        provider=Provider.ONECLOUD,
        platform="windows-client",
        os="windows-11",
        image="ide-extension-validation-win11",
        workload_type="hardware",
    ),
    "gpu-dg2-win11-validation": ScenarioConfig(
        provider=Provider.ONECLOUD,
        platform="DG2",
        os="windows-11",
        image="gpu-runtime-validation-win11",
        workload_type="hardware",
    ),
    "cpp-mtl-linux-validation": ScenarioConfig(
        provider=Provider.ONECLOUD,
        platform="MTL",
        os="ubuntu-24.04",
        image="compiler-validation-ubuntu24",
        workload_type="hardware",
    ),
    "package-validation-caas": ScenarioConfig(
        provider=Provider.GTAX,
        platform="caas",
        os="linux",
        image="oneapi-package-validation",
        workload_type="caas",
    ),
    "static-analysis-caas": ScenarioConfig(
        provider=Provider.GTAX,
        platform="caas",
        os="linux",
        image="static-analysis-linux",
        workload_type="caas",
    ),
    "oneapi-vm-smoke-validation": ScenarioConfig(
        provider=Provider.GTAX,
        platform="vm",
        os="ubuntu-24.04",
        image="oneapi-vm-smoke",
        workload_type="vm",
    ),
}

PROVIDER_URLS = {
    Provider.ONECLOUD: os.getenv("ONECLOUD_BASE_URL", "https://dummy-onecloud-api.onrender.com").rstrip("/"),
    Provider.GTAX: os.getenv("GTAX_BASE_URL", "https://dummy-gtax-api.onrender.com").rstrip("/"),
}
PROVIDER_REQUEST_TIMEOUT_SECONDS = float(os.getenv("PROVIDER_REQUEST_TIMEOUT_SECONDS", "10"))
POLL_INTERVAL_SECONDS = float(os.getenv("PROVISION_POLL_INTERVAL_SECONDS", "2"))
PROVISION_TIMEOUT_SECONDS = float(os.getenv("PROVISION_TIMEOUT_SECONDS", "300"))

records: dict[str, ProvisionRecord] = {}

app = FastAPI(
    title="Internal Provisioning API",
    description="Jenkins-facing middleware for OneCloud and GTAX validation resource provisioning.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "providers": {
            provider.value: url for provider, url in PROVIDER_URLS.items()
        },
    }


@app.get("/provider-health", response_model=list[ProviderHealth])
def provider_health() -> list[ProviderHealth]:
    return [check_provider_health(provider) for provider in Provider]


@app.get("/scenarios", response_model=dict[str, ScenarioConfig])
def list_scenarios() -> dict[str, ScenarioConfig]:
    return SCENARIOS


@app.get("/machines", response_model=list[Machine])
def list_machines(provider: Provider | None = None, allow_partial: bool = False) -> list[Machine]:
    providers = [provider] if provider else [Provider.ONECLOUD, Provider.GTAX]
    machines: list[Machine] = []
    errors: list[str] = []
    for selected_provider in providers:
        try:
            machines.extend(fetch_machines(selected_provider))
        except ProviderApiError as exc:
            errors.append(f"{exc.provider.value}: {exc.message}")

    if errors and not allow_partial:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "One or more provider APIs could not be reached.",
                "errors": errors,
                "hint": "Call /provider-health or retry with /machines?allow_partial=true.",
            },
        )

    return machines


@app.post("/provision", response_model=ProvisionRecord, status_code=202)
def provision(request: ProvisionRequest, background_tasks: BackgroundTasks) -> ProvisionRecord:
    scenario = SCENARIOS.get(request.test_scenario)
    if scenario is None:
        raise HTTPException(status_code=400, detail=f"Unknown test scenario: {request.test_scenario}")

    request_id = str(uuid4())
    record = ProvisionRecord(
        request_id=request_id,
        test_scenario=request.test_scenario,
        team=request.team,
        jenkins_build_id=request.jenkins_build_id,
        status=ProvisionStatus.REQUESTED,
        message="Provisioning request accepted.",
        provider=scenario.provider,
        image=scenario.image,
    )
    records[request_id] = record
    background_tasks.add_task(run_provisioning_workflow, request_id, request)
    return record


@app.get("/provision/{request_id}/status", response_model=ProvisionRecord)
def get_status(request_id: str) -> ProvisionRecord:
    record = records.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Provisioning request not found")
    return record


@app.post("/reservations/{reservation_id}/release", response_model=ReleaseResponse)
def release_reservation(reservation_id: str) -> ReleaseResponse:
    match = find_record_by_reservation(reservation_id)
    request_id: str | None = None
    provider: Provider | None = None

    if match is not None:
        request_id, record = match
        provider = record.provider
    else:
        provider = infer_provider_from_reservation_id(reservation_id)

    if provider is None:
        raise HTTPException(
            status_code=404,
            detail="Reservation not found and provider could not be inferred from reservation ID",
        )

    try:
        with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(f"{PROVIDER_URLS[provider]}/reservations/{reservation_id}/release")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Provider release failed: {exc}") from exc

    if request_id is not None:
        update_record(
            request_id,
            status=ProvisionStatus.RELEASED,
            message="Reservation released.",
        )

    return ReleaseResponse(
        reservation_id=reservation_id,
        status=ProvisionStatus.RELEASED,
        message="Reservation released.",
    )


def run_provisioning_workflow(request_id: str, request: ProvisionRequest) -> None:
    scenario = SCENARIOS[request.test_scenario]

    try:
        update_record(
            request_id,
            status=ProvisionStatus.RESERVATION_PENDING,
            message="Discovering eligible machines from provider inventory.",
        )
        machine = select_machine(scenario, request.team)
        if machine is None:
            update_record(
                request_id,
                status=ProvisionStatus.NO_ELIGIBLE_MACHINE,
                message="No available team-tagged machine matched the scenario requirements.",
            )
            return

        reservation_id = create_provider_reservation(
            provider=scenario.provider,
            machine_id=machine.machine_id,
            team=request.team,
            duration_hours=request.duration_hours,
            jenkins_build_id=request.jenkins_build_id,
        )
        update_record(
            request_id,
            reservation_id=reservation_id,
            machine_id=machine.machine_id,
            status=ProvisionStatus.IMAGE_DEPLOYING,
            message="Reservation created. Triggering image deployment.",
        )

        deployment_id = deploy_provider_image(
            provider=scenario.provider,
            machine_id=machine.machine_id,
            image=scenario.image,
        )
        wait_for_deployment(request_id, scenario.provider, deployment_id)

    except httpx.HTTPStatusError as exc:
        status = ProvisionStatus.RESERVATION_FAILED
        if exc.response.request.method == "POST" and "deploy-image" in str(exc.request.url):
            status = ProvisionStatus.IMAGE_DEPLOY_FAILED
        update_record(
            request_id,
            status=status,
            message="Provider API returned an error.",
            failure_reason=f"{exc.response.status_code}: {exc.response.text}",
        )
    except httpx.HTTPError as exc:
        update_record(
            request_id,
            status=ProvisionStatus.FAILED,
            message="Provider API call failed.",
            failure_reason=str(exc),
        )
    except ProviderApiError as exc:
        update_record(
            request_id,
            status=ProvisionStatus.FAILED,
            message=f"{exc.provider.value} provider {exc.operation} failed.",
            failure_reason=exc.message,
        )
    except Exception as exc:
        update_record(
            request_id,
            status=ProvisionStatus.FAILED,
            message="Unexpected provisioning failure.",
            failure_reason=str(exc),
        )


def fetch_machines(provider: Provider) -> list[Machine]:
    try:
        with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(f"{PROVIDER_URLS[provider]}/machines")
            response.raise_for_status()
            return [Machine.model_validate(machine) for machine in response.json()]
    except httpx.HTTPStatusError as exc:
        raise ProviderApiError(
            provider,
            "machine discovery",
            f"HTTP {exc.response.status_code}: {exc.response.text}",
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderApiError(provider, "machine discovery", str(exc)) from exc


def select_machine(scenario: ScenarioConfig, team: str) -> Machine | None:
    for machine in fetch_machines(scenario.provider):
        if machine.status != "available":
            continue
        if machine.dirty:
            continue
        if team not in machine.team_tags:
            continue
        if machine.platform != scenario.platform:
            continue
        if machine.os != scenario.os:
            continue
        if scenario.image not in machine.supported_images:
            continue
        return machine
    return None


def create_provider_reservation(
    provider: Provider,
    machine_id: str,
    team: str,
    duration_hours: int,
    jenkins_build_id: str,
) -> str:
    payload = {
        "machine_id": machine_id,
        "team": team,
        "duration_hours": duration_hours,
        "jenkins_build_id": jenkins_build_id,
    }
    try:
        with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(f"{PROVIDER_URLS[provider]}/reservations", json=payload)
            response.raise_for_status()
            return str(response.json()["reservation_id"])
    except httpx.HTTPStatusError:
        raise
    except httpx.HTTPError as exc:
        raise ProviderApiError(provider, "reservation creation", str(exc)) from exc


def deploy_provider_image(provider: Provider, machine_id: str, image: str) -> str:
    try:
        with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{PROVIDER_URLS[provider]}/machines/{machine_id}/deploy-image",
                json={"image": image},
            )
            response.raise_for_status()
            return str(response.json()["deployment_id"])
    except httpx.HTTPStatusError:
        raise
    except httpx.HTTPError as exc:
        raise ProviderApiError(provider, "image deployment", str(exc)) from exc


def wait_for_deployment(request_id: str, provider: Provider, deployment_id: str) -> None:
    deadline = time.monotonic() + PROVISION_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
                response = client.get(f"{PROVIDER_URLS[provider]}/deployments/{deployment_id}/status")
                response.raise_for_status()
                provider_status = ProviderDeploymentStatus(response.json()["status"])
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderApiError(provider, "deployment status polling", str(exc)) from exc

        if provider_status == ProviderDeploymentStatus.READY:
            update_record(
                request_id,
                status=ProvisionStatus.READY,
                message="Machine reserved and image deployment completed.",
            )
            return

        if provider_status == ProviderDeploymentStatus.FAILED:
            update_record(
                request_id,
                status=ProvisionStatus.IMAGE_DEPLOY_FAILED,
                message="Provider reported image deployment failure.",
            )
            return

        time.sleep(POLL_INTERVAL_SECONDS)

    update_record(
        request_id,
        status=ProvisionStatus.PROVISIONING_TIMEOUT,
        message="Timed out waiting for image deployment to complete.",
    )


def update_record(request_id: str, **changes: object) -> ProvisionRecord:
    record = records[request_id]
    updated = record.model_copy(
        update={
            **changes,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    records[request_id] = updated
    return updated


def find_record_by_reservation(reservation_id: str) -> tuple[str, ProvisionRecord] | None:
    for request_id, record in records.items():
        if record.reservation_id == reservation_id:
            return request_id, record
    return None


def infer_provider_from_reservation_id(reservation_id: str) -> Provider | None:
    if reservation_id.startswith("onecloud-res-"):
        return Provider.ONECLOUD
    if reservation_id.startswith("gtax-res-"):
        return Provider.GTAX
    return None


def check_provider_health(provider: Provider) -> ProviderHealth:
    try:
        with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(f"{PROVIDER_URLS[provider]}/health")
        return ProviderHealth(
            provider=provider,
            base_url=PROVIDER_URLS[provider],
            reachable=response.is_success,
            status_code=response.status_code,
            error=None if response.is_success else response.text,
        )
    except httpx.HTTPError as exc:
        return ProviderHealth(
            provider=provider,
            base_url=PROVIDER_URLS[provider],
            reachable=False,
            error=str(exc),
        )
