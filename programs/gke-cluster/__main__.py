import pulumi
import pulumi_gcp as gcp
from gkecluster.cluster import GKECluster, GKEClusterArgs

cfg = pulumi.Config()            # default namespace = 'gke-cluster' (project name)
gcp_cfg = pulumi.Config("gcp")

project = gcp_cfg.require("project")
region  = gcp_cfg.require("region")

# Read the cluster object from config (as recommended above)
cluster_cfg = cfg.require_object("warpstream-cluster")

# Map your YAML -> component args (normalize names → snake_case)
args: GKEClusterArgs = {
    "project_id":              cluster_cfg.get("projectId", project),
    "location":                cluster_cfg.get("region", region),
    "name":                    cluster_cfg.get("clusterName", "gke-cluster"),

    # Labels (from your product/env/id fields)
    "resource_labels": {
        "product":      str(cluster_cfg.get("productName", "warpstream")),
        "environment":  str(cluster_cfg.get("environment", "lab")),
        "cluster_id":   str(cluster_cfg.get("id", "1")),
    },

    # Network names → component will convert to self-links
    "network":                 cluster_cfg.get("NetworkName"),
    "subnetwork":              cluster_cfg.get("SubnetworkName"),

    # Private cluster
    "enable_private_nodes":    bool(cluster_cfg.get("enablePrivateNodes", False)),
    "enable_private_endpoint": bool(cluster_cfg.get("enablePrivateEndpoint", False)),
    "master_ipv4_cidr_block":  cluster_cfg.get("masterIpv4CidrBlock"),

    # IP alias
    "enable_ip_alias":         bool(cluster_cfg.get("enableIpAlias", False)),
    "cluster_ipv4_cidr_block": cluster_cfg.get("clusterIpv4CidrBlock"),
    "services_ipv4_cidr_block":cluster_cfg.get("servicesIpv4CidrBlock"),

    # Authorized networks (single block from your yaml)
    "master_authorized_networks": [{
        "cidr_block":  cluster_cfg.get("cidrBlock", "0.0.0.0/0"),
        "display_name":cluster_cfg.get("displayName", "default"),
    }],

    # Node pool (Standard)
    "enable_autopilot":        True,
    "node_count":              int(cluster_cfg.get("nodeCount", 1)),
    "min_count":               int(cluster_cfg.get("nodeCount", 1)),
    "max_count":               int(cluster_cfg.get("nodeCount", 3)),
    "machine_type":            cluster_cfg.get("machineType", "e2-standard-4"),
    "disk_size_gb":            int(cluster_cfg.get("diskSizeGb", 100)),
    "disk_type":               cluster_cfg.get("diskType", "pd-balanced"),
    "node_service_account":    cluster_cfg.get("serviceAccount"),
    "preemptible_nodes":       bool(cluster_cfg.get("preemptibleNodes", False)),

    # Misc
    "deletion_protection":     bool(cluster_cfg.get("deleteProtection", True)),
    "enable_workload_identity":True,
    "release_channel":         "REGULAR",
}

cluster = GKECluster("primary", args)

# Optional: create the bucket described in your config
bucket_name = cluster_cfg.get("gcsBucketName")
if bucket_name:
    gcp.storage.Bucket(
        "warpstream-bucket",
        name=bucket_name,
        location=cluster_cfg.get("gcsBucketRegion", region),
        storage_class=cluster_cfg.get("gcsBucketStorageClass", "STANDARD"),
        uniform_bucket_level_access=True,
        project=project,
        labels={
            "product": args["resource_labels"]["product"],
            "environment": args["resource_labels"]["environment"],
        },
        opts=pulumi.ResourceOptions(parent=cluster),
    )

# Exports
pulumi.export("cluster_name", cluster.name)
pulumi.export("location", cluster.location)
pulumi.export("kubeconfig", cluster.kubeconfig)  # secret
