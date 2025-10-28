from __future__ import annotations

import typing as t

import configomatic
import easykube

if t.TYPE_CHECKING:
    from ..config import Configuration
    from ..models import v1alpha1 as api


class SchedulingFailed(Exception):
    """
    Exception raised when a scheduling strategy fails to apply.
    """
    def __init__(self, message: str, retry_after: t.Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class SchedulingStrategy:
    """
    Base class for scheduling strategy plugins.
    """
    class Config(configomatic.Section):
        pass

    async def apply(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark,
        resources: t.Iterable[t.Dict[str, t.Any]]
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        """
        Apply the scheduling strategy by creating any supporting resources and modifying
        the incoming benchmark resources to consume those supporting resources when required.

        Returns the modified benchmark resources.
        """
        raise NotImplementedError

    async def cleanup(
        self,
        client: easykube.AsyncClient,
        instance: api.Benchmark
    ):
        """
        Clean up any supporting resources that were created in order to schedule the
        specified benchmark.
        """
        raise NotImplementedError

    @classmethod
    def config_class(cls) -> t.Type[configomatic.Section]:
        """
        Returns the config class for this scheduling strategy plugin.
        """
        return cls.Config

    @classmethod
    def from_config(
        cls,
        settings: Configuration,
        config: configomatic.Section
    ) -> 'SchedulingStrategy':
        """
        Create an instance of this scheduling strategy using the given config.
        """
        raise NotImplementedError
