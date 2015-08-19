#!/usr/bin/env bash
#
# Usage: ./provision-vpc.sh [name]
# The [name] is the CloudFormation stack name, and defaults to 'deis-vpc'

if [ -z "$1" ]
  then
    STACK_NAME=deis-vpc
  else
    STACK_NAME=$1
fi

set -e

THIS_DIR=$(cd $(dirname $0); pwd) # absolute path
CONTRIB_DIR=$(dirname $(dirname $THIS_DIR))

source $CONTRIB_DIR/utils.sh

# check for AWS API tools in $PATH
if ! which aws > /dev/null; then
  echo_red 'Please install the AWS command-line tool and ensure it is in your $PATH.'
  exit 1
fi

if [ ! -z "$AWS_CLI_PROFILE" ]; then
  EXTRA_AWS_CLI_ARGS+="--profile $AWS_CLI_PROFILE"
fi

# Prepare bailout function to prevent us polluting the namespace
bailout() {
  aws cloudformation delete-stack --stack-name $STACK_NAME
}

# Create an AWS cloudformation stack based on the a generated template
if [ -f vpc.parameters.json ]; then
  aws cloudformation create-stack \
    --template-body "$($THIS_DIR/gen-vpc-json.py)" \
    --stack-name $STACK_NAME \
    --parameters "$(<$THIS_DIR/vpc.parameters.json)" \
    $EXTRA_AWS_CLI_ARGS
else
  aws cloudformation create-stack \
    --template-body "$($THIS_DIR/gen-vpc-json.py)" \
    --stack-name $STACK_NAME \
    $EXTRA_AWS_CLI_ARGS
fi

# Loop until stack creation is complete
ATTEMPTS=60
SLEEPTIME=10
COUNTER=1
INSTANCE_IDS=""
until [ "$STACK_STATUS" = "CREATE_COMPLETE" ]; do
  if [ $COUNTER -gt $ATTEMPTS ]; then
    echo "Provisioning failed"
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

  echo "Waiting for provisioning to complete ($STACK_STATUS, $(expr 61 - $COUNTER)0s) ..."
  sleep $SLEEPTIME

  let COUNTER=COUNTER+1
done

echo_green "\nYour Deis VPC was deployed to AWS CloudFormation as stack "$STACK_NAME".\n"

aws --output text cloudformation describe-stacks --stack-name $STACK_NAME
