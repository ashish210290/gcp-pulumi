# components/kubeconfig/kubeconfig.py

from typing import Optional, Union
import pulumi
from pulumi import Output, ResourceOptions

_StrOrOut = Union[str, Output[str]]

def _as_output(v: _StrOrOut) -> Output[str]:
    return v if isinstance(v, Output) else Output.from_input(v)

def kubeconfig_gke_exec(cluster_name: _StrOrOut,
                        endpoint: _StrOrOut,
                        ca_cert_b64: _StrOrOut) -> Output[str]:
    """
    Kubeconfig that uses the gke-gcloud-auth-plugin (required with modern client-go).
    """
    n = _as_output(cluster_name)
    e = _as_output(endpoint)
    c = _as_output(ca_cert_b64)

    def _render(name, ep, ca):
        return f"""apiVersion: v1
kind: Config
current-context: {name}
clusters:
- name: {name}
  cluster:
    server: https://{ep}
    certificate-authority-data: {ca}
contexts:
- name: {name}
  context:
    cluster: {name}
    user: {name}
users:
- name: {name}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1
      command: gke-gcloud-auth-plugin
      installHint: >-
        Install gke-gcloud-auth-plugin: https://cloud.google.com/blog/products/containers-kubernetes/kubectl-auth-changes-in-gke
      provideClusterInfo: true
      interactiveMode: false
"""
    return Output.all(n, e, c).apply(lambda xs: _render(*xs))
