import datetime
import itertools
import logging
import math
import random
import typing as t

import easykube
import easykube_runtime
from easykube_runtime.ext import kube_custom_resource as kcr_runtime

from .. import template
from ..config import settings
from ..models import v1alpha1 as api


class BenchmarkSetReconciler(kcr_runtime.CustomResourceReconciler):
    """
    Reconciler for the benchmark set.
    """
    def __init__(
        self,
        api_group: str,
        template_loader: template.Loader,
        *,
        finalizer: t.Optional[str] = None
    ):
        super().__init__(api_group, api.BenchmarkSet, finalizer = finalizer)
        self._template_loader = template_loader

    async def expand_matrix(
        self,
        client: easykube.AsyncClient,
        instance: api.BenchmarkSet
    ) -> t.Tuple[int, t.Iterable[t.Dict[str, t.Any]]]:
        """
        Returns a tuple consisting of the count, and an iterable of the parameter sets.
        """
        # If product and explicit are both empty, we produce exactly one empty parameter set
        if not instance.spec.matrix.product and not instance.spec.matrix.explicit:
            return 1, [{}]

        # Resolve the generators in the product section
        # This allows us to determine the number of parameter sets the matrix will produce
        # without actually producing them yet
        product = {
            k: (await gen.generate(client, self))
            for k, gen in instance.spec.matrix.product.items()
        }
        explicit = instance.spec.matrix.explicit
        product_count = math.prod(len(vs) for vs in product.values()) if product else 0
        count = product_count + len(explicit)

        # Define a generator over all the parameter sets
        # This avoids us keeping large sets in memory while we iterate
        def iter_parameters():
            if product:
                yield from (
                    dict(params)
                    for params in itertools.product(*(
                        [(k, v) for v in vs]
                        for k, vs in product.items()
                    ))
                )
            yield from explicit

        return count, iter_parameters()

    def resolve_templates(self, obj: t.Any, params: t.Dict[str, t.Any]) -> t.Any:
        """
        Recursively resolve any templates in the object using the specified parameters.
        """
        if isinstance(obj, dict):
            return { k: self.resolve_templates(v, params) for k, v in obj.items() }
        elif isinstance(obj, list):
            return [self.resolve_templates(v, params) for v in obj]
        elif isinstance(obj, str):
            # We have reached a leaf node that is a string - render it as a template
            return self._template_loader.yaml_string(
                obj,
                # Allow different shorthands for the set of parameters
                p = params,
                params = params,
                parameters = params
            )
        else:
            return obj

    async def create_benchmarks(
        self,
        client: easykube.AsyncClient,
        instance: api.BenchmarkSet,
        parameter_sets: t.Iterable[t.Dict[str, t.Any]]
    ):
        """
        Create the benchmark resources in the set.
        """
        # Calculate the width that we want to pad indexes to in benchmark names
        # We do this so that benchmarks are ordered by default
        padding_width = math.floor(math.log(instance.status.count, 10)) + 1

        # Produce a benchmark for each parameter sets
        for idx, params in enumerate(parameter_sets):
            resource = {
                "apiVersion": instance.spec.template.api_version,
                "kind": instance.spec.template.kind,
                "metadata": {
                    "name": f"{instance.metadata.name}-{str(idx).zfill(padding_width)}",
                    "namespace": instance.metadata.namespace,
                    # Label the benchmarks so we can find them easily
                    "labels": {
                        f"{settings.api_group}/benchmark-set": instance.metadata.name,
                    },
                    "ownerReferences": [
                        {
                            "apiVersion": instance.api_version,
                            "kind": instance.kind,
                            "name": instance.metadata.name,
                            "uid": instance.metadata.uid,
                            "blockOwnerDeletion": True,
                            "controller": True,
                        },
                    ]
                },
                "spec": {
                    **self.resolve_templates(instance.spec.template.spec, params),
                    # Create the benchmarks in a paused state
                    "paused": True,
                }
            }
            await client.apply_object(resource, force = True)

    async def reconcile_normal(
        self,
        client: easykube.AsyncClient,
        instance: api.BenchmarkSet,
        logger: logging.LoggerAdapter
    ) -> t.Tuple[api.BenchmarkSet, t.Optional[easykube_runtime.Result]]:
        """
        Reconcile the given benchmark set.
        """
        # If the benchmark set is finished, we are done
        if instance.status.finished_at is not None:
            logger.info("All benchmarks in set are terminal - skipping")
            return instance, easykube_runtime.Result()

        # Until the benchmarks are fully created they are paused so we can change them
        if not instance.status.benchmarks_created:
            logger.info("Expanding parameter matrix")
            # Produce the parameter sets and write out the count
            instance.status.count, parameter_sets = await self.expand_matrix(
                client,
                instance
            )
            instance = await self.save_instance_status(client, instance)

            # Create the benchmarks
            logger.info("Creating child benchmarks")
            await self.create_benchmarks(client, instance, parameter_sets)
            instance.status.benchmarks_created = True
            instance = await self.save_instance_status(client, instance)
            return instance, easykube_runtime.Result()

        # Reset the counts
        instance.status.running = 0
        instance.status.succeeded = 0
        instance.status.failed = 0

        # Find the benchmarks associated with the set and process them to get our counts
        logger.info("Checking child benchmark status")
        api_version = instance.spec.template.api_version
        kind = instance.spec.template.kind
        benchmarks = await client.api(api_version).resource(kind)
        labels = { f"{settings.api_group}/benchmark-set": instance.metadata.name }
        async for benchmark in benchmarks.list(labels = labels):
            # If the benchmark is paused, unpause it
            paused = benchmark["spec"].get("paused", False)
            if paused:
                logger.info(f"Un-pausing benchmark - {benchmark['metadata']['name']}")
                benchmark = await benchmarks.patch(
                    benchmark["metadata"]["name"],
                    {
                        "spec": {
                            "paused": False,
                        },
                    },
                    namespace = benchmark["metadata"]["namespace"]
                )
            
            # Update the counts based on the benchmark phase
            benchmark_phase = benchmark.get("status", {}).get("phase", "Unknown")
            if benchmark_phase == api.BenchmarkPhase.RUNNING:
                instance.status.running += 1
            elif benchmark_phase == api.BenchmarkPhase.FINALIZING:
                instance.status.running += 1
            elif benchmark_phase == api.BenchmarkPhase.COMPLETED:
                instance.status.succeeded += 1
            elif benchmark_phase == api.BenchmarkPhase.FAILED:
                instance.status.failed += 1

        # Determine whether we have finished
        terminal_count = instance.status.succeeded + instance.status.failed
        if terminal_count >= instance.status.count:
            instance.status.finished_at = datetime.datetime.now()

        instance = await self.save_instance_status(client, instance)
        return instance, easykube_runtime.Result()

    async def reconcile_delete(
        self,
        client: easykube.AsyncClient,
        instance: api.BenchmarkSet,
        logger: logging.LoggerAdapter
    ) -> t.Tuple[api.BenchmarkSet, t.Optional[easykube_runtime.Result]]:
        """
        Reconcile the deletion of the given benchmark set.
        """
        # Just ensure that all the child benchmarks are deleted
        logger.info("Deleting child benchmarks")
        api_version = instance.spec.template.api_version
        kind = instance.spec.template.kind
        benchmarks = await client.api(api_version).resource(kind)
        labels = { f"{settings.api_group}/benchmark-set": instance.metadata.name }
        await benchmarks.delete_all(labels = labels)
        return instance, easykube_runtime.Result()
