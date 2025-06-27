import datetime
import itertools
import random
import typing as t

from pydantic import Field, TypeAdapter

from kube_custom_resource import CustomResource, schema

from ...config import settings


class BenchmarkSetTemplate(schema.BaseModel):
    """
    Defines the shape of a template for a benchmark set.
    """
    api_version: schema.constr(pattern = r"^" + settings.api_group) = Field(
        ...,
        description = "The API version of the benchmark to create."
    )
    kind: schema.constr(min_length = 1) = Field(
        ...,
        description = "The kind of the benchmark to create."
    )
    spec: schema.Dict[str, schema.Any] = Field(
        default_factory = dict,
        description = "The fixed part of the spec for the benchmark."
    )


class Generator(schema.BaseModel):
    """
    Defines the interface for a generator.
    """
    async def generate(self, ek_client, benchmark_set) -> t.List[t.Any]:
        """
        Generate the values for the generator.
        """
        raise NotImplementedError


class KubernetesResourceSelector(schema.BaseModel):
    """
    Respresents a selector for filtering Kubernetes resources.

    Currently only matchLabels is supported - matchExpressions support is planned in the future.
    """
    match_labels: schema.Dict[str, str] = Field(
        ...,
        description = "The labels to match."
    )

    def get_labels(self) -> t.Dict[str, str]:
        """
        Returns the labels to use in the easykube list command for this selector.
        """
        return dict(self.match_labels)


class KubernetesNodeGeneratorConfig(schema.BaseModel):
    """
    Configuration for a generator that produces Kubernetes node names.
    """
    selector: schema.Optional[KubernetesResourceSelector] = Field(
        None,
        description = "The selector to use to filter the nodes."
    )


class KubernetesNodeGenerator(Generator):
    """
    Generates a list of Kubernetes node names that match the given configuration.
    """
    nodes: KubernetesNodeGeneratorConfig = Field(
        ...,
        definition = "Configuration for the generation of Kubernetes nodes."
    )

    async def generate(self, ek_client, benchmark_set) -> t.List[t.Any]:
        nodes = await ek_client.api("v1").resource("nodes")
        labels = self.nodes.selector.get_labels() if self.nodes.selector else {}
        return [
            node["metadata"]["name"]
            async for node in nodes.list(labels = labels)
        ]


class ValuesFromGenerator(Generator):
    """
    Defines a generator that just produces values from a list.
    """
    values_from: t.List[schema.Any] = Field(
        ...,
        description = "List of values to produce."
    )

    async def generate(self, ek_client, benchmark_set) -> t.List[t.Any]:
        return list(self.values_from)


class CombinatorialGeneratorConfig(schema.BaseModel):
    """
    Configuration for a combinatorial generator that produces subsequences from the items
    of another generator.
    """
    length: schema.conint(gt = 0) = Field(
        ...,
        description = "The length of subsequences to generate."
    )
    with_replacement: bool = Field(
        False,
        description = (
            "Indicates whether to produce subsequences with or without replacement. "
            "By default, subsequences are produced without replacement."
        )
    )
    # In order to support recursive definitions, given that Kubernetes CRDs do not support $refs,
    # this must be defined as a broad type and validated after
    source: schema.Dict[str, schema.Any] = Field(
        ...,
        description = (
            "The source of values from which subsequences are produced. "
            "Must be a generator, but Kubernetes does not support recursive schemas."
        )
    )


class CombinationsGenerator(Generator):
    """
    Defines a generator that produces combinations from another generator.
    """
    combinations: CombinatorialGeneratorConfig = Field(
        ...,
        description = "Configuration for the generation of combinations."
    )

    async def generate(self, ek_client, benchmark_set) -> t.List[t.Any]:
        generator = TypeAdapter(ValuesGenerator).validate_python(self.combinations.source)
        values = await generator.generate(ek_client, benchmark_set)
        length = self.combinations.length
        if self.combinations.with_replacement:
            return list(itertools.combinations_with_replacement(values, length))
        else:
            return list(itertools.combinations(values, length))


class PermutationsGenerator(Generator):
    """
    Defines a generator that produces permutations from another generator.
    """
    permutations: CombinatorialGeneratorConfig = Field(
        ...,
        description = "Configuration for the generation of permutations."
    )

    async def generate(self, ek_client, benchmark_set) -> t.List[t.Any]:
        generator = TypeAdapter(ValuesGenerator).validate_python(self.permutations.source)
        values = await generator.generate(ek_client, benchmark_set)
        length = self.permutations.length
        if self.permutations.with_replacement:
            return list(itertools.product(values, repeat = length))
        else:
            return list(itertools.permutations(values, length))


