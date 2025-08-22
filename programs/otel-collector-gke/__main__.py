import base64
import os
import yaml
import pulumi
from pulumi import Config, ResourceOptions, Output
import pulumi_gcp as gcp
from pulumi_kubernetes import Provider
from pulumi_kubernetes.core.v1 import Namespace
from pulumi_kubernetes.yaml import ConfigGroup
from pulumi_kubernetes.helm.v4 import Chart, ChartArgs, RepositoryOptsArgs

PROJECT = pulumi.get_project()
STACK = pulumi.get_stack()

# ---------- helpers ----------
def deep_merge(a: dict, b: dict) -> dict:
    """Recursively merge dict b into a (b wins)."""
    out = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_yaml_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def get_kubeconfig_from_secret(secret_ref: str, version: str = None, is_b64=False) -> Output[str]:
    # secret_ref can be full resource path or name; version defaults to latest
    res = gcp.secretmanager.get_secret_version_output(
        secret=secret_ref,
        version=version if version else None,
        # set to True if you stored kubeconfig as base64 text
        is_secret_data_base64=is_b64
    )
    # secret_data is the raw string; Pulumi fetches it server-side
    return res.secret_data
    # Note: GCP Secret Manager value limit is 64KiB; kubeconfigs fit comfortably.  :contentReference[oaicite:3]{index=3}

def make_provider(name: str, kubeconfig_input: Output[str]) -> Provider:
    # Each app/cluster gets its own provider
    return Provider(
        f"k8s-{name}",
        kubeconfig=kubeconfig_input,
        enable_server_side_apply=True,
        # optional: speed up previews when many providers are used
        suppress_helm_hook_warnings=True,
    )

def ensure_namespace(name: str, provider: Provider) -> Namespace:
    return Namespace(
        f"ns-{name}",
        metadata={"name": name},
        opts=ResourceOptions(provider=provider),
    )

def apply_extra_manifests(name: str, paths: list, provider: Provider, namespace: str):
    if not paths:
        return None
    # Inject namespace where missing
    def inject_ns(obj, opts):
        if "metadata" not in obj:
            obj["metadata"] = {}
        obj["metadata"].setdefault("namespace", namespace)

    return ConfigGroup(
        f"extras-{name}",
        files=paths,
        transformations=[inject_ns],
        opts=ResourceOptions(provider=provider),
    )

def deploy_otel_helm(app_name: str, ns: str, helm_cfg: dict, provider: Provider):
    repo = helm_cfg["repo"]
    chart = helm_cfg["chart"]
    version = helm_cfg.get("version")
    values = helm_cfg.get("values", {})

    return Chart(
        f"otel-{app_name}",
        ChartArgs(
            chart=chart,
            version=version,
            repository_opts=RepositoryOptsArgs(repo=repo),
            namespace=ns,
            values=values,
            # Waits for resources to settle; helps with CRDs in OTel chart
            skip_await=False,
            # If you include CRDs, you can set include_crds=True (default False)
        ),
        opts=ResourceOptions(provider=provider),
    )

# ---------- load defaults + apps ----------
defaults = load_yaml_file("defaults.yaml")

cfg = Config()
app_files = cfg.get_object("apps") or []
if not app_files:
    pulumi.log.warn("No apps configured under 'apps' in Pulumi.<stack>.yaml")

outputs = {}

for app_file in app_files:
    app = load_yaml_file(app_file)
    app_name = app.get("name") or os.path.splitext(os.path.basename(app_file))[0]
    sm = app.get("secretManager", {})
    ns_name = app.get("namespace", "observability")

    # 1) Kubeconfig from Secret Manager
    kubeconfig = get_kubeconfig_from_secret(
        sm.get("secret"),
        sm.get("version", None),
        is_b64=bool(sm.get("isBase64", False)),
    )

    # 2) Provider per cluster
    provider = make_provider(app_name, kubeconfig)

    # 3) Namespace
    ns = ensure_namespace(ns_name, provider)

    # 4) Merge Helm values: defaults -> app overrides
    helm_cfg = app.get("helm", {})
    merged_values = deep_merge(defaults, helm_cfg.get("values", {}))
    helm_cfg_final = dict(helm_cfg)
    helm_cfg_final["values"] = merged_values

    # 5) Deploy Helm (OTel Collector)
    chart = deploy_otel_helm(app_name, ns_name, helm_cfg_final, provider)

    # 6) Optional extra YAML manifests
    extras = apply_extra_manifests(app_name, app.get("manifests", []), provider, ns_name)

    outputs[app_name] = {
        "namespace": ns.metadata["name"],
        "helm_release": chart.release_name,  # Output (when applicable)
    }

pulumi.export("apps", outputs)

