import boto3
import json
import logging
from os import getenv, environ
import sys

# Initialize logger
logger = logging.getLogger()
log_level = getenv("LOGLEVEL", "INFO")
level = logging.getLevelName(log_level)
logger.setLevel(level)

# Initialize AWS clients
autoscaling = boto3.client('autoscaling')
cloudwatch = boto3.client('cloudwatch')
dynamodb = boto3.resource('dynamodb')
ec2 = boto3.resource('ec2')

# DynamoDB table name
TABLE_NAME = environ.get('DYNAMODB_TABLE_NAME')

# SNS Topic
SNS_TOPIC_ARN = environ.get('SNS_TOPIC_ARN')


def get_alarms(application_name_tag_value, application_type_tag_value, ddb_alarm_table):
    '''
        Retrieve list of alarms and configuration from dynamo db
    '''
    batch_keys = {
        ddb_alarm_table: {
            'Keys': [{'Name': application_name_tag_value, 'Type': application_type_tag_value}],
            'ConsistentRead': True,
            'ProjectionExpression': 'Alarms'
        }
    }

    logger.info('Retrieving alarms from ddb')
    retrieved_alarms = dynamodb.batch_get_item(RequestItems=batch_keys)
    logger.info(f'Retrieved alarms: {retrieved_alarms}')

    return retrieved_alarms['Responses']


def create_alarm(AlarmName, MetricName, ComparisonOperator, Period, Threshold, Statistic, Namespace, Dimensions, action_enabled, sns_topic_arn, AlarmDescription):
    '''
        Create CloudWatch Alarms
    '''
    Threshold = float(Threshold)
    Period = int(Period)

    try:
        alarm = {
            'AlarmName': AlarmName,
            'AlarmDescription': AlarmDescription,
            'MetricName': MetricName,
            'Namespace': Namespace,
            'Dimensions': Dimensions,
            'Period': Period,
            'EvaluationPeriods': 1,
            'Threshold': Threshold,
            'ComparisonOperator': ComparisonOperator,
            'Statistic': Statistic,
            'ActionsEnabled': action_enabled,
            'AlarmActions': [sns_topic_arn]
        }

        cloudwatch.put_metric_alarm(**alarm)

        logger.info(f'Created alarm {AlarmName}')

    except Exception as e:
        # If any other exceptions which we didn't expect are raised
        # then fail and log the exception message.
        logger.error(f'Error creating alarm {AlarmName} with dimension {Dimensions}!: {e}')


def get_alarm_name(application_name_tag_value, application_type_tag_value, instance_id):
    '''
        Returns the names of the alarms created in CloudWatch
    '''
    try:
        alarm_prefix = f'{application_name_tag_value}-{application_type_tag_value}-{instance_id}'
        response = cloudwatch.describe_alarms(AlarmNamePrefix=alarm_prefix)
        alarm_names = [alarm['AlarmName'] for alarm in response['MetricAlarms']]
        logger.info(f'Alarm names to delete: {alarm_names}')
        return alarm_names

    except Exception as e:
        # If any other exceptions which we didn't expect are raised
        # then fail and log the exception message.
        logger.error(f'Error getting alarm names {alarm_prefix} from cloudwatch: {e}')


def lambda_handler(event, context):
    # Extract relevant details from the event
    logger.info(event)
    instance_id = event['detail']['EC2InstanceId']
    lifecycle_hook_name = event['detail']['LifecycleHookName']
    autoscaling_group_name = event['detail']['AutoScalingGroupName']
    lifecycle_transition = event['detail']['LifecycleTransition']

    # Read instance tags
    instance = ec2.Instance(instance_id)
    instance_tags = {tag['Key']: tag['Value'] for tag in instance.tags}
    logger.info(instance_tags)

    # Retrieve alarm definitions from DynamoDB
    application_name_tag_value = instance_tags['application-name']
    application_type_tag_value = instance_tags['application-type']
    dynamodb_alarm_table = TABLE_NAME

    # Check if instance has the tag create-cloudwatch-alarm
    if 'create-cloudwatch-alarm' in instance_tags:
        logger.info('create-cloudwatch-alarm found')

        # Handle instance launch
        if lifecycle_transition == 'autoscaling:EC2_INSTANCE_LAUNCHING':

            try:
                alarms = get_alarms(application_name_tag_value, application_type_tag_value, dynamodb_alarm_table)[dynamodb_alarm_table][0]['Alarms']
                logger.info(f'Alarms: {alarms}')
            except Exception as e:
                logger.error(f'Could not retrieve alarm configuration from dynamo db table {dynamodb_alarm_table} : {e}')
                sys.exit(-1)

            # Create CloudWatch alarms
            for metric in alarms:
                metric_name = alarms[metric]['MetricName']
                namespace = 'AWS/EC2'
                dimensions = [
                    {
                        'Name': 'InstanceId',
                        'Value': instance_id
                    }
                ]

                # Create cw alarm
                alarm = alarms[metric]['AlarmName']
                alarm_name = f'{application_name_tag_value}-{application_type_tag_value}-{instance_id}-{alarm}'
                alarm_description = alarms[metric]['AlarmDescription']
                comparison_operator = alarms[metric]['ComparisonOperator']
                period = alarms[metric]['Period']
                statistic = alarms[metric]['Statistic']
                threshold = alarms[metric]['Threshold']
                action_enabled = alarms[metric]['ActionsEnabled'] == 'True'
                alarm = create_alarm(alarm_name, metric_name, comparison_operator, period, threshold, statistic, namespace, dimensions, action_enabled, SNS_TOPIC_ARN, alarm_description)

        # Handle instance termination
        elif lifecycle_transition == 'autoscaling:EC2_INSTANCE_TERMINATING':
            # Get name of cw alarms
            alarms_to_delete = get_alarm_name(application_name_tag_value, application_type_tag_value, instance_id)
            # Delete cw alarms
            cloudwatch.delete_alarms(AlarmNames=alarms_to_delete)
            logger.info(f'{alarms_to_delete} deleted')

        # Complete the lifecycle action
        logger.info('Completing lifecycle action')
        autoscaling.complete_lifecycle_action(
            LifecycleHookName=lifecycle_hook_name,
            AutoScalingGroupName=autoscaling_group_name,
            LifecycleActionResult='CONTINUE',
            InstanceId=instance_id
        )

    else:
        logger.info('create-cloudwatch-alarm tag not found. Skipping CloudWatch alarm creation')
        sys.exit(0)

    return {
        'statusCode': 200,
        'body': json.dumps('Lambda function executed successfully!')
    }
