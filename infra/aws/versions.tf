terraform {
  backend "s3" {}
  required_version = ">= 1.13.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge(var.tags, { Application = var.name, ManagedBy = "OpenTofu" })
  }
}
