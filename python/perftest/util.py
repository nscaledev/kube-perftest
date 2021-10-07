import contextlib
import json

import kubernetes


@contextlib.contextmanager
def suppress_kubernetes_api_exceptions(status, reason = None):
    """
    Context manager that suppresses exceptions raised by the Kubernetes client
    with the given status and optional reason.

    Can optionally suppress only errors with a particular status and reason.
    """
    try:
        yield
    except kubernetes.client.rest.ApiException as exc:
        # If the status doesn't match, re-raise the exception
        if exc.status != status:
            raise
        # If a reason was given, check if it matches the reason from the exception
        # and re-raise it if not
        if reason:
            if reason != json.loads(exc.body).get('reason'):
                raise
        # If we get to here, the exception is suppressed


@contextlib.contextmanager
def suppress_not_found():
    """
    Context manager that suppresses the notfound raised by the Kubernetes client when
    attempting to fetch, modify or delete a resource that doesn't exist.
    """
    with suppress_kubernetes_api_exceptions(404):
        yield


@contextlib.contextmanager
def suppress_already_exists():
    """
    Context manager that suppresses the conflict raised by the Kubernetes client when
    attempting to create a resource that already exists.
    """
    with suppress_kubernetes_api_exceptions(409, "AlreadyExists"):
        yield


class DeploymentPhase:
    """
    Possible phases for a deployment.
    """
    UPDATING    = "Updating"
    READY       = "Ready"
    AVAILABLE   = "Available"
    UNAVAILABLE = "Unavailable"


def deployment_phase(deployment):
    """
    Returns the phase for a deployment.
    """
    # Requested number of replicas
    requested = deployment['spec']['replicas']
    # Number of available pods (ready for > minReadySeconds) matching selector
    available = deployment['status'].get('availableReplicas') or 0
    # Number of ready pods matching selector
    ready = deployment['status'].get('readyReplicas') or 0
    # Number of non-terminated pods that match the current spec
    updated = deployment['status'].get('updatedReplicas') or 0
    if updated < requested:
        return DeploymentPhase.UPDATING
    elif available >= 1:
        return DeploymentPhase.AVAILABLE
    elif ready >= 1:
        return DeploymentPhase.READY
    else:
        return DeploymentPhase.UNAVAILABLE


class JobPhase:
    """
    Possible phases for a job.
    """
    PENDING   = "Pending"
    RUNNING   = "Running"
    SUCCEEDED = "Succeeded"
    FAILED    = "Failed"


def job_phase(job):
    """
    Returns the phase for a job.
    """
    # The job is completed if it has a completion time
    completed = bool(job['status'].get('completionTime'))
    # The required number of completions
    completions = job['spec']['completions']
    # The number of active pods
    active = job['status'].get('active', 0)
    # The number of succeeded pods
    succeeded = job['status'].get('succeeded', 0)
    if completed:
        return JobPhase.SUCCEEDED if succeeded >= completions else JobPhase.FAILED
    else:
        return JobPhase.RUNNING if active > 0 else JobPhase.PENDING
