import itertools as it
import re
import typing as t

from pydantic import Field

from kube_custom_resource import schema

from ...config import settings
from ...errors import PodLogFormatError, PodResultsIncompleteError

from . import base


RDMA_BANDWIDTH_REGEX = re.compile(
    r"(?P<bytes>\d+)"
    r"\s+"
    r"(?P<iterations>\d+)"
    r"\s+"
    r"(?P<bw_peak>\d+(\.\d+)?)"
    r"\s+"
    r"(?P<bw_avg>\d+(\.\d+)?)"
    r"\s+"
    r"(?P<msg_rate>\d+(\.\d+)?)"
)

RDMA_LATENCY_REGEX = re.compile(
    r"(?P<bytes>\d+)"
    r"\s+"
    r"(?P<iterations>\d+)"
    r"\s+"
    r"(?P<average>\d+(\.\d+)?)"
    r"\s+"
    r"(?P<tps_average>\d+(\.\d+)?)"
)


class RDMAMode(str, schema.Enum):
    """
    Enumeration of possible modes for the RDMA benchmarks.
    """
    READ = "read"
    WRITE = "write"


class RDMAComponent(base.PodCustomisation):
    """
    Overrides for the individual RDMA components.
    """
    device: schema.constr(min_length = 1) = Field(
        "mlx5_0",
        description = "The device to use. Defaults to mlx5_0 if not given."
    )
    cpu_affinity: schema.Optional[schema.XKubernetesIntOrString] = Field(
        None,
        description = (
            "The CPU affinity for the process. "
            "Any format accepted by taskset can be given. "
            "If not given, the local CPU list for the device is used."
        )
    )


class RDMASpec(base.BenchmarkSpec):
    """
    Defines the common parameters for RDMA benchmarks.
    """
    image: schema.constr(min_length = 1) = Field(
        f"{settings.default_image_prefix}perftest:{settings.default_image_tag}",
        description = "The image to use for the benchmark."
    )
    mode: RDMAMode = Field(
        RDMAMode.READ,
        description = "The mode for the test."
    )
    connection_manager: bool = Field(
        False,
        description = "Indicates whether to use the RDMA connection manager."
    )
    duration: schema.conint(gt = 0) = Field(
        30,
        description = "The number of seconds to run the test for (default 30)."
    )
    server: RDMAComponent = Field(
        default_factory = RDMAComponent,
        description = "Customisations for the server pod."
    )
    client: RDMAComponent = Field(
        default_factory = RDMAComponent,
        description = "Customisations for the client pod."
    )


class RDMABandwidthSpec(RDMASpec):
    """
    Defines the parameters for the RDMA bandwidth benchmark.
    """
    message_size: schema.conint(gt = 0) = Field(
        # Large messages should get us a good bandwidth number
        4194304,
        description = "The message size to use in bytes (default 4194304)."
    )
    qps: schema.conint(gt = 0) = Field(
        1,
        description = "The number of Queue Pairs (QPs) to use."
    )


class RDMABandwidthResult(schema.BaseModel):
    """
    Represents an RDMA bandwidth result.
    """
    bytes: schema.conint(gt = 0) = Field(
        ...,
        description = "The number of bytes."
    )
    iterations: schema.conint(gt = 0) = Field(
        ...,
        description = "The number of iterations."
    )
    peak_bandwidth: schema.confloat(ge = 0) = Field(
        ...,
        description = "The peak bandwidth in Gbit/sec."
    )
    average_bandwidth: schema.confloat(ge = 0) = Field(
        ...,
        description = "The average bandwidth in Gbit/sec."
    )
    message_rate: schema.confloat(ge = 0) = Field(
        ...,
        description = "The message rate in Mpps."
    )


class RDMABandwidthStatus(base.BenchmarkStatus):
    """
    Represents the status of the RDMA bandwidth benchmark.
    """
    result: schema.Optional[RDMABandwidthResult] = Field(
        None,
        description = "The result of the test."
    )
    formatted_result: schema.Optional[schema.constr(min_length = 1)] = Field(
        None,
        description = "Result formatted with units for rendering."
    )


