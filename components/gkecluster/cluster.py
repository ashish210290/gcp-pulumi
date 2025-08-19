import pulumi
from pulumi import Output, ResourceOptions
import pulumi_gcp as gcp
from typing import Optional, List, Dict, Any, TypedDict
from kubeconfig.kubeconfig import kubeconfig_gcp_user 

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
        cluster = gcp.container.Cluster(
            f"{name}-cluster",
            name=cluster_name,
            location=location,
            project=project_id,
            release_channel=gcp.container.ClusterReleaseChannelArgs(channel=release_channel),
            network=network,
            subnetwork=subnetwork,
            private_cluster_config=priv_cfg,
            master_authorized_networks_config=man_cfg,
            ip_allocation_policy=ip_alloc,
            workload_identity_config=wi_cfg,
            enable_autopilot=enable_autopilot,
            remove_default_node_pool=not enable_autopilot,
            initial_node_count=1,  # required by API when removing default pool
            deletion_protection=bool(args.get("deletion_protection", False)),
            resource_labels=labels,
            opts=ResourceOptions(parent=self),
        )

        # Node pool (Standard clusters)
        if not enable_autopilot:
            gcp.container.NodePool(
                f"{name}-np",
                project=project_id,
                cluster=cluster.name,
                location=location,
                autoscaling=gcp.container.NodePoolAutoscalingArgs(
                    min_node_count=int(args.get("min_count", args.get("node_count", 1))),
                    max_node_count=int(args.get("max_count", max(args.get("node_count", 1), 2))),
                ),
                node_config=gcp.container.NodePoolNodeConfigArgs(
                    machine_type=args.get("machine_type", "e2-standard-4"),
                    disk_size_gb=int(args.get("disk_size_gb", 100)),
                    disk_type=args.get("disk_type", "pd-balanced"),
                    oauth_scopes=args.get("oauth_scopes", [
                        "https://www.googleapis.com/auth/cloud-platform",
                        "https://www.googleapis.com/auth/compute",
                        "https://www.googleapis.com/auth/devstorage.read_write",
                        "https://www.googleapis.com/auth/logging.write",
                        "https://www.googleapis.com/auth/monitoring"
                    ]),
                    service_account=args.get("node_service_account"),
                    preemptible=bool(args.get("preemptible_nodes", False)),  # use 'spot' if you prefer Spot VMs
                ),
                opts=ResourceOptions(parent=cluster),
            )

        # Outputs
        ca = cluster.master_auth.apply(lambda ma: getattr(ma, "clusterCaCertificate", None) or ma["clusterCaCertificate"])
        self.name = cluster.name
        self.location = cluster.location
        self.endpoint = cluster.endpoint
        self.ca_certificate = ca
        self.kubeconfig = pulumi.secret(kubeconfig_gcp_user(self.name, self.endpoint, self.ca_certificate))

        self.register_outputs({
            "name": self.name,
            "location": self.location,
            "endpoint": self.endpoint,
            "ca_certificate": self.ca_certificate,
            "kubeconfig": self.kubeconfig,
        })
