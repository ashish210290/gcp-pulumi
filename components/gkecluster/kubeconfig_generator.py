# components/gke-cluster/kubeconfig_generator.py
from typing import Dict
from pulumi import Output
from pulumi_gcp import container
import json


class KubeconfigGenerator:
    """
    A utility class for generating Kubernetes configuration (kubeconfig) for GKE clusters.
    """
    
    @staticmethod
    def generate_kubeconfig(cluster: container.Cluster) -> Output[str]:
        """
        Generate a kubeconfig string for the given GKE cluster.
        
        :param cluster: The GKE cluster for which to generate the kubeconfig.
        :return: An Output containing the kubeconfig as a JSON string.
        """
        dns_endpoint = cluster.control_plane_endpoints_config.apply(
            lambda cfg: cfg.dns_endpoint_config.endpoint if cfg and cfg.dns_endpoint_config else cluster.endpoint
        )
       
        return Output.all(cluster.name, dns_endpoint).apply(
            lambda args: KubeconfigGenerator._generate_kubeconfig_string(args[0], args[1])
        )
    
    @staticmethod
    def _generate_kubeconfig_string(cluster_name: str, dns_endpoint: str) -> str:
        """
        Generate a kubeconfig string for the given cluster details.
        
        :param cluster_name: The name of the GKE cluster.
        :param dns_endpoint: The DNS endpoint from control plane endpoints config.
        :return: A JSON string representing the kubeconfig.
        """
        context = f"gke_{cluster_name}"
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": context,
            "contexts": [{
                "name": context,
                "context": {
                    "cluster": cluster_name,
                    "user": "admin",
                }
            }],
            "clusters": [{
                "name": cluster_name,
                "cluster": {
                    "server": f"https://{dns_endpoint}"
                }
            }],
            "users": [{
                "name": "admin",
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1beta1",
                        "command": "gke-gcloud-auth-plugin",
                        "installHint": "Install gke-gcloud-auth-plugin for use with kubectl by following https://cloud.google.com/blog/products/containers-kubernetes/kubectl-auth-changes-in-gke",
                        "provideClusterInfo": True
                    }
                }
            }]
        }
        return json.dumps(kubeconfig, indent=2)