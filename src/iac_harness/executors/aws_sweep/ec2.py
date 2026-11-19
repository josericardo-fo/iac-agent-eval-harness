"""Remoção de EC2/VPC (us-east-1 e, para EIP, volumes e snapshots, us-west-2)."""

from __future__ import annotations

from typing import Any

from .common import SECOND_REGION, WAIT_TIMEOUT_S, _error_code, _poll_gone
from .inventory import _nat_gateways


def deleters(session: Any) -> dict[str, Any]:
    """EC2/VPC: categoria → função que apaga um item."""
    ec2 = session.client("ec2")
    ec2_west = session.client("ec2", region_name=SECOND_REGION)

    def ec2_instance(i: str) -> None:
        ec2.terminate_instances(InstanceIds=[i])
        ec2.get_waiter("instance_terminated").wait(
            InstanceIds=[i], WaiterConfig={"Delay": 15, "MaxAttempts": WAIT_TIMEOUT_S // 15}
        )

    def nat_gateway(i: str) -> None:
        # O EIP do NAT só é liberado depois que o NAT chega a `deleted` (leva ~1 min).
        ec2.delete_nat_gateway(NatGatewayId=i)
        _poll_gone(lambda: _nat_gateways(ec2), i)

    def elastic_ip(allocation_id: str, client: Any = ec2) -> None:
        addr = client.describe_addresses(AllocationIds=[allocation_id])["Addresses"][0]
        if addr.get("AssociationId"):
            client.disassociate_address(AssociationId=addr["AssociationId"])
        client.release_address(AllocationId=allocation_id)

    def security_group(group_id: str) -> None:
        # SGs que se referenciam em ciclo (ex.: saída do app → db, entrada do db ← app) nunca saem
        # com DeleteSecurityGroup puro: revoga antes as regras que apontam para outro SG.
        group = ec2.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0]
        for key, revoke in (
            ("IpPermissions", ec2.revoke_security_group_ingress),
            ("IpPermissionsEgress", ec2.revoke_security_group_egress),
        ):
            perms = [p for p in group.get(key, []) if p.get("UserIdGroupPairs")]
            if perms:
                revoke(GroupId=group_id, IpPermissions=perms)
        ec2.delete_security_group(GroupId=group_id)

    def delete_volume(volume_id: str, client: Any = ec2) -> None:
        # Volume de instância terminada some sozinho; o resto sai já ("in-use" falha e é retentado).
        try:
            client.delete_volume(VolumeId=volume_id)
        except Exception as exc:
            if _error_code(exc) != "InvalidVolume.NotFound":
                raise

    def delete_snapshot(snapshot_id: str, client: Any = ec2) -> None:
        try:
            client.delete_snapshot(SnapshotId=snapshot_id)
        except Exception as exc:
            if _error_code(exc) != "InvalidSnapshot.NotFound":
                raise

    def network_interface(i: str) -> None:
        try:
            ec2.delete_network_interface(NetworkInterfaceId=i)
        except Exception as exc:
            # Some sozinha com o término da instância: já apagada não é falha.
            if _error_code(exc) != "InvalidNetworkInterfaceID.NotFound":
                raise

    def igw(i: str) -> None:
        g = ec2.describe_internet_gateways(InternetGatewayIds=[i])["InternetGateways"][0]
        for a in g.get("Attachments", []):
            ec2.detach_internet_gateway(InternetGatewayId=i, VpcId=a["VpcId"])
        ec2.delete_internet_gateway(InternetGatewayId=i)

    def route_table(i: str) -> None:
        t = ec2.describe_route_tables(RouteTableIds=[i])["RouteTables"][0]
        for a in t.get("Associations", []):
            if not a.get("Main"):
                ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
        ec2.delete_route_table(RouteTableId=i)

    return {
        "ec2_instances": ec2_instance,
        "ec2_nat_gateways": nat_gateway,
        "ec2_elastic_ips": elastic_ip,
        "ec2_snapshots": delete_snapshot,
        "ec2_volumes": delete_volume,
        "usw2_elastic_ips": lambda i: elastic_ip(i, ec2_west),
        "usw2_snapshots": lambda i: delete_snapshot(i, ec2_west),
        "usw2_volumes": lambda i: delete_volume(i, ec2_west),
        "ec2_network_interfaces": network_interface,
        "ec2_security_groups": security_group,
        "ec2_subnets": lambda i: ec2.delete_subnet(SubnetId=i),
        "ec2_internet_gateways": igw,
        "ec2_route_tables": route_table,
        "ec2_vpcs": lambda i: ec2.delete_vpc(VpcId=i),
    }
