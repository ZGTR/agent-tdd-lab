mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_subnet" { defaults = { vpc_id = "vpc-0123456789abcdef0" } }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:123456789012:repository/agent-tdd"
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agent-tdd"
    }
  }
}

variables {
  vpc_id              = "vpc-0123456789abcdef0"
  subnet_ids          = ["subnet-0123456789abcdef0"]
  allowed_https_cidrs = ["10.0.0.0/16"]
}

run "bootstrap_has_no_task" {
  command = plan
  assert {
    condition     = length(aws_ecs_task_definition.worker) == 0 && length(aws_sfn_state_machine.worker) == 0
    error_message = "Bootstrap must not register a task with an unpushed or mutable image."
  }
  assert {
    condition     = aws_ecr_repository.worker.image_tag_mutability == "IMMUTABLE" && !aws_ecr_repository.worker.force_delete
    error_message = "ECR must preserve immutable images and reject destructive repository deletion."
  }
  assert {
    condition     = length(aws_security_group.worker.ingress) == 0 && output.network_configuration.awsvpcConfiguration.assignPublicIp == "DISABLED"
    error_message = "The worker must have no inbound access or public IP."
  }
}

run "pinned_task_is_hardened" {
  command = plan
  variables { image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[0].readonlyRootFilesystem
    error_message = "Worker root filesystem must be read-only."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[0].user == "10001:10001"
    error_message = "Worker must not run as root."
  }
  assert {
    condition     = endswith(jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[0].image, var.image_digest)
    error_message = "Task must use the requested immutable digest."
  }
  assert {
    condition     = aws_iam_role.execution.name != aws_iam_role.task.name && aws_cloudwatch_log_group.worker.retention_in_days == 30
    error_message = "Execution/application roles must be separated and logs must have bounded retention."
  }
}

run "reject_mutable_image" {
  command = plan
  variables { image_digest = "latest" }
  expect_failures = [var.image_digest]
}

run "reject_invalid_size" {
  command = plan
  variables { size = { cpu = 256, memory = 4096 } }
  expect_failures = [var.size]
}

run "reject_wrong_vpc" {
  command = plan
  override_data {
    target = data.aws_subnet.selected["subnet-0123456789abcdef0"]
    values = { vpc_id = "vpc-aaaaaaaaaaaaaaaaa" }
  }
  expect_failures = [aws_security_group.worker]
}
run "reject_missing_egress" {
  command = plan
  variables { allowed_https_cidrs = [] }
  expect_failures = [var.allowed_https_cidrs]
}
run "reject_unbounded_logs" {
  command = plan
  variables { log_retention_days = 0 }
  expect_failures = [var.log_retention_days]
}

run "runner_has_deadline_and_no_automatic_effect_retries" {
  command = plan
  variables { image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
  assert {
    condition     = jsondecode(aws_sfn_state_machine.worker[0].definition).States.RunWorker.TimeoutSeconds == 300
    error_message = "A managed execution deadline must bound the job."
  }
  assert {
    condition     = !can(jsondecode(aws_sfn_state_machine.worker[0].definition).States.RunWorker.Retry)
    error_message = "Effectful jobs must not be retried without application idempotency semantics."
  }
}

run "nonzero_exit_is_not_success" {
  command = plan
  variables { image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
  assert {
    condition     = jsondecode(aws_sfn_state_machine.worker[0].definition).States.CheckExitCode.Choices[0].And[1].NumericEquals == 0 && jsondecode(aws_sfn_state_machine.worker[0].definition).States.CheckExitCode.Default == "WorkerFailed"
    error_message = "A stopped task is not a successful application run without exit code zero."
  }
}

run "inference_is_opt_in" {
  command = plan
  assert {
    condition     = length(aws_iam_role_policy.task_bedrock) == 0 && length(aws_iam_role.evaluation) == 0
    error_message = "Default workers and CI must have no inference permission."
  }
}

run "selected_models_and_ci_identity_only" {
  command = plan
  variables {
    bedrock_model_arns = ["arn:aws:bedrock:us-east-1::foundation-model/example.model-v1"]
    github_repository  = "ZGTR/agent-tdd-lab"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.evaluation[0].policy).Statement[0].Action == ["bedrock:InvokeModel"] && jsondecode(aws_iam_role_policy.evaluation[0].policy).Statement[0].Resource == ["arn:aws:bedrock:us-east-1::foundation-model/example.model-v1"]
    error_message = "CI may invoke only the selected model resources."
  }
  assert {
    condition     = jsondecode(aws_iam_role.evaluation[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:ZGTR/agent-tdd-lab:environment:live-evaluation"
    error_message = "OIDC trust must bind the exact repository and protected evaluation environment."
  }
}

run "reject_wildcard_inference" {
  command = plan
  variables { bedrock_model_arns = ["arn:aws:bedrock:us-east-1::foundation-model/*"] }
  expect_failures = [var.bedrock_model_arns]
}

run "reject_ci_role_without_model_access" {
  command = plan
  variables { github_repository = "ZGTR/agent-tdd-lab" }
  expect_failures = [aws_iam_role.evaluation]
}
