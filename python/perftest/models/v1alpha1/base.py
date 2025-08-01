import datetime
import ipaddress
import typing as t

from pydantic import Field

from kube_custom_resource import CustomResource, schema

from ...config import settings

if t.TYPE_CHECKING:
    from ...template import Loader


class ImagePullPolicy(str, schema.Enum):
    """
    Enumeration of the possible pull policies.
    """
    ALWAYS = "Always"
    IF_NOT_PRESENT = "IfNotPresent"
    NEVER = "Never"


class MetadataCustomisation(schema.BaseModel):
    """
    Defines customisation options for metadata.
    """
    labels: schema.Dict[str, str] = Field(
        default_factory = dict,
        description = "Labels to add to the resource metadata."
    )
    annotations: schema.Dict[str, str] = Field(
        default_factory = dict,
        description = "The annotations to add to the resource metadata."
    )


class PodCustomisation(MetadataCustomisation):
    """
    Defines the available customisations for pods.
    """
    node_affinity: schema.Dict[str, schema.Any] = Field(
        default_factory = dict,
        description = "Node affinity to apply to the pods."
    )
    resources: schema.Dict[str, schema.Any] = Field(
        default_factory = dict,
        description = "Resources to apply to the pod's containers."
    )
    node_selector: schema.Dict[str, str] = Field(
        default_factory = dict,
        description = "Node selector labels to apply to the pods."
    )
    tolerations: t.List[schema.Dict[str, schema.Any]] = Field(
        default_factory = list,
        description = "The tolerations to apply to the pods."
    )


class BenchmarkSpec(schema.BaseModel):
    """
    Base class for benchmark specs.
    """
    image: schema.constr(min_length = 1) = Field(
        ...,
        description = "The image to use for the benchmark."
    )
    image_pull_policy: ImagePullPolicy = Field(
        ImagePullPolicy(settings.default_image_pull_policy.value),
        description = "The pull policy for the image."
    )
    paused: bool = Field(
        False,
        description = (
            "Indicates that the benchmark reconciliation is paused. "
            "When true, benchmark resources will not be created."
        )
    )
    host_network: bool = Field(
        False,
        description = "Indicates whether to use host networking or not."
    )
    default_metadata: MetadataCustomisation = Field(
        default_factory = MetadataCustomisation,
        description = "Default metadata to apply to all resources for the benchmark."
    )
    job_set: MetadataCustomisation = Field(
        default_factory = MetadataCustomisation,
        description = "Metadata to apply to the job set only."
    )
    pods: PodCustomisation = Field(
        default_factory = PodCustomisation,
        description = "Customisations that apply to all the pods in the benchmark."
    )


class BenchmarkPhase(str, schema.Enum):
    """
    Enumeration of possible phases for a benchmark.
    """
    # Indicates that the state of the benchmark is not known
    UNKNOWN = "Unknown"
    # Indicates that the benchmark is waiting to be scheduled
    PENDING = "Pending"
    # Indicates that all the pods requested for the benchmark are running
    RUNNING = "Running"
    # Indicates that the benchmark execution has completed but there are still tasks to perform
    FINALIZING = "Finalizing"
    # Indicates that the benchmark has completed successfully
    COMPLETED = "Completed"
    # Indicates that the benchmark reached the maximum number of retries without completing
    FAILED = "Failed"

    def is_terminal(self):
        """
        Indicates if the phase is a terminal phase.
        """
        return self in {self.COMPLETED, self.FAILED}


class ResourceRef(schema.BaseModel):
    """
    Reference to a resource that is part of a benchmark.
    """
    api_version: schema.constr(min_length = 1) = Field(
        ...,
        description = "The API version of the resource."
    )
    kind: schema.constr(min_length = 1) = Field(
        ...,
        description = "The kind of the resource."
    )
    name: schema.constr(min_length = 1) = Field(
        ...,
        description = "The name of the resource."
    )


class PodInfo(schema.BaseModel):
    """
    Model for basic information about a pod.
    """
    pod_ip: ipaddress.IPv4Address = Field(
        ...,
        description = "The IP of the pod."
    )
    node_name: schema.constr(min_length = 1) = Field(
        ...,
        description = "The name of the node that the pod was scheduled on."
    )
    node_ip: ipaddress.IPv4Address = Field(
        ...,
        description = "The IP of the node that the pod was scheduled on."
    )

    @classmethod
    def from_pod(cls, pod: t.Dict[str, t.Any]) -> 'PodInfo':
        """
        Returns a new pod info object from the given pod.
        """
        return cls(
            pod_ip = pod["status"]["podIP"],
            node_name = pod["spec"]["nodeName"],
            node_ip = pod["status"]["hostIP"]
        )


class BenchmarkStatus(schema.BaseModel):
    """
    Base class for benchmark statuses.
    """
    phase: BenchmarkPhase = Field(
        BenchmarkPhase.UNKNOWN,
        description = "The phase of the benchmark."
    )
    managed_resources: t.List[ResourceRef] = Field(
        default_factory = list,
        description = "List of references to the managed resources for this benchmark."
    )
    pods: t.Dict[str, t.List[PodInfo]] = Field(
        default_factory = dict,
        description = "The pods in the benchmark, indexed by their component."
    )
    started_at: schema.Optional[datetime.datetime] = Field(
        None,
        description = "The time at which the benchmark started."
    )
    finished_at: schema.Optional[datetime.datetime] = Field(
        None,
        description = "The time at which the benchmark finished."
    )


class Benchmark(CustomResource, abstract = True):
    """
    Base class for benchmark resources.
    """
    spec: BenchmarkSpec = Field(
        ...,
        description = "The specification of the benchmark."
    )
    status: BenchmarkStatus = Field(
        default_factory = BenchmarkStatus,
        description = "The status of the benchmark."
    )

    def has_computed_result(self) -> bool:
        """
        Indicates if the benchmark has computed its result.
        """
        raise NotImplementedError

    async def compute_result_from_logs(self, logs: t.AsyncIterable[str]) -> t.Self:
        """
        Compute the result for this benchmark from the pod logs.
        """
        raise NotImplementedError
