import pulumi
from warpstreamagents.warpstream_cluster import WarpstreamCluster, WarpstreamClusterArgs

ws_cfg  = pulumi.Config("warpstream")
gcp_cfg = pulumi.Config("gcp")

project = gcp_cfg.require("project")
region  = gcp_cfg.require("region")

component = WarpstreamCluster(
    "warpstream",
    WarpstreamClusterArgs(
        project_id=project,
        region=region,
        kubeconfig_secret_id=ws_cfg.require("kubeconfigSecretId"),

        namespace=ws_cfg.get("namespace") or "warpstream",
        stack_prefix=ws_cfg.get("stackPrefix") or "ws",

        bucket_name=ws_cfg.get("bucketName") or "",   # optional
        force_destroy_bucket=True,

        # TLS in Secret Manager (optional)
        gcp_tls_cert_secret_id=ws_cfg.get("gcpTlsCertSecretId"),
        k8s_tls_secret_name=ws_cfg.get("k8sTlsSecretName") or "warpstream-tls",

        # Helm
        chart_version=ws_cfg.get("chartVersion") or "0.1.19",
        values_template_path=ws_cfg.get("valuesFile") or "values.yaml",

        # Secrets for placeholders
        agent_key_secret_id=ws_cfg.require("agentKeySecretId"),
        virtual_cluster_id_secret_id=ws_cfg.require("virtualClusterIdSecretId"),

        # Optional placeholders
        dns_record_name=ws_cfg.get("dnsRecordName"),
        warpstream_region=ws_cfg.get("warpstreamRegion") or region,
    ),
)

pulumi.export("bucketName", component.bucket_name)
pulumi.export("gsaEmail", component.gsa_email)
pulumi.export("ksaName", component.ksa_name)
