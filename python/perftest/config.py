import enum
import typing as t

from pydantic import Field, conint, constr

from configomatic import Configuration as BaseConfiguration, LoggingConfiguration


class ImagePullPolicy(str, enum.Enum):
    """
    Enumeration of the possible pull policies.
    """
    ALWAYS = "Always"
    IF_NOT_PRESENT = "IfNotPresent"
    NEVER = "Never"


class Configuration(BaseConfiguration):
    """
    Top-level configuration model.
    """
    class Config:
        default_path = "/etc/kube-perftest/config.yaml"
        path_env_var = "KUBE_PERFTEST_CONFIG"
        env_prefix = "KUBE_PERFTEST"

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

    #: The number of workers to use for the runtime manager
    runtime_manager_worker_count: conint(gt = 0) = 10


settings = Configuration()
