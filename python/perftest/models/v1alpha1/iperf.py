import json
import typing as t

from pydantic import Field, constr

from kube_custom_resource import schema

from ...config import settings
from ...errors import PodLogFormatError, PodResultsIncompleteError
from ...utils import format_amount

from . import base


class IPerfSpec(base.BenchmarkSpec):
    """
    Defines the parameters for the iperf benchmark.
    """
    image: constr(min_length = 1) = Field(
        f"{settings.default_image_prefix}iperf:{settings.default_image_tag}",
        description = "The image to use for the benchmark."
    )
    duration: schema.conint(gt = 0) = Field(
        ...,
        description = "The duration of the benchmark."
    )
    streams: schema.conint(gt = 0) = Field(
        ...,
        description = "The number of streams to use."
    )
    server: base.PodCustomisation = Field(
        default_factory = base.PodCustomisation,
        description = "Customisations for the server pod."
    )
    client: base.PodCustomisation = Field(
        default_factory = base.PodCustomisation,
        description = "Customisations for the client pod."
    )


class IPerfStatus(base.BenchmarkStatus):
    """
    Represents the status of the iperf benchmark.
    """
    result: schema.Dict[str, schema.Any] = Field(
        default_factory = dict,
        description = "The complete result for the benchmark."
    )
    formatted_result: schema.Optional[schema.constr(min_length = 1)] = Field(
        None,
        description = "Result formatted with units for rendering."
    )


class IPerf(
    base.Benchmark,
    subresources = {"status": {}},
    printer_columns = [
        {
            "name": "Host Network",
            "type": "boolean",
            "jsonPath": ".spec.hostNetwork",
        },
        {
            "name": "Duration",
            "type": "integer",
            "jsonPath": ".spec.duration",
        },
        {
            "name": "Streams",
            "type": "integer",
            "jsonPath": ".spec.streams",
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
    Custom resource for running an iperf benchmark.
    """
    spec: IPerfSpec = Field(
        ...,
        description = "The parameters for the benchmark."
    )
    status: IPerfStatus = Field(
        default_factory = IPerfStatus,
        description = "The status of the benchmark."
    )

    def has_computed_result(self) -> bool:
        """
        Indicates if the benchmark has computed its result.
        """
        return bool(self.status.result)

    async def compute_result_from_logs(self, logs: t.AsyncIterable[str]) -> t.Self:
        """
        Compute the result for this benchmark from the pod logs.
        """
        # Only the client pods are labelled for log collection and there should only be one
        try:
            client_log = await anext(aiter(logs))
        except StopAsyncIteration:
            raise PodResultsIncompleteError("client logs are not available")
        # Parse the log as JSON
        try:
            self.status.result = json.loads(client_log)
        except json.JSONDecodeError:
            raise PodLogFormatError("unable to parse client log as JSON")
        # The headline result is the average receive bandwidth
        try:
            bits_per_second = self.status.result["end"]["sum_received"]["bits_per_second"]
        except KeyError:
            raise PodLogFormatError("unable to extract bandwidth from result JSON")
        # Format the result using the utility function
        amount, prefix = format_amount(bits_per_second, quotient = 1000)
        self.status.formatted_result = f"{amount} {prefix}bits/sec"
        return self
