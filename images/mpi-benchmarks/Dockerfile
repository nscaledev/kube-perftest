#####
# Dockerfile for the Intel MPI benchmarks
# https://www.intel.com/content/www/us/en/develop/documentation/imb-user-guide/top.html
#####

FROM quay.io/rockylinux/rockylinux:9.5

RUN yum install -y \
      openssh-clients openssh-server \
      rdma-core ucx-ib ucx-rdmacm \
      mpitests-openmpi && \
    yum clean all -y && \
    rm -rf /var/cache

# Make sure the MPI binaries are on the PATH
ENV OPENMPI_ROOT=/usr/lib64/openmpi
ENV PATH=$OPENMPI_ROOT/bin:$PATH
ENV LD_LIBRARY_PATH=$OPENMPI_ROOT/lib

# Install helper scripts
COPY ./scripts/* /usr/local/bin
