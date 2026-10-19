output "repository_url" { value = aws_ecr_repository.worker.repository_url }
output "cluster_arn" { value = aws_ecs_cluster.worker.arn }
output "task_definition_arn" { value = try(aws_ecs_task_definition.worker[0].arn, null) }
output "task_role_name" { value = aws_iam_role.task.name }
output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "log_group_name" { value = aws_cloudwatch_log_group.worker.name }
output "network_configuration" { value = local.network_configuration }
output "state_machine_arn" { value = try(aws_sfn_state_machine.worker[0].arn, null) }
