output "ecr_repository_url" { value = aws_ecr_repository.api.repository_url }
output "api_url" { value = "http://${aws_lb.api.dns_name}" }
output "ecs_cluster" { value = aws_ecs_cluster.main.name }
