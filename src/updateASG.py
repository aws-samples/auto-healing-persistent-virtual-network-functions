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
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)
ec2_client = boto3.client('ec2')
asg_client = boto3.client('autoscaling')
ec2 = boto3.resource('ec2')

def lambda_handler(event, context):
    AutoScalingGroupName = os.environ['AutoScalingGroupName']
    ASGUpdateHealthCheckGraceTime = os.environ['ASGUpdateHealthCheckGraceTime']
    LambdaInfoTracing = str(os.environ['LambdaInfoTracing'])

    # printing event received:
    infolog("lambda_handler -- Event keys: {}".format(list(event['detail'].keys())),LambdaInfoTracing)
    infolog("lambda_handler -- Complete Event: {}".format(str(event['detail'])),LambdaInfoTracing)
    
    # Tracing EC2 details
    infolog("lambda_handler -- Event: {}".format(event["detail-type"]),LambdaInfoTracing)
    infolog("lambda_handler -- AutoScalingGroupName: {}".format(AutoScalingGroupName),LambdaInfoTracing)
    infolog("lambda_handler -- ASGUpdateHealthCheckGraceTime: {}".format(ASGUpdateHealthCheckGraceTime),LambdaInfoTracing)

    if ASGUpdateHealthCheckGraceTime and AutoScalingGroupName:
        try:
            # Wait time to accomplish detachment
            time.sleep(float(ASGUpdateHealthCheckGraceTime))
            response = asg_client.update_auto_scaling_group(AutoScalingGroupName=AutoScalingGroupName,DesiredCapacity=1)
            infolog("lambda_handler -- Updated AutiScalingGroup with Desired Capacity 1: {}".format(response),LambdaInfoTracing)
            
        except botocore.exceptions.ClientError as e:
            errorlog("Error trying AutoScalingGroup Update: {}".format(e.response['Error']))
            errorlog('{"Error": "1"}')

def errorlog(error):
    """ 
    Log
    
    takes message as an input and print it with time in iso format 
    """
    logger.error('{}Z {}'.format(datetime.utcnow().isoformat(), error))

def infolog(string,LambdaInfoTracing):
    """ 
    Log
    
    takes message as an input and print it with time in iso format 
    """
    if str(LambdaInfoTracing) == "true": 
        logger.info('{}Z {}'.format(datetime.utcnow().isoformat(), string))