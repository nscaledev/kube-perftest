#####
# Dockerfile for the RDMA bandwidth and latency benchmarks
#####

FROM ubuntu:jammy

RUN apt-get update && \
    apt-get install -y perftest && \
    rm -rf /var/lib/apt/lists/*
