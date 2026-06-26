output "api_base_url" {
  description = "Base URL Jenkins should use for PROVISION_API."
  value       = "http://${aws_lb.api.dns_name}"
}

output "alb_dns_name" {
  description = "Application Load Balancer DNS name."
  value       = aws_lb.api.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for pushing the middleware API image."
  value       = aws_ecr_repository.api.repository_url
}

output "dynamodb_table_name" {
  description = "DynamoDB table used by the API."
  value       = aws_dynamodb_table.provisioning_requests.name
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.api.name
}

output "ecs_service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.api.name
}
