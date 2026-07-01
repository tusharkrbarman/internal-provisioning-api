# Terraform Deployment

This folder defines the production-style AWS infrastructure for the internal provisioning middleware API.

For the GitHub Actions infrastructure pipeline, see [INFRA_PIPELINE.md](INFRA_PIPELINE.md).

It creates:

- ECR repository for the API image
- DynamoDB table for provisioning request state
- CloudWatch log group for ECS logs
- IAM execution role and task role
- IAM GitHub deployment role for application releases
- ECS Fargate cluster, task definition, and service
- ECS Service Auto Scaling for Fargate task count
- Application Load Balancer, listener, target group, and security groups

## 1. Configure Variables

Copy the example file:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set:

- `vpc_id`
- `alb_subnet_ids`
- `service_subnet_ids`
- optionally `alb_name` if you want a different public load balancer name
- optionally `container_image` if you want to use an already-pushed ECR image
- optionally `github_repository` if this repo is forked or renamed
- optionally `ecr_force_delete`; it defaults to `true` so the destroy workflow can remove the app ECR repo even after images are pushed
- optionally `autoscaling_min_capacity`, `autoscaling_max_capacity`, `autoscaling_cpu_target`, and `autoscaling_memory_target`

For the current demo-style deployment, using the same public subnets for `alb_subnet_ids` and `service_subnet_ids` is acceptable.

For stricter production networking, place the ALB in public subnets, place ECS in private subnets, set `assign_public_ip = false`, and provide NAT for outbound calls to OneCloud and GTAX.

## ECS Service Auto Scaling

This project uses ECS Service Auto Scaling, not an EC2 Auto Scaling Group. Fargate manages the worker compute. Terraform creates an Application Auto Scaling target for the ECS service desired task count and two target tracking policies:

- CPU target tracking with `ECSServiceAverageCPUUtilization`
- memory target tracking with `ECSServiceAverageMemoryUtilization`

The ECS service starts with `desired_count`, then AWS can scale it between `autoscaling_min_capacity` and `autoscaling_max_capacity`. Terraform ignores later drift on `desired_count` so a normal infrastructure apply does not reset a service that AWS scaled during traffic.

For higher availability in production, set `autoscaling_min_capacity = 2` and run the service across at least two subnets/AZs.

When bootstrapping a brand-new ECR repository with no image yet, temporarily set both values to `0` so ECS does not try to start a task before the image exists:

```hcl
desired_count            = 0
autoscaling_min_capacity = 0
```

After the first image is pushed, set them back to at least `1`.

## 2. Initialize Terraform

```powershell
terraform init
```

## 3. Preview Changes

```powershell
terraform plan -var-file="terraform.tfvars"
```

## 4. Apply

```powershell
terraform apply -var-file="terraform.tfvars"
```

## 5. Push Docker Image

After Terraform creates the ECR repository, use the `ecr_repository_url` output:

```powershell
$REGION = "ap-south-1"
$ECR_URI = terraform output -raw ecr_repository_url
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

docker build -t internal-provisioning-api ..
docker tag "internal-provisioning-api:latest" "$ECR_URI`:latest"
docker push "$ECR_URI`:latest"
```

Then force a new ECS deployment:

```powershell
aws ecs update-service `
  --region ap-south-1 `
  --cluster internal-provisioning-api-cluster `
  --service internal-provisioning-api-service `
  --force-new-deployment
```

## 6. Jenkins URL

Use this output as the Jenkins `PROVISION_API` value:

```powershell
terraform output -raw api_base_url
```

Example Jenkins value:

```text
http://provisioning-api-alb-xxxxxxxx.ap-south-1.elb.amazonaws.com
```

## 7. GitHub App Deployment Secret

Terraform also outputs the IAM role used by the app deployment workflow:

```powershell
terraform output -raw github_deploy_role_arn
```

Store that value in this GitHub repository secret:

```text
AWS_DEPLOY_ROLE_ARN
```

The app deployment workflow can then build the Docker image in GitHub Actions, push it to ECR, register a new ECS task definition revision, and update the ECS service.

## Terraform Role Permissions For Auto Scaling

If the GitHub Terraform role is managed manually, make sure it can manage ECS Service Auto Scaling resources. Add these actions to the policy attached to `internal-provisioning-api-terraform-role`:

```json
{
  "Sid": "ManageProjectApplicationAutoScaling",
  "Effect": "Allow",
  "Action": [
    "application-autoscaling:RegisterScalableTarget",
    "application-autoscaling:DeregisterScalableTarget",
    "application-autoscaling:DescribeScalableTargets",
    "application-autoscaling:PutScalingPolicy",
    "application-autoscaling:DeleteScalingPolicy",
    "application-autoscaling:DescribeScalingPolicies"
  ],
  "Resource": "*"
}
```

## Existing Console-Created Resources

If you already created resources manually in AWS Console, do not blindly run `terraform apply` against the same names. In production, import existing resources into Terraform state first, or recreate them through Terraform in a clean environment.

Examples:

```powershell
terraform import aws_ecr_repository.api internal-provisioning-api
terraform import aws_dynamodb_table.provisioning_requests internal-provisioning-requests
```
