variable "name" {
  type    = string
  default = "agent-tdd"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "Use 3–31 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  type = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]+$", var.vpc_id))
    error_message = "An existing VPC ID is required."
  }
}

variable "subnet_ids" {
  type = set(string)
  validation {
    condition     = length(var.subnet_ids) > 0 && alltrue([for id in var.subnet_ids : can(regex("^subnet-[0-9a-f]+$", id))])
    error_message = "Provide at least one existing private subnet."
  }
}

variable "allowed_https_cidrs" {
  description = "Explicit IPv4 HTTPS destinations. Use endpoint subnet CIDRs, or opt into public HTTPS via a NAT path."
  type        = set(string)
  validation {
    condition     = length(var.allowed_https_cidrs) > 0 && alltrue([for cidr in var.allowed_https_cidrs : can(cidrnetmask(cidr))])
    error_message = "Provide at least one valid IPv4 CIDR for outbound HTTPS."
  }
}

variable "image_digest" {
  description = "Null bootstraps ECR/network/roles only; sha256 digest registers an immutable task revision."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.image_digest == null ? true : can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "Use an immutable sha256 digest, not an image tag."
  }
}

variable "command" {
  type    = list(string)
  default = ["run", "workflow", "--telemetry", "--result-mode", "metadata"]
  validation {
    condition     = length(var.command) > 0 && alltrue([for item in var.command : length(item) > 0])
    error_message = "Container command must be nonempty."
  }
}

variable "size" {
  type    = object({ cpu = number, memory = number })
  default = { cpu = 256, memory = 512 }
  validation {
    condition = (
      var.size.cpu == 256 ? contains([512, 1024, 2048], var.size.memory) :
      var.size.cpu == 512 ? contains([1024, 2048, 3072, 4096], var.size.memory) : false
    )
    error_message = "Supported Fargate sizes: CPU 256 with 512/1024/2048 MiB, or CPU 512 with 1024/2048/3072/4096 MiB."
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
  validation {
    condition     = contains([7, 14, 30, 60, 90, 180, 365], var.log_retention_days)
    error_message = "Select an explicit retention of 7, 14, 30, 60, 90, 180, or 365 days."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "execution_timeout_seconds" {
  description = "Step Functions execution deadline, including task provisioning. ECS cancellation is best effort."
  type        = number
  default     = 300
  validation {
    condition     = var.execution_timeout_seconds >= 60 && var.execution_timeout_seconds <= 3600 && floor(var.execution_timeout_seconds) == var.execution_timeout_seconds
    error_message = "Execution deadline must be a whole number between 60 and 3600 seconds."
  }
}

variable "alarm_topic_arns" {
  description = "Existing SNS topics for failure/timeout alarms. Empty leaves alarms visible in CloudWatch without notifications."
  type        = list(string)
  default     = []
}
