from __future__ import annotations

import os
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field


SERVICE_NAME = os.getenv("SERVICE_NAME", "internal-provisioning-api")


class JsonLogFormatter(logging.Formatter):
    reserved_keys = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "event": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in self.reserved_keys or key.startswith("_"):
                continue
            payload[key] = serialize_log_value(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"))


def serialize_log_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def configure_logging() -> logging.Logger:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    service_logger = logging.getLogger(SERVICE_NAME)
    service_logger.handlers.clear()
    service_logger.addHandler(handler)
    service_logger.setLevel(log_level)
    service_logger.propagate = False
    return service_logger


logger = configure_logging()


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
    idempotency_key: str
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
    expires_at: datetime | None = None


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


class ProvisionStore:
    store_type = "base"

    def create_or_get(self, record: ProvisionRecord) -> tuple[ProvisionRecord, bool]:
        raise NotImplementedError

    def get(self, request_id: str) -> ProvisionRecord | None:
        raise NotImplementedError

    def update(self, request_id: str, **changes: object) -> ProvisionRecord:
        raise NotImplementedError

    def find_by_reservation(self, reservation_id: str) -> tuple[str, ProvisionRecord] | None:
        raise NotImplementedError


class InMemoryProvisionStore(ProvisionStore):
    store_type = "memory"

    def __init__(self) -> None:
        self.records: dict[str, ProvisionRecord] = {}
        self.idempotency_index: dict[str, str] = {}

    def create_or_get(self, record: ProvisionRecord) -> tuple[ProvisionRecord, bool]:
        existing_request_id = self.idempotency_index.get(record.idempotency_key)
        if existing_request_id is not None:
            return self.records[existing_request_id], False

        self.records[record.request_id] = record
        self.idempotency_index[record.idempotency_key] = record.request_id
        return record, True

    def get(self, request_id: str) -> ProvisionRecord | None:
        return self.records.get(request_id)

    def update(self, request_id: str, **changes: object) -> ProvisionRecord:
        record = self.records[request_id]
        updated = record.model_copy(
            update={
                **changes,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.records[request_id] = updated
        return updated

    def find_by_reservation(self, reservation_id: str) -> tuple[str, ProvisionRecord] | None:
        for request_id, record in self.records.items():
            if record.reservation_id == reservation_id:
                return request_id, record
        return None


class DynamoDbProvisionStore(ProvisionStore):
    store_type = "dynamodb"

    def __init__(
        self,
        table_name: str,
        region_name: str | None = None,
        reservation_id_index: str = "reservation_id-index",
    ) -> None:
        try:
            import boto3
            from botocore.exceptions import ClientError
            from boto3.dynamodb.conditions import Key
        except ImportError as exc:
            raise RuntimeError("Install boto3 to use PROVISION_STORE=dynamodb") from exc

        self.client_error = ClientError
        self.key_condition = Key
        self.table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self.reservation_id_index = reservation_id_index

    def create_or_get(self, record: ProvisionRecord) -> tuple[ProvisionRecord, bool]:
        item = record.model_dump(mode="json", exclude_none=True)
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(request_id)",
            )
            return record, True
        except self.client_error as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

            response = self.table.get_item(Key={"request_id": record.request_id})
            return ProvisionRecord.model_validate(response["Item"]), False

    def get(self, request_id: str) -> ProvisionRecord | None:
        response = self.table.get_item(Key={"request_id": request_id})
        item = response.get("Item")
        if item is None:
            return None
        return ProvisionRecord.model_validate(item)

    def update(self, request_id: str, **changes: object) -> ProvisionRecord:
        record = self.get(request_id)
        if record is None:
            raise KeyError(request_id)

        updated = record.model_copy(
            update={
                **changes,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.table.put_item(Item=updated.model_dump(mode="json", exclude_none=True))
        return updated

    def find_by_reservation(self, reservation_id: str) -> tuple[str, ProvisionRecord] | None:
        response = self.table.query(
            IndexName=self.reservation_id_index,
            KeyConditionExpression=self.key_condition("reservation_id").eq(reservation_id),
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return None

        record = ProvisionRecord.model_validate(items[0])
        return record.request_id, record


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
        provider=Provider.ONECLOUD,
        platform="any",
        os="any",
        image="onecloud-default-validation",
        workload_type="hardware",
    ),
}

PROVIDER_URLS = {
    Provider.ONECLOUD: os.getenv("ONECLOUD_BASE_URL", "https://dummy-onecloud-api.onrender.com").rstrip("/"),
    Provider.GTAX: os.getenv("GTAX_BASE_URL", "https://dummy-gtax-api.onrender.com").rstrip("/"),
}
PROVIDER_REQUEST_TIMEOUT_SECONDS = float(os.getenv("PROVIDER_REQUEST_TIMEOUT_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.getenv("PROVISION_POLL_INTERVAL_SECONDS", "2"))
PROVISION_TIMEOUT_SECONDS = float(os.getenv("PROVISION_TIMEOUT_SECONDS", "300"))
PROVISION_RECORD_TTL_HOURS = int(os.getenv("PROVISION_RECORD_TTL_HOURS", "48"))
PROVISION_STORE = os.getenv("PROVISION_STORE", "memory").lower()
DYNAMODB_TABLE_NAME = os.getenv("PROVISION_DYNAMODB_TABLE", "internal-provisioning-requests")
DYNAMODB_RESERVATION_ID_INDEX = os.getenv("PROVISION_DYNAMODB_RESERVATION_ID_INDEX", "reservation_id-index")
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")


def create_provision_store() -> ProvisionStore:
    if PROVISION_STORE == "memory":
        return InMemoryProvisionStore()
    if PROVISION_STORE == "dynamodb":
        return DynamoDbProvisionStore(
            table_name=DYNAMODB_TABLE_NAME,
            region_name=AWS_REGION,
            reservation_id_index=DYNAMODB_RESERVATION_ID_INDEX,
        )
    raise RuntimeError(f"Unsupported PROVISION_STORE={PROVISION_STORE}")


store = create_provision_store()

app = FastAPI(
    title="Internal Provisioning API",
    description="Jenkins-facing middleware for OneCloud and GTAX validation resource provisioning.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "deployment": "ecs-fargate",
        "store": store.store_type,
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
def provision(
    request: ProvisionRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProvisionRecord:
    return create_provisioning_request(request, background_tasks, idempotency_key)


@app.post("/provision/request-id", response_class=PlainTextResponse, status_code=202)
def provision_request_id(
    request: ProvisionRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    record = create_provisioning_request(request, background_tasks, idempotency_key)
    return record.request_id


def create_provisioning_request(
    request: ProvisionRequest,
    background_tasks: BackgroundTasks,
    supplied_idempotency_key: str | None = None,
) -> ProvisionRecord:
    scenario = SCENARIOS.get(request.test_scenario)
    if scenario is None:
        raise HTTPException(status_code=400, detail=f"Unknown test scenario: {request.test_scenario}")

    idempotency_key = normalize_idempotency_key(request, supplied_idempotency_key)
    request_id = request_id_from_idempotency_key(idempotency_key)
    record = ProvisionRecord(
        request_id=request_id,
        idempotency_key=idempotency_key,
        test_scenario=request.test_scenario,
        team=request.team,
        jenkins_build_id=request.jenkins_build_id,
        status=ProvisionStatus.REQUESTED,
        message="Provisioning request accepted.",
        provider=scenario.provider,
        image=scenario.image,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=PROVISION_RECORD_TTL_HOURS),
    )
    stored_record, created = store.create_or_get(record)

    if created:
        logger.info(
            "provision.request.accepted",
            extra=record_log_context(stored_record),
        )
        background_tasks.add_task(run_provisioning_workflow, stored_record.request_id, request)
    else:
        logger.info(
            "provision.request.reused",
            extra=record_log_context(stored_record),
        )

    return stored_record


@app.get("/provision/{request_id}/status", response_model=ProvisionRecord)
def get_status(request_id: str) -> ProvisionRecord:
    record = store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Provisioning request not found")
    return record


@app.get("/provision/{request_id}/status-line", response_class=PlainTextResponse)
def get_status_line(request_id: str) -> str:
    record = get_status(request_id)
    fields = [
        record.status.value,
        safe_status_field(record.message),
        record.reservation_id or "",
        record.machine_id or "",
    ]
    return "|".join(fields)


@app.post("/reservations/{reservation_id}/release", response_model=ReleaseResponse)
def release_reservation(reservation_id: str) -> ReleaseResponse:
    match = find_record_by_reservation(reservation_id)
    request_id: str | None = None
    provider: Provider | None = None
    record: ProvisionRecord | None = None

    if match is not None:
        request_id, record = match
        provider = record.provider
        if record.status == ProvisionStatus.RELEASED:
            logger.info(
                "release.already_completed",
                extra=record_log_context(record),
            )
            return ReleaseResponse(
                reservation_id=reservation_id,
                status=ProvisionStatus.RELEASED,
                message="Reservation already released.",
            )
    else:
        provider = infer_provider_from_reservation_id(reservation_id)

    if provider is None:
        raise HTTPException(
            status_code=404,
            detail="Reservation not found and provider could not be inferred from reservation ID",
        )

    logger.info(
        "release.requested",
        extra={
            "request_id": request_id,
            "reservation_id": reservation_id,
            "provider": provider.value,
        },
    )

    try:
        with httpx.Client(timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(f"{PROVIDER_URLS[provider]}/reservations/{reservation_id}/release")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error(
            "release.failed",
            extra={
                "request_id": request_id,
                "reservation_id": reservation_id,
                "provider": provider.value,
                "failure_reason": str(exc),
            },
        )
        raise HTTPException(status_code=502, detail=f"Provider release failed: {exc}") from exc

    if request_id is not None:
        record = update_record(
            request_id,
            status=ProvisionStatus.RELEASED,
            message="Reservation released.",
        )

    logger.info(
        "release.completed",
        extra=record_log_context(record)
        if record is not None
        else {
            "request_id": request_id,
            "reservation_id": reservation_id,
            "provider": provider.value,
        },
    )

    return ReleaseResponse(
        reservation_id=reservation_id,
        status=ProvisionStatus.RELEASED,
        message="Reservation released.",
    )


def run_provisioning_workflow(request_id: str, request: ProvisionRequest) -> None:
    scenario = SCENARIOS[request.test_scenario]

    try:
        logger.info(
            "scenario.resolved",
            extra={
                "request_id": request_id,
                "test_scenario": request.test_scenario,
                "jenkins_build_id": request.jenkins_build_id,
                "team": request.team,
                "provider": scenario.provider.value,
                "image": scenario.image,
            },
        )
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

        deployment_image = choose_deployment_image(scenario, machine)
        update_record(
            request_id,
            reservation_id=reservation_id,
            machine_id=machine.machine_id,
            image=deployment_image,
            status=ProvisionStatus.IMAGE_DEPLOYING,
            message="Reservation created. Deploying validation image.",
        )

        deployment_id = deploy_provider_image(
            provider=scenario.provider,
            machine_id=machine.machine_id,
            image=deployment_image,
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

        if scenario.provider == Provider.GTAX and scenario.workload_type == "caas":
            if machine.platform != "caas":
                continue
            if scenario.image not in machine.supported_images:
                continue
            logger.info(
                "machine.selected",
                extra={
                    "provider": scenario.provider.value,
                    "machine_id": machine.machine_id,
                    "platform": machine.platform,
                    "os": machine.os,
                    "image": scenario.image,
                    "team": team,
                },
            )
            return machine

        if scenario.provider == Provider.ONECLOUD:
            if not machine.supported_images:
                continue
            logger.info(
                "machine.selected",
                extra={
                    "provider": scenario.provider.value,
                    "machine_id": machine.machine_id,
                    "platform": machine.platform,
                    "os": machine.os,
                    "image": choose_deployment_image(scenario, machine),
                    "team": team,
                },
            )
            return machine

        if machine.platform != scenario.platform:
            continue
        logger.info(
            "machine.selected",
            extra={
                "provider": scenario.provider.value,
                "machine_id": machine.machine_id,
                "platform": machine.platform,
                "os": machine.os,
                "image": scenario.image,
                "team": team,
            },
        )
        return machine

    return None


def choose_deployment_image(scenario: ScenarioConfig, machine: Machine) -> str:
    if scenario.image in machine.supported_images:
        return scenario.image
    if machine.supported_images:
        return machine.supported_images[0]
    return scenario.image


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
            reservation_id = str(response.json()["reservation_id"])
            logger.info(
                "reservation.created",
                extra={
                    "provider": provider.value,
                    "reservation_id": reservation_id,
                    "machine_id": machine_id,
                    "team": team,
                    "jenkins_build_id": jenkins_build_id,
                },
            )
            return reservation_id
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
            deployment_id = str(response.json()["deployment_id"])
            logger.info(
                "image.deployment.started",
                extra={
                    "provider": provider.value,
                    "machine_id": machine_id,
                    "image": image,
                    "deployment_id": deployment_id,
                },
            )
            return deployment_id
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
                message="Machine reserved and validation image deployed. Jenkins can start validation.",
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
    updated = store.update(request_id, **changes)
    logger.info(
        "provision.status.updated",
        extra=record_log_context(updated),
    )
    return updated


def find_record_by_reservation(reservation_id: str) -> tuple[str, ProvisionRecord] | None:
    return store.find_by_reservation(reservation_id)


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


def safe_status_field(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").replace("\r", " ")


def normalize_idempotency_key(
    request: ProvisionRequest,
    supplied_idempotency_key: str | None,
) -> str:
    if supplied_idempotency_key and supplied_idempotency_key.strip():
        return supplied_idempotency_key.strip()

    return "jenkins:{team}:{build}:{scenario}".format(
        team=request.team,
        build=request.jenkins_build_id,
        scenario=request.test_scenario,
    )


def request_id_from_idempotency_key(idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{SERVICE_NAME}:{idempotency_key}"))


def record_log_context(record: ProvisionRecord) -> dict[str, Any]:
    return {
        "request_id": record.request_id,
        "idempotency_key": record.idempotency_key,
        "jenkins_build_id": record.jenkins_build_id,
        "test_scenario": record.test_scenario,
        "team": record.team,
        "status": record.status.value,
        "provider": record.provider.value if record.provider else None,
        "reservation_id": record.reservation_id,
        "machine_id": record.machine_id,
        "image": record.image,
        "status_message": record.message,
        "failure_reason": record.failure_reason,
    }
