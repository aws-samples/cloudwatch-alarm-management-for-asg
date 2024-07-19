#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Aspects } from 'aws-cdk-lib';
import { CloudWatchAlarmASGStack } from '../lib/cloudwatch-alarm-management-asg-stack';
import { AwsSolutionsChecks } from 'cdk-nag';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }))
new CloudWatchAlarmASGStack(app, 'CloudWatchAlarmASGStack');