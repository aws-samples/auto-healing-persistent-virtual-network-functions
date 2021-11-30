#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: MIT-0
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy of this
#  software and associated documentation files (the "Software"), to deal in the Software
#  without restriction, including without limitation the rights to use, copy, modify,
#  merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
#  permit persons to whom the Software is furnished to do so.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
#  INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
#  PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
#  HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
#  OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
#  SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import boto3
import json
import logging
import time
import botocore
import os
import sys
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)
ec2_client = boto3.client('ec2')
asg_client = boto3.client('autoscaling')
ec2 = boto3.resource('ec2')

def lambda_handler(event, context):
    instance_id = event['detail']['EC2InstanceId']
    LifecycleHookName = event['detail']['LifecycleHookName']
    AutoScalingGroupName = event['detail']['AutoScalingGroupName']
    secgroup_id = os.environ['SecGroupId']
    vpc_id = os.environ['VPCId']
    route_table_id = os.environ['WANRouteTable']
    cidr = str(os.environ['VIPCIDRBlock'])
    vip = str(os.environ['VIPAddress']).split('/')[0]
    eipaddress = str(os.environ['EIPAddress']).split('/')[0]
    eipallocation = str(os.environ['EIPAllocationId']).split('/')[0]
    LambdaInfoTracing = str(os.environ['LambdaInfoTracing'])
    InstanceRequiresReboot = str(os.environ['InstanceRequiresReboot'])
    SubnetCreationAttempts = int(os.environ['SubnetCreationAttempts'])

    # printing event received:
    infolog("lambda_handler -- Event keys: {}".format(list(event['detail'].keys())),LambdaInfoTracing)
    infolog("lambda_handler -- Complete Event: {}".format(str(event['detail'])),LambdaInfoTracing)

    # Find out AZ from the instance
    AZ_list = [] 
    
    try:
        event_instanceid = event['detail']['EC2InstanceId']
        infolog("lambda_handler -- EC2 instance triggering event: {}".format(event_instanceid),LambdaInfoTracing)
    except botocore.exceptions.ClientError as e:
        errorlog("Error extracting EC2 instance from event details: {}".format(e.response['Error']))

    try:
        instance_list = ec2_client.describe_instances()
        infolog("lambda_handler -- EC2 instances description response: {}".format(instance_list),LambdaInfoTracing)
        for reservation in instance_list["Reservations"]:
            for instance in reservation.get("Instances", []):
                if instance['InstanceId'] == event_instanceid:
                    AZ_list.append(instance["Placement"]["AvailabilityZone"])
        infolog("lambda_handler -- AZs out of EC2 instances description response: {}".format(AZ_list),LambdaInfoTracing)
    except botocore.exceptions.ClientError as e:
            errorlog("Error extracting AZ from EC2 instances description response: {}".format(e.response['Error']))
    
    if AZ_list:
        AZ = AZ_list[0]
    else:
        errorlog("No AZs could be extracted")
        return

    # Tracing EC2 details
    infolog("lambda_handler -- Event: {}".format(event["detail-type"]),LambdaInfoTracing)
    infolog("lambda_handler -- Instance Id: {}".format(instance_id),LambdaInfoTracing)
    infolog("lambda_handler -- LifecycleHookName: {}".format(LifecycleHookName),LambdaInfoTracing)
    infolog("lambda_handler -- AutoScalingGroupName: {}".format(AutoScalingGroupName),LambdaInfoTracing)
    infolog("lambda_handler -- secgroup_id: {}".format(secgroup_id),LambdaInfoTracing)
    infolog("lambda_handler -- vpc_id: {}".format(vpc_id),LambdaInfoTracing)
    infolog("lambda_handler -- route_table_id: {}".format(route_table_id),LambdaInfoTracing)
    infolog("lambda_handler -- cidr: {}".format(cidr),LambdaInfoTracing)
    infolog("lambda_handler -- vip: {}".format(vip),LambdaInfoTracing)
    infolog("lambda_handler -- eipaddress: {}".format(eipaddress),LambdaInfoTracing)
    infolog("lambda_handler -- eipallocation: {}".format(eipallocation),LambdaInfoTracing)
    infolog("lambda_handler -- AZ: {}".format(AZ),LambdaInfoTracing)
    infolog("lambda_handler -- InstanceRequiresReboot: {}".format(InstanceRequiresReboot),LambdaInfoTracing)
    infolog("lambda_handler -- SubnetCreationAttempts: {}".format(SubnetCreationAttempts),LambdaInfoTracing)

    if event["detail-type"] == "EC2 Instance-launch Lifecycle Action":
        
        subnet_id = None
        attempts = 0
        # Attempts to create secondary subnet in same AZ and associate it to Route Table
        while (not subnet_id) and attempts < SubnetCreationAttempts : 
            infolog("lambda_handler -- Attempt nr. {} to create and associate subnet".format(attempts),LambdaInfoTracing)
            subnet_id = create_and_associate_subnet(vpc_id,cidr,AZ,route_table_id,LambdaInfoTracing)
            attempts += 1
            time.sleep(10)

        if not subnet_id:
            # No subnet could be created after SubnetCreationAttempts attempts, abandon lifecycle hook
            # Lifecycle Hook event failed
            complete_lifecycle_action_failure(LifecycleHookName,AutoScalingGroupName,instance_id,LambdaInfoTracing)
            return

        # Create ENI within secondary subnet in same AZ
        interface_id = create_interface(subnet_id,secgroup_id,vip,eipaddress,eipallocation,LambdaInfoTracing)

        if not interface_id:
            # No ENI could be created
            # Lifecycle Hook event failed
            complete_lifecycle_action_failure(LifecycleHookName,AutoScalingGroupName,instance_id,LambdaInfoTracing)
            disassociate_delete_subnet(subnet_id,route_table_id,LambdaInfoTracing)
            return

        # Index is 1 because it is secondary interface to the instance
        attachment = attach_interface(interface_id,instance_id,1,LambdaInfoTracing)

        if not attachment:
            # ENI could not be attached
            # Lifecycle Hook event failed
            complete_lifecycle_action_failure(LifecycleHookName,AutoScalingGroupName,instance_id,LambdaInfoTracing)
            delete_interface(interface_id,eipaddress,eipallocation,LambdaInfoTracing)
            return
        
        if str(InstanceRequiresReboot) == "true": 
            # ENI attachment requires instance reboot
            time.sleep(30)
            restart_instance(instance_id,LambdaInfoTracing)
            time.sleep(120)

        # Lifecycle Hook event successfully completed otherwise
        complete_lifecycle_action_success(LifecycleHookName,AutoScalingGroupName,instance_id,LambdaInfoTracing)
        return

    if event["detail-type"] == "EC2 Instance-terminate Lifecycle Action":
        
        # Obtained Subnet ID from same VPC and CIDR range
        subnet_id = get_subnet(vpc_id,cidr,LambdaInfoTracing)

        # Obtained Interface ID from same subnet
        interface_id = get_interface(subnet_id,vip,LambdaInfoTracing)
        
        # Interface ID could be extracted from Subnet ID
        if interface_id is not None:
            try:
                # Detach the ENI from the instance
                detach_interface(interface_id,LambdaInfoTracing)
            except botocore.exceptions.ClientError as e:
                errorlog("Error detaching interface: {}".format(e.response['Error']))
            
            try:
                # After detaching, delete the interface
                delete_interface(interface_id,eipaddress,eipallocation,LambdaInfoTracing)
            except botocore.exceptions.ClientError as e:
                errorlog("Error deleting interface: {}".format(e.response['Error']))

        if subnet_id is not None:
            try:
                # After having detached and deleted the ENI, subnet can be deleted
                disassociate_delete_subnet(subnet_id,route_table_id,LambdaInfoTracing)
            except botocore.exceptions.ClientError as e:
                errorlog("Error deleting subnet: {}".format(e.response['Error']))

        # After detaching ENI, deleting it and deleting the subnet, this is a successful lifecycle hook
        complete_lifecycle_action_success(LifecycleHookName,AutoScalingGroupName,instance_id,LambdaInfoTracing)
        return

