#!/usr/bin/env python
import argparse
import json
import os
import urllib
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--channel', help='the CoreOS channel to use', default='stable')
parser.add_argument('--version', help='the CoreOS version to use', default='current')
args = vars(parser.parse_args())

url = "http://{channel}.release.core-os.net/amd64-usr/{version}/coreos_production_ami_all.json".format(**args)
try:
    amis = json.load(urllib.urlopen(url))
except (IOError, ValueError):
    print "The URL {} is invalid.".format(url)
    raise

CURR_DIR = os.path.dirname(os.path.realpath(__file__))

# Add AWS-specific units to the shared user-data
FORMAT_DOCKER_VOLUME = '''
  [Unit]
  Description=Formats the added EBS volume for Docker
  ConditionPathExists=!/etc/docker-volume-formatted
  [Service]
  Type=oneshot
  RemainAfterExit=yes
  ExecStart=/usr/sbin/wipefs -f /dev/xvdf
  ExecStart=/usr/sbin/mkfs.ext4 -i 4096 -b 4096 /dev/xvdf
  ExecStart=/bin/touch /etc/docker-volume-formatted
'''
MOUNT_DOCKER_VOLUME = '''
  [Unit]
  Description=Mount Docker volume to /var/lib/docker
  Requires=format-docker-volume.service
  After=format-docker-volume.service
  Before=docker.service
  [Mount]
  What=/dev/xvdf
  Where=/var/lib/docker
  Type=ext4
'''
DOCKER_DROPIN = '''
  [Unit]
  Requires=var-lib-docker.mount
  After=var-lib-docker.mount
'''
FORMAT_ETCD_VOLUME = '''
  [Unit]
  Description=Formats the etcd device
  ConditionPathExists=!/etc/etcd-volume-formatted
  [Service]
  Type=oneshot
  RemainAfterExit=yes
  ExecStart=/usr/sbin/wipefs -f /dev/xvdg
  ExecStart=/usr/sbin/mkfs.ext4 -i 4096 -b 4096 /dev/xvdg
  ExecStart=/bin/touch /etc/etcd-volume-formatted
'''
MOUNT_ETCD_VOLUME = '''
  [Unit]
  Description=Mounts the etcd volume
  Requires=format-etcd-volume.service
  After=format-etcd-volume.service
  [Mount]
  What=/dev/xvdg
  Where=/media/etcd
  Type=ext4
'''
PREPARE_ETCD_DATA_DIRECTORY = '''
  [Unit]
  Description=Prepares the etcd data directory
  Requires=media-etcd.mount
  After=media-etcd.mount
  Before=etcd.service
  [Service]
  Type=oneshot
  RemainAfterExit=yes
  ExecStart=/usr/bin/chown -R etcd:etcd /media/etcd
'''
ETCD_DROPIN = '''
  [Unit]
  Requires=prepare-etcd-data-directory.service
  After=prepare-etcd-data-directory.service
'''

new_units = [
    dict({'name': 'format-docker-volume.service', 'command': 'start', 'content': FORMAT_DOCKER_VOLUME}),
    dict({'name': 'var-lib-docker.mount', 'command': 'start', 'content': MOUNT_DOCKER_VOLUME}),
    dict({'name': 'docker.service', 'drop-ins': [{'name': '90-after-docker-volume.conf', 'content': DOCKER_DROPIN}]}),
    dict({'name': 'format-etcd-volume.service', 'command': 'start', 'content': FORMAT_ETCD_VOLUME}),
    dict({'name': 'media-etcd.mount', 'command': 'start', 'content': MOUNT_ETCD_VOLUME}),
    dict({'name': 'prepare-etcd-data-directory.service', 'command': 'start', 'content': PREPARE_ETCD_DATA_DIRECTORY}),
    dict({'name': 'etcd.service', 'drop-ins': [{'name': '90-after-etcd-volume.conf', 'content': ETCD_DROPIN}]})
]

def prepare_user_data(data):
    # coreos-cloudinit will start the units in order, so we want these to be processed before etcd/fleet
    # are started
    data['coreos']['units'] = new_units + data['coreos']['units']
    dump = yaml.dump(data, default_flow_style=False)
    # Configure etcd to use its EBS volume
    dump = dump.replace('ETCD_HOST_DATA_DIR=/var/lib/etcd2', 'ETCD_HOST_DATA_DIR=/media/etcd')
    return dump

header = ["#cloud-config", "---"]

control_plane_data = yaml.load(file(os.path.join(CURR_DIR, 'user-data', 'control-plane-user-data'), 'r'))
control_plane_dump = prepare_user_data(control_plane_data)

data_plane_data = yaml.load(file(os.path.join(CURR_DIR, 'user-data', 'data-plane-user-data'), 'r'))
data_plane_dump = prepare_user_data(data_plane_data)

template = json.load(open(os.path.join(CURR_DIR, 'cluster.template.json'), 'r'))

template['Resources']['ControlPlaneLaunchConfig']['Properties']['UserData']['Fn::Base64']['Fn::Join'] = ["\n", header + control_plane_dump.split("\n")]
template['Resources']['DataPlaneLaunchConfig']['Properties']['UserData']['Fn::Base64']['Fn::Join'] = ["\n", header + data_plane_dump.split("\n")]
template['Parameters']['ControlPlaneSize']['Default'] = str(os.getenv('DEIS_NUM_CONTROL_PLANE_INSTANCES', 3))
template['Parameters']['DataPlaneSize']['Default'] = str(os.getenv('DEIS_NUM_DATA_PLANE_INSTANCES', 3))
template['Mappings']['CoreOSAMIs'] = dict(map(lambda n: (n['name'], dict(PV=n['pv'], HVM=n['hvm'])), amis['amis']))

VPC_ID = os.getenv('VPC_ID', None)
VPC_SUBNETS = os.getenv('VPC_SUBNETS', None)
VPC_PRIVATE_SUBNETS = os.getenv('VPC_PRIVATE_SUBNETS', VPC_SUBNETS)
VPC_ZONES = os.getenv('VPC_ZONES', None)

# Update VpcId fields
template['Resources']['DeisWebELBSecurityGroup']['Properties']['VpcId'] = VPC_ID
template['Resources']['CoreOSSecurityGroup']['Properties']['VpcId'] = VPC_ID

# Update subnets and zones
template['Resources']['ControlPlaneAutoScale']['Properties']['AvailabilityZones'] = VPC_ZONES.split(',')
template['Resources']['ControlPlaneAutoScale']['Properties']['VPCZoneIdentifier'] = VPC_PRIVATE_SUBNETS.split(',')
template['Resources']['DataPlaneAutoScale']['Properties']['AvailabilityZones'] = VPC_ZONES.split(',')
template['Resources']['DataPlaneAutoScale']['Properties']['VPCZoneIdentifier'] = VPC_PRIVATE_SUBNETS.split(',')
template['Resources']['DeisWebELB']['Properties']['Subnets'] = VPC_SUBNETS.split(',')

# Update ingress to the cluster based on whether a bastion server is being used
bastion_security_group_id = os.getenv('BASTION_SECURITY_GROUP_ID')
if bastion_security_group_id:
    del template['Parameters']['SSHFrom']
    template['Parameters']['BastionSecurityGroupID']['Default'] = bastion_security_group_id
    del template['Resources']['CoreOSSecurityGroup']['Properties']['SecurityGroupIngress'][0]
else:
    del template['Parameters']['BastionSecurityGroupID']
    del template['Resources']['CoreOSSecurityGroup']['Properties']['SecurityGroupIngress'][1]

print json.dumps(template)
