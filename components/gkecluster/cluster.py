import pulumi
from pulumi import Output, ResourceOptions
import pulumi_gcp as gcp
from typing import Optional, List, Dict, Any, TypedDict
from kubeconfig.kubeconfig import kubeconfig_gke_exec 

def _self_link_network(project: str, network_name: Optional[str]) -> Optional[str]:
    if not network_name:
        return None
    if network_name.startswith("projects/"):
        return network_name
    return f"projects/{project}/global/networks/{network_name}"

def _self_link_subnet(project: str, region: str, subnet_name: Optional[str]) -> Optional[str]:
    if not subnet_name:
        return None
    if subnet_name.startswith("projects/"):
        return subnet_name
    return f"projects/{project}/regions/{region}/subnetworks/{subnet_name}"

class GKEClusterArgs(TypedDict, total=False):
    # Required
    project_id: str
    location: str
    name: str

    # Release channel / labels
    release_channel: str                    # REGULAR | RAPID | STABLE
    resource_labels: Dict[str, str]         # labels to stamp on the cluster

    # Networking
    network: Optional[str]                  # full self-link OR name
    subnetwork: Optional[str]               # full self-link OR name
    enable_private_nodes: bool
    enable_private_endpoint: bool
    master_ipv4_cidr_block: Optional[str]

    # IP alias
    enable_ip_alias: bool
    cluster_ipv4_cidr_block: Optional[str]
    services_ipv4_cidr_block: Optional[str]

    # Authorized networks
    master_authorized_networks: Optional[List[Dict[str, Any]]]  # [{cidr_block, display_name}]

    # Node pool (for Standard clusters)
    enable_autopilot: bool
    node_count: int
    min_count: int
    max_count: int
    machine_type: str
    disk_size_gb: int
    disk_type: str                              # pd-balanced | pd-ssd
    node_service_account: Optional[str]
    preemptible_nodes: bool                     # or set to True for spot/preemptible

    # Deletion protection & Workload Identity
    deletion_protection: bool
    enable_workload_identity: bool

