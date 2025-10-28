from __future__ import annotations

import typing as t

from pydantic import constr

import configomatic
import easykube

if t.TYPE_CHECKING:
    from ..config import Configuration
    from ..models import v1alpha1 as api

from .base import SchedulingStrategy, SchedulingFailed


class SerialStrategy(SchedulingStrategy):
    """
    Scheduling strategy plugin that runs benchmarks serially, one after the other.

    This is implemented by using a configmap as a lock - if you are able to create the configmap
    then your benchmark can run.
    """
    class Config(configomatic.Section):
        """
        Config class for the serial scheduling plugin.
        """
        # The name of the configmap to use for the lock
        lock_configmap_name: constr(min_length = 1) = "kube-perftest-serial-lock"
        # The duration to wait before retrying a scheduling failure
        retry_after: int = 10

    def __init__(self, settings: Configuration, config: Config):
        self._settings = settings
        self._config = config

    async def _acquire_lock(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ) -> None:
        """
        Acquires the lock for the specified benchmark instance.
        """
        # Attempt to create the configmap with the benchmark details
        configmaps = await client.api("v1").resource("configmaps")
        try:
            await configmaps.create(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": self._config.lock_configmap_name,
                        "namespace": instance.metadata.namespace,
                        "labels": {
                            self._settings.kind_label: instance.kind,
                            self._settings.namespace_label: instance.metadata.namespace,
                            self._settings.name_label: instance.metadata.name,
                        },
                    },
                }
            )
        except easykube.ApiError as exc:
            if exc.status_code == 409:
                # Retry after the specified duration
                raise SchedulingFailed("Lock already acquired", self._config.retry_after)
            else:
                raise

    async def _release_lock(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ) -> None:
        """
        Releases the lock for the specified benchmark instance.
        """
        configmaps = await client.api("v1").resource("configmaps")
        # Delete the configmap by label, just in case it has been acquired by another benchmark
        await configmaps.delete_all(
            labels = {
                self._settings.kind_label: instance.kind,
                self._settings.namespace_label: instance.metadata.namespace,
                self._settings.name_label: instance.metadata.name,
            },
            namespace = instance.metadata.namespace
        )

    async def apply(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark,
        resources: t.Iterable[t.Dict[str, t.Any]]
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        await self._acquire_lock(client, instance)
        return resources

    async def cleanup(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ):
        await self._release_lock(client, instance)

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
