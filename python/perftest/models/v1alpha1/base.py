import datetime
import ipaddress
import typing as t

from pydantic import Field

from kube_custom_resource import CustomResource, schema

if t.TYPE_CHECKING:
    from ...template import Loader


class ImagePullPolicy(str, schema.Enum):
    """
    Enumeration of the possible pull policies.
    """
    ALWAYS = "Always"
    IF_NOT_PRESENT = "IfNotPresent"
    NEVER = "Never"


class PodCustomisation(schema.BaseModel):
    """
    Defines the available customisations for pods.
    """
    labels: schema.Dict[str, str] = Field(
        default_factory = dict,
        description = "Labels to add to pods."
    )
    annotations: schema.Dict[str, str] = Field(
        default_factory = dict,
        description = "The annotations for pods."
    )
    resources: schema.Dict[str, schema.Any] = Field(
        default_factory = dict,
        description = "Resources to apply to the pods."
    )
    node_selector: schema.Dict[str, str] = Field(
        default_factory = dict,
        description = "Node selector labels to apply to the pods."
    )
    tolerations: t.List[schema.Dict[str, schema.Any]] = Field(
        default_factory = list,
        description = "The tolerations to apply to the pods."
    )


class BenchmarkSpec(PodCustomisation):
    """
    Base class for benchmark specs.
    """
    host_network: bool = Field(
        False,
        description = "Indicates whether to use host networking or not."
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
    # Indicates that the benchmark has completed and a summary result is being generated
    SUMMARISING = "Summarising"
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
    priority_class_name: schema.Optional[schema.constr(min_length = 1)] = Field(
        None,
        description = "The name of the priority class for the benchmark."
    )
    managed_resources: t.List[ResourceRef] = Field(
        default_factory = list,
        description = "List of references to the managed resources for this benchmark."
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

    def get_template(self) -> str:
        """
        Returns the name of the template to use for this benchmark.
        """
        return f"{self._meta.singular_name}.yaml.j2"

    def get_resources(self, template_loader: 'Loader') -> t.Iterable[t.Dict[str, t.Any]]:
        """
        Returns the resources to create for this benchmark.
        """
        return template_loader.yaml_template_all(self.get_template(), benchmark = self)

    def jobset_modified(self, jobset: t.Dict[str, t.Any]):
        """
        Update the status of this benchmark when the jobset for the benchmark is modified.
        """
        # If the benchmark is already in a terminal state, there is nothing to do
        if self.status.phase.is_terminal():
            return
        # If the jobset has a terminal state, the benchmark should be in the corresponding state
        terminal_state = jobset.get("status", {}).get("terminalState")
        if terminal_state:
            self.status.phase = (
                # When the jobset completes, we trigger the summary phase
                BenchmarkPhase.SUMMARISING
                if terminal_state == "Completed"
                else BenchmarkPhase.FAILED
            )
            return
        # The only other phase transition we want to manage here is pending -> running
        # So if we are not in the pending state, just stay in our current state
        if self.status.phase != BenchmarkPhase.PENDING:
            return
        # We transition from pending to running the first time that all the replicated jobs
        # have the expected number of replicas in the ready state
        ready_replicas = {
            rjs["name"]: rjs["ready"]
            for rjs in jobset.get("status", {}).get("replicatedJobsStatus", [])
        }
        if all(
            ready_replicas.get(rj["name"], 0) >= rj["replicas"]
            for rj in jobset.get("spec", {}).get("replicatedJobs", [])
        ):
            self.status.phase = BenchmarkPhase.RUNNING

    async def pod_modified(
        self,
        pod: t.Dict[str, t.Any],
        fetch_pod_log: t.Callable[[], t.Awaitable[str]]
    ):
        """
        Update the status of this benchmark when a pod that is part of the benchmark is modified.

        Receives the pod instance and an async function that can be called to get the pod log.
        """
        raise NotImplementedError

    def summarise(self):
        """
        Update the status of this benchmark with overall results.
        """
        raise NotImplementedError
