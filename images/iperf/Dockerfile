#####
# Dockerfile for the iperf benchmark
#####

FROM ubuntu:noble

RUN apt-get update && \
    apt-get install -y iperf3 && \
    rm -rf /var/lib/apt/lists/*
