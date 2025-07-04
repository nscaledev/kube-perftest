from __future__ import annotations

import asyncio
import copy
import typing as t

from pydantic import constr

import configomatic
import easykube

if t.TYPE_CHECKING:
    from ..config import Configuration
    from ..models import v1alpha1 as api

from .base import SchedulingStrategy


class PriorityClassStrategy(SchedulingStrategy):
    """
    Scheduling strategy plugin that uses priority classes to allow benchmarks to preempt
    each other until they can run.
    """
    class Config(configomatic.Section):
        """
        Config class for the priority class scheduling plugin.
        """
        #: The initial priority when there are no existing priority classes for benchmarks
        #: By default, we use negative priorities so that benchmarks will not preempt other pods
        initial_priority: int = -1
        #: The prefix to use for generating priority class names
        name_prefix: constr(min_length = 1) = "kube-perftest-"

    def __init__(self, settings: Configuration, config: Config):
        self._settings = settings
        self._config = config
        self._lock = asyncio.Lock()

    async def _get_or_create_priority_class(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ) -> t.Dict[str, t.Any]:
        """
        If a priority class already exists for the benchmark, return it.

        If not, create a new priority class with a lower priority than all the existing
        priority classes for benchmarks and return that.
        """
        # We use a lock when creating priority classes to avoid any race conditions that result
        # in two benchmarks getting the same priority
        # The lock has to cover both the search and the creation of any new priority classes
        async with self._lock:
            # First, we look at all the priority classes associated with benchmarks to determine
            # what priority we should use
            # If while we are doing that we find a priority class that has already been created
            # for this benchmark, then we use it
            current_priority = self._config.initial_priority + 1
            pcs = await client.api("scheduling.k8s.io/v1").resource("priorityclasses")
            async for pc in pcs.list(labels = { self._settings.kind_label: easykube.PRESENT }):
                # If the priority class has labels that match the benchmark, use it
                # If not, use the priority of the class to adjust the current priority
                labels = pc["metadata"]["labels"]
                if (
                    labels[self._settings.kind_label] == instance.kind and
                    labels[self._settings.namespace_label] == instance.metadata.namespace and
                    labels[self._settings.name_label] == instance.metadata.name
                ):
                    return pc
                else:
                    current_priority = min(current_priority, pc["value"])
            return await pcs.create(
                {
                    "apiVersion": "scheduling.k8s.io/v1",
                    "kind": "PriorityClass",
                    "metadata": {
                        "generateName": self._config.name_prefix,
                        "labels": {
                            self._settings.kind_label: instance.kind,
                            self._settings.namespace_label: instance.metadata.namespace,
                            self._settings.name_label: instance.metadata.name,
                        },
                    },
                    "value": current_priority - 1,
                    "globalDefault": False,
                    "preemptionPolicy": "PreemptLowerPriority",
                }
            )

    def _mutate_resources(
        self,
        priorityclass: t.Dict[str, t.Any],
        resources: t.Iterable[t.Dict[str, t.Any]]
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        """
        Mutate the resources to use the given priority class.
        """
        # When we reach a jobset, mutate it to include the priority class
        for resource in resources:
            if (
                resource["apiVersion"].startswith("jobset.x-k8s.io") and
                resource["kind"] == "JobSet"
            ):
                jobset = copy.deepcopy(resource)
                for rj in jobset["spec"]["replicatedJobs"]:
                    job_template = rj["template"]
                    pod_template = job_template["spec"]["template"]
                    pod_template["spec"]["priorityClassName"] = priorityclass["metadata"]["name"]
                yield jobset
            else:
                yield resource

    async def apply(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark,
        resources: t.Iterable[t.Dict[str, t.Any]]
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        priorityclass = await self._get_or_create_priority_class(client, instance)
        return self._mutate_resources(priorityclass, resources)

    async def cleanup(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ):
        # Delete the priority class corresponding to the benchmark
        pcs = await client.api("scheduling.k8s.io/v1").resource("priorityclasses")
        await pcs.delete_all(
            labels = {
                self._settings.kind_label: instance.kind,
                self._settings.namespace_label: instance.metadata.namespace,
                self._settings.name_label: instance.metadata.name,
            }
        )

    @classmethod
    def from_config(
        cls,
        settings: Configuration,
        config: configomatic.Section
    ) -> 'SchedulingStrategy':
        """
        Create an instance of this scheduling strategy using the given config.
        """
        assert isinstance(config, cls.Config)
        return cls(settings, config)
