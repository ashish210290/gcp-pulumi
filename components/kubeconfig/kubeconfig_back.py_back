from typing import Optional, Union
import pulumi
from pulumi import Output, ResourceOptions
import pulumi_kubernetes as k8s

_StrOrOut = Union[str, Output[str]]

def _as_output(v: _StrOrOut) -> Output[str]:
    return v if isinstance(v, Output) else Output.from_input(v)

def kubeconfig_gcp_user(cluster_name: _StrOrOut,
                        endpoint: _StrOrOut,
                        ca_cert_b64: _StrOrOut) -> Output[str]:
    """
    Render a kubeconfig that uses GCP auth-provider (works great for GKE + Workload Identity).
    All inputs can be plain strings or Pulumi Outputs. Returns an Output[str].
    """
    name = _as_output(cluster_name)
    ep   = _as_output(endpoint)
    ca   = _as_output(ca_cert_b64)

    def _render(n, e, c):
        return f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {c}
    server: https://{e}
  name: {n}
contexts:
- context:
    cluster: {n}
    user: {n}
  name: {n}
current-context: {n}
kind: Config
preferences: {{}}
users:
- name: {n}
  user:
    auth-provider:
      name: gcp
"""
    return Output.all(name, ep, ca).apply(lambda xs: _render(*xs))

def kubeconfig_bearer_token(cluster_name: _StrOrOut,
                            endpoint: _StrOrOut,
                            ca_cert_b64: _StrOrOut,
                            token: _StrOrOut) -> Output[str]:
    """
    Alternative: kubeconfig with a static Bearer token.
    """
    name = _as_output(cluster_name)
    ep   = _as_output(endpoint)
    ca   = _as_output(ca_cert_b64)
    tk   = _as_output(token)

    def _render(n, e, c, t):
        return f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {c}
    server: https://{e}
  name: {n}
contexts:
- context:
    cluster: {n}
    user: {n}
  name: {n}
current-context: {n}
kind: Config
preferences: {{}}
users:
- name: {n}
  user:
    token: {t}
"""
    return Output.all(name, ep, ca, tk).apply(lambda xs: _render(*xs))

def make_k8s_provider(name: str,
                      kubeconfig: _StrOrOut,
                      parent: Optional[pulumi.Resource] = None) -> k8s.Provider:
    """
    Convenience helper to create a k8s.Provider from a kubeconfig.
    """
    return k8s.Provider(
        name,
        kubeconfig=_as_output(kubeconfig),
        opts=ResourceOptions(parent=parent),
    )
