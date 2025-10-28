import enum
import importlib.metadata
import typing as t

from pydantic import Field, ValidationInfo, field_validator, conint, constr

from configomatic import (
    Configuration as BaseConfiguration,
    Section,
    LoggingConfiguration
)


SCHEDULING_STRATEGY_GROUP = "kube-perftest.scheduling-strategy"


class ImagePullPolicy(str, enum.Enum):
    """
    Enumeration of the possible pull policies.
    """
    ALWAYS = "Always"
    IF_NOT_PRESENT = "IfNotPresent"
    NEVER = "Never"


class SchedulingStrategyConfig(Section):
    """
    Configuration for the scheduling strategy.
    """
    #: The name of the scheduling strategy to use
    #: Defaults to the built-in serial strategy
    name: constr(min_length = 1) = Field("serial", validate_default = True)
    #: The configuration for the scheulding strategy
    config: t.Dict[str, t.Any] = Field(default_factory = dict, validate_default = True)

    @field_validator("name", mode = "after")
    @classmethod
    def validate_name(cls, value):
        """
        Validate the name against the available entry points for the scheduling strategy group.
        """
        available = [
            ep.name
            for ep in importlib.metadata.entry_points(group = SCHEDULING_STRATEGY_GROUP)
        ]
        if value in available:
            return value
        else:
            raise ValueError(f"must be one of {repr(available)}")

    @field_validator("config", mode = "after")
    @classmethod
    def validate_config(cls, value, info: ValidationInfo):
        """
        Validate the config against the model for the selected strategy.
        """
        # We can only validate the config if we have a valid name
        # If name is in the data, we know it is valid
        if "name" in info.data:
            # The config must validate against the config model, but we want to return a dict
            eps = importlib.metadata.entry_points(group = SCHEDULING_STRATEGY_GROUP)
            strategy_class = eps[info.data["name"]].load()
            config = strategy_class.config_class().model_validate(value)
            return config.model_dump()
        else:
            return value


class Configuration(
    BaseConfiguration,
    default_path = "/etc/kube-perftest/config.yaml",
    path_env_var = "KUBE_PERFTEST_CONFIG",
    env_prefix = "KUBE_PERFTEST"
):
    """
    Top-level configuration model.
    """
    #: The logging configuration
    logging: LoggingConfiguration = Field(default_factory = LoggingConfiguration)

    #: The API group of the cluster CRDs
    api_group: constr(min_length = 1) = "perftest.nscale.com"
    #: A list of categories to place CRDs into
    crd_categories: t.List[constr(min_length = 1)] = Field(
        default_factory = lambda: ["perftest"]
    )

    #: The field manager name to use for server-side apply
    easykube_field_manager: constr(min_length = 1) = "kube-perftest-operator"

    #: The default image prefix to use for benchmark images
    default_image_prefix: constr(min_length = 1) = "ghcr.io/nscaledev/kube-perftest-"
    #: The default tag to use for benchmark images
    #: The chart will set this to the tag that matches the operator image
    default_image_tag: constr(min_length = 1) = "dev"
    #: The image pull policy to use for benchmarks
    default_image_pull_policy: ImagePullPolicy = ImagePullPolicy.IF_NOT_PRESENT

    #: Label specifying the kind of the benchmark that a resource belongs to
    kind_label: constr(min_length = 1) = "perftest.nscale.com/benchmark-kind"
    #: Label specifying the namespace of the benchmark that a resource belongs to
    namespace_label: constr(min_length = 1) = "perftest.nscale.com/benchmark-namespace"
    #: Label specifying the name of the benchmark that a resource belongs to
    name_label: constr(min_length = 1) = "perftest.nscale.com/benchmark-name"
    #: Label specifying the component of the benchmark that a resource belongs to
    component_label: constr(min_length = 1) = "perftest.nscale.com/benchmark-component"
    #: Label specifying that a pod needs its logs collected
    log_collection_label: constr(min_length = 1) = "perftest.nscale.com/collect-logs"

    #: The scheduling strategy to use
    scheduling_strategy: SchedulingStrategyConfig = Field(
        default_factory = SchedulingStrategyConfig
    )

    #: The number of workers to use for the runtime manager
    runtime_manager_worker_count: conint(gt = 0) = 10


settings = Configuration()
