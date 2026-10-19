data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${var.region}.s3"
}
data "aws_subnet" "selected" {
  for_each = var.subnet_ids
  id       = each.value
}

resource "aws_ecr_repository" "worker" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "worker" {
  name = var.name
}

locals {
  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:*" }
      }
    }]
  })
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = local.trust
}
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = local.trust
  # No application permissions by default. Attach scoped application policies outside this module.
}
resource "aws_iam_role_policy" "execution" {
  name = "image-and-logs"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = aws_ecr_repository.worker.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.worker.arn}:*"
      }
    ]
  })
}

resource "aws_security_group" "worker" {
  name_prefix = "${var.name}-"
  description = "No ingress; explicit HTTPS and regional S3 egress only"
  vpc_id      = var.vpc_id
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = alltrue([for subnet in data.aws_subnet.selected : subnet.vpc_id == var.vpc_id])
      error_message = "Every selected subnet must belong to the supplied VPC."
    }
  }
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each          = var.allowed_https_cidrs
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "s3" {
  security_group_id = aws_security_group.worker.id
  prefix_list_id    = data.aws_prefix_list.s3.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_ecs_task_definition" "worker" {
  count                    = var.image_digest == null ? 0 : 1
  family                   = var.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.size.cpu)
  memory                   = tostring(var.size.memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = "${aws_ecr_repository.worker.repository_url}@${var.image_digest}"
    essential              = true
    command                = var.command
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    stopTimeout            = 60
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}
