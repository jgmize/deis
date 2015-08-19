#!/usr/bin/env python
import json
import os

CURR_DIR = os.path.dirname(os.path.realpath(__file__))

template = json.load(open(os.path.join(CURR_DIR, 'vpc.template.json'), 'r'))

include_private_subnets = os.getenv('INCLUDE_PRIVATE_SUBNETS', 'true')
include_private_subnets = include_private_subnets in [ 'true', 'TRUE', 'True', '1', 'yes' ]

if include_private_subnets:
    # Get mappings from separate files
    template['Mappings']['NatAMIs'] = json.load(open(os.path.join(CURR_DIR, 'nat-amis.json'), 'r'))
    template['Mappings']['BastionAMIs'] = json.load(open(os.path.join(CURR_DIR, 'bastion-amis.json'), 'r'))
else:
    # Skip anything related to the more robust network setup
    del template['Parameters']['KeyPair']
    del template['Parameters']['IamInstanceProfile']
    del template['Parameters']['SSHFrom']
    del template['Parameters']['NatInstanceType']
    del template['Parameters']['BastionInstanceType']
    del template['Parameters']['EC2VirtualizationType']
    del template['Parameters']['EC2EBSVolumeType']
    del template['Parameters']['AssociatePublicIP']
    del template['Parameters']['RootVolumeSize']
    del template['Mappings']['NatAMIs']
    del template['Mappings']['BastionAMIs']
    del template['Mappings']['SubnetConfig']['PrivateSubnet1']
    del template['Mappings']['SubnetConfig']['PrivateSubnet2']
    del template['Mappings']['SubnetConfig']['PrivateSubnet3']
    del template['Conditions']['UseIamInstanceProfile']
    del template['Resources']['PrivateSubnet1']
    del template['Resources']['PrivateSubnet2']
    del template['Resources']['PrivateSubnet3']
    del template['Resources']['PrivateRouteTable']
    del template['Resources']['PrivateRoute']
    del template['Resources']['PrivateSubnet1RouteTableAssociation']
    del template['Resources']['PrivateSubnet2RouteTableAssociation']
    del template['Resources']['PrivateSubnet3RouteTableAssociation']
    del template['Resources']['NatSecurityGroup']
    del template['Resources']['NatHost']
    del template['Resources']['NatIpAddress']
    del template['Resources']['BastionSecurityGroup']
    del template['Resources']['BastionHost']
    del template['Resources']['BastionIpAddress']
    del template['Outputs']['PrivateSubnet1Id']
    del template['Outputs']['PrivateSubnet2Id']
    del template['Outputs']['PrivateSubnet3Id']
    del template['Outputs']['BastionSecurityGroupId']
    del template['Outputs']['BastionElasticIp']

print json.dumps(template)
