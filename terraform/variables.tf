variable "aws_region" {
  description = "AWS region where the provisioning API infrastructure will run."
  type        = string
  default     = "ap-south-1"
}

variable "name" {
  description = "Base name used for production AWS resources."
  type        = string
  default     = "internal-provisioning-api"
}

variable "alb_name" {
  description = "Application Load Balancer name. AWS load balancer names cannot start with internal-."
  type        = string
  default     = "provisioning-api-alb"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the app deployment role."
  type        = string
  default     = "tusharkrbarman/internal-provisioning-api"
}

variable "github_deploy_environment" {
  description = "GitHub environment name used by the app deployment workflow."
  type        = string
  default     = "production"
}

variable "vpc_id" {
  description = "VPC ID where the ALB and ECS service will be deployed."
  type        = string
}

variable "alb_subnet_ids" {
  description = "Public subnet IDs for the Application Load Balancer."
  type        = list(string)

  validation {
    condition     = length(var.alb_subnet_ids) >= 2
    error_message = "Provide at least two ALB subnet IDs across different Availability Zones."
  }
}

variable "service_subnet_ids" {
  description = "Subnet IDs for the ECS service. Use private subnets with NAT in production."
  type        = list(string)

  validation {
    condition     = length(var.service_subnet_ids) >= 2
    error_message = "Provide at least two ECS service subnet IDs across different Availability Zones."
  }
}

variable "assign_public_ip" {
  description = "Whether the ECS task receives a public IP. Use false when running in private subnets with NAT."
  type        = bool
  default     = true
}

variable "ecr_repository_name" {
  description = "ECR repository name for the middleware API image."
  type        = string
  default     = "internal-provisioning-api"
}

variable "ecr_force_delete" {
  description = "Delete images automatically when destroying the app ECR repository."
  type        = bool
  default     = true
}

variable "container_image" {
  description = "Full container image URI. Leave empty to use the Terraform-created ECR repository plus image_tag."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Image tag used when container_image is empty."
  type        = string
  default     = "latest"
}

variable "container_port" {
  description = "Port exposed by the FastAPI container."
  type        = number
  default     = 8080
}

variable "task_cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Initial number of ECS tasks to run before service auto scaling adjusts capacity."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count >= 0
    error_message = "desired_count must be greater than or equal to 0."
  }
}

variable "autoscaling_min_capacity" {
  description = "Minimum number of ECS tasks maintained by service auto scaling."
  type        = number
  default     = 1

  validation {
    condition     = var.autoscaling_min_capacity >= 0
    error_message = "autoscaling_min_capacity must be greater than or equal to 0."
  }
}

variable "autoscaling_max_capacity" {
  description = "Maximum number of ECS tasks allowed by service auto scaling."
  type        = number
  default     = 3

  validation {
    condition     = var.autoscaling_max_capacity >= 1
    error_message = "autoscaling_max_capacity must be at least 1."
  }
}

variable "autoscaling_cpu_target" {
  description = "Target average CPU utilization percentage for ECS service auto scaling."
  type        = number
  default     = 70

  validation {
    condition     = var.autoscaling_cpu_target > 0 && var.autoscaling_cpu_target <= 100
    error_message = "autoscaling_cpu_target must be greater than 0 and less than or equal to 100."
  }
}

variable "autoscaling_memory_target" {
  description = "Target average memory utilization percentage for ECS service auto scaling."
  type        = number
  default     = 75

  validation {
    condition     = var.autoscaling_memory_target > 0 && var.autoscaling_memory_target <= 100
    error_message = "autoscaling_memory_target must be greater than 0 and less than or equal to 100."
  }
}

variable "autoscaling_scale_in_cooldown" {
  description = "Seconds to wait before allowing another scale-in action."
  type        = number
  default     = 120
}

variable "autoscaling_scale_out_cooldown" {
  description = "Seconds to wait before allowing another scale-out action."
  type        = number
  default     = 60
}

variable "dynamodb_table_name" {
  description = "DynamoDB table for provisioning request state."
  type        = string
  default     = "internal-provisioning-requests"
}

variable "reservation_id_index_name" {
  description = "DynamoDB GSI name used to find records during release calls."
  type        = string
  default     = "reservation_id-index"
}

variable "onecloud_base_url" {
  description = "Base URL for the OneCloud provider API."
  type        = string
  default     = "https://dummy-onecloud-api.onrender.com"
}

variable "gtax_base_url" {
  description = "Base URL for the GTAX provider API."
  type        = string
  default     = "https://dummy-gtax-api.onrender.com"
}

variable "provider_request_timeout_seconds" {
  description = "Timeout for provider API calls."
  type        = number
  default     = 60
}

variable "provision_poll_interval_seconds" {
  description = "Polling interval while waiting for provider image deployment."
  type        = number
  default     = 2
}

variable "provision_timeout_seconds" {
  description = "Maximum time to wait for provider image deployment."
  type        = number
  default     = 300
}

variable "provision_record_ttl_hours" {
  description = "How long provisioning records are considered valid by the application."
  type        = number
  default     = 48
}

variable "log_level" {
  description = "Application log level."
  type        = string
  default     = "INFO"
}

variable "log_retention_days" {
  description = "CloudWatch log retention period."
  type        = number
  default     = 30
}

variable "allowed_http_cidr_blocks" {
  description = "CIDR blocks allowed to call the public ALB listener."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "tags" {
  description = "Extra tags applied to all supported resources."
  type        = map(string)
  default     = {}
}