class GKECluster(pulumi.ComponentResource):
    kubeconfig: Output[str]
    name: Output[str]
    location: Output[str]
    endpoint: Output[str]
    ca_certificate: Output[str]

    def __init__(self, name: str, args: GKEClusterArgs, opts: Optional[ResourceOptions] = None):
        super().__init__("custom:component:GKECluster", name, None, opts)

        project_id = args["project_id"]
        location = args["location"]
        cluster_name = args["name"]
        release_channel = args.get("release_channel", "REGULAR")
        enable_autopilot = bool(args.get("enable_autopilot", False))

        # Workload Identity
        wi_cfg = None
        if args.get("enable_workload_identity", True):
            wi_cfg = gcp.container.ClusterWorkloadIdentityConfigArgs(
                workload_pool=f"{project_id}.svc.id.goog"
            )

        # Private cluster
        priv_cfg = None
        if args.get("enable_private_nodes") or args.get("enable_private_endpoint") or args.get("master_ipv4_cidr_block"):
            priv_cfg = gcp.container.ClusterPrivateClusterConfigArgs(
                enable_private_nodes=bool(args.get("enable_private_nodes", False)),
                enable_private_endpoint=bool(args.get("enable_private_endpoint", False)),
                master_ipv4_cidr_block=args.get("master_ipv4_cidr_block"),
            )

        # Authorized networks
        man_cfg = None
        if args.get("master_authorized_networks"):
            blocks = [
                gcp.container.ClusterMasterAuthorizedNetworksConfigCidrBlockArgs(
                    cidr_block=e["cidr_block"],
                    display_name=e.get("display_name"),
                )
                # 'display_name' is optional, so we use get() to avoid KeyError
                for e in args.get("master_authorized_networks", [])
                if "cidr_block" in e
            ]
            man_cfg = (
                gcp.container.ClusterMasterAuthorizedNetworksConfigArgs(cidr_blocks=blocks)
                if blocks else None
            )

        # IP alias
        ip_alloc = None
        if args.get("enable_ip_alias"):
            ip_alloc_kwargs = {}

            # Use whichever style you have: explicit CIDRs OR secondary range names
            if args.get("cluster_ipv4_cidr_block"):
                ip_alloc_kwargs["cluster_ipv4_cidr_block"] = args["cluster_ipv4_cidr_block"]
            if args.get("services_ipv4_cidr_block"):
                ip_alloc_kwargs["services_ipv4_cidr_block"] = args["services_ipv4_cidr_block"]
            
            ip_alloc = gcp.container.ClusterIpAllocationPolicyArgs(**ip_alloc_kwargs)    

        # Normalize network + subnet to self-links if needed
        network = args.get("network")
        subnetwork = args.get("subnetwork")
        if network and not network.startswith("projects/"):
            network = _self_link_network(project_id, network)
        if subnetwork and not subnetwork.startswith("projects/"):
            subnetwork = _self_link_subnet(project_id, location, subnetwork)

        # Labels
        labels = args.get("resource_labels")

        # Create cluster
        cluster_kwargs = {
            "name": cluster_name,
            "location": location,
            "project": project_id,
            "release_channel": gcp.container.ClusterReleaseChannelArgs(channel=release_channel),
            "network": network,
            "subnetwork": subnetwork,
            "private_cluster_config": priv_cfg,
            "master_authorized_networks_config": man_cfg,
            "ip_allocation_policy": ip_alloc,
            "workload_identity_config": wi_cfg,
            "deletion_protection": bool(args.get("deletion_protection", False)),
            "resource_labels": labels,
        }

        # Node pool (Standard clusters)
        if enable_autopilot:
            cluster_kwargs["enable_autopilot"] = True
        else:
            # Standard node pool
            cluster_kwargs["remove_default_node_pool"] = True
            cluster_kwargs["initial_node_count"] = 1  # will be replaced by a custom node pool below
            
            node_sa = args.get("node_service_account")
            if node_sa:
                cluster_kwargs["node_config"] = gcp.container.ClusterNodeConfigArgs(
                    service_account=node_sa,
                    oauth_scopes=args.get("oauth_scopes", [
                         "https://www.googleapis.com/auth/cloud-platform",
                        "https://www.googleapis.com/auth/compute",
                        "https://www.googleapis.com/auth/devstorage.read_write", 
                        "https://www.googleapis.com/auth/logging.write",
                        "https://www.googleapis.com/auth/monitoring",
                        ]),
                )
            
        # Create the GKE cluster
        cluster = gcp.container.Cluster(
            f"{name}-cluster",
            **{k:v for k,v in cluster_kwargs.items() if v is not None},
            opts=ResourceOptions(parent=self),
        )  
        
        # --- Managed NodePool for Standard clusters ---
        if not enable_autopilot:
            # Node labels/taints so only WarpStream agents land here
            node_labels = {"warpstream": "agent"}
            node_taints = [
                gcp.container.NodePoolNodeConfigTaintArgs(
                    key="warpstream",
                    value="agent",
                    effect="NO_SCHEDULE",  # requires toleration in your Deployment
                )
            ]

            self.agent_pool = gcp.container.NodePool(
                f"{name}-agent-pool",
                project=project_id,
                location=location,                  # same region/zone style as cluster
                cluster=cluster.name,
                initial_node_count=max(1, int(args.get("node_count", 1))),   # bootstrap count
                node_config=gcp.container.NodePoolNodeConfigArgs(
                    machine_type=args.get("machine_type", "n2-standard-8"),  # big enough for 4CPU/16Gi pod
                    disk_size_gb=int(args.get("disk_size_gb", 100)),
                    disk_type=args.get("disk_type", "pd-balanced"),
                    service_account=args.get("node_service_account"),
                    oauth_scopes=args.get("oauth_scopes", [
                        "https://www.googleapis.com/auth/cloud-platform",
                        "https://www.googleapis.com/auth/compute",
                        "https://www.googleapis.com/auth/devstorage.read_write",
                        "https://www.googleapis.com/auth/logging.write",
                        "https://www.googleapis.com/auth/monitoring",
                    ]),
                    preemptible=bool(args.get("preemptible_nodes", False)),
                ),
                autoscaling=gcp.container.NodePoolAutoscalingArgs(
                    min_node_count=int(args.get("min_count", 1)),
                    max_node_count=int(args.get("max_count", 3)),
                ),
                management=gcp.container.NodePoolManagementArgs(
                    auto_repair=True,
                    auto_upgrade=True,
                ),
                upgrade_settings=gcp.container.NodePoolUpgradeSettingsArgs(
                    max_surge=1,
                    max_unavailable=0,
                ),
                opts=ResourceOptions(parent=cluster),
            )
        
        stack_prefix = pulumi.Config("stack").get("prefix") or "ws"
        
        # Build the three secret ids
        kubeconfig_secret_id = pulumi.Output.format("{prefix}-{name}-kubeconfig", prefix=stack_prefix, name=cluster.name)
        endpoint_secret_id = pulumi.Output.format("{prefix}-{name}-cluster-endpoint", prefix=stack_prefix, name=cluster.name)
        cluster_name_secret_id = pulumi.Output.format("{prefix}-{name}-cluster-name", prefix=stack_prefix, name=cluster.name)
        
        
        # Build kubeconfig straight from cluster outputs
        ca_out = cluster.master_auth.apply(lambda m: m.cluster_ca_certificate)
        kubeconfig_out = pulumi.Output.secret(
            kubeconfig_gke_exec(cluster.name, cluster.endpoint, ca_out)
        )
        
        secrets ={
            "kubeconfig": {
                "secret_id": kubeconfig_secret_id,
                "data": kubeconfig_out,
            },
            "cluster_name": {
                "secret_id": cluster_name_secret_id,
                "data": cluster.name,
            },
            "endpoint": {
                "secret_id": endpoint_secret_id,
                "data": cluster.endpoint,
            },
        }
        
        for secret_name, secret_info in secrets.items():
            # Create the secret in Secret Manager
            sec = gcp.secretmanager.Secret(
                f"{name}-{secret_name}-secret",
                secret_id=secret_info["secret_id"],
                replication=gcp.secretmanager.SecretReplicationArgs(
                    user_managed=gcp.secretmanager.SecretReplicationUserManagedArgs(
                        replicas=[
                            gcp.secretmanager.SecretReplicationUserManagedReplicaArgs(
                                location=location,
                            )
                        ]
                    )
                ),
                opts=ResourceOptions(parent=cluster),
            )
            # Create the first version with the data
            gcp.secretmanager.SecretVersion(
                f"{name}-{secret_name}-version",
                secret=sec.id,
                secret_data=secret_info["data"] ,
                opts=ResourceOptions(parent=sec),
            )
        
        # Outputs
        ca = cluster.master_auth.apply(lambda ma: getattr(ma, "cluster_ca_certificate", None))
        self.name = cluster.name
        self.location = cluster.location
        self.endpoint = cluster.endpoint
        self.ca_certificate = ca_out
        self.kubeconfig = kubeconfig_out

        self.register_outputs({
            "name": self.name,
            "location": self.location,
            "endpoint": self.endpoint,
            "ca_certificate": self.ca_certificate,
            "kubeconfig": self.kubeconfig,
            "agent_pool_name": self.agent_pool.name
        })
