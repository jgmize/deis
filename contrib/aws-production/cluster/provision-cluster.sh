#!/usr/bin/env bash
#
# Usage: ./provision-cluster.sh [name]
# The [name] is the CloudFormation stack name, and defaults to 'deis-cluster'

if [ -z "$1" ]
  then
    STACK_NAME=deis-cluster
  else
    STACK_NAME=$1
fi

set -e

THIS_DIR=$(cd $(dirname $0); pwd) # absolute path
CONTRIB_DIR=$(dirname $(dirname $THIS_DIR))
USER_DATA_DIR=$THIS_DIR/user-data

source $CONTRIB_DIR/utils.sh

# Check for AWS API tools in $PATH
if ! which aws > /dev/null; then
  echo_red 'Please install the AWS command-line tool and ensure it is in your $PATH.'
  exit 1
fi

if [ ! -z "$AWS_CLI_PROFILE" ]; then
  EXTRA_AWS_CLI_ARGS+="--profile $AWS_CLI_PROFILE"
fi

if [ -z "$DEIS_NUM_CONTROL_PLANE_INSTANCES" ]; then
  DEIS_NUM_CONTROL_PLANE_INSTANCES=3
fi

if [ -z "$DEIS_NUM_DATA_PLANE_INSTANCES" ]; then
  DEIS_NUM_DATA_PLANE_INSTANCES=3
fi

# Make sure we have all required info
if [ -z "$VPC_ID" ] || [ -z "$VPC_SUBNETS" ] || [ -z "$VPC_ZONES" ]; then
  echo_red 'You must specify VPC_ID, VPC_SUBNETS, and VPC_ZONES (and VPC_PRIVATE_SUBNETS if you have some).'
  exit 1
fi

# Check that the CoreOS user-data file is valid
$CONTRIB_DIR/util/check-user-data.sh $USER_DATA_DIR/control-plane-user-data
$CONTRIB_DIR/util/check-user-data.sh $USER_DATA_DIR/data-plane-user-data

# Prepare bailout function to prevent us polluting the namespace
bailout() {
  aws cloudformation delete-stack --stack-name $STACK_NAME
}

# Create an AWS cloudformation stack based on the a generated template
aws cloudformation create-stack \
  --template-body "$($THIS_DIR/gen-cluster-json.py --channel $COREOS_CHANNEL --version $COREOS_VERSION)" \
  --stack-name $STACK_NAME \
  --parameters "$(<$THIS_DIR/cluster.parameters.json)" \
  $EXTRA_AWS_CLI_ARGS

# Loop until the instances are created
ATTEMPTS=60
SLEEPTIME=10
COUNTER=1
INSTANCE_IDS=""
DEIS_NUM_TOTAL_INSTANCES=$(($DEIS_NUM_CONTROL_PLANE_INSTANCES + $DEIS_NUM_DATA_PLANE_INSTANCES))
until [ $(wc -w <<< $INSTANCE_IDS) -eq $DEIS_NUM_TOTAL_INSTANCES -a "$STACK_STATUS" = "CREATE_COMPLETE" ]; do
  if [ $COUNTER -gt $ATTEMPTS ]; then
    echo "Provisioning instances failed (timeout, $(wc -w <<< $INSTANCE_IDS) of $DEIS_NUM_TOTAL_INSTANCES provisioned after 10m)"
    echo "Destroying stack $STACK_NAME"
    bailout
    exit 1
  fi

  STACK_STATUS=$(aws --output text cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[].StackStatus' $EXTRA_AWS_CLI_ARGS)
  if [ $STACK_STATUS != "CREATE_IN_PROGRESS" -a $STACK_STATUS != "CREATE_COMPLETE" ] ; then
    echo "error creating stack: "
    aws --output text cloudformation describe-stack-events \
      --stack-name $STACK_NAME \
      --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
      $EXTRA_AWS_CLI_ARGS
    bailout
    exit 1
  fi

  INSTANCE_IDS=$(aws ec2 describe-instances \
    --filters Name=tag:aws:cloudformation:stack-name,Values=$STACK_NAME Name=instance-state-name,Values=running \
    --query 'Reservations[].Instances[].[ InstanceId ]' \
    --output text \
    $EXTRA_AWS_CLI_ARGS)

  echo "Waiting for instances to be provisioned ($STACK_STATUS, $(expr 61 - $COUNTER)0s) ..."
  sleep $SLEEPTIME

  let COUNTER=COUNTER+1
done

# Loop until the instances pass health checks
COUNTER=1
INSTANCE_STATUSES=""
until [ `wc -w <<< $INSTANCE_STATUSES` -eq $DEIS_NUM_TOTAL_INSTANCES ]; do
  if [ $COUNTER -gt $ATTEMPTS ];
    then echo "Health checks not passed after 10m, giving up"
    echo "Destroying stack $STACK_NAME"
    bailout
    exit 1
  fi

  if [ $COUNTER -ne 1 ]; then sleep $SLEEPTIME; fi
  echo "Waiting for instances to pass initial health checks ($(expr 61 - $COUNTER)0s) ..."
  INSTANCE_STATUSES=$(aws ec2 describe-instance-status \
    --filters Name=instance-status.reachability,Values=passed \
    --instance-ids $INSTANCE_IDS \
    --query 'InstanceStatuses[].[ InstanceId ]' \
    --output text \
    $EXTRA_AWS_CLI_ARGS)
  let COUNTER=COUNTER+1
done

# Print instance info
echo "Instances are available:"
aws ec2 describe-instances \
  --filters Name=tag:aws:cloudformation:stack-name,Values=$STACK_NAME Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].[InstanceId,PublicIpAddress,InstanceType,Placement.AvailabilityZone,State.Name]' \
  --output text \
  $EXTRA_AWS_CLI_ARGS

# Get ELB public DNS name through cloudformation
# TODO: is "first output value" going to be reliable enough?
export ELB_DNS_NAME=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --max-items 1 \
  --query 'Stacks[].[ Outputs[0].[ OutputValue ] ]' \
  --output=text \
  $EXTRA_AWS_CLI_ARGS)

# Get ELB friendly name through aws elb
ELB_NAME=$(aws elb describe-load-balancers \
  --query 'LoadBalancerDescriptions[].[ DNSName,LoadBalancerName ]' \
  --output=text \
  $EXTRA_AWS_CLI_ARGS | grep -F $ELB_DNS_NAME | head -n1 | cut -f2)
echo "Using ELB $ELB_NAME at $ELB_DNS_NAME"

# Instances launched into a VPC may not have a PublicIPAddress
for ip_type in PublicIpAddress PrivateIpAddress; do
  FIRST_INSTANCE=$(aws ec2 describe-instances \
    --filters Name=tag:aws:cloudformation:stack-name,Values=$STACK_NAME Name=instance-state-name,Values=running \
    --query "Reservations[].Instances[].[$ip_type]" \
    --output text \
    $EXTRA_AWS_CLI_ARGS | head -1)
  if [[ ! $FIRST_INSTANCE == "None" ]]; then
    break
  fi
done

echo_green "\nYour Deis cluster was deployed to AWS CloudFormation as stack "$STACK_NAME".\n"
