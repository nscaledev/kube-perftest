import datetime
import logging
import typing as t

import easykube
import easykube_runtime
from easykube_runtime.ext import kube_custom_resource as kcr_runtime

from .. import errors, scheduling, template
from ..config import settings
from ..models import v1alpha1 as api


class BenchmarkReconciler(kcr_runtime.CustomResourceReconciler):
    """
    Reconciler for benchmark models.
    """
    def __init__(
        self,
        api_group: str,
        model: t.Type[api.Benchmark],
        template_loader: template.Loader,
        scheduling_strategy: scheduling.SchedulingStrategy,
        *,
        finalizer: t.Optional[str] = None
    ):
        super().__init__(api_group, model, finalizer = finalizer)
        self._template_loader = template_loader
        self._scheduling_strategy = scheduling_strategy

    async def ensure_benchmark_resources(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ) -> t.AsyncIterable[t.Dict[str, t.Any]]:
        """
        Ensures that the benchmark resources are created and return the resources.
        """
        # Template the resources for the benchmark
        template = f"{instance._meta.singular_name}.yaml.j2"
        resources = self._template_loader.yaml_template_all(template, benchmark = instance)
        # Give the scheduling strategy a chance to modify the benchmark resources
        resources = await self._scheduling_strategy.apply(
            client,
            instance,
            resources
        )
        # Ensure that the resources exist
        # Adopt the resources so that we can identify the owning benchmark easily
        for resource in resources:
            metadata = resource.setdefault("metadata", {})
            metadata.setdefault("labels", {}).update({
                settings.kind_label: instance.kind,
                settings.namespace_label: instance.metadata.namespace,
                settings.name_label: instance.metadata.name,
            })
            metadata["namespace"] = instance.metadata.namespace
            metadata.setdefault("ownerReferences", []).append(
                {
                    "apiVersion": instance.api_version,
                    "kind": instance.kind,
                    "name": instance.metadata.name,
                    "uid": instance.metadata.uid,
                    "blockOwnerDeletion": True,
                    "controller": True,
                }
            )
            yield (await client.apply_object(resource, force = True))

    async def reconcile_benchmark_status(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ) -> api.Benchmark:
        """
        Update the phase of the given benchmark by inspecting its resources.
        """
        # Get the jobset associated with the benchmark
        jobset_api = await client.api_preferred_version("jobset.x-k8s.io")
        jobsets = await jobset_api.resource("jobsets")
        jobset = await jobsets.first(
            labels = {
                settings.kind_label: instance.kind,
                settings.namespace_label: instance.metadata.namespace,
                settings.name_label: instance.metadata.name,
            },
            namespace = instance.metadata.namespace
        )

        # If there is no matching jobset, stay in the current state
        if not jobset:
            return instance

        # Update the phase based on the jobset status
        terminal_state = jobset.get("status", {}).get("terminalState")
        if terminal_state == "Completed":
            instance.status.phase = api.BenchmarkPhase.FINALIZING
        elif terminal_state:
            instance.status.phase = api.BenchmarkPhase.FAILED
            instance.status.finished_at = datetime.datetime.now()
        elif instance.status.phase == api.BenchmarkPhase.PENDING:
            # We transition from pending to running the first time that all the replicated
            # jobs have the expected number of replicas in the ready state
            ready_replicas = {
                rjs["name"]: rjs["ready"]
                for rjs in jobset.get("status", {}).get("replicatedJobsStatus", [])
            }
            if all(
                ready_replicas.get(rj["name"], 0) >= rj["replicas"]
                for rj in jobset.get("spec", {}).get("replicatedJobs", [])
            ):
                instance.status.phase = api.BenchmarkPhase.RUNNING
                instance.status.started_at = datetime.datetime.now()

        # When the benchmark is in the running phase, update the pod list
        if instance.status.phase == api.BenchmarkPhase.RUNNING:
            instance.status.pods = {}
            pods = await client.api("v1").resource("pods")
            async for pod in pods.list(
                labels = {
                    "jobset.sigs.k8s.io/jobset-name": jobset["metadata"]["name"],
                },
                namespace = jobset["metadata"]["namespace"]
            ):
                labels = pod["metadata"].get("labels", {})
                component = labels.get(
                    settings.component_label,
                    labels.get("jobset.sigs.k8s.io/replicatedjob-name", "__unknown__")
                )
                instance.status.pods.setdefault(component, []).append(api.PodInfo.from_pod(pod))

        return instance

    async def produce_benchmark_logs(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ) -> t.AsyncIterable[str]:
        """
        Returns an async iterable that yields the logs for each container in each pod flagged
        for log collection by the benchmark.
        """
        # Collect the logs from all the pods that belong to the benchmark and are flagged
        # Pass them to the benchmark to compute the result
        pods = await client.api("v1").resource("pods")
        pod_logs = await client.api("v1").resource("pods/log")
        async for pod in pods.list(
            labels = {
                settings.kind_label: instance.kind,
                settings.namespace_label: instance.metadata.namespace,
                settings.name_label: instance.metadata.name,
                settings.log_collection_label: easykube.PRESENT,
            },
            namespace = instance.metadata.namespace
        ):
            for container in pod["spec"]["containers"]:
                yield await pod_logs.fetch(
                    pod["metadata"]["name"],
                    container = container["name"],
                    namespace = pod["metadata"]["namespace"]
                )

    async def cleanup_resources(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ):
        """
        Cleans up the resources that were used to run the benchmark.
        """
        # Delete all of the managed resources
        for ref in instance.status.managed_resources:
            resource = await client.api(ref.api_version).resource(ref.kind)
            await resource.delete(ref.name, namespace = instance.metadata.namespace)

        # Ask the scheduling strategy to clean up any supporting resources
        await self._scheduling_strategy.cleanup(client, instance)

    async def reconcile_normal(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark,
        logger: logging.LoggerAdapter
    ) -> t.Tuple[api.Benchmark, t.Optional[easykube_runtime.Result]]:
        """
        Reconcile the given benchmark.
        """
        # If the benchmark is in a terminal phase, there is nothing to do
        if instance.status.phase.is_terminal():
            logger.info("Benchmark is in a terminal phase - skipping")
            return instance, easykube_runtime.Result()

        # Acknowledge that we are aware of the benchmark at the earliest possible opportunity
        if instance.status.phase == api.BenchmarkPhase.UNKNOWN:
            logger.info("Acknowledging benchmark creation")
            # Save the spec with the defaults set
            instance = await self.save_instance(client, instance, include_defaults = True)
            instance.status.phase = api.BenchmarkPhase.PENDING
            instance = await self.save_instance_status(client, instance)
            return instance, easykube_runtime.Result()

        # If the benchmark requires finalization, do that
        if instance.status.phase == api.BenchmarkPhase.FINALIZING:
            # Compute the result if required
            if not instance.has_computed_result():
                logger.info("Computing benchmark result")
                try:
                    instance = await instance.compute_result_from_logs(
                        self.produce_benchmark_logs(
                            client,
                            instance
                        )
                    )
                except (errors.PodResultsIncompleteError, errors.PodLogFormatError):
                    instance.status.phase = api.BenchmarkPhase.FAILED
                instance = await self.save_instance_status(client, instance)
                return instance, easykube_runtime.Result()

            # Clean up the managed resources and mark the benchmark as completed
            await self.cleanup_resources(client, instance)
            instance.status.managed_resources = []
            instance.status.phase = api.BenchmarkPhase.COMPLETED
            instance.status.finished_at = datetime.datetime.now()
            instance = await self.save_instance_status(client, instance)
            return instance, easykube_runtime.Result()

        # If the benchmark resources have not been created, create them
        if not instance.status.managed_resources:
            if instance.spec.paused:
                logger.info("Benchmark is paused - skipping benchmark resource creation")
            else:
                logger.info("Creating benchmark resources")
                # Store a reference to each resource so it can be deleted later
                instance.status.managed_resources = [
                    api.ResourceRef(
                        api_version = resource["apiVersion"],
                        kind = resource["kind"],
                        name = resource["metadata"]["name"]
                    )
                    async for resource in self.ensure_benchmark_resources(client, instance)
                ]
                # Save the resource references now, so we don't repeat this work later
                instance = await self.save_instance_status(client, instance)
                return instance, easykube_runtime.Result()

        # Reconcile the status of the benchmark
        logger.info("Reconciling benchmark status")
        instance = await self.reconcile_benchmark_status(client, instance)
        instance = await self.save_instance_status(client, instance)
        return instance, easykube_runtime.Result()

    async def reconcile_delete(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark,
        logger: logging.LoggerAdapter
    ) -> t.Tuple[api.Benchmark, t.Optional[easykube_runtime.Result]]:
        """
        Reconcile the deletion of the given benchmark.
        """
        await self.cleanup_resources(client, instance)
        return instance, easykube_runtime.Result()
