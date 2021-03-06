"""All the methods."""
import argparse
import logging

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)


def get_old_ami_info(image=None, client=None):
    """Get necessary information from currently deployed AMI.

    Args:
        image (str): AMI ID.
        client (botocore.client.EC2): Boto3 client.

    Returns:
        trimmed_data (list): Trimmed version of the API data.
    """
    print('### Gathering information from image {0}. ###\n'.format(image))
    trimmed_data = list()

    response = client.describe_instances(Filters=[{
        'Name': 'image-id',
        'Values': [image]
    }, {
        'Name': 'instance-state-name',
        'Values': ['running']
    }])

    LOG.debug('Raw response for describe_instances: %s', response)

    for instances in response['Reservations']:
        data = instances['Instances'][0]
        trimmed_data.append({
            'InstanceType': data['InstanceType'],
            'KeyName': data['KeyName'],
            'SecurityGroupIds': [sg.get('GroupId') for sg in data['SecurityGroups']],
            'SubnetId': data['SubnetId'],
            'InstanceId': data['InstanceId'],
        })

        LOG.info('Added instance id %s to the list.', data['InstanceId'])

    LOG.info('Trimmed result: %s', trimmed_data)
    return trimmed_data


def get_elb_name(instances=None, client=None):
    """Find and get the load balancer name.

    Args:
        instances (list): List of instances.
        client (botocore.client.ELB): Boto3 client.

    Returns:
        elb_name (str): Name of the ELB.
    """
    elb_name = None
    response = client.describe_load_balancers()
    LOG.debug('Response from describe load balancers: %s', response)

    for elb in response['LoadBalancerDescriptions']:
        instances_in_elb = [vm['InstanceId'] for vm in elb['Instances']]
        if set(instances) & set(instances_in_elb):
            elb_name = elb['LoadBalancerName']

    LOG.info('ELB Name: %s', elb_name)
    print('### ELB name {0} is being used for instances {1}. ###\n'.format(elb_name, instances))

    if elb_name is None:
        raise Exception('No ELB found for instances: {0}.'.format(instances))

    return elb_name


def register_to_elb(lb_name=None, instances=None, client=None):
    """Register instances given to given load balancer.

    Args:
        lb_name (str): ELB Name.
        instances (list): Instance IDs list.
        client (botocore.client.ELB): Boto3 client.

    Returns:
        health_check (bool): True or False.
    """
    health_check = False
    instances_list = [{'InstanceId': vm} for vm in instances]

    response = client.register_instances_with_load_balancer(
        LoadBalancerName=lb_name,
        Instances=instances_list, )
    LOG.info('Registered instances to ELB %s: \n%s.', lb_name, response)

    waiter = client.get_waiter('instance_in_service')
    print('### Wait for registered instances pass health checks. ###\n')

    try:
        waiter.wait(
            LoadBalancerName=lb_name,
            Instances=instances_list, )
        health_check = True
    except client.exceptions.ClientError as error:
        LOG.error('Instances might not have passed health checks. %s', error)

    return health_check


def launch_new_instances(image=None, data=None, client=None):
    """Launch new instances with given new AMI.

    Args:
        image (str): New AMI id to deploy.
        data (list): Trimmed data from old AMI.
        client (botocore.client.EC2): Boto3 client.

    Returns:
        instances (list): Instance Ids of newly launched AMIs.
    """
    instances = list()

    for resource in data:
        response = client.run_instances(
            ImageId=image,
            InstanceType=resource['InstanceType'],
            KeyName=resource['KeyName'],
            SecurityGroupIds=resource['SecurityGroupIds'],
            SubnetId=resource['SubnetId'],
            MaxCount=1,
            MinCount=1, )
        instances.append(response['Instances'][0]['InstanceId'])

    waiter = client.get_waiter('instance_running')
    print('### Wait for newly launched instances state change to running. ###\n')
    waiter.wait(InstanceIds=instances)

    LOG.info('Newly launched instances are: %s', instances)
    print('### Instances launched based on AMI {0} are {1}. ###'.format(image, instances))

    return instances


def terminate_old_instances(lb_name=None, instances=None, client_ec2=None, client_elb=None):
    """Deregister old instances from ELB and terminate.

    Args:
        lb_name (str): ELB Name.
        instances (list): List of instance ids.
        client_ec2 (botocore.client.EC2): Boto3 client for ec2.
        client_elb (botocore.client.ELB): Boto3 client for elb.

    Returns:
        None
    """
    print('### Deregister from ELB {0} and terminate instances {1} ###'.format(lb_name, instances))
    instances_list = [{'InstanceId': vm} for vm in instances]

    response = client_elb.deregister_instances_from_load_balancer(
        LoadBalancerName=lb_name,
        Instances=instances_list, )

    LOG.info('Deregistered instances %s from ELB %s.', instances, lb_name)
    LOG.debug(response)

    terminate = client_ec2.terminate_instances(InstanceIds=instances)
    LOG.info('Terminated old instances.')
    LOG.debug(terminate)


def argument_parser():
    """Argument parser.

    Returns:
        old_ami_id (str): Old AMI ID.
        new_ami_id (str): New AMI ID.
    """
    parser = argparse.ArgumentParser(description='Deployment script.')
    parser.add_argument(
        'old_ami_id', metavar='old-ami-id', type=str, nargs=1, help='Provide OLD AMI-ID that is deployed.')
    parser.add_argument(
        'new_ami_id', metavar='new-ami-id', type=str, nargs=1, help='Provide NEW AMI-ID to deploy.')
    args = parser.parse_args()

    if args.old_ami_id == args.new_ami_id:
        raise Exception('Both AMI ids cannot be same.')

    return args.old_ami_id, args.new_ami_id
