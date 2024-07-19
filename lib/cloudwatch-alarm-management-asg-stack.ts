import { Duration, Stack, RemovalPolicy, CustomResource } from 'aws-cdk-lib';
import { Function, Runtime, AssetCode, StartingPosition } from 'aws-cdk-lib/aws-lambda';
import { Table, AttributeType, BillingMode, StreamViewType } from 'aws-cdk-lib/aws-dynamodb';
import { Rule } from 'aws-cdk-lib/aws-events';
import { LambdaFunction } from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import { Provider } from 'aws-cdk-lib/custom-resources';
import { ServicePrincipal, Policy, PolicyStatement,Role } from 'aws-cdk-lib/aws-iam';
import { DynamoEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { NagSuppressions } from 'cdk-nag';

export class CloudWatchAlarmASGStack extends Stack {
  constructor(scope: Construct, id: string) {
    super(scope, id);

    const appNameTag = this.node.tryGetContext('appNameTag'); 
    const appTypeTag = this.node.tryGetContext('appTypeTag'); 
    const snsTopicArn = this.node.tryGetContext('snsTopicArn'); 
    
    // Define the DynamoDB table
    const cwAlarmDynamoDBTable = new Table(this, 'ddbAlarmsTable', {
      partitionKey: { name: 'Name', type: AttributeType.STRING },
      sortKey: { name: 'Type', type: AttributeType.STRING },
      removalPolicy: RemovalPolicy.DESTROY,
      billingMode: BillingMode.PAY_PER_REQUEST,
      stream: StreamViewType.NEW_AND_OLD_IMAGES, // Enable DynamoDB Streams
      pointInTimeRecovery: true
    });

    // Define IAM role for Lambda function
    let lambdaExecutionRole = new Role(this, 'LambdaExecutionRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
    });
    
    // Add policies for DynamoDB , CloudWatch and EC2 access
    lambdaExecutionRole.attachInlinePolicy(new Policy(this, 'DynamoDbAccessPolicy', {
      statements: [
        new PolicyStatement({
          actions: [
            'dynamodb:GetItem',
            'dynamodb:PutItem',
            'dynamodb:UpdateItem',
            'dynamodb:DeleteItem',
            'dynamodb:BatchGetItem',
            'dynamodb:DescribeTable',
            'dynamodb:Scan',
            'dynamodb:BatchWriteItem',
          ],
          resources: [cwAlarmDynamoDBTable.tableArn]
        })
      ]  
    }));
    
    lambdaExecutionRole.attachInlinePolicy(new Policy(this, 'CWEC2Policy', {
      statements: [
        new PolicyStatement({
          actions: [
            'logs:CreateLogGroup',
            'logs:CreateLogStream',
            'logs:PutLogEvents',
            'cloudwatch:DescribeAlarms',
            'cloudwatch:ListMetrics',
            'cloudwatch:PutMetricAlarm', 
            'cloudwatch:DeleteAlarms',
            'ec2:DescribeImages',
            'ec2:DescribeInstances',
            'autoscaling:CompleteLifecycleAction',
            'autoscaling:DescribeTags',
          ],
          resources: ['*']
        })
      ]
    }));

    // Define the Lambda function
    const cwAlarmLambdaFunction = new Function(this, 'CW_AlarmsASG_Lambda', {
      runtime: Runtime.PYTHON_3_12,
      handler: 'cw_alarm.lambda_handler', 
      code: new AssetCode('./lambda/cw_alarm'),
      memorySize: 512,
      timeout: Duration.seconds(15),
      description: 'Lambda function to manage CloudWatch Alarms for ASGs',
      environment: {
        DYNAMODB_TABLE_NAME: cwAlarmDynamoDBTable.tableName,
        SNS_TOPIC_ARN: snsTopicArn
      },
      role:lambdaExecutionRole
    });   

    // Define EventBrdige rule
    const cwAlarmEventBridgeRule = new Rule(this, 'CW_AlarmsASG_EventRule', {
      eventPattern: {
        source: ['aws.autoscaling'],
        detailType: ['EC2 Instance-launch Lifecycle Action', 'EC2 Instance-terminate Lifecycle Action']
      }
    });

    // Add the Lambda function as a target for the EventBridge rule
    cwAlarmEventBridgeRule.addTarget(new LambdaFunction(cwAlarmLambdaFunction));

    const cwAlarmLambdaFunctionDefaultAlarms = new Function(this, 'Default_Alarms_Lambda', {
      runtime: Runtime.PYTHON_3_12,
      handler: 'default_alarms.lambda_handler', 
      code: new AssetCode('./lambda/ddb'),
      memorySize: 512,
      timeout: Duration.seconds(15),
      description: 'Lambda function to populate DDB table with default alarms',
      environment: {
        DYNAMODB_TABLE_NAME: cwAlarmDynamoDBTable.tableName,
        APPICATION_NAME_TAG: appNameTag,
        APPLICATION_TYPE_TAG: appTypeTag
      },
      role:lambdaExecutionRole
    });
    
    cwAlarmLambdaFunctionDefaultAlarms.addEventSource(new DynamoEventSource(cwAlarmDynamoDBTable, {
      startingPosition: StartingPosition.LATEST,
    }));
 
    const provider = new Provider(this, 'Provider', {
      onEventHandler: cwAlarmLambdaFunctionDefaultAlarms,
    });

    const resource = new CustomResource(this, 'Resource', {
      serviceToken: provider.serviceToken
    });

    NagSuppressions.addResourceSuppressionsByPath(
          this,
          '/CloudWatchAlarmASGStack/CWEC2Policy/Resource',
          [
            {
              id: 'AwsSolutions-IAM5',
              reason: 'Wildcard permissions are required for specific actions',
            },
          ],
    );

    NagSuppressions.addResourceSuppressionsByPath(
      this,
      '/CloudWatchAlarmASGStack/LambdaExecutionRole/DefaultPolicy/Resource',
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Wildcard permissions are required for specific actions',
        },
      ],
    );

    NagSuppressions.addResourceSuppressionsByPath(
      this,
      '/CloudWatchAlarmASGStack/Provider/framework-onEvent/ServiceRole/Resource',
      [
        {
          id: 'AwsSolutions-IAM4',
          reason: 'Managed policy used by default when using Provider. Upgrade to latest version planned when it becomes GA in all AWS regions: https://github.com/aws/aws-cdk/issues/28125',
        },
      ],
    );        

    NagSuppressions.addResourceSuppressionsByPath(
    this,
    '/CloudWatchAlarmASGStack/Provider/framework-onEvent/ServiceRole/DefaultPolicy/Resource',
    [
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Wildcard permissions are required for specific actions',
      },
    ]
    );

    NagSuppressions.addResourceSuppressionsByPath(
    this,
    '/CloudWatchAlarmASGStack/Provider/framework-onEvent/Resource',
    [
      {
        id: 'AwsSolutions-L1',
        reason: 'Provider lambda uses Nodejs 18 by default ',
      },
    ]
    );

  }
}