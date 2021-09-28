#!/usr/bin/env bash

set -e

#####
## Run the iperf performance test
#####

echo "[INFO] Installing kube-perftest helm repository"
helm repo add perftest https://stackhpc.github.io/kube-perftest >/dev/null
helm repo update >/dev/null

# Generate a name for the job
JOBNAME="perftest-iperf-$(cat /dev/urandom | tr -dc 'a-z0-9' | head -c 5)"

# Convert the given arguments into a --set statement, if required
if [ "$#" -gt 0 ]; then
    args=("$@")
    setclientargs="$(IFS=,; echo -n "--set-string client.args={${args[*]}}")"
fi

# Run the job by installing the helm chart
echo "[INFO] Launching job with args: $@"
helm install $JOBNAME perftest/iperf \
  --devel \
  $setclientargs \
  --wait \
  --wait-for-jobs \
  >/dev/null

serverlabels="app.kubernetes.io/instance=$JOBNAME,app.kubernetes.io/component=server"
clientlabels="app.kubernetes.io/instance=$JOBNAME,app.kubernetes.io/component=client"

# Print information about where the pods were scheduled
echo "[INFO] Server scheduled on nodes:"
kubectl get po -l $serverlabels --no-headers -o custom-columns=NODENAME:.spec.nodeName,NODEIP:.status.hostIP
echo "[INFO] Clients scheduled on nodes:"
kubectl get po -l $clientlabels --no-headers -o custom-columns=NODENAME:.spec.nodeName,NODEIP:.status.hostIP

# Print the logs from the server
echo "[INFO] Experiment results:"
kubectl logs -l $serverlabels

# Delete the resources for the job
echo "[INFO] Cleaning up job resources"
helm delete $JOBNAME >/dev/null

echo "[DONE]"
