# Optional inference access: no credentials or wildcard model access in state.
locals {
  inference_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = sort(tolist(var.bedrock_model_arns))
    }]
  })
}

resource "aws_iam_role_policy" "task_bedrock" {
  count  = length(var.bedrock_model_arns) == 0 ? 0 : 1
  name   = "selected-model-inference"
  role   = aws_iam_role.task.id
  policy = local.inference_policy
}

resource "aws_iam_role" "evaluation" {
  count = var.github_repository == null ? 0 : 1
  name  = "${var.name}-evaluation"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:live-evaluation"
        }
      }
    }]
  })
  lifecycle {
    precondition {
      condition     = length(var.bedrock_model_arns) > 0
      error_message = "The CI evaluation role requires explicit model/profile resources."
    }
  }
}

resource "aws_iam_role_policy" "evaluation" {
  count  = var.github_repository == null ? 0 : 1
  name   = "selected-model-inference"
  role   = aws_iam_role.evaluation[0].id
  policy = local.inference_policy
}
