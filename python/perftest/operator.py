import asyncio
import datetime
import functools
import logging
import math
import random
import string
import sys

import kopf

from pydantic.json import custom_pydantic_encoder

from easykube import AsyncClient, Configuration, ApiError, PRESENT

from kube_custom_resource import CustomResourceRegistry

from . import errors, models, template, utils
from .models import v1alpha1 as api
from .config import settings


logger = logging.getLogger(__name__)


#####
# Initialise global variables
#####
# Template loader for rendering benchmark resources
TEMPLATE_LOADER: template.Loader = template.Loader(settings = settings)
# easykube client configured from the environment
# This means KUBECONFIG from envvar if specified, then service account if available,
# then default config file if available
EK_CLIENT: AsyncClient = (
    Configuration
        .from_environment(
            # Custom JSON encoder derived from the Pydantic encoder that produces UTC
            # ISO8601-compliant strings for datetimes.
            json_encoder = lambda obj: custom_pydantic_encoder(
                {
                    datetime.datetime: lambda dt: (
                        dt
                            .astimezone(tz = datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ")
                    )
                },
                obj
            )
        )
        .async_client(default_field_manager = settings.easykube_field_manager)
)
# Initialise the registry, discover custom resource models and build CRDs
REGISTRY: CustomResourceRegistry = CustomResourceRegistry(
    settings.api_group,
    settings.crd_categories
)
REGISTRY.discover_models(models)
# Atomic integer for holding the current priority
PRIORITY_LOCK: asyncio.Lock


@kopf.on.startup()
async def on_startup(**kwargs):
    """
    Apply kopf settings and register CRDs.
    """
    kopf_settings = kwargs["settings"]
    kopf_settings.persistence.finalizer = f"{settings.api_group}/finalizer"
    kopf_settings.persistence.progress_storage = kopf.AnnotationsProgressStorage(
        prefix = settings.api_group
    )
    kopf_settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage(
        prefix = settings.api_group,
        key = "last-handled-configuration",
    )
    # This has to be initialised inside the event loop
    global PRIORITY_LOCK
    PRIORITY_LOCK = asyncio.Lock()
    # Install the CRDs for the models in the registry
    for crd in REGISTRY:
        try:
            # We include default values in the CRDs, as freezing defaults at create
            # time is appropriate for our use case
            await EK_CLIENT.apply_object(crd.kubernetes_resource(), force = True)
        except Exception:
            logger.exception("error applying CRD %s.%s - exiting", crd.plural_name, crd.api_group)
            sys.exit(1)
    # Give Kubernetes a chance to create the APIs for the CRDs
    await asyncio.sleep(0.5)
    # Check to see if the APIs for the CRDs are up
    # If they are not, the kopf watches will not start properly so we exit and get restarted
    for crd in REGISTRY:
        preferred_version = next(k for k, v in crd.versions.items() if v.storage)
        api_version = f"{crd.api_group}/{preferred_version}"
        try:
            await EK_CLIENT.get(f"/apis/{api_version}/{crd.plural_name}")
        except Exception:
            logger.exception(
                "api for %s.%s not available - exiting",
                crd.plural_name,
                crd.api_group
            )
            sys.exit(1)
    

@kopf.on.cleanup()
async def on_cleanup(**kwargs):
    """
    Runs on operator shutdown.
    """
    await EK_CLIENT.aclose()


def benchmark_handler(register_fn, **kwargs):
    """
    Decorator that registers a handler with kopf for every benchmark that is defined.
    """
    def decorator(func):
        @functools.wraps(func)
        async def handler(**handler_kwargs):
            # Get the model instance associated with the Kubernetes resource, making
            # sure to account for nested handlers
            if "benchmark" not in handler_kwargs:
                handler_kwargs["benchmark"] = REGISTRY.get_model_instance(handler_kwargs["body"])
            return await func(**handler_kwargs)
        for crd in REGISTRY:
            preferred_version = next(k for k, v in crd.versions.items() if v.storage)
            api_version = f"{crd.api_group}/{preferred_version}"
            # Ignore the benchmarkset
            if crd.kind == api.BenchmarkSet._meta.kind:
                continue
            handler = register_fn(api_version, crd.kind, **kwargs)(handler)
        return handler
    return decorator


async def save_benchmark_status(benchmark):
    """
    Saves the status of the given benchmark and returns a new benchmark.
    """
    ekapi = EK_CLIENT.api(benchmark.api_version)
    resource = await ekapi.resource(f"{benchmark._meta.plural_name}/status")
    try:
        data = await resource.server_side_apply(
            benchmark.metadata.name,
            {
                "metadata": { "resourceVersion": benchmark.metadata.resource_version },
                "status": benchmark.status.model_dump(exclude_defaults = True),
            },
            namespace = benchmark.metadata.namespace
        )
    except ApiError as exc:
        if exc.status_code == 409:
            raise kopf.TemporaryError("conflict applying benchmark status update", delay = 1)
        else:
            raise
    else:
        return REGISTRY.get_model_instance(data)


@benchmark_handler(kopf.on.create)
async def handle_benchmark_created(benchmark, **kwargs):
    """
    Executes whenever a benchmark is created or the spec of a benchmark is updated.
    """
    # Acknowledge that we are aware of the benchmark at the earliest possible opportunity
    if benchmark.status.phase == api.BenchmarkPhase.UNKNOWN:
        benchmark.status.phase = api.BenchmarkPhase.PENDING
        benchmark = await save_benchmark_status(benchmark)
    # Find the priority class to use for the benchmark
    # If we have not already created one, then create one
    # Use a lock to avoid two benchmarks getting the same priority
    current_priority = settings.initial_priority + 1
    async with PRIORITY_LOCK:
        resource = await EK_CLIENT.api("scheduling.k8s.io/v1").resource("priorityclasses")
        async for pc in resource.list(labels = { settings.kind_label: PRESENT }):
            # If the priority class has labels that match the benchmark, use it
            # If not, use the priority of the class to adjust the current priority
            labels = pc["metadata"]["labels"]
            if (
                labels[settings.kind_label] == benchmark.kind and
                labels[settings.namespace_label] == benchmark.metadata.namespace and
                labels[settings.name_label] == benchmark.metadata.name
            ):
                priority_class = pc
                break
            else:
                current_priority = min(current_priority, pc["value"])
        else:
            priority_class = await resource.create(
                {
                    "apiVersion": "scheduling.k8s.io/v1",
                    "kind": "PriorityClass",
                    "metadata": {
                        "generateName": settings.resource_prefix,
                        "labels": {
                            settings.kind_label: benchmark.kind,
                            settings.namespace_label: benchmark.metadata.namespace,
                            settings.name_label: benchmark.metadata.name,
                        },
                    },
                    "value": current_priority - 1,
                    "globalDefault": False,
                    "preemptionPolicy": "PreemptLowerPriority",
                }
            )
    # Store the name of the priority class on the benchmark
    benchmark.status.priority_class_name = priority_class["metadata"]["name"]
    # Apply the benchmark resources to the cluster
    for resource in benchmark.get_resources(TEMPLATE_LOADER):
        # Make sure to adopt the resources so that they get removed with the benchmark
        metadata = resource.setdefault("metadata", {})
        metadata.setdefault("labels", {}).update({
            settings.kind_label: benchmark.kind,
            settings.namespace_label: benchmark.metadata.namespace,
            settings.name_label: benchmark.metadata.name,
        })
        metadata["namespace"] = benchmark.metadata.namespace
        metadata.setdefault("ownerReferences", []).append(
            {
                "apiVersion": benchmark.api_version,
                "kind": benchmark.kind,
                "name": benchmark.metadata.name,
                "uid": benchmark.metadata.uid,
                "blockOwnerDeletion": True,
                "controller": True,
            }
        )
        applied = await EK_CLIENT.apply_object(resource, force = True)
        # Store a reference to the resource so it can be deleted later
        benchmark.status.managed_resources.append(
            api.ResourceRef(
                api_version = applied["apiVersion"],
                kind = applied["kind"],
                name = applied["metadata"]["name"]
            )
        )
    # Save the resource refs
    await save_benchmark_status(benchmark)


@benchmark_handler(kopf.on.update, field = "status.phase")
async def handle_benchmark_status_changed(benchmark, **kwargs):
    """
    Executes when either the phase or the summary result for a benchmark changes.
    """
    # If the benchmark is transitioning to a finished state, set the finished time
    if benchmark.status.phase in {
        api.BenchmarkPhase.COMPLETED,
        api.BenchmarkPhase.FAILED
    }:
        if not benchmark.status.finished_at:
            benchmark.status.finished_at = datetime.datetime.now()
            await save_benchmark_status(benchmark)
        # If the benchmark has an owning set, register the completion with it
        ref = next(
            (
                ref
                for ref in benchmark.metadata.owner_references
                if (
                    ref.api_version.startswith(settings.api_group) and
                    ref.kind == api.BenchmarkSet._meta.kind
                )
            ),
            None
        )
        if ref:
            resource = await EK_CLIENT.api(ref.api_version).resource(ref.kind)
            try:
                benchmark_set = api.BenchmarkSet.model_validate(
                    await resource.fetch(
                        ref.name,
                        namespace = benchmark.metadata.namespace
                    )
                )
                succeeded = benchmark.status.phase == api.BenchmarkPhase.COMPLETED
                benchmark_set.status.completed[benchmark.metadata.name] = succeeded
                await save_benchmark_status(benchmark_set)
            except ApiError as exc:
                if exc.status_code != 404:
                    raise
    elif benchmark.status.phase == api.BenchmarkPhase.RUNNING:
        if not benchmark.status.started_at:
            benchmark.status.started_at = datetime.datetime.now()
            await save_benchmark_status(benchmark)
    elif benchmark.status.phase == api.BenchmarkPhase.SUMMARISING:
        # Allow the benchmark to summarise itself and save
        try:
            benchmark.summarise()
        except errors.PodResultsIncompleteError as exc:
            # Convert this into a temporary error with a short delay, as it is likely
            # to be resolved quickly
            raise kopf.TemporaryError(str(exc), delay = 1)
        else:
            benchmark = await save_benchmark_status(benchmark)
        # Once the benchmark summary has been saved successfully, we can delete the managed resources
        for ref in benchmark.status.managed_resources:
            resource = await EK_CLIENT.api(ref.api_version).resource(ref.kind)
            await resource.delete(ref.name, namespace = benchmark.metadata.namespace)
        # Make sure to delete the priority class
        resource = await EK_CLIENT.api("scheduling.k8s.io/v1").resource("priorityclasses")
        await resource.delete(benchmark.status.priority_class_name)
        # Once the resources are deleted, we can mark the benchmark as completed
        benchmark.status.phase = api.BenchmarkPhase.COMPLETED
        benchmark.status.managed_resources = []
        await save_benchmark_status(benchmark)


@benchmark_handler(kopf.on.delete)
async def handle_benchmark_deleted(benchmark, **kwargs):
    """
    Executes when a benchmark is deleted.
    """
    for ref in benchmark.status.managed_resources:
        resource = await EK_CLIENT.api(ref.api_version).resource(ref.kind)
        await resource.delete(ref.name, namespace = benchmark.metadata.namespace)
    # Make sure to delete the priority class
    resource = await EK_CLIENT.api("scheduling.k8s.io/v1").resource("priorityclasses")
    await resource.delete(benchmark.status.priority_class_name)


def on_benchmark_resource_event(*args, **kwargs):
    """
    Decorator that registers a handler with kopf for events on resources that are
    owned by a benchmark, based on labels.
    """
    def decorator(func):
        @functools.wraps(func)
        async def handler(**handler_kwargs):
            # Implement retries for kopf temporary errors
            # kopf does not provide this for event handlers normally, but it is important
            # to deal with conflicts when applying updates to benchmark state
            # To do this, we annotate the resource with a random value to trigger a retry
            ekapi = await EK_CLIENT.api_preferred_version(settings.api_group)
            resource = await ekapi.resource(handler_kwargs["labels"][settings.kind_label])
            try:
                benchmark = REGISTRY.get_model_instance(
                    await resource.fetch(
                        handler_kwargs["labels"][settings.name_label],
                        namespace = handler_kwargs["labels"][settings.namespace_label]
                    )
                )
                return await func(benchmark = benchmark, **handler_kwargs)
            except ApiError as exc:
                if exc.status_code == 404:
                    return
                else:
                    raise
            except kopf.TemporaryError as exc:
                logger.warning(f"{exc} - retrying")
                try:
                    await EK_CLIENT.patch_object(
                        handler_kwargs["body"],
                        {
                            "metadata": {
                                "annotations": {
                                    f"{settings.api_group}/force-retry": "".join(
                                        random.choices(
                                            string.ascii_lowercase + string.digits,
                                            k = 8
                                        )
                                    ),
                                },
                            },
                        }
                    )
                except ApiError as exc:
                    if exc.status_code == 404:
                        return
                    else:
                        raise
        kwargs.setdefault("labels", {}).update({ settings.kind_label: kopf.PRESENT })
        return kopf.on.event(*args, **kwargs)(handler)
    return decorator


@on_benchmark_resource_event("jobset.x-k8s.io", "jobset")
async def handle_jobset_event(benchmark, body, **kwargs):
    """
    Executes whenever an event occurs for a jobset that is part of a benchmark.
    """
    if type == "DELETED":
        return
    # If the benchmark is already in a terminal phase, there is nothing to do
    if benchmark.status.phase.is_terminal():
        return
    # Allow the benchmark to update it's status based on the job
    benchmark.jobset_modified(body)
    await save_benchmark_status(benchmark)


@on_benchmark_resource_event("pod")
async def handle_pod_event(type, benchmark, body, name, namespace, **kwargs):
    """
    Executes whenever an event occurs for a pod that is part of a benchmark.
    """
    if type == "DELETED":
        return
    # If the benchmark is already in a terminal phase, there is nothing to do
    if benchmark.status.phase.is_terminal():
        return
    # Allow the benchmark to make a status update based on the pod
    # We pass a callback that allows the benchmark to access the pod log if required
    async def fetch_pod_log() -> str:
        resource = await EK_CLIENT.api("v1").resource("pods/log")
        return await resource.fetch(name, namespace = namespace)
    await benchmark.pod_modified(body, fetch_pod_log)
    await save_benchmark_status(benchmark)


@kopf.on.create(settings.api_group, api.BenchmarkSet._meta.kind)
async def handle_benchmark_set_created(body, **kwargs):
    """
    Executed whenever a benchmark set is created.
    """
    benchmark_set = api.BenchmarkSet.model_validate(body)
    # Update the count before we create anything
    # We can calculate this without producing any permutations
    benchmark_set.status.permutation_count = benchmark_set.spec.permutations.get_count()
    benchmark_set.status.count = (
        benchmark_set.status.permutation_count *
        benchmark_set.spec.repetitions
    )
    if benchmark_set.status.succeeded is None:
        benchmark_set.status.succeeded = 0
        benchmark_set.status.failed = 0
    await save_benchmark_status(benchmark_set)
    # Calculate the width that we want to pad indexes to
    # We do this so that benchmarks are ordered by default
    padding_width = math.floor(math.log(benchmark_set.status.count, 10)) + 1
    # Produce the resources for the benchmark set
    idx = 1
    for permutation in benchmark_set.spec.permutations.get_permutations():
        for _ in range(benchmark_set.spec.repetitions):
            resource = {
                "apiVersion": benchmark_set.spec.template.api_version,
                "kind": benchmark_set.spec.template.kind,
                "metadata": {
                    # Use a name that is unique to the permutation
                    "name": f"{benchmark_set.metadata.name}-{str(idx).zfill(padding_width)}",
                    "namespace": benchmark_set.metadata.namespace,
                    "ownerReferences": [
                        {
                            "apiVersion": benchmark_set.api_version,
                            "kind": benchmark_set.kind,
                            "name": benchmark_set.metadata.name,
                            "uid": benchmark_set.metadata.uid,
                            "blockOwnerDeletion": True,
                            "controller": True,
                        },
                    ]
                },
                "spec": utils.mergeconcat(benchmark_set.spec.template.spec, permutation)
            }
            await EK_CLIENT.apply_object(resource, force = True)
            idx = idx + 1


@kopf.on.update(settings.api_group, api.BenchmarkSet._meta.kind, field = "status.completed")
async def handle_benchmark_set_completed(body, **kwargs):
    """
    Executed whenever a completed benchmark is registered for a benchmark set.
    """
    benchmark_set = api.BenchmarkSet.model_validate(body)
    succeeded = failed = 0
    for completed in benchmark_set.status.completed.values():
        if completed:
            succeeded = succeeded + 1
        else:
            failed = failed + 1
    benchmark_set.status.succeeded = succeeded
    benchmark_set.status.failed = failed
    if (succeeded + failed) == benchmark_set.status.count:
        benchmark_set.status.finished_at = datetime.datetime.now()
    await save_benchmark_status(benchmark_set)