class RDMABandwidth(
    base.Benchmark,
    subresources = {"status": {}},
    printer_columns = [
        {
            "name": "Host Network",
            "type": "boolean",
            "jsonPath": ".spec.hostNetwork",
        },
        {
            "name": "Mode",
            "type": "string",
            "jsonPath": ".spec.mode",
        },
        {
            "name": "QPs",
            "type": "integer",
            "jsonPath": ".spec.qps",
        },
        {
            "name": "Msg Size",
            "type": "string",
            "jsonPath": ".spec.messageSize",
        },
        {
            "name": "Duration",
            "type": "string",
            "jsonPath": ".spec.duration",
        },
        {
            "name": "Paused",
            "type": "boolean",
            "jsonPath": ".spec.paused",
        },
        {
            "name": "Status",
            "type": "string",
            "jsonPath": ".status.phase",
        },
        {
            "name": "Server",
            "type": "string",
            "jsonPath": ".status.pods.server[0].nodeName",
            "priority": 1,
        },
        {
            "name": "Client",
            "type": "string",
            "jsonPath": ".status.pods.client[0].nodeName",
            "priority": 1,
        },
        {
            "name": "Started",
            "type": "date",
            "jsonPath": ".status.startedAt",
        },
        {
            "name": "Finished",
            "type": "date",
            "jsonPath": ".status.finishedAt",
        },
        {
            "name": "Avg Bandwidth",
            "type": "string",
            "jsonPath": ".status.formattedResult",
        },
    ]
):
    """
    Custom resource for running an RDMA bandwidth benchmark.
    """
    spec: RDMABandwidthSpec = Field(
        ...,
        description = "The parameters for the benchmark."
    )
    status: RDMABandwidthStatus = Field(
        default_factory = RDMABandwidthStatus,
        description = "The status of the benchmark."
    )

    def has_computed_result(self) -> bool:
        """
        Indicates if the benchmark has computed its result.
        """
        return self.status.result is not None

    async def compute_result_from_logs(self, logs: t.AsyncIterable[str]) -> t.Self:
        """
        Compute the result for this benchmark from the pod logs.
        """
        # Only the client pods are labelled for log collection and there should only be one
        try:
            client_log = await anext(aiter(logs))
        except StopAsyncIteration:
            raise PodResultsIncompleteError("client logs are not available")
        # Find the first line that matches our regex and save the result
        for line in client_log.splitlines():
            match = RDMA_BANDWIDTH_REGEX.search(line.strip())
            if match is not None:
                self.status.result = RDMABandwidthResult(
                    bytes = match.group("bytes"),
                    iterations = match.group("iterations"),
                    peak_bandwidth = match.group("bw_peak"),
                    average_bandwidth = match.group("bw_avg"),
                    message_rate = match.group("msg_rate")
                )
                break
        if self.status.result is None:
            raise PodLogFormatError("unable to locate results in pod log")
        # Format the average result for display
        self.status.formatted_result = f"{self.status.result.average_bandwidth} Gbit/sec"
        return self


class RDMALatencySpec(RDMASpec):
    """
    Defines the parameters for the RDMA bandwidth benchmark.
    """
    message_size: schema.conint(gt = 0) = Field(
        # Small messages will have the best latency
        2,
        description = "The message size to use in bytes (default 2)."
    )


class RDMALatencyResult(schema.BaseModel):
    """
    Represents an RDMA latency result.
    """
    bytes: schema.conint(gt = 0) = Field(
        ...,
        description = "The number of bytes."
    )
    iterations: schema.conint(gt = 0) = Field(
        ...,
        description = "The number of iterations."
    )
    average: schema.confloat(ge = 0) = Field(
        ...,
        description = "The average latency in usecs."
    )
    tps_average: schema.confloat(ge = 0) = Field(
        ...,
        description = "The average number of transactions per second."
    )


class RDMALatencyStatus(base.BenchmarkStatus):
    """
    Represents the status of the RDMA latency benchmark.
    """
    result: schema.Optional[RDMALatencyResult] = Field(
        None,
        description = "Result of the latency test."
    )
    formatted_result: schema.Optional[schema.constr(min_length = 1)] = Field(
        None,
        description = "Result formatted with units for rendering."
    )


class RDMALatency(
    base.Benchmark,
    plural_name = "rdmalatencies",
    subresources = {"status": {}},
    printer_columns = [
        {
            "name": "Host Network",
            "type": "boolean",
            "jsonPath": ".spec.hostNetwork",
        },
        {
            "name": "Mode",
            "type": "string",
            "jsonPath": ".spec.mode",
        },
        {
            "name": "Msg Size",
            "type": "string",
            "jsonPath": ".spec.messageSize",
        },
        {
            "name": "Duration",
            "type": "string",
            "jsonPath": ".spec.duration",
        },
        {
            "name": "Paused",
            "type": "boolean",
            "jsonPath": ".spec.paused",
        },
        {
            "name": "Status",
            "type": "string",
            "jsonPath": ".status.phase",
        },
        {
            "name": "Server",
            "type": "string",
            "jsonPath": ".status.pods.server[0].nodeName",
            "priority": 1,
        },
        {
            "name": "Client",
            "type": "string",
            "jsonPath": ".status.pods.client[0].nodeName",
            "priority": 1,
        },
        {
            "name": "Started",
            "type": "date",
            "jsonPath": ".status.startedAt",
        },
        {
            "name": "Finished",
            "type": "date",
            "jsonPath": ".status.finishedAt",
        },
        {
            "name": "Avg Latency",
            "type": "string",
            "jsonPath": ".status.formattedResult",
        },
    ]
):
    """
    Custom resource for running an RDMA latency benchmark.
    """
    spec: RDMALatencySpec = Field(
        ...,
        description = "The parameters for the benchmark."
    )
    status: RDMALatencyStatus = Field(
        default_factory = RDMALatencyStatus,
        description = "The status of the benchmark."
    )

    def has_computed_result(self) -> bool:
        """
        Indicates if the benchmark has computed its result.
        """
        return self.status.result is not None

    async def compute_result_from_logs(self, logs: t.AsyncIterable[str]) -> t.Self:
        """
        Compute the result for this benchmark from the pod logs.
        """
        # Only the client pods are labelled for log collection and there should only be one
        try:
            client_log = await anext(aiter(logs))
        except StopIteration:
            raise PodResultsIncompleteError("client logs are not available")
        # Find the first line that matches our regex and save the result
        for line in client_log.splitlines():
            match = RDMA_LATENCY_REGEX.search(line.strip())
            if match is not None:
                self.status.result = RDMALatencyResult(
                    bytes = match.group("bytes"),
                    iterations = match.group("iterations"),
                    average = match.group("average"),
                    tps_average = match.group("tps_average")
                )
                break
        if self.status.result is None:
            raise PodLogFormatError("unable to locate results in pod log")
        # Format the average result for display
        self.status.formatted_result = f"{self.status.result.average} usec"
        return self
