# Top level Component to orchestrate all WarpStream Agent resources
import pulumi
from pulumi_gcp import storage as gcp_storage
from pulumi_gcp import secretmanager as gcp_secretmanager
from pulumi_gcp import projects as gcp_projects
from pulumi_gcp import serviceaccount as gcp_serviceaccount