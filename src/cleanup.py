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

from __future__ import print_function
from crhelper import CfnResource
from datetime import datetime
import boto3
import json
import logging
import time
import botocore
import os
import sys

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialise the helper, all inputs are optional, this example shows the defaults
helper = CfnResource(json_logging=False, log_level='DEBUG', boto_level='CRITICAL', sleep_on_delete=120, ssl_verify=None)

ec2_client = boto3.client('ec2')
ec2 = boto3.resource('ec2')

try:
    ## Init code goes here
    pass
except Exception as e:
    helper.init_failure(e)


@helper.create
def create(event, context):
    LambdaInfoTracing = str(event['ResourceProperties']['LambdaInfoTracing'])
    infolog("cleanup -- custom-resource create call",LambdaInfoTracing)


@helper.update
def update(event, context):
    LambdaInfoTracing = str(event['ResourceProperties']['LambdaInfoTracing'])
    infolog("cleanup -- custom-resource update call",LambdaInfoTracing)


@helper.delete
def delete(event, context):
    LambdaInfoTracing = str(event['ResourceProperties']['LambdaInfoTracing'])
    infolog("cleanup -- custom-resource delete call",LambdaInfoTracing)
    # Delete never returns anything. Should not fail if the underlying resources are already deleted.
    # Desired state.

    vpc_id = event['ResourceProperties']['VPCId']
    route_table_id = event['ResourceProperties']['WANRouteTable']
    cidr = str(event['ResourceProperties']['VIPCIDRBlock'])
    vip = str(event['ResourceProperties']['VIPAddress']).split('/')[0]
    eipaddress = str(event['ResourceProperties']['EIPAddress']).split('/')[0]
    eipallocation = str(event['ResourceProperties']['EIPAllocationId']).split('/')[0]

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

def lambda_handler(event, context):

    # printing event received:
    LambdaInfoTracing = str(event['ResourceProperties']['LambdaInfoTracing'])
    infolog("cleanup -- Complete Event: {}".format(str(event['ResourceProperties'])),LambdaInfoTracing)

    # Invoke decorator
    helper(event, context)