def create_and_associate_subnet(vpc_id,cidr,az,route_table_id,LambdaInfoTracing):
    """
    create subnet id from VPC in a specific AZ with a private IPv4 CIDR range
  
    :param vpc_id: VPC id
    :param cidr: CIDR IPv4 range for subnet
    :param az: Availability Zone
    :param route_table_id: Route Table id
      
    """
    subnet_id = None
    if vpc_id and cidr:
        try:
            infolog("create_and_associate_subnet -- VPC ID parameter: {}".format(vpc_id),LambdaInfoTracing)
            infolog("create_and_associate_subnet -- CIDR parameter: {}".format(cidr),LambdaInfoTracing)
            infolog("create_and_associate_subnet -- AZ parameter: {}".format(az),LambdaInfoTracing)
            subnet = ec2_client.create_subnet(TagSpecifications=[{'ResourceType': 'subnet', 'Tags': [ {'Key': 'Name', 'Value': 'VIP Subnet'}]}],AvailabilityZone=az,CidrBlock=cidr,VpcId= vpc_id)
            infolog("create_and_associate_subnet -- EC2 create subnet response: {}".format(subnet),LambdaInfoTracing)
            subnet_id = subnet['Subnet']['SubnetId']
            infolog("create_and_associate_subnet -- EC2 created subnet ID: {}".format(subnet_id),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error creating subnet: {}".format(e.response['Error']))
            if e.response['Error']['Code'] == 'InvalidSubnet.Conflict':
                errorlog("create_and_associate_subnet -- Previous subnet {} has not been deleted yet".format(cidr))
    
    if subnet_id:
        try:
            infolog("create_and_associate_subnet -- Route Table parameter: {}".format(route_table_id),LambdaInfoTracing)
            infolog("create_and_associate_subnet -- created Subnet ID: {}".format(subnet_id),LambdaInfoTracing)
            response = ec2_client.associate_route_table(RouteTableId=route_table_id,SubnetId=subnet_id)
            infolog("create_and_associate_subnet -- found Route Table: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error associating subnet: {}".format(e.response['Error']))
        
    return subnet_id

def create_interface(subnet_id,sg_id,vip,eipaddress,eipallocation,LambdaInfoTracing):
    """
    create interface id with subnet, Security Group and a specific private IPv4 address
  
    :param subnet_id: subnet id within VPC
    :param sg_id: Security Group ID
    :param vip: (virtual) private IPv4 address that is mapped to interface
      
    """

    network_interface_id = None
    if subnet_id:
        try:
            infolog("create_interface -- subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
            infolog("create_interface -- Security Group ID parameter: {}".format(sg_id),LambdaInfoTracing)
            infolog("create_interface -- Virtual IP address parameter:: {}".format(vip),LambdaInfoTracing)
            network_interface = ec2_client.create_network_interface(Description='VIP ENI',Groups=[sg_id],SubnetId=subnet_id,PrivateIpAddress=vip)
            infolog("create_interface -- EC2 create ENI response: {}".format(network_interface),LambdaInfoTracing)
            network_interface_id = network_interface['NetworkInterface']['NetworkInterfaceId']
            infolog("create_interface -- EC2 created ENI ID: {}".format(network_interface_id),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error creating network interface: {}".format(e.response['Error']))

    # Associate existing EIP allocation
    if eipallocation:
        try:
            infolog("create_interface -- eipaddress parameter: {}".format(eipaddress),LambdaInfoTracing)
            infolog("create_interface -- eipallocation parameter: {}".format(eipallocation),LambdaInfoTracing)
            response = ec2_client.associate_address(AllocationId=eipallocation,NetworkInterfaceId=network_interface_id)
            infolog("create_interface -- EC2 associate EIP response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error associating EIP to network interface: {}".format(e.response['Error']))

    return network_interface_id


def get_subnet(vpc_id,cidr,LambdaInfoTracing):
    """
    obtain subnet id from VPC based on IPv4 CIDR range
  
    :param vpc_id: VPC id
    :param cidr: CIDR IPv4 range from subnet
      
    """

    subnet_id = None
    if vpc_id and cidr:
        try:
            infolog("get_subnet -- VPC ID parameter: {}".format(vpc_id),LambdaInfoTracing)
            infolog("get_subnet -- CIDR parameter: {}".format(cidr),LambdaInfoTracing)
            response = ec2_client.describe_subnets(
                Filters=[
                    {
                        'Name': 'cidr-block',
                        'Values': [cidr]
                    }
                ]
            )
            infolog("get_subnet -- EC2 describe subnet reponse: {}".format(response),LambdaInfoTracing)
            if response['Subnets']:
                subnet_id = response['Subnets'][0]['SubnetId']
                infolog("get_subnet -- EC2 obtained subnet ID: {}".format(subnet_id),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining subnet: {}".format(e.response['Error']))
    return subnet_id


def get_interface(subnet_id,vip,LambdaInfoTracing):
    """
    obtain interface id from subnet based on specific IPv4 address
  
    :param subnet_id: subnet id within VPC
    :param vip: (virtual) IPv4 address that is mapped to interface
      
    """

    interface_id = None
    if subnet_id:
        try:
            infolog("get_interface -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
            infolog("get_interface -- Virtual IP address parameter: {}".format(vip),LambdaInfoTracing)
            response = ec2_client.describe_network_interfaces(
                Filters=[{"Name": "private-ip-address", "Values": [vip]}]
            )
            infolog("get_interface -- EC2 describe ENI response: {}".format(response),LambdaInfoTracing)
            if response['NetworkInterfaces']:
                interface_id = response['NetworkInterfaces'][0]['NetworkInterfaceId']
                infolog("get_interface -- EC2 obtained interface ID: {}".format(interface_id),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining interface: {}".format(e.response['Error']))
    return interface_id


def detach_interface(network_interface_id,LambdaInfoTracing):
    """
    detach  interface if it is attached to instance
  
    :param network_interface_id: network interface id that 
                               we previously obtain
      
    """

    attachment = None
    response = None
    if network_interface_id:
        try:
            infolog("detach_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
            response = ec2_client.describe_network_interfaces(
                Filters=[{"Name": "network-interface-id", "Values": [network_interface_id]}]
            )
            infolog("detach_interface -- EC2 describe ENI response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining interface description: {}".format(e.response['Error']))
    
    if response:
        try:
            if "Attachment" in response['NetworkInterfaces'][0]:
                attachment = response['NetworkInterfaces'][0]['Attachment']['AttachmentId']
                infolog("detach_interface -- EC2 obtained attachmend id: {}".format(attachment),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining attachment: {}".format(e.response['Error']))
    
    if attachment:
        try:
            response = ec2_client.detach_network_interface(AttachmentId=attachment,Force=True)
            infolog("detach_interface -- EC2 obtained response from interface detachment: {}".format(response),LambdaInfoTracing)
            # Wait time to accomplish detachment
            time.sleep(60)
        except botocore.exceptions.ClientError as e:
            errorlog("Error trying detachment: {}".format(e.response['Error']))
    
    return attachment

def attach_interface(network_interface_id,instance_id,index,LambdaInfoTracing):
    """
    attach  interface to instance
  
    :param network_interface_id: network interface id that 
                               we previously obtain
    :param instance_id: instance ID to attach interface to
    :param index: index for interface attachment (starting from '0')
      
    """

    attachment = None
    if network_interface_id and instance_id:
        try:
            infolog("attach_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
            infolog("attach_interface -- Instance ID parameter: {}".format(instance_id),LambdaInfoTracing)
            attach_interface = ec2_client.attach_network_interface(
                NetworkInterfaceId=network_interface_id,
                InstanceId=instance_id,
                DeviceIndex=index
            )
            infolog("attach_interface -- EC2 attach ENI response: {}".format(attach_interface),LambdaInfoTracing)
            attachment = attach_interface['AttachmentId']
            infolog("attach_interface -- created network attachment ID: {}".format(attachment),LambdaInfoTracing)

            network_interface = ec2.NetworkInterface(network_interface_id)

            #modify_attribute doesn't allow multiple parameter change at once..
            network_interface.modify_attribute(
                SourceDestCheck={
                    'Value': False
                }
            )
            network_interface.modify_attribute(
                Attachment={
                    'AttachmentId': attachment,
                    'DeleteOnTermination': True
                },
            )
            infolog("attach_interface -- created network interface: {}".format(network_interface),LambdaInfoTracing)

        except botocore.exceptions.ClientError as e:
            errorlog("Error attaching network interface: {}".format(e.response['Error']))

    return attachment


def delete_interface(network_interface_id,eipaddress,eipallocation,LambdaInfoTracing):
    """
    delete interface
  
    :param network_interface_id: network interface id to be deleted
      
    """
    association = None

    if network_interface_id:
        try:
            infolog("delete_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
            response = ec2_client.describe_network_interfaces(
                Filters=[{"Name": "network-interface-id", "Values": [network_interface_id]}]
            )
            infolog("delete_interface -- EC2 describe ENI response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining interface description: {}".format(e.response['Error']))
    
    # Disassociate existing EIP allocation
    if ( "NetworkInterfaces" in response ) and eipallocation:
        try:
            if response['NetworkInterfaces'][0]:
                if "Association" in response['NetworkInterfaces'][0]:
                    association = response['NetworkInterfaces'][0]['Association']['AssociationId']
                    infolog("delete_interface -- EC2 obtained association id: {}".format(association),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining association: {}".format(e.response['Error']))

    if association:
        try:
            infolog("delete_interface -- eipaddress parameter: {}".format(eipaddress),LambdaInfoTracing)
            infolog("delete_interface -- eipallocation parameter: {}".format(eipallocation),LambdaInfoTracing)
            response = ec2_client.disassociate_address(AssociationId=association)
            infolog("delete_interface -- EC2 disassociate EIP response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error disassociating EIP from network interface: {}".format(e.response['Error']))
    
    # Then delete the interface
    try:
        infolog("delete_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
        ec2_client.delete_network_interface(
            NetworkInterfaceId=network_interface_id
        )
        infolog("delete_interface -- EC2 deleted network interface: {}".format(network_interface_id),LambdaInfoTracing)
        return True

    except botocore.exceptions.ClientError as e:
        errorlog("Error deleting interface {}: {}".format(network_interface_id,e.response['Error']))


def disassociate_delete_subnet(subnet_id,route_table_id,LambdaInfoTracing):
    """
    disassociate_delete subnet
  
    :param subnet_id: subnet id to be deleted
    :param route_table_id: route table id to disassociate subnet from
      
    """
    try:
        infolog("disassociate_delete_subnet -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
        infolog("disassociate_delete_subnet -- Route Table ID parameter: {}".format(route_table_id),LambdaInfoTracing)
        response = ec2_client.describe_route_tables(RouteTableIds=[route_table_id])
        infolog("disassociate_delete_subnet -- EC2 obtained route table description: {}".format(response),LambdaInfoTracing)
        RouteTableAssociationId = response['RouteTables'][0]['Associations'][0]['RouteTableAssociationId']
        infolog("disassociate_delete_subnet -- EC2 obtained route table id subnet: {}".format(RouteTableAssociationId),LambdaInfoTracing)
    except botocore.exceptions.ClientError as e:
        errorlog("Error obtaining Route Table Association {}: {}".format(subnet_id,e.response['Error']))
    
    if RouteTableAssociationId:
        try:
            infolog("disassociate_delete_subnet -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
            infolog("disassociate_delete_subnet -- Route Table ID parameter: {}".format(route_table_id),LambdaInfoTracing)
            response = ec2_client.disassociate_route_table(AssociationId=RouteTableAssociationId)
            infolog("disassociate_delete_subnet -- EC2 disassociating subnet: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error disassociating subnet {}: {}".format(subnet_id,e.response['Error']))

    try:
        infolog("disassociate_delete_subnet -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
        ec2_client.delete_subnet(
            SubnetId=subnet_id
        )
        infolog("disassociate_delete_subnet -- EC2 deleted subnet: {}".format(subnet_id),LambdaInfoTracing)
        return True
    except botocore.exceptions.ClientError as e:
        errorlog("Error deleting subnet {}: {}".format(subnet_id,e.response['Error']))


def complete_lifecycle_action_success(hookname,groupname,instance_id,LambdaInfoTracing):
    """ 
    Complete Lifecycle Action Success
  
    Complete the lifecycle with success if no exception occurs
  
    :param hookname: Life cycle hook name
    :param groupname: Autoscaling group name
    :param instanceid: Instance id for newly launched instance
  
    """

    try:
        infolog("complete_lifecycle_action_success -- hookname parameter: {}".format(hookname),LambdaInfoTracing)
        infolog("complete_lifecycle_action_success -- ASG parameter: {}".format(groupname),LambdaInfoTracing)
        infolog("complete_lifecycle_action_success -- Instance ID parameter: {}".format(instance_id),LambdaInfoTracing)
        asg_client.complete_lifecycle_action(
            LifecycleHookName=hookname,
            AutoScalingGroupName=groupname,
            InstanceId=instance_id,
            LifecycleActionResult='CONTINUE'
        )
        infolog("complete_lifecycle_action_success -- Lifecycle hook CONTINUEd for: {}".format(instance_id),LambdaInfoTracing)
    except botocore.exceptions.ClientError as e:
            errorlog("Error completing life cycle hook for instance {}: {}".format(instance_id, e.response['Error']))
            errorlog('{"Error": "1"}')

def complete_lifecycle_action_failure(hookname,groupname,instance_id,LambdaInfoTracing):
    """ 
    Complete Lifecycle Action Failure

    Complete the lifecycle with failure if exception occurs

    :param hookname: Life cycle hook name
    :param groupname: Autoscaling group name
    :param instanceid: Instance id for newly launched instance
    
    """

    try:
        infolog("complete_lifecycle_action_failure -- hookname parameter: {}".format(hookname),LambdaInfoTracing)
        infolog("complete_lifecycle_action_failure -- ASG parameter: {}".format(groupname),LambdaInfoTracing)
        infolog("complete_lifecycle_action_failure -- Instance ID parameter: {}".format(instance_id),LambdaInfoTracing)
        asg_client.complete_lifecycle_action(
            LifecycleHookName=hookname,
            AutoScalingGroupName=groupname,
            InstanceId=instance_id,
            LifecycleActionResult='ABANDON'
        )
        infolog("complete_lifecycle_action_failure -- Lifecycle hook ABANDONed for: {}".format(instance_id),LambdaInfoTracing)
    except botocore.exceptions.ClientError as e:
            errorlog("Error completing life cycle hook for instance {}: {}".format(instance_id, e.response['Error']))
            errorlog('{"Error": "1"}')

def restart_instance(instance_id,LambdaInfoTracing):
    """
    restart instance for proper ENI attachment
  
    :param instance_id: instance ID to restart and discover with newly attached interface 
      
    """
    if instance_id:
        try:
            infolog("restart_instance -- Instance ID: {}".format(instance_id),LambdaInfoTracing)
            response = ec2_client.reboot_instances(
                InstanceIds=[instance_id]
            )
            infolog("restart_instance -- EC2 restart EC2 response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error restarting EC2 instance: {}".format(e.response['Error']))

def errorlog(error):
    """ 
    Log
    
    takes message as an input and print it with time in iso format 
    """
    logger.error('%s', error)

def infolog(string,LambdaInfoTracing):
    """ 
    Log
    
    takes message as an input and print it with time in iso format 
    """
    if str(LambdaInfoTracing) == "true": 
        logger.info('%s', string)