class SampleGeneratorConfig(schema.BaseModel):
    """
    Configuration for a sample generator that samples a number of items randomly from
    another generator.
    """
    size: schema.conint(gt = 0) = Field(
        ...,
        description = "The size of the sample."
    )
    # In order to support recursive definitions, given that Kubernetes CRDs do not support $refs,
    # this must be defined as a broad type and validated after
    source: schema.Dict[str, schema.Any] = Field(
        ...,
        description = (
            "The source of values from which the sample is taken. "
            "Must be a generator, but Kubernetes does not support recursive schemas."
        )
    )


class SampleGenerator(Generator):
    """
    Defines a generator that samples another generator randomly.
    """
    sample: SampleGeneratorConfig = Field(
        ...,
        description = "Configuration for the sampling."
    )

    async def generate(self, ek_client, benchmark_set) -> t.List[t.Any]:
        generator = TypeAdapter(ValuesGenerator).validate_python(self.sample.source)
        values = await generator.generate(ek_client, benchmark_set)
        sample_size = min(len(values), self.sample.size)
        return random.sample(values, sample_size)


ValuesGenerator = t.Annotated[
    t.Union[
        CombinationsGenerator,
        KubernetesNodeGenerator,
        PermutationsGenerator,
        SampleGenerator,
        ValuesFromGenerator,
    ],
    schema.StructuralUnion
]


class BenchmarkSetMatrix(schema.BaseModel):
    """
    Defines the matrix of parameters to use for the benchmarks in the set.

    If no matrix is defined, a single empty parameter set is used.
    """
    product: schema.Dict[str, ValuesGenerator] = Field(
        default_factory = dict,
        description = (
            "Parameter sets are generated using the cross-product of the given keys/values."
        )
    )
    explicit: t.List[schema.Dict[str, schema.Any]] = Field(
        default_factory = list,
        description = "A list of explicit parameter sets to use."
    )


class BenchmarkSetSpec(schema.BaseModel):
    """
    Defines the parameters for a benchmark set.
    """
    template: BenchmarkSetTemplate = Field(
        ...,
        description = "The template to use for the benchmarks."
    )
    matrix: BenchmarkSetMatrix = Field(
        default_factory = BenchmarkSetMatrix,
        description = "The parameter sets to use for the benchmarks."
    )


class BenchmarkSetStatus(schema.BaseModel):
    """
    Represents the status of a benchmark set.
    """
    count: schema.Optional[schema.conint(ge = 0)] = Field(
        None,
        description = "The number of benchmarks in the set."
    )
    benchmarks_created: bool = Field(
        False,
        description = "Indicates whether the benchmarks have been created."
    )
    running: schema.Optional[schema.conint(ge = 0)] = Field(
        None,
        description = "The number of benchmarks that are currently running."
    )
    succeeded: schema.Optional[schema.conint(ge = 0)] = Field(
        None,
        description = "The number of benchmarks that have completed successfully."
    )
    failed: schema.Optional[schema.conint(ge = 0)] = Field(
        None,
        description = "The number of benchmarks that have failed."
    )
    finished_at: schema.Optional[datetime.datetime] = Field(
        None,
        description = "The time at which the benchmark finished."
    )


class BenchmarkSet(
    CustomResource,
    subresources = {"status": {}},
    printer_columns = [
        {
            "name": "Count",
            "type": "integer",
            "jsonPath": ".status.count",
        },
        {
            "name": "Running",
            "type": "integer",
            "jsonPath": ".status.running",
        },
        {
            "name": "Succeeded",
            "type": "integer",
            "jsonPath": ".status.succeeded",
        },
        {
            "name": "Failed",
            "type": "integer",
            "jsonPath": ".status.failed",
        },
        {
            "name": "Finished",
            "type": "date",
            "jsonPath": ".status.finishedAt",
        },
    ]
):
    """
    Custom resource for a parameterised set of benchmarks.
    """
    spec: BenchmarkSetSpec = Field(
        ...,
        description = "The spec for the benchmark set."
    )
    status: BenchmarkSetStatus = Field(
        default_factory = BenchmarkSetStatus,
        description = "The status of the benchmark set."
    )
