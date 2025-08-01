# kube-perftest  <!-- omit in toc -->

`kube-perftest` is a framework for building and running performance benchmarks for
[Kubernetes](https://kubernetes.io/) clusters.

- [Benchmarks](#benchmarks)
  - [Common options](#common-options)
  - [iperf](#iperf)
  - [RDMA Bandwidth](#rdma-bandwidth)
  - [RDMA Latency](#rdma-latency)
- [Benchmark set](#benchmark-set)

## Benchmarks

### Common options

The following options, all of which are optional, are supported for all benchmarks:

```yaml
spec:
  # The image to use for the benchmark - each benchmark has a default image
  image: registry.example.com/benchmark-image:dev
  # The pull policy to use with the image - defaults to IfNotPresent
  imagePullPolicy: Always
  # Indicates whether the benchmark is paused - defaults to false
  paused: true
  # Indicates whether the benchmark should use host networking - defaults to false
  hostNetwork: true
  # Default metadata that should be applied to all objects produced by the benchmark
  defaultMetadata:
    labels:
      example.org/label: labelvalue
    annotations:
      example.org/annotation: annotationvalue
  # Metadata that should be applied only to the jobset
  jobSet:
    labels:
      example.org/label: labelvalue
    annotations:
      example.org/annotation: annotationvalue
  # Customisations for the pods that are part of the benchmark
  pods:
    # Metadata that should be applied only to the pods
    labels:
      example.org/label: labelvalue
    annotations:
      example.org/annotation: annotationvalue
    # Node affinity for the pods
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: kubernetes.io/hostname
                operator: In
                values:
                  - host1
                  - host2
    # Resources for the pods
    resources:
      requests:
        nvidia.com/gpu: 1
    # The node selector for the pods
    nodeSelector:
      nscale.com/reserved: benchmarking
    # The tolerations for the pods
    tolerations:
      - key: nscale.com/reserved
        operator: Equal
        value: benchmarking
```

### iperf

Runs the [iperf](https://en.wikipedia.org/wiki/Iperf) network performance tool to measure bandwidth
for a transfer between two pods.

```yaml
apiVersion: perftest.nscale.com/v1alpha1
kind: IPerf
metadata:
  name: iperf
spec:
  # The duration of the test
  duration: 30
  # The number of parallel streams to use
  streams: 8
  # Customisations for the iperf server and client pods
  # Supports the same options as "pods" above
  server:
    ...
  client:
    ...
```

### RDMA Bandwidth

Runs the RDMA bandwidth benchmarks (i.e. `ib_{read,write}_bw`) from the
[perftest collection](https://github.com/linux-rdma/perftest).

> [!NOTE]
> This benchmark requires an RDMA-capable device to be specified for the pods,
> which may require the pods to be placed on an RDMA-capable Multus network, e.g.:
>
> ```yaml
> spec:
>   pods:
>     annotations:
>       # Add the Multus network annotation if required
>       k8s.v1.cni.cncf.io/networks: sriov-network
>     resources:
>       limits:
>         # Specify an RDMA shared device
>         rdma/hca_shared_device: 1
>         # Or an SR-IOV device
>         nvidia.com/mlnxnic: 1
> ```

```yaml
apiVersion: perftest.nscale.com/v1alpha1
kind: RDMABandwidth
metadata:
  name: rdma-bandwidth
spec:
  # The mode for the test - read or write - defaults to read
  mode: read
  # Indicates whether to use the RDMA connection manager - defaults to false
  connectionManager: true
  # The duration of the test - defaults to 30
  duration: 30
  # The message size for the test in bytes - defaults to 4194304 (4MB)
  messageSize: 131072
  # The number of queue pairs to use - defaults to 1
  # A higher number of queue pairs can help to spread traffic,
  # e.g. over NICs in a bond when using RoCE-LAG
  qps: 1
  # Customisations for the server pod
  server:
    # The name of the RDMA device to use - defaults to mlx5_0
    device: mlx5_1
    # The CPU affinity to use
    # Any format accepted by taskset can be given
    # https://man7.org/linux/man-pages/man1/taskset.1.html
    # If not given, the local CPU list for the device is used
    cpuAffinity: 0-15
    # All the options from pods above are also supported
    ...
  # Customisations for the client pod
  client:
    # Supports the same options as server
    ...
```

### RDMA Latency

Runs the RDMA latency benchmarks (i.e. `ib_{read,write}_lat`) from the
[perftest collection](https://github.com/linux-rdma/perftest).

> [!NOTE]
> This benchmark requires an RDMA-capable device to be specified for the pods,
> which may require the pods to be placed on an RDMA-capable Multus network, e.g.:
>
> ```yaml
> spec:
>   pods:
>     annotations:
>       # Add the Multus network annotation if required
>       k8s.v1.cni.cncf.io/networks: sriov-network
>     resources:
>       limits:
>         # Specify an RDMA shared device
>         rdma/hca_shared_device: 1
>         # Or an SR-IOV device
>         nvidia.com/mlnxnic: 1
> ```

```yaml
apiVersion: perftest.nscale.com/v1alpha1
kind: RDMALatency
metadata:
  name: rdma-latency
spec:
  # The mode for the test - read or write - defaults to read
  mode: read
  # Indicates whether to use the RDMA connection manager - defaults to false
  connectionManager: true
  # The duration of the test - defaults to 30
  duration: 30
  # The message size for the test in bytes - defaults to 2
  messageSize: 2
  # Customisations for the server pod
  server:
    # The name of the RDMA device to use - defaults to mlx5_0
    device: mlx5_1
    # The CPU affinity to use
    # Any format accepted by taskset can be given
    # https://man7.org/linux/man-pages/man1/taskset.1.html
    # If not given, the local CPU list for the device is used
    cpuAffinity: 0-15
    # All the options from pods above are also supported
    ...
  # Customisations for the client pod
  client:
    # Supports the same options as server
    ...
```

## Benchmark set

`kube-perftest` provides a `BenchmarkSet` resource that can be used to run the same benchmark
over a sweep of parameters.

`.spec.template` is used to define the benchmarks that are produced, and supports a basic
syntax for templating parameters defined in `.spec.matrix` into the benchmark definitions.

`.spec.matrix` is used to define a matrix of different parameters, values from which can then
be templated into the benchmark template.

`.spec.matrix.product` is used to define parameter sweeps, with a benchmark being created for
every possible combination of the parameters specified. Values are produced by generators, with
the simplest possible generator being `valuesFrom` that just yields fixed values from a list.

The following generators are currently available:

```yaml
# Produces values from a fixed list
valuesFrom: [1, 2, 3, 4, 5]

# Produces the names of Kubernetes nodes with an optional selector
nodes:
  selector:
    matchLabels:
      labelkey1: labelvalue1
      labelkey2: labelvalue2

# Produces all the possible combinations of the specified length that can be made from
# the values produced by a source generator
combinations:
  # The length of combinations to produce
  length: 2
  # Whether to produce combinations with or without replacement (default false)
  withReplacement: true
  # The generator producing the values that combinations should be made from
  source:
    ...

# Produces all the possible permutations of the specified length that can be made from
# the values produced by a source generator
permutations:
  # The length of permutations to produce
  length: 2
  # Whether to produce permutations with or without replacement (default false)
  withReplacement: true
  # The generator producing the values that permutations should be made from
  source:
    ...

# Samples a number of items randomly from a source generator
sample:
  # The size of the sample
  size: 5
  # The generator producing the values that should be sampled
  source:
    ...
```

> [!NOTE]
>
> The difference between combinations and permutations is subtle but important.
>
> Permutations are arrangements where order matters, i.e. `[A, B] != [B, A]`, whereas with
> combinations the order _does not_ matter, i.e. `[A, B] == [B, A]`.

`.spec.matrix.explicit` is used to define a list of specific parameter sets. Generators are not
supported here.

For example, the following shows how to run an iperf test between every pair of nodes with the
label `nscale.com/reserved: benchmarking` for a sweep of different numbers of streams:

```yaml
apiVersion: perftest.nscale.com/v1alpha1
kind: BenchmarkSet
metadata:
  name: iperf
spec:
  # The template for the fixed parts of the benchmark
  template:
    apiVersion: perftest.nscale.com/v1alpha1
    kind: IPerf
    spec:
      duration: 30
      # Use the number of streams from the parameter set
      streams: "{{ p.streams }}"
      # Schedule the pods for the test on nodes from the generated pair
      # The pods for an iperf test always have pod anti-affinity defined so that the server
      # and client will be scheduled on different nodes
      pods:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: kubernetes.io/hostname
                    operator: In
                    # The toyaml filter means this will be interpreted as a two-item list
                    values: "{{ p.nodes | toyaml }}"
  matrix:
    product:
      streams:
        # valuesFrom generator that produces values from the specified list
        valuesFrom: [1, 2, 4, 8, 16]
      nodes:
        # combinations generator that produces combinations of the specified length from
        #   another generator
        combinations:
          length: 2
          source:
            # nodes generator that produces the names of nodes with the specified labels
            nodes:
              selector:
                matchLabels:
                  nscale.com/reserved: benchmarking
```
