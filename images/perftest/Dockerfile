#####
# Dockerfile for the RDMA bandwidth and latency benchmarks
#####

FROM quay.io/rockylinux/rockylinux:9.5

RUN yum install -y perftest && \
    yum clean all -y && \
    rm -rf /var/cache
