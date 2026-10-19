locals {
  network_configuration = {
    awsvpcConfiguration = {
      subnets        = sort(tolist(var.subnet_ids))
      securityGroups = [aws_security_group.worker.id]
      assignPublicIp = "DISABLED"
    }
  }
}

resource "aws_iam_role" "orchestrator" {
  count = var.image_digest == null ? 0 : 1
  name  = "${var.name}-orchestrator"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnEquals    = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.name}" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "orchestrator" {
  count = var.image_digest == null ? 0 : 1
  name  = "run-fixed-worker"
  role  = aws_iam_role.orchestrator[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = aws_ecs_task_definition.worker[0].arn
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.worker.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:StopTask", "ecs:DescribeTasks"]
        Resource = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.worker.name}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:${data.aws_partition.current.partition}:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
    ]
  })
}

resource "aws_sfn_state_machine" "worker" {
  count    = var.image_digest == null ? 0 : 1
  name     = var.name
  role_arn = aws_iam_role.orchestrator[0].arn
  type     = "STANDARD"
  definition = jsonencode({
    StartAt        = "RunWorker"
    TimeoutSeconds = var.execution_timeout_seconds + 60
    States = {
      RunWorker = {
        Type           = "Task"
        Resource       = "arn:${data.aws_partition.current.partition}:states:::ecs:runTask.sync"
        TimeoutSeconds = var.execution_timeout_seconds
        Parameters = {
          Cluster         = aws_ecs_cluster.worker.arn
          TaskDefinition  = aws_ecs_task_definition.worker[0].arn
          LaunchType      = "FARGATE"
          PlatformVersion = "1.4.0"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = local.network_configuration.awsvpcConfiguration.subnets
              SecurityGroups = local.network_configuration.awsvpcConfiguration.securityGroups
              AssignPublicIp = "DISABLED"
            }
          }
        }
        Next = "CheckExitCode"
      }
      CheckExitCode = {
        Type = "Choice"
        Choices = [{
          And = [
            { Variable = "$.Containers[0].ExitCode", IsPresent = true },
            { Variable = "$.Containers[0].ExitCode", NumericEquals = 0 }
          ]
          Next = "Succeeded"
        }]
        Default = "WorkerFailed"
      }
      Succeeded    = { Type = "Succeed" }
      WorkerFailed = { Type = "Fail", Error = "WorkerFailed", Cause = "Worker exit code missing or nonzero" }
    }
  })
  depends_on = [aws_iam_role_policy.orchestrator]
}

resource "aws_cloudwatch_metric_alarm" "execution" {
  for_each            = var.image_digest == null ? toset([]) : toset(["ExecutionsFailed", "ExecutionsTimedOut"])
  alarm_name          = "${var.name}-${each.value}"
  namespace           = "AWS/States"
  metric_name         = each.value
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.worker[0].arn }
  alarm_actions       = var.alarm_topic_arns
}
