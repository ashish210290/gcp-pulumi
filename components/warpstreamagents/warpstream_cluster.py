from typing import Optional, Dict, Any, Union
from dataclasses import dataclass
import re, yaml, os

import pulumi
from pulumi import ComponentResource, ResourceOptions, Output, Input
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s
from pulumi_kubernetes.helm.v4 import Chart, ChartArgs, RepositoryOptsArgs

InputStr = Union[str, Output[str]]

def _out(v: InputStr) -> Output[str]:
    return v if isinstance(v, Output) else Output.from_input(v)

def _subst_template(text: str, mapping: Dict[str, str]) -> str:
    """Replace ${VAR} placeholders with mapping values."""
    def repl(m): return str(mapping.get(m.group(1), m.group(0)))
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, text)

@dataclass
class WarpstreamClusterArgs:
    # GCP / cluster
    project_id: str
    region: str
    kubeconfig_secret_id: str          # SM secret holding kubeconfig
    namespace: str = "warpstream"
    stack_prefix: str = "ws"
    
    gsa_email: Optional[str]   # if set, use this GSA instead of creating one
    ksa_name: Optional[str]    # override KSA name (defaults to "<prefix>-<ns>-sa")
    grant_bucket_roles: bool   # default True; set False to skip bucket grants

    # Storage
    bucket_name: str = ""              # if empty => create "{stack_prefix}-{namespace}-bucket"
    force_destroy_bucket: bool = True

    # Workload Identity
    ksa_name: Optional[str] = None     # defaults to "{stack_prefix}-{namespace}-sa"

    # TLS (optional)
    gcp_tls_cert_secret_id: Optional[str] = None
    k8s_tls_secret_name: str = "warpstream-tls"

    # Helm/chart
    chart_version: str = "0.1.19"
    chart_repo: str = "https://warpstreamlabs.github.io/charts"
    chart_name: str = "warpstream-agent"

    # values.yaml templating (your file with ${...} placeholders)
    values_template_path: str = "values.yaml"

    # Secrets to pull (Agent Key & Virtual Cluster ID)
    agent_key_secret_id: Optional[str] = None
    virtual_cluster_id_secret_id: Optional[str] = None

    # Optional extra placeholders
    dns_record_name: Optional[str] = None
    warpstream_region: Optional[str] = None     # defaults to region

