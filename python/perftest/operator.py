import asyncio
import datetime
import logging

from pydantic.json import custom_pydantic_encoder

import easykube
import easykube_runtime
from easykube_runtime.ext import kube_custom_resource as kcr_runtime

from . import models, reconcilers, template
from .config import settings
from .models import v1alpha1 as api


logger = logging.getLogger(__name__)


async def run():
    """
    Run the operator logic.
    """
    logger.info("Creating template loader")
    template_loader = template.Loader(settings = settings)

    logger.info("Creating Kubernetes client")
    config = easykube.Configuration.from_environment(
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
    client = config.async_client(default_field_manager = settings.easykube_field_manager)

    async with client:
        logger.info("Registering CRDs")
        registry = await kcr_runtime.register_crds(
            client,
            settings.api_group,
            models,
            categories = settings.crd_categories
        )

        logger.info("Initialising runtime manager")
        manager = easykube_runtime.Manager(worker_count = settings.runtime_manager_worker_count)

        # Create the benchmarkset controller
        benchmarkset_watch = kcr_runtime.create_watch(settings.api_group, api.BenchmarkSet)
        manager.register_watch(benchmarkset_watch)
        benchmarkset_controller = manager.create_controller(
            benchmarkset_watch,
            reconcilers.BenchmarkSetReconciler(settings.api_group, template_loader)
        )

        # Create a watch for jobsets
        # This watch is shared between all the benchmark controllers
        jobset_api = await client.api_preferred_version("jobset.x-k8s.io")
        jobsets = await jobset_api.resource("jobsets")
        jobset_watch = manager.create_watch(
            jobsets.api_version,
            jobsets.kind,
            labels = { settings.kind_label: easykube.PRESENT }
        )

        # Create a controller for each benchmark in the registry
        for crd in registry:
            preferred_version = next(v for v in crd.versions.values() if v.storage)
            model = preferred_version.model
            if issubclass(model, api.Benchmark):
                # Create a watch for the benchmark model and register it
                benchmark_watch = kcr_runtime.create_watch(settings.api_group, model)
                manager.register_watch(benchmark_watch)

                # Subscribe the benchmarkset controller to the watch
                benchmarkset_controller.subscribe_watch(
                    benchmark_watch,
                    kcr_runtime.map_owners(settings.api_group, api.BenchmarkSet)
                )

                # Create the controller for the benchmark and subscribe it to the jobset watch
                benchmark_controller = manager.create_controller(
                    benchmark_watch,
                    reconcilers.BenchmarkReconciler(
                        settings.api_group,
                        model,
                        template_loader
                    )
                )
                benchmark_controller.subscribe_watch(
                    jobset_watch,
                    kcr_runtime.map_owners(settings.api_group, model)
                )

        logger.info("Starting manager")
        await manager.run(client)


def main():
    """
    Launches the operator using asyncio.
    """
    # Apply the logging configuration
    settings.logging.apply()
    # Run the operator using the default loop
    asyncio.run(run())