def get_subnet(vpc_id,cidr,LambdaInfoTracing):
    """
    obtain subnet id from VPC based on IPv4 CIDR range
  
    :param vpc_id: VPC id
    :param cidr: CIDR IPv4 range from subnet
      
    """

    subnet_id = None
    if vpc_id and cidr:
        try:
            infolog("cleanup -- get_subnet -- VPC ID parameter: {}".format(vpc_id),LambdaInfoTracing)
            infolog("cleanup -- get_subnet -- CIDR parameter: {}".format(cidr),LambdaInfoTracing)
            response = ec2_client.describe_subnets(
                Filters=[
                    {
                        'Name': 'cidr-block',
                        'Values': [cidr]
                    }
                ]
            )
            infolog("cleanup -- get_subnet -- EC2 describe subnet reponse: {}".format(response),LambdaInfoTracing)
            if response['Subnets']:
                subnet_id = response['Subnets'][0]['SubnetId']
                infolog("cleanup -- get_subnet -- EC2 obtained subnet ID: {}".format(subnet_id),LambdaInfoTracing)
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
            infolog("cleanup -- get_interface -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
            infolog("cleanup -- get_interface -- Virtual IP address parameter: {}".format(vip),LambdaInfoTracing)
            response = ec2_client.describe_network_interfaces(
                Filters=[{"Name": "private-ip-address", "Values": [vip]}]
            )
            infolog("cleanup -- get_interface -- EC2 describe ENI response: {}".format(response),LambdaInfoTracing)
            if response['NetworkInterfaces']:
                interface_id = response['NetworkInterfaces'][0]['NetworkInterfaceId']
                infolog("cleanup -- get_interface -- EC2 obtained interface ID: {}".format(interface_id),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining interface: {}".format(e.response['Error']))
    return interface_id


def detach_interface(network_interface_id,LambdaInfoTracing):
    """
    detach interface if it is attached to instance
  
    :param network_interface_id: network interface id that 
                               we previously obtain
      
    """

    attachment = None
    response = None
    if network_interface_id:
        try:
            infolog("cleanup -- detach_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
            response = ec2_client.describe_network_interfaces(
                Filters=[{"Name": "network-interface-id", "Values": [network_interface_id]}]
            )
            infolog("cleanup -- detach_interface -- EC2 describe ENI response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining interface description: {}".format(e.response['Error']))
    
    if response:
        try:
            if "Attachment" in response['NetworkInterfaces'][0]:
                attachment = response['NetworkInterfaces'][0]['Attachment']['AttachmentId']
                infolog("cleanup -- detach_interface -- EC2 obtained attachmend id: {}".format(attachment),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error obtaining attachment: {}".format(e.response['Error']))
    
    if attachment:
        try:
            response = ec2_client.detach_network_interface(AttachmentId=attachment,Force=True)
            infolog("cleanup -- detach_interface -- EC2 obtained response from interface detachment: {}".format(response),LambdaInfoTracing)
            # Wait time to accomplish detachment
            time.sleep(60)
        except botocore.exceptions.ClientError as e:
            errorlog("Error trying detachment: {}".format(e.response['Error']))
    
    return attachment

def delete_interface(network_interface_id,eipaddress,eipallocation,LambdaInfoTracing):
    """
    delete interface
  
    :param network_interface_id: network interface id to be deleted
      
    """
    association = None

    if network_interface_id:
        try:
            infolog("cleanup -- delete_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
            response = ec2_client.describe_network_interfaces(
                Filters=[{"Name": "network-interface-id", "Values": [network_interface_id]}]
            )
            infolog("cleanup -- delete_interface -- EC2 describe ENI response: {}".format(response),LambdaInfoTracing)
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
            infolog("cleanup -- delete_interface -- eipaddress parameter: {}".format(eipaddress),LambdaInfoTracing)
            infolog("cleanup -- delete_interface -- eipallocation parameter: {}".format(eipallocation),LambdaInfoTracing)
            response = ec2_client.disassociate_address(AssociationId=association)
            infolog("cleanup -- delete_interface -- EC2 disassociate EIP response: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error disassociating EIP from network interface: {}".format(e.response['Error']))
    
    # Then delete the interface
    try:
        infolog("cleanup -- delete_interface -- Network Interface ID parameter: {}".format(network_interface_id),LambdaInfoTracing)
        ec2_client.delete_network_interface(
            NetworkInterfaceId=network_interface_id
        )
        infolog("cleanup -- delete_interface -- EC2 deleted network interface: {}".format(network_interface_id),LambdaInfoTracing)
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
        infolog("cleanup -- disassociate_delete_subnet -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
        infolog("cleanup -- disassociate_delete_subnet -- Route Table ID parameter: {}".format(route_table_id),LambdaInfoTracing)
        response = ec2_client.describe_route_tables(RouteTableIds=[route_table_id])
        infolog("cleanup -- disassociate_delete_subnet -- EC2 obtained route table description: {}".format(response),LambdaInfoTracing)
        RouteTableAssociationId = response['RouteTables'][0]['Associations'][0]['RouteTableAssociationId']
        infolog("cleanup -- disassociate_delete_subnet -- EC2 obtained route table id subnet: {}".format(RouteTableAssociationId),LambdaInfoTracing)
    except botocore.exceptions.ClientError as e:
        errorlog("Error obtaining Route Table Association {}: {}".format(subnet_id,e.response['Error']))
    
    if RouteTableAssociationId:
        try:
            infolog("cleanup -- disassociate_delete_subnet -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
            infolog("cleanup -- disassociate_delete_subnet -- Route Table ID parameter: {}".format(route_table_id),LambdaInfoTracing)
            response = ec2_client.disassociate_route_table(AssociationId=RouteTableAssociationId)
            infolog("cleanup -- disassociate_delete_subnet -- EC2 disassociating subnet: {}".format(response),LambdaInfoTracing)
        except botocore.exceptions.ClientError as e:
            errorlog("Error disassociating subnet {}: {}".format(subnet_id,e.response['Error']))

    try:
        infolog("cleanup -- disassociate_delete_subnet -- Subnet ID parameter: {}".format(subnet_id),LambdaInfoTracing)
        ec2_client.delete_subnet(
            SubnetId=subnet_id
        )
        infolog("cleanup -- disassociate_delete_subnet -- EC2 deleted subnet: {}".format(subnet_id),LambdaInfoTracing)
        return True
    except botocore.exceptions.ClientError as e:
        errorlog("Error deleting subnet {}: {}".format(subnet_id,e.response['Error']))

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