class WarpstreamCluster(ComponentResource):
    def __init__(self, name: str, args: WarpstreamClusterArgs, opts: Optional[ResourceOptions] = None):
        super().__init__("custom:warpstream:Cluster", name, None, opts)

        # --- 1) Read kubeconfig from Secret Manager (off-cluster) ---
        sm_kcfg = gcp.secretmanager.get_secret_version(
            project=args.project_id, secret=args.kubeconfig_secret_id, version="latest"
        )
        kubeconfig = Output.from_input(sm_kcfg.secret_data)

        # --- 2) K8s provider + namespace ---
        provider = k8s.Provider(f"{name}-provider", kubeconfig=kubeconfig, opts=ResourceOptions(parent=self))
        ns = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata={"name": args.namespace},
            opts=ResourceOptions(parent=self, provider=provider, ignore_changes=["metadata.labels","metadata.annotations"]),
        )

        # --- 3) Bucket (create if not provided) ---
        bucket_name = args.bucket_name or f"{args.stack_prefix}-{args.namespace}-bucket"
        bucket = gcp.storage.Bucket(
            f"{name}-bucket",
            name=bucket_name,
            location=args.region,
            uniform_bucket_level_access=True,
            force_destroy=args.force_destroy_bucket,
            labels={"app":"warpstream","ns":args.namespace,"stack":args.stack_prefix},
            opts=ResourceOptions(parent=self),
        )

        # --- 4) Workload Identity: (existing) GSA + KSA + bindings ---
        ksa_name = args.get("ksa_name") or f"{args['stack_prefix']}-{args['namespace']}-sa"
        use_existing_gsa = bool(args.get("gsa_email"))
        grant_bucket_roles = args.get("grant_bucket_roles", True)

        if use_existing_gsa:
            # Use the provided GSA (no creation)
            gsa_email_out = pulumi.Output.from_input(args["gsa_email"])
            # Most google APIs accept either email or resource name; build resource-name for safety:
            gsa_resource_id = pulumi.Output.format(
                "projects/{project}/serviceAccounts/{email}",
                project=args["project_id"], email=gsa_email_out
            )
        else:
            # Create a new GSA
            gsa = gcp.serviceaccount.Account(
                f"{name}-gsa",
                project=args["project_id"],
                account_id=f"{args['stack_prefix']}-{args['namespace']}-sa",
                display_name=f"WarpStream SA ({args['namespace']})",
                description="GSA for WarpStream Agent",
                opts=ResourceOptions(parent=self),
            )
            gsa_email_out = gsa.email
            gsa_resource_id = gsa.name

        # (Optional) grant the GSA objectAdmin on your bucket so the agent can use it
        if grant_bucket_roles:
            gcp.storage.BucketIAMMember(
                f"{name}-bucket-wi",
                bucket=bucket.name,
                role="roles/storage.objectAdmin",
                member=gsa_email_out.apply(lambda e: f"serviceAccount:{e}"),
                opts=ResourceOptions(parent=bucket),
            )

        # Bind WI: allow KSA to impersonate the GSA
        # Use IAMMember (additive) to avoid clobbering existing bindings.
        wi_member = gcp.serviceaccount.IAMMember(
            f"{name}-wi-member",
            service_account_id=gsa_resource_id,
            role="roles/iam.workloadIdentityUser",
            member=pulumi.Output.format(
                "serviceAccount:{proj}.svc.id.goog[{ns}/{ksa}]",
                proj=args["project_id"], ns=args["namespace"], ksa=ksa_name,
            ),
            # If you created the GSA above, parent to it; otherwise parent to component
            opts=ResourceOptions(parent=gsa if not use_existing_gsa else self),
        )

        # KSA with WI annotation that points to the GSA email
        ksa = k8s.core.v1.ServiceAccount(
            f"{name}-ksa",
            metadata={
                "name": ksa_name,
                "namespace": args["namespace"],
                "annotations": {
                    "iam.gke.io/gcp-service-account": gsa_email_out,  # <-- the email, not resource-id
                },
                "labels": {"app": "warpstream-agent", "stack": args["stack_prefix"]},
            },
            opts=ResourceOptions(parent=ns, provider=provider, depends_on=[wi_member]),
        )

        # # --- 4) Workload Identity: GSA + KSA + bindings ---
        # ksa_name = args.ksa_name or f"{args.stack_prefix}-{args.namespace}-sa"
        # gsa = gcp.serviceaccount.Account(
        #     f"{name}-gsa",
        #     project=args.project_id,
        #     account_id=f"{args.stack_prefix}-{args.namespace}-sa",
        #     display_name=f"WarpStream SA ({args.namespace})",
        #     description="GSA for WarpStream Agent",
        #     opts=ResourceOptions(parent=self),
        # )
        # # bucket access
        # gcp.storage.BucketIAMMember(
        #     f"{name}-bucket-wi",
        #     bucket=bucket.name,
        #     role="roles/storage.objectAdmin",
        #     member=gsa.email.apply(lambda e: f"serviceAccount:{e}"),
        #     opts=ResourceOptions(parent=bucket),
        # )
        # # WI binding
        # gcp.serviceaccount.IAMBinding(
        #     f"{name}-wi-binding",
        #     service_account_id=gsa.name,
        #     role="roles/iam.workloadIdentityUser",
        #     members=[f"serviceAccount:{args.project_id}.svc.id.goog[{args.namespace}/{ksa_name}]"],
        #     opts=ResourceOptions(parent=gsa),
        # )
        # # KSA with WI annotation
        # ksa = k8s.core.v1.ServiceAccount(
        #     f"{name}-ksa",
        #     metadata={
        #         "name": ksa_name, "namespace": args.namespace,
        #         "annotations": {"iam.gke.io/gcp-service-account": gsa.email},
        #         "labels": {"app":"warpstream-agent","stack":args.stack_prefix},
        #     },
        #     opts=ResourceOptions(parent=ns, provider=provider),
        # )

        # --- 5) (optional) TLS secret from Secret Manager (JSON: tls.crt/tls.key/ca.crt) ---
        tls_secret = None
        cert_secret_name = None
        if args.gcp_tls_cert_secret_id:
            sm_tls = gcp.secretmanager.get_secret_version(
                project=args.project_id, secret=args.gcp_tls_cert_secret_id
            )
            def _mk_tls(json_str: str):
                import json
                d = json.loads(json_str)
                return {"tls.crt": d["tls.crt"], "tls.key": d["tls.key"], "ca.crt": d.get("ca.crt","")}
            tls_data = Output.from_input(sm_tls.secret_data).apply(_mk_tls)

            cert_secret_name = f"{args.stack_prefix}-{args.k8s_tls_secret_name}"
            tls_secret = k8s.core.v1.Secret(
                f"{name}-tls",
                metadata={"name": cert_secret_name, "namespace": args.namespace},
                type="Opaque",
                data=tls_data,
                opts=ResourceOptions(parent=ns, provider=provider),
            )

        # --- 6) Pull AgentKey + VirtualClusterID from Secret Manager ---
        def _sm_value(secret_id: Optional[str]) -> Output[str]:
            if not secret_id:
                return Output.from_input("")
            ver = gcp.secretmanager.get_secret_version(project=args.project_id, secret=secret_id)
            return Output.from_input(ver.secret_data)

        agent_key_out = _sm_value(args.agent_key_secret_id)
        vcid_out      = _sm_value(args.virtual_cluster_id_secret_id)

        # --- 7) Load values.yaml template and fill ${...} placeholders ---
        if not os.path.exists(args.values_template_path):
            raise FileNotFoundError(f"values template not found: {args.values_template_path}")
        template_text = open(args.values_template_path, "r", encoding="utf-8").read()

        bucket_url_out       = bucket.name.apply(lambda n: f"gs://{n}")
        sa_name_out          = Output.from_input(ksa.metadata["name"])
        region_out           = Output.from_input(args.warpstream_region or args.region)
        dns_out              = Output.from_input(args.dns_record_name or "")
        cert_secret_name_out = Output.from_input(cert_secret_name or "")
        enable_tls_out       = Output.from_input(bool(cert_secret_name))  # true only if TLS secret exists

        # Resolve all dynamic values, substitute, then parse YAML -> dict
        values_dict_out = Output.all(
            bucket_url_out, agent_key_out, vcid_out, region_out,
            sa_name_out, dns_out, cert_secret_name_out, enable_tls_out,
        ).apply(lambda vals: yaml.safe_load(_subst_template(template_text, {
            "BUCKET_URL":             vals[0],
            "AGENT_KEY":              vals[1],
            "VIRTUAL_CLUSTER_ID":     vals[2],
            "WARPSTREAM_REGION":      vals[3],
            "SERVICE_ACCOUNT_NAME":   vals[4],
            "DNS_RECORD_NAME":        vals[5],
            "CERTIFICATE_SECRET_NAME":vals[6],
        })) or {})

        # If TLS not provided, force certificate.enableTLS: false
        def _ensure_tls(dct: Dict[str, Any], enabled: bool) -> Dict[str, Any]:
            d = dict(dct or {})
            d.setdefault("certificate", {})
            d["certificate"]["enableTLS"] = bool(enabled)
            if not enabled:
                # chart may ignore secretName if enableTLS=false, but keep consistent
                d["certificate"].pop("secretName", None)
            return d
        values_dict_out = Output.all(values_dict_out, enable_tls_out).apply(lambda xs: _ensure_tls(xs[0], xs[1]))

        # --- 8) Install Helm chart ---
        chart = Chart(
            f"{name}-chart",
            ChartArgs(
                chart=args.chart_name,
                version=args.chart_version,
                repository_opts=RepositoryOptsArgs(repo=args.chart_repo),
                namespace=args.namespace,
                values=values_dict_out,
            ),
            opts=ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=[ns, ksa] + ([tls_secret] if tls_secret else []),
            ),
        )

        # Outputs
        self.bucket_name = bucket.name
        self.gsa_email   = gsa.email
        self.ksa_name    = sa_name_out
        self.k8s_provider= provider

        self.register_outputs({
            "bucketName": self.bucket_name,
            "gsaEmail": self.gsa_email,
            "ksaName": self.ksa_name,
            "namespace": args.namespace,
        })
