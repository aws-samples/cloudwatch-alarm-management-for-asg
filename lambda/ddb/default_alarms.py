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


def lambda_handler(event, context):
    logger.info(event)
    # Determine the event type
    if 'Records' in event:
        # Handle DynamoDB event
        handle_cloudwatch_alarms(event)
    elif 'RequestType' in event:
        # Handle CloudFormation Custom Resource event
        handle_cloudformation_event(event)
    else:
        # Handle unknown event type
        logger.info(f"Unsupported event structure: {json.dumps(event)}")


def handle_cloudwatch_alarms(event):
    logger.info("Processing DynamoDB event")
    cloudwatch = boto3.client('cloudwatch')
    records = event.get('Records', [])

    for record in records:
        new_image = record['dynamodb'].get('NewImage', {})
        name = new_image['Name']['S']
        type = new_image['Type']['S']
        # Get new alarm definitions
        new_alarms = new_image['Alarms']['M']
        # Extract metric names
        new_metric_names = [a['M']['MetricName']['S'] for a in new_alarms.values()]
        existing_alarms = get_existing_alarms_for_asg(cloudwatch, name, type)
        logger.info(f"Existing alarms for {name}-{type}: {existing_alarms}")

        for existing_alarm in existing_alarms:
            # Extract instance ID from alarm name
            logger.info(f"Existing alarm: {existing_alarm}")

            alarm_name = existing_alarm['AlarmName']
            alarm_name_parts = alarm_name.split('-')
            instance_id = '-'.join(alarm_name_parts[2:-1])
            logger.info(f"Instance ID: {instance_id}")
            existing_metric_names = [alarm['MetricName'] for alarm in existing_alarms]

            metric_name = existing_alarm['MetricName']

            if metric_name in new_metric_names:
                # Alarm exists in new definition, update it
                new_alarm = get_new_alarm_def(metric_name, new_alarms)
                update_alarm(cloudwatch, instance_id, new_alarm, name, type)

            else:
                # Not in new definition, delete it
                delete_alarm(cloudwatch, existing_alarm['AlarmName'])
            for m in new_metric_names:
                if m not in existing_metric_names:
                    new_alarm = get_new_alarm_def(m, new_alarms)
                    create_alarm(cloudwatch, instance_id, new_alarm, name, type)


def get_existing_alarms_for_asg(cloudwatch, name, type):
    prefix = f"{name}-{type}"
    existing = cloudwatch.describe_alarms(AlarmNamePrefix=prefix)
    return existing['MetricAlarms']


def get_new_alarm_def(metric_name, new_alarms):
    for alarm in new_alarms.values():
        if alarm['M']['MetricName']['S'] == metric_name:
            return alarm
    return None


def delete_alarm(cloudwatch, metric_name):
    try:
        cloudwatch.delete_alarms(AlarmNames=[metric_name])
    except Exception as e:
        logger.info(f"Error deleting alarm {metric_name}: {e}")


def update_alarm(cloudwatch, instance_id, alarm_def, name, type):
    metric_name = alarm_def['M']['MetricName']['S']
    alarm_name = f"{name}-{type}-{instance_id}-{metric_name}"
    evaluation_periods = alarm_def['M']['EvaluationPeriods']['S']
    comparison_operator = alarm_def['M']['ComparisonOperator']['S']
    statistic = alarm_def['M']['Statistic']['S']
    threshold = alarm_def['M']['Threshold']['S']
    period = alarm_def['M']['Period']['S']

    threshold = float(threshold)
    period = int(period)
    evaluation_periods = int(evaluation_periods)

    cloudwatch.put_metric_alarm(
        AlarmName=alarm_name,
        MetricName=metric_name,
        Namespace='AWS/EC2',
        Dimensions=[
            {
              'Name': 'InstanceId',
              'Value': instance_id
            }
        ],
        Threshold=threshold,
        Period=period,
        EvaluationPeriods=evaluation_periods,
        ComparisonOperator=comparison_operator,
        Statistic=statistic
    )


def create_alarm(cloudwatch, instance_id, alarm_def, name, type):
    metric_name = alarm_def['M']['MetricName']['S']
    alarm_name = f"{name}-{type}-{instance_id}-{metric_name}"
    threshold = alarm_def['M']['Threshold']['S']
    period = alarm_def['M']['Period']['S']
    evaluation_periods = alarm_def['M']['EvaluationPeriods']['S']
    comparison_operator = alarm_def['M']['ComparisonOperator']['S']
    statistic = alarm_def['M']['Statistic']['S']

    threshold = float(threshold)
    period = int(period)
    evaluation_periods = int(evaluation_periods)

    cloudwatch.put_metric_alarm(
        AlarmName=alarm_name,
        MetricName=metric_name,
        Namespace='AWS/EC2',
        Dimensions=[
          {
            'Name': 'InstanceId',
            'Value': instance_id
          }
        ],
        Threshold=threshold,
        Period=period,
        EvaluationPeriods=evaluation_periods,
        ComparisonOperator=comparison_operator,
        Statistic=statistic
    )


def handle_cloudformation_event(event):
    if event['RequestType'] == 'Create':
        logger.info('Got Create')
        logger.info('Load json alarms file')

        try:
            with open('default_alarms.json', 'r', encoding='utf-8') as jsnfile:
                cfg = json.load(jsnfile)
        except Exception as e:
            logger.error(f'Could not load default alarms from json file {jsnfile}: {e}')
            sys.exit(-1)

        application_name_tag_value = 'app'
        application_type_tag_value = 'dev'

        logger.info('Writing default alarms in ddb')
        write_default_alarms(application_name_tag_value, application_type_tag_value, TABLE_NAME, cfg)


def write_default_alarms(application_name_tag_value, application_type_tag_value, ddb_alarm_table, alarm_config):
    try:
        dynamodb.batch_write_item(
            RequestItems={
                ddb_alarm_table: [
                    {
                        "PutRequest": {
                            "Item": {
                                "Name": application_name_tag_value,
                                "Type": application_type_tag_value,
                                "Alarms": alarm_config
                            }
                        }
                    }
                ]
            }
        )
        return True

    except Exception as e:
        # If any other exceptions which we didn't expect are raised
        # then fail and log the exception message.
        logger.error(f'Error creating default alarms in dynamo db: {e}')
